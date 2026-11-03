import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TimelineItem} from '../data/scenes';
import {C} from '../theme';
import {CalloutColumn, SceneShell, fadeIn} from '../ui';

const K = [
  {h: '写真と看板の対応付け', s: '索引 69基'},
  {h: '手書きの読み取り', s: '転記 140頁'},
  {h: '帳票の凡例の解釈', s: 'D=◎ … A=× と a/b/c'},
  {h: '規制との突き合わせ', s: '4自治体の規則'},
];
export const S06a: React.FC<{t: TimelineItem}> = ({t}) => {
  const f = useCurrentFrame();
  return (
    <SceneShell t={t}>
      <div style={{position: 'absolute', left: 80, top: 110, width: 1260, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 28}}>
        {K.map((k, i) => {
          const o = fadeIn(f, 6 + i * 12);
          return (
            <div key={i} style={{opacity: o, transform: `translateY(${(1 - o) * 24}px)`, background: C.panel, borderRadius: 12, padding: '44px 36px', height: 270, boxSizing: 'border-box', borderTop: `6px solid ${C.accent}`}}>
              <div style={{fontSize: 40, fontWeight: 700}}>{k.h}</div>
              <div style={{fontSize: 28, color: C.sub, marginTop: 20}}>{k.s}</div>
            </div>
          );
        })}
      </div>
      <CalloutColumn items={t.def.callouts} first={60} />
    </SceneShell>
  );
};
