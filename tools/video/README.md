# SignGrid 提出動画(Remotion)

画面録画・生成写真・ナレーション・BGM を台本順に並べ、字幕とテロップを載せて 1 本の mp4(1920x1080, 30fps, H.264)にする。composition は `Submission`(`src/Root.tsx`)。

## 現行版(v3、2026-11-04)

- 場面は 12: `01` `02` `03` `04` `05` `06a` `06b` `07` `07b` `08` `09` `10`(`src/data/timeline.ts` の `SCENE_IDS`、場面の中身は `src/scenes/S<番号>.tsx`)。v3 は v2 に `07b`(写真からの寸法推定と初回の実測)を足した版。
- 場面の長さ= max(4 秒, ナレーション秒 + 1.0 秒)。ナレーションは場面頭から 0.4 秒後に始まる。ナレーションの無い場面は 6 秒として数える。
- 台詞(字幕)はナレーションの `index.json` の `text` をそのまま使う。台詞を変えるときは wav と `index.json` を作り直す。
- BGM は声の区間で下げ、冒頭と場面 `04` のロゴで一瞬上げ、末尾 2 秒でフェードする(`src/Root.tsx` の `bgmGain`)。

## 作り方
```
cd tools/video
npm i
npm run render        # sync_narration.mjs を走らせてから ../../runs/video_v2/signgrid_v2.mp4 を出力
npm run studio        # プレビュー
node timeline.mjs     # 場面ごとの開始秒・長さ・声の秒を表示
node timeline.mjs cut # 加えて、各場面の 25/50/75% のフレームを ../../runs/video_v2/frames/<場面>_<25|50|75>.png に出す
```
- `npm run sync`(render に含む)で `runs/video_v2/assets/<NARR_DIR>/index.json` を `src/data/narration.json` に写す。wav を差し替えたら必ず再実行する。
- 出力名を変えるときは render を直接呼ぶ: `node sync_narration.mjs && npx remotion render src/index.ts Submission ../../runs/video_v2/<名前>.mp4 --codec h264`

## ナレーションと出力名の切替(環境変数)

| 変数 | 使う所 | 既定 | 例 |
|---|---|---|---|
| `NARR_DIR` | `sync_narration.mjs` | `narration`(本人の声) | `NARR_DIR=narration_gacrux node sync_narration.mjs`(プリセットの声 Gacrux) |
| `OUT_NAME` | `timeline.mjs cut` | `signgrid_v2` | `OUT_NAME=signgrid_v2_gacrux node timeline.mjs cut`(`runs/video_v2/signgrid_v2_gacrux.mp4` からフレームを `runs/video_v2/frames_signgrid_v2_gacrux/` へ) |

- `src/data/narration.json` には声の置き場(`dir`)も入る。場面は `public/assets/<dir>/<file>` を鳴らす。
- 声を切り替えたら、場面の長さも変わる(声の秒で決まるため)。`node timeline.mjs` で合計秒を確かめる。

## 素材の置き場

`public/` の下はシンボリックリンク(実体は git に入れない)。

| リンク | 実体 | 中身 |
|---|---|---|
| `public/assets` | `runs/video_v2/assets` | 画面録画 mp4(`01_demo-start_pc` `02_inherit-loop_pc` `03_burden-output_pc` `04_gov_sp` `05_owner_sp` `06_measure_pc`)、ナレーション(`narration/`、`narration_gacrux/`。wav と `index.json`)、生成写真(`images/`)、BGM(`music/bgm_v2.wav`) |
| `public/img` | `docs/img` | 画面のスクリーンショット(git に入っている) |
| `public/field` | `runs/field_site_img` | 客先 1店舗の画面写真(非公開。現行の場面では使っていない) |

- **`runs/` は git に入らないので、素材は main のチェックアウトには無い**。2026-11-04 時点の実体は、動画を作った git worktree(`feat/video-v2`)の `runs/video_v2/assets/` にある。新しい worktree でレンダするときは、そこから `runs/video_v2/assets/` を複製するかリンクを張ってから `npm i` と `npm run render` を行う。
- 素材が無いまま render すると、`sync_narration.mjs` が `index.json` を読めずに止まる。

## ライセンス
Remotion は会社利用に別途ライセンスが要る(購入は別途。鍵やライセンス情報はコードに入れない)。
