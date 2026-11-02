# 入口1: LINE

有資格者が現場で、看板の写真と記入済みの点検用紙の写真を LINE に送るだけ(行動は変えない)。
送られた物は看板管理台帳の下書きの元(束)になる。位置は写真の EXIF から読む。

## 何が起きるか

1. LINE が `POST /line/webhook` を呼ぶ。`X-Line-Signature` を `LINE_CHANNEL_SECRET` で検証し、合わなければ 401 で何もしない。
2. 画像は Messaging API の content エンドポイントから取得し、`data/private/line_inbox/<userId>/<日付>/<メッセージID>.jpg` に保存(未追跡)。
3. 同じ送信者から直前のメッセージの 10 分以内に届いた物は 1 件の「現場の束」にまとまる(最初から数えず、最後のメッセージからの 10 分)。
   台帳には次のように入る。
   - `bundles`: 1 束 1 行。`file` は `line://<userId>/<日付>/<最初のメッセージID>`(入口2の PDF と区別する印。台帳に kind 列は無い)。`status` は `pages_registered`。
   - `pages`: 届いた画像 1 枚が 1 ページ。種別は `不明`(写真か点検用紙かは後段の分類役が決める)。画像パスは絶対パス。
   - `sites`: 位置が取れた時だけ作る(id は `line-<束id>`、名前は `LINE受信 日付 時刻`)。位置は束で最初に取れた GPS(写真の EXIF、または LINE の位置情報メッセージ)。
   - テキストは `messages.jsonl` にメモとして残すだけで、現場名や台帳の項目にはしない。
4. 返事は 1 回の配信につき 1 通。例: `受け取りました。写真 6 枚。看板カルテの下書きを作っています`(枚数は束の累計)。挨拶・受付番号・待たせる文言は出さない。友だち追加のときだけ案内を 1 通。
5. LINE の再送(同じメッセージ ID)は数えず、返事もしない。画像を取得できなかった時は、その枚数だけ返事に添える。

## 現場の決め方(v2)

LINE の通常送信は写真の EXIF から GPS を落とすので、位置は次の順で決める。

1. 束に位置も現場も無い時、返信に「今日の現場はどれですか」と、案件(`data/sample/jobs.json`、予定日が今日±1日、最大4件)+「その他(現在地を送る)」のクイックリプライを付ける。
   選ぶと(postback)`bundles.site_id` に案件の現場(`sites.id = job-<案件ID>`)を紐づけ、住所・自治体・座標(国土地理院の住所検索 API、`<inbox>/address_cache.json` にキャッシュ、1秒1回以下)を入れて「〇〇で受け付けました」と返す。
   「その他」は現在地を送るボタン(`https://line.me/R/nv/location`)を返す。案件の出どころは `serve --jobs` で差し替える(実運用は SignJOBZ の点検指示書、後日接続)。
2. 位置情報メッセージは、同じ送信者の直近 60 分以内で現場の無い最新の束に付ける(無ければ従来どおり)。写真の束ねは 10 分窓のまま。
3. リッチメニュー(写真を送る / 現在地を送る / 今日の案件): `cli richmenu --dry-run` で JSON と画像(`data/private/richmenu.png`)を作り、`--apply` で LINE に作成・既定に設定。

返信の送信結果は logger `kanban_karte.line` に出る(成功 `reply ok 1通 bundle=#n`、失敗は HTTP ステータスと本文)。

## チャネル作成後に設定するもの

環境変数(コミットしない。`.env` か実行環境のシークレットに置く):

| 名前 | 中身 | 取得場所 |
| --- | --- | --- |
| `LINE_CHANNEL_SECRET` | チャネルシークレット | LINE Developers コンソール > チャネル基本設定 |
| `LINE_CHANNEL_ACCESS_TOKEN` | チャネルアクセストークン(長期) | Messaging API 設定 |

LINE Developers コンソールの Messaging API 設定で:

- Webhook URL: `https://<後で入れる>/line/webhook`(公開 URL は Cloudflare Tunnel か Vercel かを本線が決める。決まったらここに書く)
- Webhook の利用: オン
- 応答メッセージ(自動応答)・あいさつメッセージ: オフ(返事はこのサーバが出す)

## ローカルでの起動と検証

LINE も鍵も無しで、署名つきの模擬 webhook を流して一通り確かめられる。

```
# 模擬: 画像フォルダを同じプロセスに立てた仮サーバへ送る(台帳・受信箱は data/private/line_sim/ に作る)
python3 -m kanban_karte.intake.line.cli simulate --images 画像フォルダ/
python3 -m kanban_karte.intake.line.cli simulate --images 画像フォルダ/ --location 35.68,139.69 --text 裏口

# 本番の形で起動(LINE に繋ぐ。上の 2 つの環境変数が要る)
LINE_CHANNEL_SECRET=... LINE_CHANNEL_ACCESS_TOKEN=... python3 -m kanban_karte.intake.line.cli serve --port 8787

# 別プロセスの serve へ模擬を送る(LINE に繋がない模擬サーバ。画像は --sim-content-dir から読み、返事は画面に出すだけ)
LINE_CHANNEL_SECRET=x python3 -m kanban_karte.intake.line.cli serve --sim-content-dir data/private/line_sim_content
LINE_CHANNEL_SECRET=x python3 -m kanban_karte.intake.line.cli simulate --images 画像フォルダ/ --url http://127.0.0.1:8787
```

`serve` の台帳は既定で `data/private/ledger.sqlite`(`--db` / `KANBAN_KARTE_DB`)、受信箱は `data/private/line_inbox`(`--inbox` / `KANBAN_KARTE_LINE_INBOX`)。
ヘルスチェックは `GET /healthz`。

## 未確認・注意

- LINE は、通常の写真送信で EXIF(位置)を落とす可能性がある。未検証。実機で「写真」として送った画像に GPS が残るか、`messages.jsonl` の `gps` で確認すること。残らない場合は、LINE の位置情報メッセージ(対応済み)か、ファイルとして送る運用が要る。
- 返事は reply token を使う。token は短時間で失効するので、画像の取得が長引くと返事に失敗することがある(保存は済む。失敗は `Delivery.errors` とログに出る)。
- 1 配信の処理は同期。画像が多い配信は LINE のタイムアウトに近づく可能性がある。必要になったらキューに分ける。
