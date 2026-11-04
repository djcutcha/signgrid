import React from 'react';
import {TItem, cue} from '../data/timeline';
import {Bg, COL, KW, Person, SignBoard, Svg, Fill, Odo, pop, useS, win, clamp01, eo} from '../mg';
import {Shell} from '../lib';

// 頭上の看板 → 揺れて落ちる → 8割 → 行政が把握していない → 街に看板が並び頭上に「?」
export const S01: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const cQ = 0.5, cAcc = cue(t, '国に報告', 0.15, 0), cFall = cue(t, '落下', 0.25, 0), c8 = cue(t, '割は', 0.38, 0.2);
  const cGov = cue(t, '行政が', 0.5, 0), cHead = cue(t, 'あなたの頭', 0.68, 0), cNo = cue(t, '誰も管理', 0.85, 0);
  const swing = Math.sin(s * 5) * 7 * clamp01((s - cAcc + 0.5) / 0.8) * (s < cFall ? 1 : 0);
  const fp = clamp01((s - cFall) / 0.9);
  const fy = 400 + 480 * fp * fp, fr = 12 + 70 * fp;
  const dust = clamp01((s - cFall - 0.85) / 0.6);
  const walk = s * 6;
  const px = ((s * 120) % 2300) - 200;
  return (
    <Shell t={t}>
      <Bg />
      <Svg>
        <rect x={0} y={900} width={1920} height={6} fill="#27344f" />
        <g opacity={win(s, 0.2, cHead - 0.1)}>
          <rect x={1493} y={120} width={14} height={250} fill="#64748b" transform={`rotate(${swing} 1500 120)`} opacity={s < cFall ? 1 : 0} />
          <g opacity={s < cFall ? 1 : 0}><SignBoard x={1500 + Math.sin(swing / 57) * 140} y={400} w={320} h={110} rot={swing * 1.5} label="看板" fs={60} /></g>
          {s >= cFall && <SignBoard x={1500} y={fy} w={320} h={110} rot={fr} label="看板" fs={60} />}
          <Person x={px} y={900} k={1.3} walk={walk} />
          <Person x={1500 - px * 0.3 + 300} y={900} k={1.1} walk={walk + 2} color="#7d8ea8" />
          {dust > 0 && <ellipse cx={1500} cy={900} rx={60 + 260 * dust} ry={10 + 30 * dust} fill="none" stroke="#9fb0c8" strokeWidth={5} opacity={1 - dust} />}
        </g>
        {/* 街に看板が並ぶ */}
        <g opacity={win(s, cHead - 0.1)}>
          {Array.from({length: 9}, (_, i) => {
            const p = pop(s, cHead + i * 0.12, 0.4);
            const x = 160 + i * 205, h = 120 + ((i * 53) % 90);
            return (
              <g key={i} opacity={Math.min(1, p)}>
                <rect x={x - 5} y={430} width={10} height={470} fill="#334766" />
                <SignBoard x={x} y={380 - (1 - p) * 40} w={150} h={h * 0.8} label={['居酒屋', '歯科', '薬局', 'ラーメン', '美容室', '塾', '不動産', 'BAR', '焼肉'][i]} fs={30} color={i % 3 ? '#e2e8f0' : '#fde68a'} />
              </g>
            );
          })}
          <Person x={px % 1900} y={900} k={1.4} walk={walk} />
          <g opacity={win(s, cHead + 0.3)}>
            <text x={(px % 1900)} y={640 + Math.sin(s * 5) * 8} textAnchor="middle" fontSize={120} fontWeight={800} fill={COL.amber} fontFamily="inherit">?</text>
          </g>
        </g>
      </Svg>
      {/* 文字 */}
      <KW s={s} at={cQ} until={cAcc + 1.2} x={760} y={150} size={140}>ご存知でしたか<span style={{color: COL.amber}}>?</span></KW>
      <KW s={s} at={cFall} until={c8 - 0.1} x={960} y={640} size={100} color="#fecaca">看板落下事故</KW>
      <Fill style={{opacity: win(s, c8, cGov + 4.2, 0.2)}}>
        <KW s={s} at={c8 - 0.3} until={cHead - 0.15} x={960} y={330} size={520}><Odo value="8" at={c8 - 0.3} dur={0.7} size={520} /><span style={{color: COL.blue}}>割</span></KW>
        <KW s={s} at={cGov} until={cHead - 0.15} x={960} y={660} size={90}>行政が<span style={{color: COL.red}}>把握していない</span></KW>
      </Fill>
      <KW s={s} at={cNo} x={960} y={150} size={110}>誰も<span style={{color: COL.amber}}>管理していない</span>かも</KW>
    </Shell>
  );
};
