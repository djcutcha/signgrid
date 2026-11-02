# OCR(ページ種別 v2・看板への束ね v2)

macOS 標準の Vision フレームワークで、ページ画像の**上部の帯だけ**を読む。ローカル実行で外部通信はない。

## ビルド

```
swiftc -O kanban_karte/ocr/vision_ocr.swift -o data/private/vision_ocr
```

`data/private/` は未追跡。OCR の結果は `data/private/ocr_cache/` に、帯の画素のハッシュをキーにして保存する
(同じ画像は再実行しても OCR しない)。

## 使うところ

- `kanban_karte/intake/ocr_classifier.py` — ページ種別 v2。上部 25% の決まり文句(作業指示書・危険予知・点検表・帳票の項目名・地図)と、
  `features.py` の画素特徴(写真セル数)を組み合わせる。判定本体 `decide` は文字列と特徴量だけを受ける純関数。
- `kanban_karte/intake/ocr_grouper.py` — 看板への束ね v2。完成報告書の各面(2面付けは左右別)の見出し帯を読み、
  右上の `P.n`(その看板の何面目か)が 1 ずつ増えれば同じ看板の続き、`P.1` や増えないなら新しい看板。
  `P.n` が読めない面は、アイテムNo・写真No・品名のうち共通に読めた項目がすべて一致するときだけ続きとする。
  調査票形式(店舗名・調査日・調査対象の見出し)は 1面=1基。

## 測る

```
python3 -m kanban_karte.eval.page_kind_eval --classifier ocr     # 混同行列(runs/ に出力)
python3 -m kanban_karte.eval.grouping_eval --kinds truth         # 束ねの一致率(v1 と v2 を並べる)
python3 -m tools.page_dump                                       # 全ページの OCR ダンプ(キャッシュも温まる)
```

正解データは `data/private/sources.toml` が指す先(読むだけ)。AI は確定しない。ここで出す種別・束ねは下書きで、
有資格者が確定する。

## 既知の限界

- 束ねの正解には、離れたページを 1 基に束ねたもの(例: 16,21,22,65,70)がある。隣接ベースの束ね方では届かない。
- 写真セルが検出できない面(白っぽい写真など)は束ねの対象にならず、正解の面の集合と合わない。
- `P.n` が降順(3,2,1)の看板が昇順の看板と同じ束に混在する。見分けられず、降順の看板は分かれて出る。
- 「写真」「その他」ページは種別の手掛かりが無く、v2 でも「不明」になる(再現率 0)。
