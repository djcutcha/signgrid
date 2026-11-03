import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import {TimelineItem} from '../data/scenes';
import {CalloutColumn, Clip, Frame, Pic, SceneShell, fadeIn} from '../ui';
import {FPS} from '../theme';

export const S03: React.FC<{t: TimelineItem}> = ({t}) => {
  const f = useCurrentFrame();
  const vf = 9 * FPS + 6;
  const o2 = fadeIn(f, vf - 12, 12);
  const pan = interpolate(f, [vf - 12, t.frames], [0, -22], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const zoom = interpolate(f, [vf - 12, t.frames], [1, 1.12], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <SceneShell t={t}>
      <Frame x={80} y={100} w={1260} h={709}>
        <Clip src="assets/03_burden-output_pc.mp4" clipSec={9} style={{width: '100%', height: '100%'}} />
        <div style={{position: 'absolute', inset: 0, opacity: o2, overflow: 'hidden', background: '#fff'}}>
          <Pic src="img/rules-pc.png" style={{width: '100%', transformOrigin: 'top left', transform: `translateY(${pan}%) scale(${zoom})`}} />
        </div>
      </Frame>
      <CalloutColumn items={t.def.callouts} />
    </SceneShell>
  );
};
