import React from 'react';
import {TItem, cue} from '../data/timeline';
import {Arrow, Bg, COL, Gov, KW, Pill, PhoneSvg, Svg, pop, useS, win, clamp01, eo} from '../mg';
import {Shell} from '../lib';

// 市民のスマホ → 写真 → 安全性 / 法令の目安 → プラットフォーム → 行政(試作の提案)
export const S09: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const cPhoto = cue(t, '写真をとる', 0.2, 0), cSafe = cue(t, '安全性', 0.4, 0), cLaw = cue(t, '法令', 0.5, 0), cPlat = cue(t, 'プラットフォーム', 0.8, 0), cFuture = 0.5;
  const y = 470;
  return (
    <Shell t={t}>
      <Bg />
      <KW s={s} at={cFuture} until={cPhoto + 0.3} x={960} y={150} size={80} color={COL.sub}>今後の展開</KW>
      <Svg>
        <g opacity={pop(s, 0.8, 0.5)}>
          <PhoneSvg x={260} y={y} w={230} h={440}>
            <rect x={-80} y={-130} width={160} height={120} rx={12} fill="#334766" />
            <circle cx={0} cy={-70} r={30} fill={COL.sub} />
            <rect x={-80} y={20} width={160} height={30} rx={8} fill={COL.blue} />
            <text x={0} y={42} textAnchor="middle" fontSize={22} fontWeight={800} fill="#fff" fontFamily="inherit">送る</text>
          </PhoneSvg>
          <text x={260} y={y + 290} textAnchor="middle" fontSize={44} fontWeight={800} fill="#fff" fontFamily="inherit">市民</text>
        </g>
        <Arrow x1={400} y1={y} x2={640} y2={y} p={eo(clamp01((s - cPhoto) / 0.5))} />
        <g opacity={pop(s, cPhoto + 0.3, 0.5)}>
          <rect x={650} y={y - 90} width={190} height={150} rx={14} fill="#f8fafc" />
          <path d={`M660 ${y + 50} l60 -70 l40 40 l30 -30 l50 60 z`} fill="#64748b" /><circle cx={790} cy={y - 40} r={16} fill={COL.amber} />
          <text x={745} y={y + 110} textAnchor="middle" fontSize={40} fontWeight={800} fill="#fff" fontFamily="inherit">写真</text>
        </g>
        <Pill x={1000} y={y - 90} w={280} text="安全性" size={46} lit={pop(s, cSafe, 0.4)} color={COL.green} />
        <Pill x={1000} y={y + 50} w={280} text="法令の目安" size={42} lit={pop(s, cLaw, 0.4)} color={COL.amber} />
        <Arrow x1={880} y1={y - 20} x2={1140} y2={y - 20} p={eo(clamp01((s - cSafe) / 0.5))} color={COL.sub} w={6} />
        <g opacity={pop(s, cPlat, 0.5)} transform={`translate(1330,${y})`}>
          <rect x={-120} y={-100} width={240} height={200} rx={26} fill="#16223d" stroke={COL.blue} strokeWidth={7} />
          {[-50, 0, 50].map((yy) => <rect key={yy} x={-80} y={yy - 14} width={160} height={28} rx={8} fill={COL.blue} opacity={0.5 + 0.4 * Math.sin(s * 5 + yy)} />)}
          <text x={0} y={150} textAnchor="middle" fontSize={34} fontWeight={800} fill="#fff" fontFamily="inherit">通報プラットフォーム</text>
        </g>
        <Arrow x1={1460} y1={y} x2={1580} y2={y} p={eo(clamp01((s - cPlat - 0.6) / 0.5))} />
        <g opacity={pop(s, cPlat + 0.8, 0.5)}><Gov x={1730} y={y} k={0.75} /><text x={1730} y={y + 160} textAnchor="middle" fontSize={44} fontWeight={800} fill="#fff" fontFamily="inherit">行政</text></g>
      </Svg>
      <KW s={s} at={cPlat + 1.2} x={960} y={820} size={66}>行政に提案する<span style={{color: COL.blue}}>試作</span></KW>
    </Shell>
  );
};
