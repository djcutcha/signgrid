import React from 'react';
import {TItem, cue} from '../data/timeline';
import {C} from '../theme';
import {Chars, Drift, Fill, Photo, Shell, Word, clamp01, eo, useSec} from '../lib';

// 実写に差し替えるときは、ここに runs/video_v2/assets/images/ の下のファイル名を入れる(例: 'assets/images/02_self.png')。null の間は暗い背景
const SELF_PHOTO: string | null = null;

export const S02: React.FC<{t: TItem}> = ({t}) => {
  const s = useSec();
  const k = cue(t, '屋外広告士', 0.3, 0.2);
  const line = eo(clamp01((s - k - 0.5) / 0.6));
  const zoom = 1 + 0.05 * clamp01(s / t.sec);
  return (
    <Shell t={t}>
      {SELF_PHOTO ? <Photo src={SELF_PHOTO} frames={t.frames} z0={1.0} z1={1.06} grad={0.9} /> : <Drift frames={t.frames} />}
      <Fill style={{transform: `scale(${zoom})`}}>
        <Word at={0.3} style={{left: 0, width: 1920, top: 330, textAlign: 'center', fontSize: 40, fontWeight: 700, color: C.sub, letterSpacing: 6}}>私は</Word>
        <div style={{position: 'absolute', left: 0, width: 1920, top: 400, textAlign: 'center'}}>
          <Chars text="屋外広告士" at={k} size={190} step={0.09} />
        </div>
        <div style={{position: 'absolute', left: 960 - 480 * line, width: 960 * line, top: 650, height: 6, background: C.accent, borderRadius: 3}} />
        <Word at={cue(t, '看板をつくり', 0.5, 0.1)} style={{left: 0, width: 1920, top: 700, textAlign: 'center', fontSize: 62, fontWeight: 700}}>看板をつくり、点検する会社</Word>
      </Fill>
    </Shell>
  );
};
