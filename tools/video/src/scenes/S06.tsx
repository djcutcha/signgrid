import React from 'react';
import {TItem, cue} from '../data/timeline';
import {Bg, Book, COL, Head, KW, Svg, Fill, pop, useS, win, clamp01, eo} from '../mg';
import {Shell} from '../lib';

const NODES = [
  {t: 'Claude の\nドラフト', c: COL.blue, a: -90},
  {t: '有資格者の\nチェックと\nフィードバック', c: COL.amber, a: 0},
  {t: '承認した\n規則', c: COL.green, a: 90},
  {t: '次の点検に\n効く', c: '#a78bfa', a: 180},
];

// ここからが本題 → ループ → 熟練の知識が頭の外へ
export const S06: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const cMain = cue(t, '本題', 0.05, 0.1), cD = cue(t, 'Claudeが作成', 0.12, 0), cCk = cue(t, '有資格者がチェック', 0.28, 0), cRule = cue(t, '承認した規則', 0.5, 0), cNext = cue(t, '次の点検', 0.62, 0), cHead = cue(t, '人の頭の外', 0.88, 0.2);
  const cs = [cD, cCk, cRule, cNext];
  const R = 290, CX = 960, CY = 480;
  const loop = win(s, cD - 0.3, cHead - 0.4), hd = win(s, cHead - 0.5);
  const rot = (s - cD) * 40;
  return (
    <Shell t={t}>
      <Bg />
      <KW s={s} at={0.3} until={cD - 0.1} x={960} y={420} size={190}>ここからが<span style={{color: COL.amber}}>本題</span></KW>
      <Svg>
        <g opacity={loop}>
          <ellipse cx={CX} cy={CY} rx={430} ry={270} fill="none" stroke="#27344f" strokeWidth={10} />
          <path d={`M ${CX} ${CY - 270} A 430 270 0 1 1 ${CX} ${CY + 270} A 430 270 0 1 1 ${CX} ${CY - 270}`} fill="none" stroke={COL.blue} strokeWidth={10} pathLength={1000} strokeDasharray={`${1000 * clamp01((s - cD) / (cNext - cD + 1.2))} 99999`} strokeLinecap="round" />
          <circle cx={CX + 430 * Math.cos((rot - 90) * Math.PI / 180)} cy={CY + 270 * Math.sin((rot - 90) * Math.PI / 180)} r={16} fill="#fff" />
          {NODES.map((n, i) => {
            const x = CX + 430 * Math.cos(n.a * Math.PI / 180), y = CY + 270 * Math.sin(n.a * Math.PI / 180);
            const p = pop(s, cs[i], 0.45);
            const lines = n.t.split('\n');
            const w = 340;
            return (
              <g key={i} transform={`translate(${x},${y}) scale(${0.4 + 0.6 * Math.min(1, p)})`} opacity={Math.min(1, p)}>
                <rect x={-w / 2} y={-70} width={w} height={140} rx={26} fill="#16223d" stroke={n.c} strokeWidth={7} />
                {lines.map((l, k) => <text key={k} x={0} y={(k - (lines.length - 1) / 2) * 40 + 12} textAnchor="middle" fontSize={lines.length > 2 ? 30 : 38} fontWeight={800} fill="#fff" fontFamily="inherit">{l}</text>)}
              </g>
            );
          })}
        </g>
        <g opacity={hd}>
          <Head x={480} y={560} k={2} glow={pop(s, cHead, 0.6)} />
          <Book x={1440} y={520} k={1.8} />
          {Array.from({length: 12}, (_, i) => {
            const u = clamp01(((s - cHead) * 0.6 + i / 12) % 1.0);
            const x = 480 + 960 * u, y = 480 - Math.sin(u * Math.PI) * 220 + Math.sin(i) * 40;
            return <circle key={i} cx={x} cy={y} r={14 - 6 * u} fill={COL.yellow} opacity={Math.sin(u * Math.PI)} />;
          })}
        </g>
      </Svg>
      <KW s={s} at={cHead} x={960} y={160} size={96} color="#fff"><span style={{color: COL.yellow}}>熟練のプロの知識</span>が、人の頭の外へ</KW>
    </Shell>
  );
};
