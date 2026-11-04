import React from 'react';
import {TItem, cue} from '../data/timeline';
import {C} from '../theme';
import {Fill, Odo, Photo, Shell, Word, clamp01, useSec} from '../lib';

// 黒 → 生成写真(ゆっくり寄る)。要点語「あなたの頭の上」→「8割」
export const S01: React.FC<{t: TItem}> = ({t}) => {
  const s = useSec();
  const k8 = cue(t, '8割', 0.6, 0.3);
  const black = 1 - clamp01((s - 0.4) / 1.2);
  return (
    <Shell t={t} bg="#000" fadeIn={false}>
      <Photo src="assets/images/01_street_dusk.png" frames={t.frames} z0={1.0} z1={1.16} ox={55} oy={60} />
      <Fill style={{background: '#000', opacity: black}} />
      <Word at={0.5} until={k8 - 0.2} style={{left: 0, width: 1920, top: 330, textAlign: 'center', fontSize: 170}}>あなたの<span style={{color: C.accent}}>頭の上</span></Word>
      <Word at={k8} style={{left: 0, width: 1920, top: 130, textAlign: 'center', fontSize: 560, lineHeight: 1}}>
        <Odo value="8" at={k8} dur={0.9} size={560} />
        <span style={{fontSize: 520, color: C.accent}}>割</span>
      </Word>
    </Shell>
  );
};
