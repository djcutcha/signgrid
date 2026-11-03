import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TimelineItem} from '../data/scenes';
import {C} from '../theme';
import {Card, Clip, Frame, SceneShell} from '../ui';

export const S06b: React.FC<{t: TimelineItem}> = ({t}) => {
  const f = useCurrentFrame();
  const span = t.frames - 40;
  const idx = Math.min(5, Math.max(0, Math.floor(((f - 20) / span) * 6)));
  return (
    <SceneShell t={t}>
      <Frame x={80} y={100} w={1260} h={709}>
        <Clip src="assets/02_inherit-loop_pc.mp4" clipSec={18} style={{width: '100%', height: '100%'}} />
      </Frame>
      <div style={{position: 'absolute', left: 1400, top: 100, width: 440, display: 'flex', flexDirection: 'column', gap: 12}}>
        {t.def.callouts.map((c, i) => (
          <div key={i} style={{opacity: i <= idx ? 1 : 0.35}}>
            <Card c={c} start={0} active={i === idx} style={{padding: '16px 28px', background: i === idx ? C.accent : C.panel}} />
          </div>
        ))}
      </div>
    </SceneShell>
  );
};
