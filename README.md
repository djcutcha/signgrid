# SignGrid(サイングリッド)/ 看板カルテ

Built with Claude: Japan 2026 (2026-11-02 〜 2026-11-08) で、看板製作会社がゼロから作る作品。SignGrid は、把握されていない街の看板を台帳と地図に載せる道具。看板1基ごとの記録を「看板カルテ」と呼ぶ。
SignGrid: a ledger and map for the signboards nobody tracks. Built from scratch during Built with Claude: Japan 2026 by a Japanese signboard maker. Each sign's record is a "karte".

## 何を作るか / What this is
- 写真と紙の点検表から、看板1基=1行の「看板管理台帳」を作る
- 位置から自治体の条例を引き、自家用か・許可が要るか・期限はいつかを出す(法規の判定)
- 有資格者の18項目の点検を AI が下書きし、有資格者が確定する(安全の判定)
- 看板屋 → 店舗オーナー → 行政 の3者に同じ台帳を向ける

## 使い方(骨組み) / Quick start
```
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt && .venv/bin/pip install -e .
kanban-karte ledger import data/sample/ledger.json   # 見本データを台帳に入れる
kanban-karte ledger list
kanban-karte ledger show demo-001                    # 看板1基の台帳の1行(法規の列・安全の列)
kanban-karte intake path/to/scan.pdf                 # 入口2: 紙の束(スキャンした PDF)を取り込む
python3 -m unittest                                  # テスト
```
- 台帳は SQLite(既定 `data/private/ledger.sqlite`、`--db` で変更)。看板1基=1行(`kanban_karte/ledger/schema.sql`)
- 入口2(`kanban_karte/intake/`): PDF の埋め込み画像を取り出してページを登録し、ページ種別を付け、完成報告書の写真セルから看板の下書きを作る。判定は未確定のまま有資格者に渡す(AI は確定しない)。ページ種別の判定は今は仮のヒューリスティックで、後で Claude に差し替える口がある
- 法規の列(自家用か・許可の要否・期限)は、この段階では空。`kanban_karte/legal/` に住所→自治体の判定の口だけある
- 評価の仕組み(AI の判定をどれだけ当てるかを測る): `docs/EVAL.md`

## 見本データ / Sample data
`data/sample/` に、**架空の店(見本ストア 北見台店)**の看板3基分の台帳 JSON(`ledger.json`)と、写真の代わりの無地の画像(`photos/`)がある。店名・住所・日付・判定はすべて架空で、顧客データではない。法規の列・安全の列(18項目の判定、前回との差)がひと通り入っており、`demo-003` は法規の列をわざと空にして「未判定」の見え方を示している。自社の看板写真は後で差し替える。

## 進め方 / How we build
- 開発開始 2026-11-02 12:30 JST。期間前のコードは一切持ち込まない
- 企画・絵コンテ・作業表は期間前に作り、このリポジトリの外に置いている(運営 FAQ Q4)
- 顧客データ・社員名・単価・検算済みの規則はこのリポジトリに入れない。見本データのみ同梱する

## 持ち込みデータ / Data brought in
(README 完成時に、一覧と取得時期をここに書く)

## ライセンス / License
(決定後に記載)
