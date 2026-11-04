import React from 'react';
import {TItem, cue} from '../data/timeline';
import {Bg, COL, KW, Odo, Sprout, Svg, Fill, pop, useS, win, clamp01, eo} from '../mg';
import {Shell} from '../lib';

const CARDS = [
  {l: '要対応の見逃し', v: '5/9', red: true, k: '見逃し', fb: 0.1},
  {l: '誤警報', v: '1/38', k: '誤警報', fb: 0.3},
  {l: '完全一致', v: '70.2%', k: '完全一致', fb: 0.45},
  {l: '寸法推定の誤差(中央値)', v: '17%', k: '中央値', fb: 0.6},
];
export const S07: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const cDraft = cue(t, '下書き用', 0.78, 0), cGrow = cue(t, '育っていく', 0.88, 0);
  const sp = clamp01((s - cGrow) / 2.2);
  const dim = 1 - 0.55 * win(s, cDraft, 99, 0.4);
  return (
    <Shell t={t}>
      <Bg />
      <KW s={s} at={0.4} x={620} y={110} size={54} color={COL.sub}>現時点の、正直な数字</KW>
      <Fill style={{opacity: dim}}>
        {CARDS.map((c, i) => {
          const at = cue(t, c.k, c.fb, 0.1), p = pop(s, 0.7 + i * 0.2, 0.5);
          const x = 100 + (i % 2) * 560, y = 190 + Math.floor(i / 2) * 330;
          const col = c.red ? COL.red : COL.text;
          return (
            <div key={i} style={{position: 'absolute', left: x, top: y, width: 520, height: 290, borderRadius: 24, background: COL.panel, borderLeft: `12px solid ${c.red ? COL.red : COL.blue}`, opacity: Math.min(1, p * 1.4), transform: `scale(${0.7 + 0.3 * p})`, boxShadow: c.red ? '0 0 60px rgba(220,38,38,.35)' : undefined}}>
              <div style={{position: 'absolute', left: 36, top: 26, fontSize: 38, fontWeight: 700, color: COL.sub}}>{c.l}</div>
              <div style={{position: 'absolute', left: 30, top: 100, fontSize: 150, fontWeight: 800, color: col, opacity: clamp01((s - at + 0.15) / 0.15)}}><Odo value={c.v} at={at} size={150} color={col} dur={0.8} /></div>
              {c.red && <div style={{position: 'absolute', right: 30, top: 26, fontSize: 36, fontWeight: 800, color: COL.red}}>見逃し</div>}
            </div>
          );
        })}
      </Fill>
      <Svg>
        <g opacity={win(s, cGrow - 0.2)}>
          <Sprout x={1520} y={860} k={1.3} p={sp} />
          {sp > 0.8 && [0, 1, 2].map((i) => <circle key={i} cx={1400 + i * 120} cy={420 - ((s * 40 + i * 50) % 120)} r={8} fill={COL.yellow} opacity={0.7} />)}
        </g>
      </Svg>
      <KW s={s} at={cDraft} until={cGrow - 0.1} x={1450} y={330} size={120} color="#fff">判定は<span style={{color: COL.blue}}>下書き</span></KW>
      <KW s={s} at={cGrow} x={1450} y={150} size={66} color="#fff">育っていく過程が<br /><span style={{color: COL.green}}>楽しみ</span></KW>
    </Shell>
  );
};
