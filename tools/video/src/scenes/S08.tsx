import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TItem, cue} from '../data/timeline';
import {C, FPS} from '../theme';
import {Drift, Odo, Shell, Word, clamp01, eo} from '../lib';

const ROWS = [
  {l: '要対応の見逃し', v: '5/9', red: true, k: '見逃しは'},
  {l: '誤警報', v: '1/38', k: '誤警報は'},
  {l: '完全一致', v: '70.2%', k: '完全一致は'},
  {l: '寸法推定の誤差(中央値)', v: '17%', k: '中央値'},
];
export const S08: React.FC<{t: TItem}> = ({t}) => {
  const f = useCurrentFrame();
  const c1 = cue(t, 'だから判定は下書き', 0.78, 0.1), c2 = cue(t, '確定するのは', 0.9, 0.1);
  const d = eo(clamp01((f / FPS - c1 + 0.2) / 0.6));
  return (
    <Shell t={t}>
      <Drift frames={t.frames} />
      <div style={{position: 'absolute', left: 160, top: 60, width: 1600, opacity: 1 - 0.92 * d, transform: `scale(${1 - 0.05 * d})`, transformOrigin: '50% 40%'}}>
        <div style={{fontSize: 44, color: C.sub, fontWeight: 700, marginBottom: 14}}>正直に測りました</div>
        {ROWS.map((r, i) => {
          const at = cue(t, r.k, 0.2 + i * 0.12, 0.2);
          const p = eo(clamp01((f / FPS - at) / 0.4));
          return (
            <div key={i} style={{opacity: p, transform: `translateX(${(1 - p) * -40}px)`, display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: C.panel, borderRadius: 16, padding: '8px 56px', marginBottom: 18, borderLeft: `8px solid ${r.red ? C.warn : C.accent}`}}>
              <span style={{fontSize: 54, fontWeight: 700}}>{r.l}</span>
              <span style={{fontSize: 118, color: r.red ? C.warn : C.text}}><Odo value={r.v} at={at} size={118} color={r.red ? C.warn : C.text} /></span>
            </div>
          );
        })}
      </div>
      <Word at={c1} style={{left: 0, width: 1920, top: 280, textAlign: 'center', fontSize: 170}}>判定は<span style={{color: C.accent}}>下書き</span></Word>
      <Word at={c2} style={{left: 0, width: 1920, top: 540, textAlign: 'center', fontSize: 130}}>確定は<span style={{color: C.accent}}>有資格者</span></Word>
    </Shell>
  );
};
