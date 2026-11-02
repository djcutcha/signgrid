# 本線: 束 → 下書き → 確認 → 確定

AI は下書きまで。**確定は有資格者**。確定した看板だけが様式出力の対象になる想定(出力側は status を見ていない。下の「未接続」参照)。

```
束(bundle) ──draft──▶ signs(status=draft) ──review──▶ 訂正を corrections に記録 + 規則候補を rules に積む
                                             └─finalize─▶ status=confirmed(confirmed_at, confirmed_by)
```

## 1. 下書き(`pipeline/draft.py`)

`draft_bundle(ledger, bundle_id, judge)`。看板ごとに ClaudeJudge(例示6、現場重複なし、様式 xlsx_center の18項目系)を呼ぶ。

| signs の列 | 下書きで入る値 |
|---|---|
| `rank` / `draft_rank` | 判定役のランク 1〜4(同じ値が両方に入る。確定後に `rank` だけ動きうる) |
| `items` | 18項目のうち判断できた項目: 良好 / 経過観察 / 要改善(台帳の区分。判定役の 1→良好、2→経過観察、3・4→要改善) |
| `remark` | 判定役の reasons の先頭2つを1〜2文に |
| `evidence` | JSON `{項目キー: {photos: [写真番号], text: 1文, needs_onsite: bool}}`。写真番号は `photos` の並びの 1 始まり |
| `confidence` | 0〜1 |
| `status` | `draft`。写真が無ければ `needs_photos`(rank なし)。判定に失敗した看板は変えない |

- **判断できず**(判定役の null / 要実測)= `items` にキーを入れない(未記入)。理由は `evidence`(`needs_onsite`)に残る。確認画面の黄色= 未記入の項目と、`confidence` が低い看板。
- 確定済み(`confirmed`)の看板は再下書きで上書きしない。下書き(`draft`)は上書きする(= 訂正済みでも、確定前に再下書きすると訂正は消える)。
- **LINE の束**は看板がまだ無いので、写真全部(種別が「写真」か「不明」のページ)で1基の仮の看板 `b<束>-s001` を作る。複数基なら有資格者が確認画面で分ける前提。PDF の束は `SignGrouper` が作った看板にそのまま下書きを書く。

## 2. 確認と確定(`pipeline/review.py`)

- `apply_correction(ledger, sign_id, item, new_value, by, note=None)`: 項目の区分(1〜3 か区分名)を直す。項目は「点検箇所-番号」、様式の項目名、原文の一部(1件に決まるとき)で指定。`item='rank'` は看板全体のランク(1〜4)。下書きと同じ値は記録しない。`draft` 以外の看板は ValueError。
  - 項目の訂正で、看板の `rank` を項目の最悪値に取り直す(ただし 4 は項目では表せないので、4 の間は項目の訂正で動かさない。ランクの訂正で変える)。
- `finalize(ledger, sign_id, by)`: `draft` でランクがある看板だけ。`status=confirmed`、`confirmed_at`、`confirmed_by`。
- `rank_changed_count(ledger, bundle_id=None)` → `(確定後のランクが下書きと違う数, 確定した数)`。

### corrections 表

`id, sign_id, item(項目キー or 'rank'), old_value(NULL=判断できず), new_value, rank_before, rank_after, corrected_by, note, rule_id, created`

### 規則(rules)の生成

項目の訂正1件につき、機械生成した1行を `rules` に積む(同じ文は積み直さない)。顧客名・店名は入らない(項目名・所見・区分だけ)。

- 所見あり(訂正の `note`、無ければ下書きの evidence の文から「写真N(の)」を除いたもの):
  `基礎部・上部構造-2 基礎のクラック（ひび割れ）、… : 「支柱の根元に赤錆が広がっている」は経過観察(下書きは要改善)`
- 所見なし: `<項目キー> <原文>: 有資格者は<区分>と判定(下書きは<旧区分>)`
- 同じ「項目 + 所見(+ 条件)」で区分が違う既存の規則があれば、`conflicts_with` にその id を入れる(却下された規則は数えない)。ランクの訂正は規則にしない。
- `source` = `<看板ID> / <訂正した人>`。
- 積んだ規則は **候補(candidate)**。承認されるまで下書きに効かない(次の §承認)。`structured`(JSON)に `{item, finding, condition, result, draft, question, answer}` も持つ。

### 聞き返し(`pipeline/clarify.py`): 声 → 聞き返し → 構造化

訂正の所見(note)が無い・12字未満で条件の語(全周・根元・mm・写真…)も無いとき、**1問だけ**聞き返す(例: 所見「赤錆」→「赤錆は支柱の根元だけですか、全周ですか。写真何番で見えますか」)。
- 画面: カルテで訂正すると「聞き返し」カードが出る(`POST /api/signs/<id>/items` の応答の `clarify`)。答える → `POST /api/rules/<id>/answer`。「分からない」・空欄・答えない でも、規則は所見のまま候補に残る。
- 答えで規則の文を 項目 / 所見 / 条件(範囲・程度)/ 区分 に直し、写真番号は `structured.condition.photos` にだけ残す(看板ごとの番号なので規則の文には入れない)。例: `…: 「赤錆」(全周に回っている)は要改善(下書きは経過観察)`。条件が付くと別の規則になり、同じ条件で区分が違うときだけ矛盾になる。
- 直せるのは候補だけ(承認・却下した規則は直さない)。
- 聞き返し役: `ClaudeClarifier`(Sonnet 5.5。1問の生成と答えの構造化、1回あたり約 $0.002〜0.007)。`--sample`・鍵なし・API 失敗のときは `FixedClarifier`(固定の聞き返し文、答えの機械的な構造化)。

### 承認(`review.approve_rule / reject_rule / resolve_conflict`)

`rules.status` = candidate(既定)/ approved / rejected、`approved_by`、`approved_at`、`resolution`。古い台帳は `ensure_schema` が ALTER で足し、既存の規則は candidate になる(= 承認するまで下書きに効かない)。
- `POST /api/rules/<id>/approve|reject`(`by` 必須)。承認済みの規則と矛盾する間は承認できない(409)。
- `POST /api/rules/resolve` `{keep, drop, by, reason}`: 矛盾の組を1操作で。残す側 approved、他方 rejected、理由を `resolution` に。
- 画面(規則と出力): 状態チップ、承認/却下ボタン、矛盾の組は並べて表示して「こちらを残す」。

## 3. 規則を次回に効かせる

`ClaudeJudge(rules=[...])` / `set_rules()`: 例示ブロックの後ろ(キャッシュ印の外)に「有資格者の規則(最新20件)」を入れる。空なら送る物は変わらない(既定)。
- **入るのは承認済み(approved)だけ**。候補・却下は入れない(`Ledger.recent_rules`)。
- 下書き: `pipeline.cli draft` と Web の「下書きを作る」は台帳の承認済みの最新20件を入れる(`--no-rules` で外す)。
- 評価: `python3 -m kanban_karte.eval.run_eval --judge claude --rules-from-db <台帳>`(既定オフ)。結果 JSON の `config.rules` に使った規則が残る。
- 効果測定(`tools/seed_eval_rules.py` で承認済み規則 6 本の台帳を作り、Day2 の部分集合 102基・例示6・Opus 5.5)。種は評価用の看板から作らない:

| 条件 | 完全一致 | ±1 | 見逃し | 誤警報 | 費用 |
|---|---|---|---|---|---|
| 規則なし(既存の2回: 2026-11-02 の 102基・例示6・Opus 5.5) | 71.6% / 71.6% | 99.0% / 99.0% | 50.0% (5/10) / 30.0% | 3.3% / 4.3% | $3.64 / $3.66 |
| 承認済み規則 6 本 | 71.6% | 99.0% | 50.0% (5/10) | 4.3% | $3.85 |

  **この規則 6 本では、判定の数字は動かなかった**(完全一致・±1 は同じ。見逃し・誤警報の差は規則なしの2回の間の揺れの範囲)。規則なしは同条件の既存の測定を再利用した(費用の上限のため今回は測り直していない)。102基のうち 6 本の規則が当たる看板は少なく、規則が効くかは当たる項目だけで見る必要がある(未実施)。

## 4. コマンド

```
python3 -m kanban_karte.pipeline.cli draft    --bundle <束ID> [--no-rules] [--examples 6]
python3 -m kanban_karte.pipeline.cli review   --sign <看板ID> --item <項目名> --value <1-3> --by <有資格者> [--note 所見]
python3 -m kanban_karte.pipeline.cli finalize --sign <看板ID> --by <有資格者>
python3 -m kanban_karte.pipeline.cli changed  [--bundle <束ID>]
```
台帳は `--db`(既定 data/private/ledger.sqlite、環境変数 KANBAN_KARTE_DB)。台帳の版は 2(signs に status / draft_rank / confidence / evidence / confirmed_at / confirmed_by、corrections 表を追加。古い台帳は開くと自動で上がる)。

## 5. 試走(実 API、例示側の実写真 3基)

2026-11-02 実施(Sonnet 5.5、費用 約 $0.07)。結果の要約(所見の文は載せない):

- 下書きと正解の対応: 正解1→下書き2、正解2→下書き2、正解3→下書き3。
- 所見に店名・固有名は出なかった。項目は看板あたり 6〜8 件。
- 参考(同じ102基・例示6の評価、別の測定): Opus 5.5 は完全一致 71.6%、見逃し 5/10、誤警報 3.3%。Sonnet 5.5 は 63.7% / 3/10 / 13.0%。Opus は要対応を見逃す側に寄る。

## 未接続・未対応

- 様式出力(`output/`)は `status` を見ない。「確定した看板だけを出す」はまだ効いていない。
- 看板の分割・併合(本当の束ね)の画面・操作は無い。
- 確定後の訂正(差し戻し)は受けない。
