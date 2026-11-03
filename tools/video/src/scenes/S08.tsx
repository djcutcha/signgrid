import React from 'react';
import {TItem, cue} from '../data/timeline';
import {C} from '../theme';
import {Chars, Photo, Shell, Word} from '../lib';

export const S08: React.FC<{t: TItem}> = ({t}) => (
  <Shell t={t}>
    <Photo src="assets/images/08_office_hands.png" frames={t.frames} z0={1.0} z1={1.14} ox={40} oy={65} pan={-70} grad={0.9} />
    <div style={{position: 'absolute', left: 110, top: 560}}>
      <Chars text="明日から、" at={cue(t, '明日から', 0.0, 0.1)} size={150} step={0.07} />
    </div>
    <div style={{position: 'absolute', left: 110, top: 730}}>
      <Chars text="職人と事務が試す" at={cue(t, '現場の職人', 0.1, 0.1)} size={110} step={0.06} color="#7fb0ff" />
    </div>
    <Word at={cue(t, '人の確認込み', 0.5, 0.1)} style={{left: 110, top: 905, fontSize: 40, fontWeight: 700, color: '#dbe6f5'}}>人の確認込みの時間は、その結果で埋めます</Word>
  </Shell>
);
