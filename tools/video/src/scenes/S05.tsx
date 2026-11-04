import React from 'react';
import {Img, staticFile} from 'remotion';
import {TItem, cue} from '../data/timeline';
import {Bg, COL, Cylinder, KW, Odo, Person, Pill, Svg, Fill, pop, useS, win, clamp01, eo} from '../mg';
import {Phone, Shell} from '../lib';

const LINE = Array.from({length: 9}, (_, i) => `assets/line_v3/line_${String(i + 1).padStart(2, '0')}.png`);
const LINE_PX: [number, number][] = [[546, 804], [548, 568], [548, 734], [548, 904], [546, 776], [546, 686], [548, 688], [548, 1216], [548, 310]];
const BOT = ['画像診断', '手書きの読み取り', '規制データとの突き合わせ'];

// LINEの指示で操作 → 経験が少なくても → データがDBへ → 裏のClaude 3つ → カルテ → 約9分
export const S05: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const cExp = cue(t, '経験が少', 0.14, 0), cData = cue(t, '完了後', 0.3, 0), cDb = cue(t, 'データベース', 0.4, 0), cBack = cue(t, '裏では', 0.5, 0);
  const cB = [cue(t, '画像診断', 0.55, 0), cue(t, '手書き', 0.62, 0), cue(t, '規制データ', 0.72, 0)];
  const cKarte = cue(t, 'カルテができ', 0.86, 0.2), cNine = cue(t, '1店舗の一周', 0.91, 0);
  const A = win(s, 0, cData - 0.2), Bk = win(s, cData - 0.2, cBack - 0.2), Cc = win(s, cBack - 0.2, cKarte - 0.1), D = win(s, cKarte - 0.1, cNine - 0.1), E = win(s, cNine - 0.1);
  const idx = Math.min(8, Math.floor(Math.max(0, s - 0.5) / 0.8));
  const [iw, ih] = LINE_PX[idx], PH = 680, PW = 380, sc = Math.min(PW / iw, PH / ih);
  const phoneK = 1 - 0.45 * win(s, cData - 0.2, 99, 0.5) * (1 - D);
  return (
    <Shell t={t}>
      <Bg />
      <div style={{opacity: 1 - win(s, cBack - 0.2, 99, 0.3) * 1, position: 'absolute', left: 0, top: 0, width: 1920, height: 1080, transformOrigin: '420px 460px', transform: `translate(${-120 * (1 - phoneK) * 2}px, ${-60 * (1 - phoneK) * 2}px) scale(${phoneK})`}}>
        <Phone x={230} y={80} w={PW} h={PH}>
          <div style={{width: PW, height: PH, background: '#8fa8c8'}}>
            <Img src={staticFile(LINE[idx])} style={{width: iw * sc, height: ih * sc, display: 'block', margin: '0 auto'}} />
          </div>
        </Phone>
      </div>
      <KW s={s} at={0.5} until={cExp - 0.1} x={1250} y={300} size={110}><span style={{color: '#06c755'}}>LINE</span>の指示どおり</KW>
      <KW s={s} at={0.9} until={cExp - 0.1} x={1250} y={470} size={70} color={COL.sub}>スマホを操作するだけ</KW>
      <KW s={s} at={cExp} until={cData - 0.2} x={1250} y={330} size={100}>経験が少なくても<span style={{color: COL.green}}>OK</span></KW>
      <Svg>
        <g opacity={win(s, cExp, cData - 0.2)}>
          {[0, 1, 2].map((i) => <g key={i} opacity={Math.min(1, pop(s, cExp + 0.2 + i * 0.25, 0.4))}><Person x={1060 + i * 180} y={760 - (1 - Math.min(1, pop(s, cExp + 0.2 + i * 0.25, 0.4))) * 60} k={1.5} helmet color={['#7dd3fc', '#86efac', '#fcd34d'][i]} /></g>)}
        </g>
        {/* データが DB へ */}
        <g opacity={Bk}>
          <Cylinder x={1380} y={380} w={260} h={300} fill={clamp01((s - cData) / 3)} />
          <text x={1510} y={740} textAnchor="middle" fontSize={44} fontWeight={800} fill="#fff" fontFamily="inherit">データベース</text>
          {Array.from({length: 14}, (_, i) => {
            const u = (((s - cData) * 0.7 + i / 14) % 1);
            return <circle key={i} cx={560 + 820 * u} cy={520 + Math.sin(i * 2) * 60 * (1 - u)} r={10} fill={[COL.blue, COL.amber, COL.green][i % 3]} opacity={Math.sin(u * Math.PI)} />;
          })}
        </g>
        {/* Claude の 3 つ */}
        <g opacity={Cc}>
          <circle cx={960} cy={470} r={130 + 6 * Math.sin(s * 4)} fill="#1b2a4d" stroke={COL.blue} strokeWidth={6} />
          <text x={960} y={490} textAnchor="middle" fontSize={64} fontWeight={800} fill="#fff" fontFamily="inherit">Claude</text>
          {BOT.map((b, i) => {
            const x = [380, 960, 1540][i], y = [260, 790, 260][i];
            const lit = clamp01((s - cB[i]) / 0.25);
            return (
              <g key={i}>
                <line x1={960} y1={470} x2={x} y2={y} stroke={COL.blue} strokeWidth={5} opacity={lit} strokeDasharray="12 10" />
                <Pill x={x} y={y} w={b.length > 7 ? 560 : 360} h={110} text={b} size={b.length > 7 ? 38 : 48} lit={lit} color={COL.blue} />
              </g>
            );
          })}
        </g>
        {/* カルテ */}
        <g opacity={D}>
          <g transform={`translate(960,470) scale(${0.7 + 0.3 * pop(s, cKarte, 0.5)})`}>
            <rect x={-330} y={-300} width={660} height={600} rx={24} fill="#f8fafc" />
            <rect x={-330} y={-300} width={660} height={90} rx={24} fill={COL.blue} />
            <text x={0} y={-238} textAnchor="middle" fontSize={50} fontWeight={800} fill="#fff" fontFamily="inherit">看板カルテ</text>
            {(() => {
              const f = (k: number) => Math.min(1, pop(s, cKarte + 0.2 + k * 0.17, 0.25));
              const rows: [string, string, string][] = [['基礎部', '良好', COL.green], ['支柱', '経過観察', COL.yellow], ['取付部', '要改善', COL.red], ['表示面', '良好', COL.green]];
              return (
                <g>
                  <g opacity={f(0)}>
                    <text x={-290} y={-160} fontSize={44} fontWeight={800} fill="#0b1220" fontFamily="inherit">ラーメン店 袖看板</text>
                  </g>
                  <g opacity={f(1)}>
                    <rect x={-290} y={-130} width={250} height={50} rx={12} fill="#dbeafe" /><text x={-165} y={-95} textAnchor="middle" fontSize={28} fontWeight={700} fill="#0b1220" fontFamily="inherit">種類 袖</text>
                    <rect x={-20} y={-130} width={310} height={50} rx={12} fill="#dbeafe" /><text x={135} y={-95} textAnchor="middle" fontSize={28} fontWeight={700} fill="#0b1220" fontFamily="inherit">W1950×H600</text>
                  </g>
                  {rows.map(([lab, txt, col], k) => (
                    <g key={k} opacity={f(k + 2)}>
                      <text x={-290} y={-18 + k * 64} fontSize={34} fontWeight={700} fill="#0b1220" fontFamily="inherit">{lab}</text>
                      <rect x={20} y={-52 + k * 64} width={270} height={48} rx={24} fill={col} />
                      <text x={155} y={-18 + k * 64} textAnchor="middle" fontSize={30} fontWeight={800} fill={col === COL.yellow ? '#0b1220' : '#fff'} fontFamily="inherit">{txt}</text>
                    </g>
                  ))}
                  {[0, 1, 2].map((k) => <rect key={k} x={-290 + k * 200} y={212} width={180} height={64} rx={8} fill="#cbd5e1" opacity={f(6 + k * 0.5)} />)}
                </g>
              );
            })()}
          </g>
        </g>
      </Svg>
      <Fill style={{opacity: E}}>
        <KW s={s} at={cNine} x={960} y={330} size={50} color={COL.sub}>1店舗の一周</KW>
        <KW s={s} at={cNine + 0.2} x={960} y={560} size={380}><span style={{fontSize: 200}}>約</span><Odo value="9" at={cNine + 0.2} size={380} dur={0.6} /><span style={{fontSize: 200, color: COL.blue}}>分</span></KW>
        <KW s={s} at={cNine + 0.8} x={960} y={800} size={38} color={COL.sub} weight={700}>人の確認を除いて</KW>
      </Fill>
    </Shell>
  );
};
