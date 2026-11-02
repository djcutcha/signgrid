"""入口1: LINE。有資格者が現場で写真と記入済みの点検用紙を LINE に送るだけで、台帳の束(下書きの元)になる。

構成:
  webhook.py  署名検証・イベント処理・HTTP サーバ(標準ライブラリ)
  session.py  10分以内の続けて届いた物を1件の「現場の束」にまとめ、台帳に登録する
  exif.py     写真の EXIF から位置を読む
  reply.py    受け取りの返事の文面(短く。毎回の挨拶や受付番号は出さない)
  client.py   LINE Messaging API の呼び出し(画像の取得・返信)。検証用の差し替え口つき
  simulate.py LINE が無くても検証できる模擬 webhook
  cli.py      python3 -m kanban_karte.intake.line.cli {serve,simulate}
"""
