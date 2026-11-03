import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TimelineItem} from '../data/scenes';
import {C} from '../theme';
import {CalloutColumn, SceneShell, fadeIn} from '../ui';

export const S10: React.FC<{t: TimelineItem}> = ({t}) => {
  const f = useCurrentFrame();
  const sw = Math.round(t.frames * 0.55);
  const a = fadeIn(f, 6) * (1 - fadeIn(f, sw - 12, 12));
  const b = fadeIn(f, sw);
  return (
    <SceneShell t={t}>
      <div style={{position: 'absolute', left: 80, top: 100, width: 1260, height: 700, display: 'flex', alignItems: 'center', justifyContent: 'center', opacity: a}}>
        <div style={{fontSize: 76, fontWeight: 700, lineHeight: 1.5, textAlign: 'center'}}>現場と行政、その両側から<br /><span style={{color: C.accent}}>看板事故を減らしたい</span></div>
      </div>
      <div style={{position: 'absolute', left: 0, top: 100, width: 1340, height: 700, display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', opacity: b}}>
        <div style={{fontSize: 110, fontWeight: 800}}>SignGrid <span style={{color: C.accent}}>看板カルテ</span></div>
        <div style={{fontSize: 40, color: C.sub, marginTop: 30}}>Built with Claude: Japan 2026</div>
      </div>
      <CalloutColumn items={t.def.callouts} first={30} />
    </SceneShell>
  );
};
