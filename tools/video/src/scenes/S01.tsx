import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import {TimelineItem} from '../data/scenes';
import {C, FPS} from '../theme';
import {CalloutColumn, SceneShell, fadeIn} from '../ui';

export const S01: React.FC<{t: TimelineItem}> = ({t}) => {
  const f = useCurrentFrame();
  const split = Math.round(t.frames * 0.4);
  const title = fadeIn(f, 0, 8) * (1 - fadeIn(f, FPS - 8, 8));
  const a = fadeIn(f, FPS + 6) * (1 - fadeIn(f, split - 10, 10));
  const b = fadeIn(f, split);
  const pulse = interpolate(f, [FPS + 6, t.frames], [0.96, 1.04], {extrapolateRight: 'clamp'});
  return (
    <SceneShell t={t}>
      <div style={{position: 'absolute', inset: 0, display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: title}}>
        <div style={{fontSize: 96, fontWeight: 700}}>SignGrid <span style={{color: C.accent}}>看板カルテ</span></div>
      </div>
      <div style={{position: 'absolute', left: 80, top: 100, width: 1260, height: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: a, transform: `scale(${pulse})`}}>
        <div style={{fontSize: 400, fontWeight: 800, color: '#f87171'}}>8割</div>
      </div>
      <div style={{position: 'absolute', left: 80, top: 100, width: 1260, height: 700, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', opacity: b}}>
        <div style={{fontSize: 150, fontWeight: 800}}>89件中<span style={{color: '#f87171'}}>73件</span></div>
        <div style={{fontSize: 44, color: C.sub, marginTop: 24}}>自家用 43 + 無許可 30</div>
      </div>
      {f > split && <CalloutColumn items={t.def.callouts} first={split + 6} />}
    </SceneShell>
  );
};
