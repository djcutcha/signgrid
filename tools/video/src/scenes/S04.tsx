import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TimelineItem} from '../data/scenes';
import {CalloutColumn, Frame, Pic, SceneShell, fadeIn} from '../ui';

const L = [
  {src: 'img/karte-pc.png', label: '現場の記録'},
  {src: 'img/guide-measure-pc.png', label: '看板の寸法'},
  {src: 'img/rules-pc.png', label: '場所の規制'},
];
export const S04: React.FC<{t: TimelineItem}> = ({t}) => {
  const f = useCurrentFrame();
  return (
    <SceneShell t={t}>
      {L.map((l, i) => {
        const o = fadeIn(f, 8 + i * Math.round(t.frames * 0.22));
        return (
          <div key={i} style={{transform: `translateY(${(1 - o) * 30}px)`}}>
            <Frame x={80 + i * 230} y={110 + i * 120} w={800} h={450} o={o} label={l.label}>
              <Pic src={l.src} style={{width: '100%'}} />
            </Frame>
          </div>
        );
      })}
      <CalloutColumn items={t.def.callouts} />
    </SceneShell>
  );
};
