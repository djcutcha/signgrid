import React from 'react';
import {TItem, cue} from '../data/timeline';
import {C} from '../theme';
import {Fill, Key, Phone, Shell, Word, ZoomVideo} from '../lib';

// スマホ枠の中で新規の録画(780x1688、16.2 秒)。写真 → 点線の枠 → 寸法の範囲 → 許可の判定カード
const CLIP = 16.2;
const KEYS: Key[] = [
  {u: 0, cx: 0.5, cy: 0.5, s: 1.0},
  {u: 1, cx: 0.5, cy: 0.5, s: 1.0},
];
export const S05: React.FC<{t: TItem}> = ({t}) => {
  const rate = CLIP / (t.sec - 0.9);
  const k1 = cue(t, '写真を1枚', 0.0, 0.0), kb = cue(t, '寸法を推定', 0.15, 0.1), kr = cue(t, 'その住所', 0.3, 0.1), kp = cue(t, '許可が要るか', 0.5, 0.1), kd = cue(t, '点線の枠', 0.7, 0.1);
  const H = 860, Wd = Math.round((H * 780) / 1688);
  return (
    <Shell t={t}>
      <Fill style={{background: 'radial-gradient(900px 800px at 25% 50%, rgba(59,130,246,.2), rgba(11,18,32,0) 70%)'}} />
      <Phone x={300} y={26} w={Wd} h={H}>
        <ZoomVideo src="assets/05_owner_bbox_sp.mp4" clipSec={CLIP} rate={rate} keys={KEYS} w={Wd} h={H} vw={Wd} vh={H} start={9} />
      </Phone>
      <Word at={k1} style={{left: 900, top: 150, fontSize: 200, lineHeight: 1.1}}>写真<span style={{color: C.accent}}>1枚</span></Word>
      <Word at={kb} style={{left: 900, top: 420, fontSize: 64}}>寸法を推定</Word>
      <Word at={kr} style={{left: 900, top: 520, fontSize: 64}}>住所の規制と照合</Word>
      <Word at={kp} style={{left: 900, top: 620, fontSize: 64, color: '#ffc857'}}>許可が要るか、判定</Word>
      <Word at={kd} style={{left: 900, top: 740, fontSize: 40, color: C.sub, fontWeight: 700}}>点線の枠で、どの看板を測ったか分かる</Word>
    </Shell>
  );
};
