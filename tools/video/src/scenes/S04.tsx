import React from 'react';
import {TItem, cue} from '../data/timeline';
import {Bg, Building, Check, Clock, COL, KW, LiftWorker, Odo, Sheet, SignBoard, Svg, Fill, pop, useS, win, clamp01, eo} from '../mg';
import {Shell} from '../lib';

const ITEMS = ['基礎部の傾き', 'ぐらつき', '錆・腐食', '溶接部', 'ボルトの緩み', '塗装の劣化', '支柱の変形', 'LEDの異常'];
const CITIES = [['A市', '#2563eb', -14], ['B区', '#16a34a', 9], ['C町', '#9333ea', -6], ['D市', '#ea580c', 12], ['E区', '#0891b2', -10], ['F市', '#be123c', 7]] as const;

export const S04: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const cQual = cue(t, '有資格者', 0.1, 0), cItems = cue(t, '18以上', 0.22, 0.1), cOff = cue(t, '事務所', 0.42, 0), cFmt = cue(t, '書式', 0.55, 0.3);
  const cShop = cue(t, '店舗の点検', 0.68, 0), c66 = cue(t, '66基', 0.72, 0), c773 = cue(t, '773枚', 0.8, 0), c45 = cue(t, '45店舗', 0.86, 0), c80 = cue(t, '80時間', 0.95, 0);
  const A = win(s, 0, cQual - 0.1), B = win(s, cQual - 0.1, cItems - 0.1), Cc = win(s, cItems - 0.1, cOff - 0.1), D = win(s, cOff - 0.1, cFmt - 0.5), E = win(s, cFmt - 0.5, cShop - 0.1), F = win(s, cShop - 0.1, c45 - 0.1), G = win(s, c45 - 0.1);
  const n18 = Math.min(18, Math.floor(Math.max(0, s - cItems) * 4.5));
  return (
    <Shell t={t}>
      <Bg />
      <KW s={s} at={0.4} until={cQual - 0.1} x={960} y={420} size={150}>点検は<span style={{color: COL.amber}}>大仕事</span></KW>
      <Svg>
        {/* A: 紙と写真の山 */}
        <g opacity={A}>
          {[0, 1, 2, 3, 4].map((i) => {
            const p = Math.min(1, pop(s, 1.2 + i * 0.7, 0.5));
            return <Sheet key={i} x={560 + i * 190} y={660 - (1 - p) * 300} rot={(i - 2) * 7} label={['点検表', '報告書', '写真', '様式', '台帳'][i]} color={['#475569', COL.blue, COL.amber, '#9333ea', '#16a34a'][i]} k={0.9} />;
          })}
        </g>
        {/* B: 有資格者の立会い */}
        <g opacity={B}>
          <LiftWorker x={640} y={900} k={1.3} raise={eo(clamp01((s - cQual) / 1.5))} s={s} />
          <SignBoard x={1280} y={420} w={420} h={150} label="看板" fs={80} pole={260} />
        </g>
        {/* C: チェックリスト */}
        <g opacity={Cc}>
          <rect x={520} y={90} width={880} height={780} rx={20} fill="#f8fafc" />
          {Array.from({length: 18}, (_, i) => {
            const col = i % 2, row = Math.floor(i / 2);
            const label = ITEMS[i % ITEMS.length];
            return (
              <g key={i} opacity={pop(s, cItems + i * 0.22, 0.2) > 0 ? 1 : 0.25}>
                <Check x={590 + col * 430} y={165 + row * 78} p={pop(s, cItems + i * 0.22 + 0.05, 0.25)} k={0.9} />
                <text x={630 + col * 430} y={176 + row * 78} fontSize={30} fontWeight={700} fill="#0b1220" fontFamily="inherit">{label}</text>
              </g>
            );
          })}
        </g>
        {/* D: 事務所の机 */}
        <g opacity={D}>
          <rect x={300} y={720} width={1320} height={26} rx={6} fill="#6b4f35" />
          <rect x={360} y={746} width={24} height={150} fill="#4a3624" /><rect x={1536} y={746} width={24} height={150} fill="#4a3624" />
          <rect x={760} y={430} width={400} height={260} rx={14} fill="#0f1a33" stroke="#475569" strokeWidth={8} />
          <rect x={940} y={690} width={40} height={30} fill="#475569" />
          {Array.from({length: 6}, (_, i) => {
            const p = pop(s, cOff + 0.2 + i * 0.35, 0.4);
            return <rect key={i} x={380 + (i % 3) * 120 - (1 - p) * 120} y={600 + Math.floor(i / 3) * 56 - (1 - p) * 200} width={110} height={78} rx={6} fill={['#93c5fd', '#fcd34d', '#86efac'][i % 3]} stroke="#fff" strokeWidth={4} opacity={Math.min(1, p)} transform={`rotate(${(i - 3) * 4} 400 640)`} />;
          })}
          {[0, 1, 2].map((i) => <g key={i} opacity={Math.min(1, pop(s, cOff + 1 + i * 0.5, 0.4))}><Sheet x={1260 + i * 80} y={560 - i * 18} rot={i * 7 - 6} label="点検表" color="#475569" k={0.85} /></g>)}
        </g>
        {/* E: 自治体ごとの書式 */}
        <g opacity={E}>
          {CITIES.map(([n, c, r], i) => <Sheet key={i} x={380 + i * 230} y={150 + 260 + (i % 2) * 40 + (1 - Math.min(1, pop(s, cFmt - 0.2 + i * 0.45, 0.5))) * -500} rot={r} label={n} color={c} k={1.15} />)}
          <rect x={300} y={780} width={1340} height={14} rx={7} fill="#334766" />
        </g>
        {/* F: 1店舗 66基 / 773枚 */}
        <g opacity={F}>
          <Building x={150} y={170} w={560} h={400} cols={5} rows={4} lit={0.6} />
          <rect x={150} y={570} width={560} height={14} fill="#334766" />
          <text x={430} y={650} textAnchor="middle" fontSize={40} fontWeight={800} fill={COL.sub} fontFamily="inherit">ある1店舗</text>
          {Array.from({length: 66}, (_, i) => <rect key={i} x={800 + (i % 11) * 62} y={250 + Math.floor(i / 11) * 52} width={50} height={34} rx={5} fill={i / 66 < clamp01((s - c66) / 1.6) ? COL.blue : '#16223d'} stroke="#475569" strokeWidth={2} />)}
          {Array.from({length: 150}, (_, i) => <rect key={i} x={800 + (i % 25) * 28} y={580 + Math.floor(i / 25) * 22} width={22} height={16} rx={3} fill={i / 150 < clamp01((s - c773) / 1.4) ? COL.amber : '#16223d'} />)}
        </g>
        {/* G: 45店舗 → 80時間 */}
        <g opacity={G}>
          {Array.from({length: 45}, (_, i) => {
            const p = pop(s, c45 + i * 0.035, 0.3);
            return <g key={i} opacity={Math.min(1, p)} transform={`translate(${190 + (i % 9) * 100},${150 + Math.floor(i / 9) * 100}) scale(${0.5 + 0.5 * Math.min(1, p)})`}><rect x={-34} y={-14} width={68} height={50} rx={5} fill="#334766" stroke="#93c5fd" strokeWidth={3} /><path d="M-40 -14 L0 -42 L40 -14 z" fill={i % 5 === 0 ? COL.red : COL.blue} /></g>;
          })}
          <g opacity={pop(s, c80 - 0.2, 0.4)}><Clock x={1560} y={400} r={130} t={((s - c80) * 0.9 + 0.001) % 12} /></g>
        </g>
      </Svg>
      <KW s={s} at={cQual} until={cItems - 0.1} x={960} y={110} size={84}><span style={{color: COL.amber}}>有資格者</span>が現場で立会い</KW>
      <Fill style={{opacity: Cc}}>
        <div style={{position: 'absolute', left: 1440, top: 330, fontSize: 80, fontWeight: 800, color: '#fff', textAlign: 'center', width: 400}}><span style={{fontSize: 200, color: COL.amber}}>{n18}</span><br />/ 18+<br /><span style={{fontSize: 40, color: COL.sub}}>項目</span></div>
      </Fill>
      <KW s={s} at={cOff} until={cFmt - 0.5} x={960} y={140} size={80}>事務所へ戻り、<span style={{color: COL.amber}}>報告書</span></KW>
      <KW s={s} at={cFmt} until={cShop - 0.1} x={960} y={110} size={72}>自治体ごとに<span style={{color: COL.amber}}>書式がちがう</span></KW>
      <Fill style={{opacity: F}}>
        <div style={{position: 'absolute', left: 1500, top: 230, width: 400, fontSize: 130, fontWeight: 800, color: '#fff', opacity: pop(s, c66, 0.4)}}><Odo value="66" at={c66} size={130} /><span style={{fontSize: 60, color: COL.blue}}>基</span></div>
        <div style={{position: 'absolute', left: 1540, top: 590, width: 380, fontSize: 130, fontWeight: 800, color: '#fff', opacity: pop(s, c773, 0.4)}}><Odo value="773" at={c773} size={130} /><span style={{fontSize: 60, color: COL.amber}}>枚</span></div>
      </Fill>
      <Fill style={{opacity: G}}>
        <div style={{position: 'absolute', left: 110, top: 600, width: 900, fontSize: 70, fontWeight: 800, color: COL.sub, opacity: pop(s, c45, 0.4)}}>45店舗の案件</div>
        <div style={{position: 'absolute', left: 1250, top: 580, width: 600, textAlign: 'center', fontSize: 190, fontWeight: 800, color: COL.red, opacity: pop(s, c80, 0.4)}}><Odo value="80" at={c80} size={190} color={COL.red} /><span style={{fontSize: 80}}>時間</span></div>
        <div style={{position: 'absolute', left: 1250, top: 130, width: 600, textAlign: 'center', fontSize: 40, fontWeight: 700, color: COL.sub, opacity: pop(s, c80 + 0.3, 0.4)}}>事務作業だけで</div>
      </Fill>
    </Shell>
  );
};
