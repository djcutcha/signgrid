import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TimelineItem} from '../data/scenes';
import {C} from '../theme';
import {SceneShell, fadeIn} from '../ui';

export const S07: React.FC<{t: TimelineItem}> = ({t}) => {
  const f = useCurrentFrame();
  return (
    <SceneShell t={t}>
      <div style={{position: 'absolute', left: 80, top: 90, width: 1060, background: C.panel, borderRadius: 12, padding: '10px 0'}}>
        {t.def.callouts.map((c, i) => {
          const o = fadeIn(f, 10 + i * 16);
          return (
            <div key={i} style={{opacity: o, display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '20px 40px', borderBottom: i < 4 ? `1px solid ${C.line}` : 'none'}}>
              <div>
                <div style={{fontSize: 30}}>{c.text}</div>
                {c.sub && <div style={{fontSize: 21, color: C.sub, marginTop: 4}}>{c.sub}</div>}
              </div>
              <div style={{fontSize: 56, fontWeight: 800, color: i === 0 || i === 4 ? '#f87171' : C.text, whiteSpace: 'nowrap'}}>{c.big}</div>
            </div>
          );
        })}
      </div>
      <div style={{position: 'absolute', left: 1200, top: 90, width: 640, background: C.panel, borderRadius: 12, padding: '40px 40px', borderLeft: `6px solid ${C.accent}`, opacity: fadeIn(f, Math.round(t.frames * 0.55)), boxSizing: 'border-box'}}>
        <div style={{fontSize: 24, color: C.accent, marginBottom: 14}}>Claude Code で直した例</div>
        <div style={{fontSize: 38, fontWeight: 700, lineHeight: 1.5}}>帳票の凡例が様式で逆だった</div>
      </div>
    </SceneShell>
  );
};
