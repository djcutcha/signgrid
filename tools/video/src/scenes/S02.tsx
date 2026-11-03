import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TimelineItem} from '../data/scenes';
import {C} from '../theme';
import {CalloutColumn, SceneShell, fadeIn} from '../ui';

const Big: React.FC<{x: number; at: number; k: string; n1: string; l1: string; n2: string; l2: string}> = ({x, at, k, n1, l1, n2, l2}) => {
  const f = useCurrentFrame();
  const o = fadeIn(f, at);
  return (
    <div style={{position: 'absolute', left: x, top: 130, width: 610, height: 600, background: C.panel, borderRadius: 12, opacity: o, transform: `translateY(${(1 - o) * 30}px)`, padding: 40, boxSizing: 'border-box'}}>
      <div style={{fontSize: 30, color: C.sub, marginBottom: 40}}>{k}</div>
      <div style={{fontSize: n1.length > 4 ? 100 : 130, fontWeight: 800, lineHeight: 1}}>{n1}</div>
      <div style={{fontSize: 36, marginBottom: 50, marginTop: 8}}>{l1}</div>
      <div style={{fontSize: n2.length > 4 ? 84 : 130, fontWeight: 800, lineHeight: 1, color: '#f87171'}}>{n2}</div>
      <div style={{fontSize: 36, marginTop: 8}}>{l2}</div>
    </div>
  );
};

export const S02: React.FC<{t: TimelineItem}> = ({t}) => (
  <SceneShell t={t}>
    <Big x={80} at={10} k="客先の1店舗" n1="66基" l1="看板" n2="773枚" l2="写真" />
    <Big x={720} at={Math.round(t.frames * 0.4)} k="別の客先 45店舗・1ラウンド" n1="45店舗" l1="の案件" n2="80時間超" l2="事務作業だけで" />
    <CalloutColumn items={t.def.callouts} />
  </SceneShell>
);
