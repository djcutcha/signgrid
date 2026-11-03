import React from 'react';
import {Audio, Freeze, Img, OffthreadVideo, interpolate, staticFile, useCurrentFrame, useVideoConfig} from 'remotion';
import {C, FONT, FPS} from './theme';
import {CalloutDef, TimelineItem, NARR_OFFSET, splitCaptions} from './data/scenes';

// 日本語を語のまとまりで折り返す(語の途中で改行しない)
const seg = new Intl.Segmenter('ja', {granularity: 'word'});
export const J: React.FC<{children: string}> = ({children}) => {
  const parts: string[] = [];
  // 分割したくない語は 1 語として扱う
  const KEEP = ['国交省', '点検票', '用途地域', '看板カルテ', '80時間超'];
  const pieces = children.split(new RegExp(`(${KEEP.join('|')})`));
  for (const piece of pieces) {
    const segs = KEEP.includes(piece) ? [piece] : Array.from(seg.segment(piece), (x) => x.segment);
    for (const sg of segs) {
      const prev = parts[parts.length - 1];
      // 数字・英字の直後の語(2021|年, 80|時間)や英字どうし(D/C/B/A)は切らない
      if (prev !== undefined && !KEEP.includes(sg) && /[0-9A-Za-z%.,/+$]$/.test(prev) && /^[^\s、。()]/.test(sg)) parts[parts.length - 1] = prev + sg;
      else parts.push(sg);
    }
  }
  // 禁則: 行頭に来てはいけない文字は前の塊へ、末尾の1文字だけの塊も前へ寄せる
  const out: string[] = [];
  parts.forEach((t, i) => {
    const prev = out[out.length - 1];
    if (prev !== undefined && /[(（「『【]$/.test(prev)) out[out.length - 1] = prev + t;
    else if (prev !== undefined && (/^[、。，．,.:：;；)）」』】!?！？・]/.test(t) || (i === parts.length - 1 && t.length === 1))) out[out.length - 1] = prev + t;
    else out.push(t);
  });
  return <>{out.map((t, i) => <span key={i} style={{display: 'inline-block', whiteSpace: 'pre'}}>{t}</span>)}</>;
};

export const useSec = () => useCurrentFrame() / FPS;

export const fadeIn = (frame: number, start: number, len = 14) =>
  interpolate(frame, [start, start + len], [0, 1], {extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});

export const Bg: React.FC<{children?: React.ReactNode}> = ({children}) => (
  <div style={{position: 'absolute', inset: 0, background: C.bg, fontFamily: FONT, color: C.text}}>{children}</div>
);

export const Card: React.FC<{
  c: CalloutDef; start: number; style?: React.CSSProperties; active?: boolean;
}> = ({c, start, style, active}) => {
  const f = useCurrentFrame();
  const o = fadeIn(f, start);
  const dx = interpolate(o, [0, 1], [24, 0]);
  return (
    <div style={{
      opacity: o, transform: `translateX(${dx}px)`, background: C.panel, borderRadius: 12, padding: '22px 28px',
      borderLeft: `6px solid ${c.warn ? C.warn : C.accent}`, ...style,
      outline: active ? `3px solid ${C.accent}` : undefined,
    }}>
      {c.big && <div style={{fontSize: c.big.length > 10 ? 34 : c.big.length > 5 ? 48 : 64, fontWeight: 700, lineHeight: 1.15, color: c.warn ? '#f87171' : C.text, marginBottom: 8}}>{c.big}</div>}
      <div style={{fontSize: 28, lineHeight: 1.45, textWrap: 'balance'}}><J>{c.text}</J></div>
      {c.sub && <div style={{fontSize: 21, color: C.sub, marginTop: 8, lineHeight: 1.4}}><J>{c.sub}</J></div>}
    </div>
  );
};

export const CalloutColumn: React.FC<{
  items: CalloutDef[]; x?: number; y?: number; w?: number; first?: number; gap?: number; step?: number;
}> = ({items, x = 1400, y = 100, w = 440, first = 18, gap = 20, step = 24}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w, display: 'flex', flexDirection: 'column', gap}}>
    {items.map((c, i) => <Card key={i} c={c} start={first + i * step} />)}
  </div>
);

export const Caption: React.FC<{t: TimelineItem}> = ({t}) => {
  const f = useCurrentFrame();
  const sec = f / FPS;
  const caps = splitCaptions(t.text);
  const total = caps.reduce((a, s) => a + s.length, 0) || 1;
  let acc = 0;
  let cur = '';
  for (const s of caps) {
    const a = t.textStart + (acc / total) * t.textDur;
    const b = t.textStart + ((acc + s.length) / total) * t.textDur;
    if (sec >= a && sec < b) cur = s;
    acc += s.length;
  }
  if (!cur) return null;
  return (
    <div style={{position: 'absolute', left: 0, right: 0, bottom: 64, display: 'flex', justifyContent: 'center'}}>
      <div style={{
        maxWidth: 1640, background: 'rgba(0,0,0,0.62)', color: '#fff', borderRadius: 12, padding: '14px 32px',
        fontSize: 40, lineHeight: 1.4, textAlign: 'center', fontFamily: FONT, fontWeight: 500, textWrap: 'balance',
      }}><J>{cur}</J></div>
    </div>
  );
};

export const Placeholder: React.FC<{text?: string}> = ({text}) =>
  text ? <div style={{position: 'absolute', left: 40, bottom: 20, fontSize: 24, color: '#9ca3af', fontFamily: FONT}}>{text}</div> : null;

export const Narration: React.FC<{t: TimelineItem}> = ({t}) =>
  t.narr ? <Audio src={staticFile(`assets/narration/${t.narr.file}`)} startFrom={0} /> : null;

// 場面の頭から NARR_OFFSET 秒後に鳴らす
export const SceneShell: React.FC<{t: TimelineItem; children: React.ReactNode}> = ({t, children}) => (
  <Bg>
    {children}
    {t.narr && (
      <Sequenceless t={t} />
    )}
    <Caption t={t} />
    <Placeholder text={t.def.placeholder} />
  </Bg>
);

import {Sequence} from 'remotion';
const Sequenceless: React.FC<{t: TimelineItem}> = ({t}) => (
  <Sequence from={Math.round(NARR_OFFSET * FPS)} layout="none"><Narration t={t} /></Sequence>
);

// 録画: clipSec より後は最後のフレームで静止
export const Clip: React.FC<{src: string; clipSec: number; style?: React.CSSProperties; from?: number; fit?: 'contain' | 'cover'}> = ({src, clipSec, style, fit = 'contain'}) => {
  const f = useCurrentFrame();
  const last = Math.floor(clipSec * FPS) - 2;
  const v = <OffthreadVideo src={staticFile(src)} muted style={{width: '100%', height: '100%', objectFit: fit}} />;
  return <div style={{overflow: 'hidden', ...style}}>{f > last ? <Freeze frame={last}>{v}</Freeze> : v}</div>;
};

export const Frame: React.FC<{children: React.ReactNode; x: number; y: number; w: number; h: number; o?: number; label?: string}> = ({children, x, y, w, h, o = 1, label}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w, height: h, opacity: o}}>
    <div style={{width: '100%', height: '100%', borderRadius: 12, overflow: 'hidden', border: `2px solid ${C.line}`, background: '#000', boxShadow: '0 12px 40px rgba(0,0,0,.45)'}}>{children}</div>
    {label && <div style={{position: 'absolute', left: 12, top: 12, background: C.accent, color: '#fff', fontSize: 24, padding: '4px 14px', borderRadius: 8}}>{label}</div>}
  </div>
);

export const Phone: React.FC<{children: React.ReactNode; x: number; y: number; w: number; h: number}> = ({children, x, y, w, h}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w + 28, height: h + 28, background: '#000', borderRadius: 44, padding: 14, boxShadow: '0 12px 40px rgba(0,0,0,.55)', border: '2px solid #1f2937'}}>
    <div style={{width: w, height: h, borderRadius: 32, overflow: 'hidden', background: '#000'}}>{children}</div>
  </div>
);

export const Pic: React.FC<{src: string; style?: React.CSSProperties}> = ({src, style}) =>
  <Img src={staticFile(src)} style={{display: 'block', ...style}} />;

export const useSceneFrames = () => useVideoConfig().durationInFrames;
