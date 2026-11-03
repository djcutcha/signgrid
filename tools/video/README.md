# SignGrid 提出動画 仮編集(Remotion)

台本順に画面録画・スクショ・ナレーションを並べ、字幕とテロップを載せて 1 本の mp4(1920x1080, 30fps, H.264)にする。

## 作り方
```
cd tools/video
npm i
npm run render        # narration の index.json を読み直してから ../../runs/video_rough/signgrid_rough_v1.mp4 を出力
npm run studio        # プレビュー
node timeline.mjs cut # 場面ごとの秒数を表示し、中央フレームを runs/video_rough/frames/ に出す
```
- 場面の長さ= max(台本の枠, ナレーション秒 + 0.8)。台本・テロップは `src/data/scenes.ts`、場面は `src/scenes/`。
- `npm run sync`(render に含む)で `runs/video_rough/assets/narration/index.json` を `src/data/narration.json` に写す。wav が差し替わったら必ず再実行。

## 素材の置き場
`public/` の下はシンボリックリンク(実体は git に入れない)。
- `public/assets` -> `runs/video_rough/assets`(録画 mp4 とナレーション wav)
- `public/img` -> `docs/img`
- `public/field` -> `runs/field_site_img`

## ライセンス
Remotion は会社利用に別途ライセンスが要る(購入は別途。鍵やライセンス情報はコードに入れない)。
