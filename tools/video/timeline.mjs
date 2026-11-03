// 場面ごとの開始・秒数を表示し、各場面の中央フレームを mp4 から runs/video_rough/frames/<場面>.png に切り出す
// 使い方: node timeline.mjs [cut]
import fs from 'node:fs';
import {execFileSync} from 'node:child_process';
const src = fs.readFileSync('src/data/scenes.ts', 'utf8');
const slots = [...src.matchAll(/id: '([0-9a-z]+)', title: '([^']*)', slot: (\d+)/g)].map((m) => ({id: m[1], title: m[2], slot: +m[3]}));
const narr = JSON.parse(fs.readFileSync('src/data/narration.json', 'utf8'));
let t0 = 0; const out = [];
for (const s of slots) {
  const n = narr.find((r) => r.scene === s.id);
  const frames = Math.round(Math.max(s.slot, n ? n.duration + 0.8 : 0) * 30);
  out.push({...s, start: t0 / 30, sec: frames / 30, narr: n ? n.duration : null});
  t0 += frames;
}
for (const o of out) console.log(`${o.id}\t${o.title}\t開始${o.start.toFixed(1)}s\t${o.sec.toFixed(1)}s\t(枠${o.slot}, 声${o.narr ?? '無し'})`);
console.log(`合計 ${(t0 / 30).toFixed(1)}s`);
if (process.argv[2] === 'cut') {
  for (const o of out) {
    execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-ss', String(o.start + o.sec / 2), '-i', '../../runs/video_rough/signgrid_rough_v1.mp4', '-frames:v', '1', `../../runs/video_rough/frames/${o.id}.png`]);
  }
}
