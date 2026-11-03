import React from 'react';
import {TimelineItem} from '../data/scenes';
import {CalloutColumn, Clip, Frame, SceneShell} from '../ui';

export const S05: React.FC<{t: TimelineItem}> = ({t}) => (
  <SceneShell t={t}>
    <Frame x={80} y={100} w={1260} h={709}>
      <Clip src="assets/01_demo-start_pc.mp4" clipSec={21} style={{width: '100%', height: '100%'}} />
    </Frame>
    <CalloutColumn items={t.def.callouts} />
  </SceneShell>
);
