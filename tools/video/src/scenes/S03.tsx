import React from 'react';
import {TItem, cue} from '../data/timeline';
import {C, FONT} from '../theme';
import {Fill, Odo, Photo, Shell, Word, clamp01, eo, eio, useSec} from '../lib';

const Stat: React.FC<{x: number; at: number; until: number; v: string; label: string; red?: boolean}> = ({x, at, until, v, label}) => (
  <Word at={at} until={until} style={{left: x, top: 640, width: 560}}>
    <div style={{fontSize: 180, lineHeight: 1}}><Odo value={v} at={at} size={180} /></div>
    <div style={{fontSize: 40, fontWeight: 700, color: C.sub, marginTop: 10}}>{label}</div>
  </Word>
);

const Pill: React.FC<{x: number; at: number; text: string}> = ({x, at, text}) => (
  <Word at={at} style={{left: x - 220, top: 460, width: 440, height: 160, borderRadius: 16, background: C.panel, border: `2px solid ${C.accent}`, textAlign: 'center', lineHeight: '160px', fontSize: 100}}>{text}</Word>
);

export const S03: React.FC<{t: TItem}> = ({t}) => {
  const s = useSec();
  const c66 = cue(t, '66基', 0.0, 0.2), c773 = cue(t, '773枚', 0.05), c80 = cue(t, '80時間', 0.2, 0.3);
  const cRep = cue(t, '報告書は', 0.35), cReg = cue(t, '規制は', 0.45), cRec = cue(t, '現場の記録', 0.62, 0.1);
  const sw = c80 - 0.3;
  const dim = clamp01((s - cRec) / 0.5);
  const l1 = eio(clamp01((s - cRec - 0.9) / 0.5)), l2 = eio(clamp01((s - cRec - 1.5) / 0.5));
  return (
    <Shell t={t}>
      <Photo src="assets/images/03_ladder_inspect.png" frames={Math.round(sw * 30)} z0={1.0} z1={1.09} pan={50} opacity={1 - clamp01((s - sw) / 0.3)} />
      <Photo src="assets/images/03b_paper_pile.png" frames={Math.round((t.sec - sw) * 30)} z0={1.0} z1={1.08} pan={-50} opacity={clamp01((s - sw) / 0.3)} />
      <Fill style={{background: '#0b1220', opacity: dim * 0.88}} />
      <Stat x={120} at={c66} until={cRep - 0.1} v="66" label="看板(基)" />
      <Stat x={720} at={c773} until={cRep - 0.1} v="773" label="写真(枚)" />
      <Stat x={1300} at={c80} until={cRep - 0.1} v="80" label="事務作業(時間)" />
      <Word at={cRep} until={cRec - 0.1} style={{left: 120, top: 560, fontSize: 112, lineHeight: 1.25}}>報告書は<span style={{color: C.accent}}>客先ごと</span></Word>
      <Word at={cReg} until={cRec - 0.1} style={{left: 120, top: 730, fontSize: 112, lineHeight: 1.25}}>規制は<span style={{color: C.accent}}>自治体ごと</span></Word>
      <Pill x={360} at={cRec} text="記録" />
      <Pill x={960} at={cRec + 0.5} text="寸法" />
      <Pill x={1560} at={cRec + 1.1} text="規制" />
      <svg width={1920} height={1080} style={{position: 'absolute', left: 0, top: 0}}>
        <line x1={580} y1={540} x2={580 + 160 * l1} y2={540} stroke={C.accent} strokeWidth={8} strokeLinecap="round" />
        <line x1={1180} y1={540} x2={1180 + 160 * l2} y2={540} stroke={C.accent} strokeWidth={8} strokeLinecap="round" />
      </svg>
      <Word at={cRec + 2.2} style={{left: 0, width: 1920, top: 720, textAlign: 'center', fontSize: 52, fontWeight: 700, color: C.sub}}>つなぐのは、人の頭の中だけだった</Word>
    </Shell>
  );
};
