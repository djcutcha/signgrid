import React from 'react';
import {Img, staticFile} from 'remotion';
import {TItem, cue} from '../data/timeline';
import {C, FPS} from '../theme';
import {Fill, Key, Odo, Phone, Shell, Word, ZoomVideo, clamp01, eo, useSec} from '../lib';

// 前半: スマホ枠で LINE のトーク画面 9 コマを連番で送る。後半: 01_demo-start_pc の後半(カルテ → 客先の様式が埋まる)
const LINE = Array.from({length: 9}, (_, i) => `assets/line_v3/line_${String(i + 1).padStart(2, '0')}.png`);
const LINE_PX: [number, number][] = [[546, 804], [548, 568], [548, 734], [548, 904], [546, 776], [546, 686], [548, 688], [548, 1216], [548, 310]];
const SKIP = 6, CLIP = 14.7;
const KEYS: Key[] = [
  {u: 0, cx: 0.45, cy: 0.5, s: 1.45},
  {u: 0.38, cx: 0.45, cy: 0.5, s: 1.45},
  {u: 0.5, cx: 0.67, cy: 0.25, s: 2.4},
  {u: 1, cx: 0.67, cy: 0.25, s: 2.4},
];
export const S06: React.FC<{t: TItem}> = ({t}) => {
  const s = useSec();
  const sw = cue(t, '看板ごとのカルテ', 0.3, 0.2);
  const dt = (sw - 0.5) / 9;
  const idx = Math.min(8, Math.max(0, Math.floor((s - 0.5) / dt)));
  const o2 = eo(clamp01((s - sw) / 0.4));
  const k9 = cue(t, '約9分', 0.9, 0.4), kd = cue(t, '裏では', 0.5, 0.2);
  const PH = 840, PW = 470;
  const [iw, ih] = LINE_PX[idx];
  const sc = Math.min(PW / iw, PH / ih);
  const rate = CLIP / (t.sec - sw - 0.9);
  return (
    <Shell t={t}>
      <Fill style={{background: 'radial-gradient(900px 800px at 25% 50%, rgba(59,130,246,.2), rgba(11,18,32,0) 70%)'}} />
      <div style={{opacity: 1 - o2}}>
        <Phone x={330} y={30} w={PW} h={PH}>
          <div style={{width: PW, height: PH, background: '#8fa8c8'}}>
            <Img src={staticFile(LINE[idx])} style={{width: iw * sc, height: ih * sc, display: 'block', margin: '0 auto'}} />
          </div>
        </Phone>
        <Word at={0.5} style={{left: 960, top: 230, fontSize: 190, lineHeight: 1.1}}><span style={{color: '#06c755'}}>LINE</span>で</Word>
        <Word at={0.9} style={{left: 960, top: 500, fontSize: 100, lineHeight: 1.2}}>全景・正面<br />部位・寸法</Word>
        <Word at={dt * 5 + 0.5} style={{left: 960, top: 800, fontSize: 56, color: C.sub}}>送るだけ</Word>
      </div>
      <div style={{opacity: o2}}>
        <div style={{position: 'absolute', left: 60, top: 130, width: 1120, height: 630, borderRadius: 16, overflow: 'hidden', boxShadow: '0 30px 80px rgba(0,0,0,.6)', background: '#fff'}}>
          <ZoomVideo src="assets/01_demo-start_pc.mp4" clipSec={CLIP} rate={rate} keys={KEYS} w={1120} h={630} vw={1120} vh={630} start={Math.round(sw * FPS)} skip={SKIP} />
        </div>
      </div>
      <Word at={k9} style={{right: 60, top: 70, textAlign: 'right', background: 'rgba(11,18,32,.9)', padding: '22px 42px', borderRadius: 16}}>
        <div style={{fontSize: 34, color: C.sub, fontWeight: 700}}>1店舗の一周(人の確認を除く)</div>
        <div style={{fontSize: 150, lineHeight: 1.1, display: 'flex', justifyContent: 'flex-end'}}><span style={{fontSize: 80, alignSelf: 'flex-end', marginBottom: 14}}>約</span><Odo value="9" at={k9} size={150} /><span style={{fontSize: 80, alignSelf: 'flex-end', marginBottom: 14}}>分</span></div>
      </Word>
      <Word at={k9 + 0.5} style={{right: 60, top: 340, textAlign: 'right', background: 'rgba(11,18,32,.9)', padding: '18px 42px', borderRadius: 16}}>
        <div style={{fontSize: 34, color: C.sub, fontWeight: 700}}>API の費用</div>
        <div style={{fontSize: 120, lineHeight: 1.1, color: C.accent, display: 'flex', justifyContent: 'flex-end'}}><span>$</span><Odo value="7.44" at={k9 + 0.5} size={120} color={C.accent} /></div>
      </Word>
      <Word at={kd} style={{right: 60, top: 600, textAlign: 'right', fontSize: 50, lineHeight: 1.3, background: 'rgba(11,18,32,.9)', padding: '16px 36px', borderRadius: 16}}>
        裏では <span style={{color: C.accent}}>Claude</span> が<br />対応付け・読み取り・照合
      </Word>
    </Shell>
  );
};
