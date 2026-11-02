# 画面(kanban_karte/web/)

看板管理台帳(看板1基=1行)を、有資格者が PC とスマホで見て直して確定する画面。標準ライブラリの `http.server` と素の HTML/CSS/JS だけ(フレームワーク・ビルド・CDN なし)。画面の中身はすべて見本データ(架空の店名、無地の代用写真に番号)。

## 起動

```
python3 -m kanban_karte.web                                   # 見本データ(data/sample/)で起動。台帳は一時、再起動で消える
python3 -m kanban_karte.web --db <sqlite> --data-dir <dir>    # 自分の台帳(--port 既定 8790、127.0.0.1 だけ)
python3 -m kanban_karte.web --db <新しいsqlite> --sample      # 見本データを入れた台帳を作って起動
```

`--db` を省くと環境変数 `KANBAN_KARTE_DB`、無ければ `data/private/ledger.sqlite`、それも無ければ見本データ。画像は `--data-dir`(環境変数 `KANBAN_KARTE_DATA`)の下の相対パスを `/media/<相対パス>` で配る(下に収まらない・画像でないものは 404)。

## 画面

| 画面 | 内容 |
|---|---|
| 台帳(`#/`) | 現場を選ぶ → 看板1基=1行。法規の列(自家用・許可・期限)と安全の列(ランク・判断できず N/18・写真枚数・前回との差・状態)。ランクは下書きが破線の薄い札、確定が塗りの濃い札。行で開く。スマホ幅では1基=1枚のカード。受け付けた束があれば「下書きを作る」(鍵が無ければ無効) |
| カルテ(`#/sign/<id>`) | 写真(タップで拡大・前後送り)、18項目の区分、各項目の根拠(evidence の文と写真番号、「写真3」は押せる)、所見、法規。黄色=判断できず/下書きが要実測としたマス。マスをタップ→区分を選ぶと即 POST、右側(スマホでは下)に訂正の履歴。「確定して署名」→ 名前つきで確認 → 確定。確定後は編集不可 |
| 規則と出力(`#/rules`) | 訂正から育った規則(同じ項目・同じ所見で区分が違う規則に「矛盾の可能性」の印)、現場ごとの「東京都 第2号様式を出力」「客先A型を出力」(xlsx ダウンロード。出力は常に下書き) |

## API(JSON)

画面は台帳(`ledger/`)と本線(`pipeline/`・`output/`)の関数を呼ぶだけで、規則の作り方・ランクの再計算・様式はそちらが持つ(`web/api.py`)。

| | |
|---|---|
| `GET /api/meta` | 見本データか、下書きを作れるか(鍵の有無)、18項目の定義 |
| `GET /api/sites`、`GET /api/signs?site=` | 現場(看板数・下書き数・確定数)、看板の一覧(1行分) |
| `GET /api/signs/<id>` | 看板の全部(項目・根拠・黄色・写真の URL・訂正の履歴) |
| `POST /api/signs/<id>/items` | `{item, value, by, note?}` 訂正。`item` は項目キーか `rank`(1〜4)。`pipeline.review.apply_correction` |
| `POST /api/signs/<id>/finalize` | `{by}` 確定。`pipeline.review.finalize`。確定済みへの訂正・確定は 409 |
| `GET /api/bundles`、`POST /api/bundles/<id>/draft` | 束の一覧、下書き(`pipeline.draft.draft_bundle`。鍵が無ければ 503) |
| `GET /api/rules` | 規則(`conflicts_with` つき) |
| `GET /api/sites/<id>/output/tokyo.xlsx` / `client-a.xlsx` | 様式の出力 |
| `GET /media/<相対パス>` | 写真 |

POST は `Content-Type: application/json` だけ受ける(他サイトのフォームからの送信を避ける)。

## スクリーンショット

`python3 tools/web_screenshots.py`(Playwright / Chromium。見本データの一時台帳で PC 1280 とスマホ 390 を撮り `docs/img/` に書く)。

| | PC 1280 | スマホ 390 |
|---|---|---|
| 台帳 | ![](img/ledger-pc.png) | ![](img/ledger-sp.png) |
| カルテ(黄色のマス) | ![](img/karte-pc.png) | ![](img/karte-sp.png) |
| 黄色のマスを直した後(履歴に出る) | ![](img/karte-edit-pc.png) | ![](img/karte-edit-sp.png) |
| 確定して署名した後 | ![](img/karte-confirmed-pc.png) | ![](img/karte-confirmed-sp.png) |
| 台帳(確定後) | ![](img/ledger-after-pc.png) | ![](img/ledger-after-sp.png) |
| 規則と出力(矛盾の印) | ![](img/rules-pc.png) | ![](img/rules-sp.png) |

360 / 390 / 1280 幅で横スクロールが出ないことを確認済み。

## モックとの違い

- モックは6画面。この画面は有資格者の側(受付の下書き・台帳・カルテ・確認と署名・出力)だけ。LINE の受付(別の入口)、オーナー、行政の地図(`gov/` の `map.html`)はここに入れていない。
- モックの「要確認」は「判断できず(要実測)」と書く(台帳・pipeline の用語。区分に「要確認」は無い)。項目名は様式の原文(モックの略記ではなく)。
- 確認は、モックの「1/3 の次へ」ではなくカルテの18項目の一覧上でマスをタップして直す(スマホでもそのまま押せる)。
- 見本の写真は無地の画像に番号を重ねたもの。
