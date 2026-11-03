import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TItem, cue} from '../data/timeline';
import {C, FPS} from '../theme';
import {Drift, Odo, Shell, Word, clamp01, eo} from '../lib';

const ROWS = [
  {l: '要対応の見逃し', v: '5/9', red: true, k: '見逃し'},
  {l: '誤警報', v: '1/38', k: '誤警報'},
  {l: '完全一致', v: '70.2%', k: '完全一致'},
];
export const S07: React.FC<{t: TItem}> = ({t}) => {
  const f = useCurrentFrame();
  const cm = cue(t, 'まだ道半ば', 0.55, 0.1);
  const d = eo(clamp01((f / FPS - cm) / 0.6));
  const c1 = cue(t, 'だから判定は下書き', 0.7, 0.1), c2 = cue(t, '確定するのは', 0.85, 0.1);
  return (
    <Shell t={t}>
      <Drift frames={t.frames} />
      <div style={{position: 'absolute', left: 160, top: 170, width: 1600, opacity: 1 - 0.9 * d, transform: `scale(${1 - 0.05 * d})`, transformOrigin: '50% 40%'}}>
        <div style={{fontSize: 44, color: C.sub, fontWeight: 700, marginBottom: 20}}>正直に測りました</div>
        {ROWS.map((r, i) => {
          const at = cue(t, r.k, 0.2 + i * 0.12, 0.2);
          const p = eo(clamp01((f / FPS - at) / 0.4));
          return (
            <div key={i} style={{opacity: p, transform: `translateX(${(1 - p) * -40}px)`, display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: C.panel, borderRadius: 16, padding: '18px 56px', marginBottom: 22, borderLeft: `8px solid ${r.red ? C.warn : C.accent}`}}>
              <span style={{fontSize: 58, fontWeight: 700}}>{r.l}</span>
              <span style={{fontSize: 140, color: r.red ? C.warn : C.text}}><Odo value={r.v} at={at} size={140} color={r.red ? C.warn : C.text} /></span>
            </div>
          );
        })}
      </div>
      <Word at={c1} style={{left: 0, width: 1920, top: 330, textAlign: 'center', fontSize: 150}}>判定は<span style={{color: C.accent}}>下書き</span></Word>
      <Word at={c2} style={{left: 0, width: 1920, top: 560, textAlign: 'center', fontSize: 120}}>確定は<span style={{color: C.accent}}>有資格者</span></Word>
    </Shell>
  );
};
