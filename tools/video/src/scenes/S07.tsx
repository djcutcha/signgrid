import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TItem, cue} from '../data/timeline';
import {C, FPS} from '../theme';
import {Key, Shell, Word, ZoomVideo, clamp01} from '../lib';

const STEPS = ['訂正', '聞き返し', '規則候補', '矛盾', '承認', '次に効く'];
const TH = [0, 0.16, 0.3, 0.5, 0.68, 0.86];
const KEYS: Key[] = [
  {u: 0, cx: 0.4, cy: 0.5, s: 1.9, ring: [0.35, 0.55]},
  {u: 0.16, cx: 0.42, cy: 0.3, s: 2.0, ring: [0.4, 0.33]},
  {u: 0.3, cx: 0.55, cy: 0.3, s: 1.7},
  {u: 0.5, cx: 0.33, cy: 0.42, s: 2.0, ring: [0.3, 0.4]},
  {u: 0.68, cx: 0.35, cy: 0.38, s: 2.0, ring: [0.2, 0.46]},
  {u: 0.86, cx: 0.62, cy: 0.3, s: 1.7, ring: [0.6, 0.3]},
  {u: 1, cx: 0.62, cy: 0.35, s: 1.7},
];

export const S07: React.FC<{t: TItem}> = ({t}) => {
  const f = useCurrentFrame();
  const rate = 18 / (t.sec - 0.9);
  const u = clamp01(((f - 9) / FPS) * rate / 18);
  const idx = TH.reduce((a, th, i) => (u >= th ? i : a), 0);
  const kc = cue(t, '有資格者の目が', 0.85, 0.2);
  return (
    <Shell t={t}>
      <ZoomVideo src="assets/02_inherit-loop_pc.mp4" clipSec={18} rate={rate} keys={KEYS} start={9} />
      <div style={{position: 'absolute', left: 0, width: 1920, top: 36, display: 'flex', justifyContent: 'center', gap: 14}}>
        {STEPS.map((s, i) => (
          <div key={i} style={{padding: '14px 30px', borderRadius: 14, fontSize: 40, fontWeight: 800, background: i === idx ? C.accent : 'rgba(11,18,32,.82)', color: i <= idx ? '#fff' : 'rgba(255,255,255,.45)', transform: `scale(${i === idx ? 1.08 : 1})`}}>{s}</div>
        ))}
      </div>
      <Word at={kc} style={{left: 0, width: 1920, bottom: 150, textAlign: 'center'}}>
        <span style={{display: 'inline-block', background: 'rgba(11,18,32,.9)', padding: '20px 56px', borderRadius: 16, fontSize: 84}}>知識が、<span style={{color: C.accent}}>人の頭の外</span>に残る</span>
      </Word>
    </Shell>
  );
};
