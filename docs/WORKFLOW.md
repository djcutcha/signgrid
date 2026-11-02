# 一周のデモ: 訂正が規則に育ち、矛盾を知らせ、承認された規則が次の下書きに効く

テーマ「ベテランの仕事のやり方を、本人が現場にいるうちに、次の人が受け継げる形にする」の、知識が育つ工程(声 → 聞き返し → 構造化 → 承認 → 矛盾検出)を、見本データ(`--sample`)の固定データで一周する。API は呼ばない(聞き返しは固定文、下書きは固定値の見本の判定役。実際の判定ではない)。

撮り直し: `python3 tools/web_screenshots.py --only workflow`(PC 1280 と SP 390。`docs/img/workflow-*-{pc,sp}.png`)

| # | 場面 | 画像(PC / SP) |
|---|---|---|
| 1 | **写真 → 下書き**: AI の下書き(黄色は判断できない・確信が低いマス)。確定は有資格者だけ。 | [pc](img/workflow-1-draft-pc.png) / [sp](img/workflow-1-draft-sp.png) |
| 2 | **訂正 → 聞き返し**: 取付部-1 を「要改善」に直し、所見に「赤錆」とだけ書くと、1問だけ聞き返す(範囲・程度・写真番号)。 | [pc](img/workflow-2-clarify-pc.png) / [sp](img/workflow-2-clarify-sp.png) |
| 3 | **答え → 規則候補**: 「全周に回っている」と答えると、規則が 項目 / 所見 / 条件 / 区分 の形で候補に積まれる。答えなくても所見のまま積まれる。 | [pc](img/workflow-3-candidate-pc.png) / [sp](img/workflow-3-candidate-sp.png) |
| 4 | **矛盾を知らせる**: 別の看板で、同じ項目・同じ所見・同じ条件に別の区分(経過観察)の訂正が入ると、2つの規則を並べて矛盾を知らせる。 | [pc](img/workflow-4-conflict-pc.png) / [sp](img/workflow-4-conflict-sp.png) |
| 5 | **承認前は効かない**: 候補のままでは、次の看板の下書きに規則は入らない(取付部-1 は下書きのまま経過観察)。 | [pc](img/workflow-5-not-yet-pc.png) / [sp](img/workflow-5-not-yet-sp.png) |
| 6 | **どちらを残すか → 承認**: 「こちらを残す」1操作で、残す側が承認済み・他方が却下になり、理由と承認者が残る。 | [pc](img/workflow-6-approved-pc.png) / [sp](img/workflow-6-approved-sp.png) |
| 7 | **承認済みが次の下書きに効く**: 同じ看板を下書きし直すと、承認済みの規則に従って取付部-1 が要改善になり、根拠にその旨が出る。 | [pc](img/workflow-7-effect-pc.png) / [sp](img/workflow-7-effect-sp.png) |

注意: 7 の下書きは固定値の見本(1項目だけ判定する偽の判定役)なので、他の17項目は「判断できず」の黄色になっている。規則が実際の下書きに効くかの測定は [EVAL.md](EVAL.md) の「承認済み規則 N 本」の行と [PIPELINE.md](PIPELINE.md) §3。
