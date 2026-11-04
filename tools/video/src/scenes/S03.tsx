import React from 'react';
import {TItem, cue} from '../data/timeline';
import {C} from '../theme';
import {Fill, Odo, Photo, Shell, Word, clamp01, useSec} from '../lib';

const Stat: React.FC<{x: number; at: number; until: number; v: string; unit: string; label: string}> = ({x, at, until, v, unit, label}) => (
  <Word at={at} until={until} style={{left: x, top: 520, width: 700, background: 'rgba(11,18,32,.78)', padding: '18px 40px', borderRadius: 16}}>
    <div style={{fontSize: 230, lineHeight: 1, display: 'flex', alignItems: 'flex-end', gap: 12}}><Odo value={v} at={at} size={230} /><span style={{fontSize: 110, marginBottom: 14, color: C.accent}}>{unit}</span></div>
    <div style={{fontSize: 40, fontWeight: 700, color: C.sub, marginTop: 6}}>{label}</div>
  </Word>
);

export const S03: React.FC<{t: TItem}> = ({t}) => {
  const s = useSec();
  const c66 = cue(t, '66基', 0.6, 0.3), c773 = cue(t, '773枚', 0.7, 0.3), ck = cue(t, '知識は', 0.82, 0.1);
  const sw = cue(t, 'ある店舗で', 0.45, 0.1);
  const dim = clamp01((s - ck) / 0.5);
  return (
    <Shell t={t}>
      <Photo src="assets/images/03_ladder_inspect.png" frames={Math.round(sw * 30)} z0={1.0} z1={1.09} pan={50} opacity={1 - clamp01((s - sw) / 0.3)} />
      <Photo src="assets/images/03b_paper_pile.png" frames={Math.round((t.sec - sw) * 30)} z0={1.0} z1={1.08} pan={-50} opacity={clamp01((s - sw) / 0.3)} />
      <Fill style={{background: '#0b1220', opacity: dim * 0.85}} />
      <Stat x={110} at={c66} until={ck - 0.1} v="66" unit="基" label="1店舗の看板" />
      <Stat x={900} at={c773} until={ck - 0.1} v="773" unit="枚" label="1店舗の写真" />
      <Word at={ck} style={{left: 0, width: 1920, top: 380, textAlign: 'center', fontSize: 130, lineHeight: 1.3}}>知識は、<br /><span style={{color: C.accent}}>人の頭の中</span>にしかなかった</Word>
    </Shell>
  );
};
