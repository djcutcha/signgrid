import React from 'react';
import {TItem, cue} from '../data/timeline';
import {C} from '../theme';
import {Fill, Shell, Word, clamp01, eo, useSec} from '../lib';

export const hitSec = (t: TItem) => cue(t, 'SignGrid', 0.3, 0.05);

export const S04: React.FC<{t: TItem}> = ({t}) => {
  const s = useSec();
  const h = hitSec(t);
  const p = clamp01((s - h) / 0.35);
  const pop = 1.1 - 0.1 * eo(p);
  const ring = clamp01((s - h) / 0.8);
  const line = eo(clamp01((s - (h - 0.5)) / 0.45)) * (1 - p);
  const drift = 1 + 0.04 * clamp01((s - h) / 3);
  return (
    <Shell t={t} bg="#000" fadeIn={false}>
      <Fill style={{background: `radial-gradient(900px 500px at 50% 50%, rgba(59,130,246,${0.28 * eo(p)}), rgba(0,0,0,0) 70%)`}} />
      <div style={{position: 'absolute', left: 960 - 400 * line, width: 800 * line, top: 538, height: 4, background: C.accent, opacity: 1 - p}} />
      {s >= h && <div style={{position: 'absolute', left: 960 - 80 - 700 * ring, top: 540 - 80 - 700 * ring, width: 160 + 1400 * ring, height: 160 + 1400 * ring, borderRadius: '50%', border: `4px solid rgba(59,130,246,${0.7 * (1 - ring)})`}} />}
      <Fill style={{opacity: p, transform: `scale(${pop * drift})`}}>
        <div style={{position: 'absolute', left: 0, width: 1920, top: 400, textAlign: 'center', fontSize: 160, fontWeight: 800, color: '#fff', letterSpacing: 2}}>SignGrid 看板カルテ</div>
      </Fill>
      <Word at={h + 0.5} style={{left: 0, width: 1920, top: 640, textAlign: 'center', fontSize: 44, fontWeight: 700, color: C.sub}}>写真と点検票から、看板ごとのカルテ</Word>
    </Shell>
  );
};
