import React from 'react';
import {TItem, cue} from '../data/timeline';
import {C} from '../theme';
import {Fill, Odo, Photo, Shell, Word, clamp01, useSec} from '../lib';

export const S01: React.FC<{t: TItem}> = ({t}) => {
  const s = useSec();
  const sw = cue(t, '把握', 0.45, 0.1);
  const a = 0.5;
  const fA = Math.round((sw + 0.3) * 30);
  const k8 = cue(t, '8割', 0.35, 0.3);
  const black = 1 - clamp01((s - 0.5) / 0.4);
  return (
    <Shell t={t} bg="#000" fadeIn={false}>
      <Photo src="assets/images/01_street_dusk.png" frames={fA} z0={1.0} z1={1.1} ox={55} oy={60} opacity={1 - clamp01((s - sw) / 0.3)} />
      <Photo src="assets/images/01b_sign_rust.png" frames={Math.round((t.sec - sw) * 30)} z0={1.0} z1={1.07} pan={-60} opacity={clamp01((s - sw) / 0.3)} />
      <Fill style={{background: '#000', opacity: black}} />
      <Word at={k8} style={{left: 0, width: 1920, top: 190, textAlign: 'center', fontSize: 560, lineHeight: 1}}>
        <Odo value="8" at={k8} dur={0.9} size={560} />
        <span style={{fontSize: 520, color: C.accent}}>割</span>
      </Word>
      <Word at={sw} style={{left: 0, width: 1920, top: 860, textAlign: 'center', fontSize: 54, fontWeight: 700}}>行政が把握していない看板</Word>
    </Shell>
  );
};
