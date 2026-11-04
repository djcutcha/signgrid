import React from 'react';
import {TItem, cue} from '../data/timeline';
import {Bg, COL, KW, Person, SignBoard, Svg, pop, useS, win} from '../mg';
import {Chars, Photo, Shell} from '../lib';

// 実写に差し替えるときは、ここに runs/video_v2/assets/ の下のパスを入れる(例: 'assets/images/02_self.png')。null の間は絵の背景
const SELF_PHOTO: string | null = null;

export const S02: React.FC<{t: TItem}> = ({t}) => {
  const s = useS();
  const k = cue(t, '屋外広告士', 0.25, 0.1);
  const k2 = cue(t, '看板を', 0.5, 0.1);
  return (
    <Shell t={t}>
      {SELF_PHOTO ? <Photo src={SELF_PHOTO} frames={t.frames} z0={1.0} z1={1.06} grad={0.9} /> : <Bg />}
      <Svg>
        <g opacity={pop(s, 0.5, 0.6)} transform={`translate(${280 - (1 - pop(s, 0.5, 0.6)) * 200},0)`}>
          <SignBoard x={0} y={470} w={300} h={140} rot={-6} label="看板" fs={70} pole={170} />
        </g>
        <g opacity={pop(s, 0.8, 0.6)} transform={`translate(${1640 + (1 - pop(s, 0.8, 0.6)) * 200},0)`}>
          <Person x={0} y={780} k={2.6} helmet />
        </g>
      </Svg>
      <KW s={s} at={0.3} x={960} y={300} size={44} color={COL.sub}>私は</KW>
      <div style={{position: 'absolute', left: 0, width: 1920, top: 350, textAlign: 'center'}}><Chars text="屋外広告士" at={k} size={200} step={0.09} /></div>
      <div style={{position: 'absolute', left: 960 - 480 * win(s, k + 0.5, Infinity, 0.6), width: 960 * win(s, k + 0.5, Infinity, 0.6), top: 620, height: 6, background: COL.blue}} />
      <KW s={s} at={k2} x={960} y={760} size={64}>看板をつくり、管理する会社</KW>
    </Shell>
  );
};
