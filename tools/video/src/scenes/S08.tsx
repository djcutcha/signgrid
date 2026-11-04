import React from 'react';
import {TItem, cue} from '../data/timeline';
import {Bg, COL, KW, MapBg, Person, PhoneSvg, Pin, Svg, Fill, pop, useS, win, clamp01, eo} from '../mg';
import {Shell} from '../lib';

const PINS: [number, number][] = [[560, 360], [820, 560], [1080, 330], [1300, 600], [1500, 400]];
export const S08: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const cDate = 0.4, cShop = cue(t, '店舗を回り', 0.2, 0), cPhone = cue(t, 'スマホ', 0.36, 0), cUse = cue(t, '現場で', 0.7, 0);
  const prog = clamp01((s - cShop) / 5) * (PINS.length - 1);
  const k = Math.min(PINS.length - 2, Math.floor(prog)), f = prog - k;
  const cx = PINS[k][0] + (PINS[k + 1][0] - PINS[k][0]) * eo(f), cy = PINS[k][1] + (PINS[k + 1][1] - PINS[k][1]) * eo(f) - Math.sin(f * Math.PI) * 60;
  const flip = clamp01((s - cDate) / 0.5);
  return (
    <Shell t={t}>
      <Bg />
      <Svg>
        <MapBg x={380} y={200} w={1300} h={560} />
        {PINS.map(([x, y], i) => <Pin key={i} x={x} y={y} k={Math.min(1, pop(s, cShop - 0.4 + i * 0.25, 0.4))} color={i <= prog + 0.3 ? COL.green : COL.blue} />)}
        <g opacity={win(s, cShop)}>
          <Person x={cx} y={cy + 10} k={0.9} helmet color="#7dd3fc" />
        </g>
        <g opacity={win(s, cPhone)} transform={`translate(0,${(1 - pop(s, cPhone, 0.5)) * 200})`}>
          <PhoneSvg x={240} y={640} w={190} h={380}>
            {[0, 1, 2].map((i) => <rect key={i} x={-62} y={-130 + i * 100} width={124} height={70} rx={10} fill={i === 1 ? COL.green : COL.blue} opacity={0.4 + 0.2 * i} />)}
          </PhoneSvg>
        </g>
      </Svg>
      <div style={{position: 'absolute', left: 150, top: 70, width: 520, height: 150, perspective: 800}}>
        <div style={{width: 520, height: 150, borderRadius: 24, background: COL.blue, color: '#fff', fontSize: 100, fontWeight: 800, textAlign: 'center', lineHeight: '150px', transform: `rotateX(${(1 - eo(flip)) * -90}deg)`, boxShadow: '0 20px 60px rgba(0,0,0,.5)'}}>11月5日</div>
      </div>
      <KW s={s} at={cPhone} until={cUse - 0.1} x={1280} y={880} size={72}>スマホで<span style={{color: COL.green}}>簡易点検</span></KW>
      <KW s={s} at={cUse} x={1280} y={880} size={72}>私たちの<span style={{color: COL.amber}}>現場</span>で使いたい</KW>
    </Shell>
  );
};
