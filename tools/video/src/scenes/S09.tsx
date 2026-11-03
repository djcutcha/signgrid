import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TItem, cue} from '../data/timeline';
import {C, FPS} from '../theme';
import {Fill, Odo, Phone, Shell, Word, ZoomVideo, clamp01, eo, Key} from '../lib';
import {Img, staticFile} from 'remotion';

const KEYS: Key[] = [
  {u: 0, cx: 0.5, cy: 0.2, s: 1.0},
  {u: 0.5, cx: 0.5, cy: 0.45, s: 1.08, ring: [0.5, 0.5]},
  {u: 1, cx: 0.5, cy: 0.6, s: 1.05},
];
export const S09: React.FC<{t: TItem}> = ({t}) => {
  const f = useCurrentFrame();
  const sw = cue(t, '見つからない', 0.7, 0.2);
  const rate = 14 / (sw - 0.3);
  const o2 = eo(clamp01((f / FPS - sw) / 0.4));
  const ka = cue(t, '職員が撮った', 0.15, 0.1), kb = cue(t, '台帳の下書き', 0.4, 0.1);
  const z = 1 + 0.08 * clamp01((f / FPS - sw) / 6);
  return (
    <Shell t={t}>
      <Fill style={{background: 'radial-gradient(900px 800px at 30% 50%, rgba(59,130,246,.2), rgba(11,18,32,0) 70%)'}} />
      <div style={{opacity: 1 - o2}}>
        <Phone x={330} y={104} w={380} h={822}>
          <ZoomVideo src="assets/04_gov_sp.mp4" clipSec={14} rate={rate} keys={KEYS} w={380} h={822} vw={380} vh={822} start={9} />
        </Phone>
        <Word at={ka} style={{left: 900, top: 250, fontSize: 130}}>撮る <span style={{color: C.accent}}>→</span> 送る</Word>
        <Word at={kb} style={{left: 900, top: 470, fontSize: 80}}>
          <span style={{display: 'inline-flex', alignItems: 'center', gap: 18}}>写真<Odo value="10" at={kb} size={110} />枚 <span style={{color: C.accent}}>→</span> 候補<Odo value="6" at={kb + 0.2} size={110} color={C.accent} />基</span>
        </Word>
      </div>
      <div style={{position: 'absolute', left: 160, top: 80, width: 1600, height: 800, borderRadius: 16, overflow: 'hidden', opacity: o2, boxShadow: '0 30px 80px rgba(0,0,0,.6)', background: '#fff'}}>
        <Img src={staticFile('img/gov-capture-map-pc.png')} style={{width: 1600, objectFit: 'cover', objectPosition: 'top', transform: `scale(${z})`, transformOrigin: '30% 20%'}} />
      </div>
      <Word at={sw + 0.2} style={{left: 0, width: 1920, bottom: 70, textAlign: 'center'}}>
        <span style={{display: 'inline-block', background: 'rgba(11,18,32,.92)', padding: '20px 56px', borderRadius: 16, fontSize: 76}}>違法と決めつけない。<span style={{color: C.accent}}>候補を絞る</span></span>
      </Word>
    </Shell>
  );
};
