import React from 'react';
import {TItem, cue} from '../data/timeline';
import {Bg, COL, KW, Person, SignBoard, Svg, Fill, pop, useS, win, clamp01, eo} from '../mg';
import {Shell} from '../lib';

export const S10: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const sg = cue(t, 'SignGrid', 0.62, 0.05);
  const off = clamp01((s - (sg - 0.5)) / 0.3), p = eo(clamp01((s - sg) / 0.4));
  const px = ((s * 130) % 2300) - 200;
  const cMe = cue(t, 'あなた自身', 0.4, 0);
  return (
    <Shell t={t} bg="#07132a">
      <Bg hue="rgba(34,197,94,0.18)" />
      <Fill style={{opacity: 1 - off}}>
        <Svg>
          <rect x={0} y={900} width={1920} height={6} fill="#2f5a45" />
          {Array.from({length: 9}, (_, i) => {
            const pp = pop(s, 0.2 + i * 0.1, 0.4), x = 160 + i * 205;
            return (
              <g key={i} opacity={Math.min(1, pp)}>
                <rect x={x - 5} y={430} width={10} height={470} fill="#334766" />
                <SignBoard x={x} y={380} w={150} h={90} label={['居酒屋', '歯科', '薬局', 'ラーメン', '美容室', '塾', '不動産', 'BAR', '焼肉'][i]} fs={30} stroke={COL.green} color={i % 3 ? '#e2e8f0' : '#fde68a'} />
                <g opacity={clamp01((s - 1 - i * 0.2) / 0.3)}><circle cx={x + 62} cy={332} r={20} fill={COL.green} /><path d={`M${x + 52} 332 l7 8 l13 -16`} stroke="#fff" strokeWidth={5} fill="none" strokeLinecap="round" /></g>
              </g>
            );
          })}
          <Person x={px} y={900} k={1.4} walk={s * 6} color="#bbf7d0" />
          <Person x={(px + 900) % 2200} y={900} k={1.2} walk={s * 6 + 2} color="#86efac" />
          <Person x={(px + 1500) % 2200} y={900} k={1.3} walk={s * 6 + 4} color="#a7f3d0" />
        </Svg>
        <KW s={s} at={0.4} until={sg - 0.2} x={960} y={140} size={84}>いつも歩く道の<span style={{color: COL.green}}>安全</span>を</KW>
        <KW s={s} at={cMe} until={sg - 0.2} x={960} y={270} size={96}><span style={{color: COL.amber}}>あなた自身</span>が守れる街へ</KW>
      </Fill>
      <Fill style={{opacity: p, transform: `scale(${1.08 - 0.08 * p})`}}>
        <div style={{position: 'absolute', left: 0, width: 1920, top: 400, textAlign: 'center', fontSize: 160, fontWeight: 800, color: '#fff'}}>SignGrid 看板カルテ</div>
        <div style={{position: 'absolute', left: 0, width: 1920, top: 640, textAlign: 'center', fontSize: 40, fontWeight: 700, color: COL.sub, letterSpacing: 3}}>Built with Claude: Japan 2026</div>
      </Fill>
    </Shell>
  );
};
