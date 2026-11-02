# 国土地理院 市区町村コード表(複製)

- ファイル: `muni.csv`(列: muniCd[5桁・ゼロ埋め], prefCd[2桁], prefName, muniName)
- 出典: 国土地理院「地理院地図」が使う市区町村コード表 https://maps.gsi.go.jp/js/muni.js (`GSI.MUNI_ARRAY`)
- 取得日: 2026-11-02(1919行)
- 加工: JS 配列を CSV に変換、コードを5桁にゼロ埋め、全角空白を半角空白にしただけ。内容は書き換えていない。
- 備考: 国土地理院の逆ジオコーダ(`LonLatToAddress`)が返す `muniCd` と同じ体系(例: 越谷市=11222)。
  当初の想定だった「公開 CSV」の配布ページ(www.gsi.go.jp/KOKUJYOHO/MUNI-download.html)は 2026-11-02 時点でトップページへ転送され
  取得できなかったため、同じ表を持つ地理院地図の muni.js から複製した。
- 利用: 国土地理院の利用規約に従い、出典を表示する。
