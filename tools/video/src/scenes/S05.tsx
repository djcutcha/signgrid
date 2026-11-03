import React from 'react';
import {TItem, cue} from '../data/timeline';
import {C} from '../theme';
import {Fill, Key, Odo, Shell, Word, ZoomVideo} from '../lib';

const KEYS: Key[] = [
  {u: 0, cx: 0.4, cy: 0.18, s: 1.5, ring: [0.3, 0.16]},
  {u: 0.28, cx: 0.4, cy: 0.22, s: 1.55, ring: [0.35, 0.2]},
  {u: 0.43, cx: 0.5, cy: 0.28, s: 1.5, ring: [0.4, 0.26]},
  {u: 0.62, cx: 0.5, cy: 0.5, s: 1.3},
  {u: 0.72, cx: 0.55, cy: 0.3, s: 1.6, ring: [0.5, 0.3]},
  {u: 1, cx: 0.5, cy: 0.3, s: 1.6},
];

export const S05: React.FC<{t: TItem}> = ({t}) => {
  const rate = 21 / (t.sec - 0.9);
  const k9 = cue(t, '約9分', 0.7, 0.4), kd = cue(t, '7ドル', 0.9, 0.4);
  return (
    <Shell t={t}>
      <ZoomVideo src="assets/01_demo-start_pc.mp4" clipSec={21} rate={rate} keys={KEYS} start={9} />
      <Fill style={{background: 'linear-gradient(to bottom, rgba(11,18,32,.0) 60%, rgba(11,18,32,.35))'}} />
      <Word at={k9} style={{right: 70, top: 60, textAlign: 'right', background: 'rgba(11,18,32,.88)', padding: '22px 42px', borderRadius: 16}}>
        <div style={{fontSize: 36, color: C.sub, fontWeight: 700}}>1店舗の一周(人の確認を除く)</div>
        <div style={{fontSize: 150, lineHeight: 1.1, display: 'flex', justifyContent: 'flex-end'}}><span style={{fontSize: 90, alignSelf: 'flex-end', marginBottom: 14}}>約</span><Odo value="9" at={k9} size={150} /><span style={{fontSize: 90, alignSelf: 'flex-end', marginBottom: 14}}>分</span></div>
      </Word>
      <Word at={kd} style={{right: 70, top: 330, textAlign: 'right', background: 'rgba(11,18,32,.88)', padding: '18px 42px', borderRadius: 16}}>
        <div style={{fontSize: 36, color: C.sub, fontWeight: 700}}>API の費用</div>
        <div style={{fontSize: 120, lineHeight: 1.1, color: C.accent, display: 'flex', justifyContent: 'flex-end'}}><span>$</span><Odo value="7.44" at={kd} size={120} color={C.accent} /></div>
      </Word>
    </Shell>
  );
};
