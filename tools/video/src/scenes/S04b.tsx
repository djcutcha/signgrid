import React from 'react';
import {TItem, cue} from '../data/timeline';
import {Bg, COL, KW, Spark, Svg, Fill, pop, useS, win, clamp01, eo} from '../mg';
import {Shell} from '../lib';

export const hitSec = (t: TItem) => cue(t, 'SignGrid', 0.62, 0.05);

// 暗転 → 「AIで改善できないか」→ ロゴ
export const S04b: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const h = hitSec(t), cAI = cue(t, 'AIで', 0.2, 0.1);
  const p = clamp01((s - h) / 0.35);
  const ring = clamp01((s - h) / 0.8);
  return (
    <Shell t={t} bg="#000" fadeIn={false}>
      <Fill style={{background: `radial-gradient(900px 500px at 50% 50%, rgba(59,130,246,${0.3 * eo(p)}), rgba(0,0,0,0) 70%)`}} />
      <Svg>
        {s < h + 0.2 && [0, 1, 2, 3, 4, 5].map((i) => <Spark key={i} x={420 + i * 220} y={300 + ((i * 97) % 300)} r={30 + (i % 3) * 14} p={pop(s, cAI + i * 0.12, 0.5)} />)}
        {s >= h && <circle cx={960} cy={540} r={80 + 700 * ring} fill="none" stroke={COL.blue} strokeWidth={4} opacity={0.7 * (1 - ring)} />}
      </Svg>
      <KW s={s} at={0.5} until={h - 0.2} x={960} y={420} size={90} color={COL.sub}>少しでも、この状況を</KW>
      <KW s={s} at={cAI} until={h - 0.2} x={960} y={600} size={170}><span style={{color: COL.blue}}>AI</span>で改善できないか</KW>
      <Fill style={{opacity: p, transform: `scale(${1.1 - 0.1 * eo(p)})`}}>
        <div style={{position: 'absolute', left: 0, width: 1920, top: 400, textAlign: 'center', fontSize: 160, fontWeight: 800, color: '#fff'}}>SignGrid 看板カルテ</div>
      </Fill>
    </Shell>
  );
};
