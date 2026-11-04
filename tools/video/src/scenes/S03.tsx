import React from 'react';
import {TItem, cue} from '../data/timeline';
import {Arrow, Bg, Book, COL, KW, LiftWorker, Pill, SignBoard, Svg, Fill, pop, useS, win, clamp01, eo} from '../mg';
import {Shell} from '../lib';

// 高所作業車の作業員 → 許可 必要/不要 の分かれ道 → 未申請の札(出典つき) → 台帳に載らない看板 → 野放し
export const S03: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const cFork = cue(t, '許可が必要', 0.1, 0), cNeed = cue(t, '必要な場合でも', 0.4, 0), cNun = cue(t, 'なんと', 0.4, 0);
  const cLedg = cue(t, '台帳', 0.65, 0.4), cFree = cue(t, '野放し', 0.9, 0);
  const cNot = cue(t, '不要な看板', 0.62, 0.2);
  const forkP = win(s, 0.3, cNun + 0.5), p2 = win(s, cNun - 0.2, cNot - 0.1), p3 = win(s, cNot - 0.1);
  const raise = eo(clamp01(s / 2));
  return (
    <Shell t={t}>
      <Bg />
      <Svg>
        <g opacity={win(s, 0.1, cNun + 0.3)}><LiftWorker x={260} y={860} k={1.25} raise={raise} s={s} /></g>
        {/* 分かれ道 */}
        <g opacity={forkP}>
          <SignBoard x={1000} y={470} w={190} h={90} label="設置" fs={44} />
          <Arrow x1={1100} y1={470} x2={1380} y2={330} p={eo(clamp01((s - cFork) / 0.5))} />
          <Arrow x1={1100} y1={470} x2={1380} y2={640} p={eo(clamp01((s - cFork - 0.9) / 0.5))} color={COL.sub} />
          <g opacity={pop(s, cFork + 0.4, 0.4)}><Pill x={1600} y={330} w={400} text="許可が必要" size={52} h={110} /></g>
          <g opacity={pop(s, cFork + 1.3, 0.4)}><Pill x={1600} y={640} w={400} text="許可は不要" size={52} h={110} color="#64748b" /></g>
        </g>
        {/* 未申請の札 */}
        <g opacity={p2}>
          {Array.from({length: 10}, (_, i) => {
            const bad = i < 7, p = pop(s, cNun + 0.3 + i * 0.1, 0.35);
            return <SignBoard key={i} x={420 + i * 130} y={720} w={96} h={60} op={p} dashed={bad} stroke={bad ? COL.red : COL.green} label={bad ? '?' : '✓'} fs={36} />;
          })}
          <g opacity={pop(s, cNun, 0.5)}>
            <rect x={360} y={170} width={1200} height={380} rx={26} fill="#2a1216" stroke={COL.red} strokeWidth={6} />
            <text x={960} y={225} textAnchor="middle" fontSize={40} fontWeight={800} fill="#fecaca" fontFamily="inherit">許可が必要なのに</text>
          </g>
        </g>
        {/* 台帳 */}
        <g opacity={p3}>
          <Book x={1500} y={520} k={1.5} />
          {Array.from({length: 12}, (_, i) => {
            const p = pop(s, cNot + 0.2 + i * 0.28, 0.4), inL = i < 3;
            const x = inL ? 1400 + i * 90 : 300 + (i % 6) * 190, y = inL ? 560 : 330 + Math.floor(i / 6) * 260 + (i % 2) * 30;
            return <SignBoard key={i} x={x} y={y - (1 - p) * 60} w={110} h={70} op={Math.min(1, p) * (inL ? 1 : 0.9)} dashed={!inL} stroke="#94a3b8" label={inL ? '' : '?'} />;
          })}
          <text x={1500} y={690} textAnchor="middle" fontSize={34} fontWeight={700} fill={COL.sub} fontFamily="inherit">台帳</text>
        </g>
      </Svg>
      <Fill style={{opacity: p2}}>
        <div style={{position: 'absolute', left: 360, width: 1200, top: 232, textAlign: 'center', fontSize: 190, fontWeight: 800, color: '#fff', transform: `scale(${0.7 + 0.3 * pop(s, cNun + 0.2, 0.5)})`, opacity: pop(s, cNun + 0.2, 0.4)}}>6〜8割<span style={{fontSize: 90, color: COL.red}}> 未申請</span></div>
        <div style={{position: 'absolute', left: 340, width: 1240, top: 470, textAlign: 'center', fontSize: 28, lineHeight: 1.45, color: '#fecaca', opacity: pop(s, cNun + 1.0, 0.4)}}>福岡市 実態調査(平成22・23年度)表示面10㎡以上 無許可 65%<br />横須賀市 沿線調査 未申請 82%</div>
      </Fill>
      <KW s={s} at={cNot} until={cFree - 0.2} x={960} y={150} size={80}>台帳に<span style={{color: COL.amber}}>載らない</span>看板</KW>
      <KW s={s} at={cFree} x={960} y={170} size={230} color={COL.red} shake={14}>野放し</KW>
    </Shell>
  );
};
