import React from 'react';
import {TimelineItem} from '../data/scenes';
import {CalloutColumn, Clip, Frame, Phone, Pic, SceneShell} from '../ui';

export const S08: React.FC<{t: TimelineItem}> = ({t}) => (
  <SceneShell t={t}>
    <Frame x={80} y={130} w={880} h={495}>
      <Clip src="assets/06_measure_pc.mp4" clipSec={5} style={{width: '100%', height: '100%'}} />
    </Frame>
    <Phone x={1010} y={90} w={340} h={736}>
      <Pic src="img/guide-karte-sp.png" style={{width: '100%'}} />
    </Phone>
    <CalloutColumn items={t.def.callouts} x={1440} w={400} />
  </SceneShell>
);
