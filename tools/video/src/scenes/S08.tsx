import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import {TimelineItem} from '../data/scenes';
import {FPS} from '../theme';
import {CalloutColumn, Clip, Frame, Phone, Pic, SceneShell, fadeIn} from '../ui';

// 前半: 06_measure の録画(5秒)→ 後半: guide-karte-sp の静止画をスマホ枠でゆっくりスクロール
export const S08: React.FC<{t: TimelineItem}> = ({t}) => {
  const f = useCurrentFrame();
  const sw = 5 * FPS + 6;
  const o2 = fadeIn(f, sw, 14);
  const scroll = interpolate(f, [sw, t.frames - 20], [0, -420], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  return (
    <SceneShell t={t}>
      <Frame x={80} y={100} w={1260} h={709} o={1 - o2}>
        <Clip src="assets/06_measure_pc.mp4" clipSec={5} style={{width: '100%', height: '100%'}} />
      </Frame>
      <div style={{opacity: o2}}>
        <Phone x={531} y={90} w={340} h={716}>
          <Pic src="img/guide-karte-sp.png" style={{width: '100%', transform: `translateY(${scroll}px)`}} />
        </Phone>
      </div>
      <CalloutColumn items={t.def.callouts} />
    </SceneShell>
  );
};
