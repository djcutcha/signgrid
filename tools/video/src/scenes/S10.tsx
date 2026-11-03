import React from 'react';
import {TItem, cue} from '../data/timeline';
import {C} from '../theme';
import {Fill, Photo, Shell, Word, clamp01, eo, useSec} from '../lib';

export const S10: React.FC<{t: TItem}> = ({t}) => {
  const s = useSec();
  const sg = cue(t, 'SignGrid', 0.8, 0.05);
  const off = clamp01((s - (sg - 0.5)) / 0.3);
  const p = eo(clamp01((s - sg) / 0.4));
  return (
    <Shell t={t} bg="#000">
      <Photo src="assets/images/10_city_aerial.png" frames={Math.round(sg * 30)} z0={1.0} z1={1.12} ox={50} oy={55} opacity={1 - off} />
      <Word at={cue(t, 'その両側', 0.55, 0.3)} until={sg - 0.7} style={{left: 0, width: 1920, top: 700, textAlign: 'center', fontSize: 120}}>現場と行政、<span style={{color: '#7fb0ff'}}>その両側から</span></Word>
      <Fill style={{opacity: p, transform: `scale(${1.06 - 0.06 * p})`}}>
        <div style={{position: 'absolute', left: 0, width: 1920, top: 400, textAlign: 'center', fontSize: 160, fontWeight: 800, color: '#fff'}}>SignGrid 看板カルテ</div>
        <div style={{position: 'absolute', left: 0, width: 1920, top: 640, textAlign: 'center', fontSize: 40, fontWeight: 700, color: C.sub, letterSpacing: 3}}>Built with Claude: Japan 2026</div>
      </Fill>
    </Shell>
  );
};
