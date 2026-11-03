// runs/video_v2/assets/narration/index.json を src/data/narration.json に写す(最終レンダ前に必ず実行)
import fs from 'node:fs';
import path from 'node:path';
const src = path.resolve('../../runs/video_v2/assets/narration/index.json');
const rows = JSON.parse(fs.readFileSync(src, 'utf8')).map((r) => ({
  scene: r.scene,
  file: path.basename(r.file),
  text: r.text,
  duration: r.duration,
}));
fs.writeFileSync('src/data/narration.json', JSON.stringify(rows, null, 1));
const total = rows.reduce((a, r) => a + r.duration, 0);
console.log(`synced ${rows.length} scenes, narration total ${total.toFixed(1)}s, mtime ${fs.statSync(src).mtime.toISOString()}`);
