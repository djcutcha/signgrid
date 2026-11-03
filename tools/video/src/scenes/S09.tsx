import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TimelineItem} from '../data/scenes';
import {Card, Clip, Frame, Phone, Pic, SceneShell, fadeIn} from '../ui';
import {FPS} from '../theme';

export const S09: React.FC<{t: TimelineItem}> = ({t}) => {
  const f = useCurrentFrame();
  const sw = 14 * FPS + 10;
  const o2 = fadeIn(f, sw, 12);
  const c = t.def.callouts;
  const col = (xs: number, items: number[]) => (
    <div style={{position: 'absolute', left: xs, top: 100, width: 440, display: 'flex', flexDirection: 'column', gap: 20}}>
      {items.map((i) => <Card key={i} c={c[i]} start={18 + i * 24} />)}
    </div>
  );
  return (
    <SceneShell t={t}>
      <div style={{opacity: 1 - o2}}>
        <Phone x={781} y={90} w={330} h={714}>
          <Clip src="assets/04_gov_sp.mp4" clipSec={14} style={{width: '100%', height: '100%'}} />
        </Phone>
      </div>
      <Frame x={540} y={170} w={840} h={473} o={o2}>
        <Pic src="img/gov-capture-map-pc.png" style={{width: '100%'}} />
      </Frame>
      {col(60, [0, 1])}
      {col(1420, [2, 3])}
    </SceneShell>
  );
};
