// 場面の秒数を表示し、各場面の 25/50/75% のフレームを runs/video_v2/frames/<場面>_<25|50|75>.png に切り出す
// 使い方: node timeline.mjs [cut]
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
const ids = ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11'];
const narr = JSON.parse(fs.readFileSync('src/data/narration.json', 'utf8'));
let t0 = 0; const out = [];
for (const id of ids) {
  const n = narr.find((r) => r.scene === id);
  const frames = Math.round(Math.max(4, (n ? n.duration : 6) + 1.0) * 30);
  out.push({id, start: t0 / 30, sec: frames / 30, narr: n ? n.duration : null});
  t0 += frames;
}
for (const o of out) console.log(`${o.id}\t開始${o.start.toFixed(2)}s\t${o.sec.toFixed(2)}s\t(声${o.narr})`);
console.log(`合計 ${(t0 / 30).toFixed(2)}s`);
if (process.argv[2] === 'cut') {
  fs.mkdirSync(`../../runs/video_v2/frames${process.env.OUT_NAME ? '_' + process.env.OUT_NAME : ''}`, {recursive: true});
  for (const o of out) for (const p of [25, 50, 75]) {
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-ss', String(o.start + (o.sec * p) / 100), '-i', `../../runs/video_v2/${process.env.OUT_NAME || 'signgrid_v2'}.mp4`, '-frames:v', '1', `../../runs/video_v2/frames${process.env.OUT_NAME ? '_' + process.env.OUT_NAME : ''}/${o.id}_${p}.png`]);
  }
}
