import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TItem, cue} from '../data/timeline';
import {C, FPS} from '../theme';
import {Drift, Fill, Key, Odo, Phone, Shell, Word, ZoomVideo, clamp01, eo} from '../lib';

// 前半: 05_owner_sp(スマホ縦)。入力欄と「寸法を推定」→ 調べた結果(許可の判定)へ寄る
const KEYS_A: Key[] = [
  {u: 0, cx: 0.5, cy: 0.88, s: 1.25, ring: [0.5, 0.86]},
  {u: 0.17, cx: 0.5, cy: 0.2, s: 1.3},
  {u: 0.45, cx: 0.5, cy: 0.27, s: 1.4, ring: [0.5, 0.26]},
  {u: 1, cx: 0.5, cy: 0.33, s: 1.35},
];
// 後半: 06_measure_pc(PC)。寸法の欄(実測バッジ)→ 判断できない行へ寄る
const KEYS_B: Key[] = [
  {u: 0, cx: 0.72, cy: 0.32, s: 1.7, ring: [0.69, 0.27]},
  {u: 0.55, cx: 0.74, cy: 0.35, s: 1.7},
  {u: 0.8, cx: 0.4, cy: 0.78, s: 1.6, ring: [0.3, 0.78]},
  {u: 1, cx: 0.4, cy: 0.78, s: 1.6},
];

export const S07b: React.FC<{t: TItem}> = ({t}) => {
  const f = useCurrentFrame();
  const sec = f / FPS;
  const sw = cue(t, '自信が低い', 0.5, 0.5);
  const o2 = eo(clamp01((sec - sw) / 0.3));
  const k1 = cue(t, '写真から', 0.0, 0.0), k2 = cue(t, '誤差の中央値', 0.2, 0.2);
  const k3 = sw + 0.3, k4 = cue(t, '初回に実測', 0.65, 0.2), k5 = k4 + 0.5;
  const rateA = 9.77 / sw;
  const rateB = 4.87 / (t.sec - sw - 0.3 - 0.9);
  return (
    <Shell t={t}>
      <Drift frames={t.frames} />
      <Fill style={{background: 'radial-gradient(900px 800px at 25% 50%, rgba(59,130,246,.2), rgba(11,18,32,0) 70%)'}} />
      <div style={{opacity: 1 - o2}}>
        <Phone x={150} y={104} w={380} h={822}>
          <ZoomVideo src="assets/05_owner_sp.mp4" clipSec={9.77} rate={rateA} keys={KEYS_A} w={380} h={822} vw={380} vh={822} start={9} />
        </Phone>
        <Word at={k1} until={sw} style={{left: 700, top: 250, fontSize: 100}}>
          写真から<span style={{color: C.accent}}>寸法</span>を推定
        </Word>
        <Word at={k2} until={sw} style={{left: 700, top: 470}}>
          <div style={{fontSize: 56, color: C.sub, fontWeight: 700}}>誤差の中央値</div>
          <div style={{display: 'flex', alignItems: 'flex-end', gap: 8, color: C.accent}}>
            <Odo value="17" at={k2} size={190} color={C.accent} /><span style={{fontSize: 110, marginBottom: 10}}>%</span>
          </div>
        </Word>
      </div>
      <div style={{opacity: o2}}>
        <div style={{position: 'absolute', left: 80, top: 100, width: 900, height: 760, borderRadius: 16, overflow: 'hidden', boxShadow: '0 30px 80px rgba(0,0,0,.6)', background: '#fff'}}>
          <ZoomVideo src="assets/06_measure_pc.mp4" clipSec={4.87} rate={rateB} keys={KEYS_B} w={900} h={760} vw={1920} vh={1080} start={Math.round((sw + 0.3) * FPS)} />
        </div>
        <Word at={k3} style={{left: 1120, top: 170, fontSize: 64, lineHeight: 1.25}}>
          自信が低い<br /><span style={{color: C.accent}}>→ 要実測</span>
        </Word>
        <Word at={k4} style={{left: 1120, top: 430, fontSize: 64, lineHeight: 1.25}}>
          初回に実測して<br /><span style={{color: C.accent}}>台帳へ</span>
        </Word>
        <Word at={k5} style={{left: 1120, top: 700, fontSize: 30, lineHeight: 1.5, whiteSpace: 'nowrap', color: C.sub, fontWeight: 700}}>
          20基の検証。<br />自信が低い看板は誤差 20〜25%
        </Word>
      </div>
    </Shell>
  );
};
