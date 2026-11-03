import React from 'react';
import {Audio, Easing, Freeze, Img, OffthreadVideo, Sequence, interpolate, staticFile, useCurrentFrame} from 'remotion';
import {C, FONT, FPS, H, W} from './theme';
import {NARR_OFFSET, TItem} from './data/timeline';

export const clamp01 = (x: number) => Math.min(1, Math.max(0, x));
export const eo = Easing.out(Easing.cubic);
export const eio = Easing.inOut(Easing.cubic);
export const useSec = () => useCurrentFrame() / FPS;

export const Fill: React.FC<{children?: React.ReactNode; style?: React.CSSProperties}> = ({children, style}) => (
  <div style={{position: 'absolute', left: 0, top: 0, width: W, height: H, ...style}}>{children}</div>
);

// 場面: 前後 6 フレームの黒、ナレーション
export const Shell: React.FC<{t: TItem; children: React.ReactNode; bg?: string; fadeIn?: boolean}> = ({t, children, bg = C.bg, fadeIn = true}) => {
  const f = useCurrentFrame();
  const o = Math.min(fadeIn ? clamp01(f / 6) : 1, clamp01((t.frames - f) / 6));
  return (
    <Fill style={{background: '#000', fontFamily: FONT, color: C.text}}>
      <Fill style={{opacity: o, background: bg}}>{children}</Fill>
      {t.narr && (
        <Sequence from={Math.round(NARR_OFFSET * FPS)} layout="none">
          <Audio src={staticFile(`assets/${(t.narr as any).dir ?? 'narration'}/${t.narr.file}`)} />
        </Sequence>
      )}
    </Fill>
  );
};

// 要点語: 入り(下から 30px+フェード+わずかな拡大) と抜け
export const Word: React.FC<{at: number; until?: number; style?: React.CSSProperties; children: React.ReactNode}> = ({at, until, style, children}) => {
  const f = useCurrentFrame();
  const a = f / FPS - at;
  if (a < 0) return null;
  const inn = eo(clamp01(a / 0.4));
  const out = until === undefined ? 0 : clamp01((f / FPS - until) / 0.25);
  if (out >= 1) return null;
  return (
    <div style={{position: 'absolute', opacity: inn * (1 - out), transform: `translateY(${(1 - inn) * 30 - out * 14}px) scale(${0.96 + 0.04 * inn})`, textShadow: '0 4px 28px rgba(0,0,0,.55)', fontWeight: 800, ...style}}>
      {children}
    </div>
  );
};

// 文字ごとに時間差で下から入る見出し
export const Chars: React.FC<{text: string; at: number; step?: number; size: number; color?: string; weight?: number}> = ({text, at, step = 0.06, size, color = C.text, weight = 800}) => {
  const f = useCurrentFrame();
  return (
    <span style={{display: 'inline-flex', fontSize: size, fontWeight: weight, color, lineHeight: 1.2}}>
      {Array.from(text).map((ch, i) => {
        const p = eo(clamp01((f / FPS - at - i * step) / 0.45));
        return <span key={i} style={{display: 'inline-block', opacity: p, transform: `translateY(${(1 - p) * 40}px)`, whiteSpace: 'pre'}}>{ch}</span>;
      })}
    </span>
  );
};

// 桁ロール(オドメーター)。数字は 0 から目的の数字まで回り、それ以外はそのまま
export const Odo: React.FC<{value: string; at: number; dur?: number; size: number; color?: string}> = ({value, at, dur = 0.8, size, color = C.text}) => {
  const f = useCurrentFrame();
  let di = 0;
  return (
    <span style={{display: 'inline-flex', alignItems: 'flex-start', fontSize: size, lineHeight: 1, height: '1.12em', color, fontWeight: 800}}>
      {Array.from(value).map((ch, i) => {
        if (/\d/.test(ch)) {
          const d = +ch;
          const k = di++;
          const p = eo(clamp01((f / FPS - at - k * 0.06) / dur));
          return (
            <span key={i} style={{display: 'inline-block', height: '1.12em', width: '0.64em', overflow: 'hidden'}}>
              <span style={{display: 'block', transform: `translateY(${-(p * (10 + d)) * 1.12}em)`}}>
                {Array.from({length: 20}, (_, n) => <span key={n} style={{display: 'block', height: '1.12em', lineHeight: '1.12em', textAlign: 'center'}}>{n % 10}</span>)}
              </span>
            </span>
          );
        }
        const p = clamp01((f / FPS - at) / 0.3);
        return <span key={i} style={{display: 'inline-block', height: '1.12em', lineHeight: '1.12em', opacity: p}}>{ch}</span>;
      })}
    </span>
  );
};

// 写真: ゆっくり寄り+横パン、下から上の黒グラデ
export const Photo: React.FC<{src: string; frames: number; z0?: number; z1?: number; pan?: number; ox?: number; oy?: number; opacity?: number; grad?: number}> = ({src, frames, z0 = 1.0, z1 = 1.08, pan = 0, ox = 50, oy = 50, opacity = 1, grad = 0.85}) => {
  const f = useCurrentFrame();
  const p = clamp01(f / Math.max(1, frames));
  const s = z0 + (z1 - z0) * p;
  return (
    <Fill style={{opacity, overflow: 'hidden', background: '#000'}}>
      <Img src={staticFile(src)} style={{width: W, height: H, objectFit: 'cover', transformOrigin: `${ox}% ${oy}%`, transform: `translateX(${pan * (p - 0.5)}px) scale(${s})`}} />
      <Fill style={{background: `linear-gradient(to top, rgba(5,10,20,${grad}) 0%, rgba(5,10,20,0.25) 45%, rgba(5,10,20,0.12) 100%)`}} />
    </Fill>
  );
};

export type Key = {u: number; cx: number; cy: number; s: number; ring?: [number, number]};
// 録画を拡大して要所へ寄せる。u= 録画の進み(0〜1)。キー間は ease
export const ZoomVideo: React.FC<{src: string; clipSec: number; rate: number; keys: Key[]; w?: number; h?: number; vw?: number; vh?: number; start?: number}> = ({src, clipSec, rate, keys, w = W, h = H, vw = W, vh = H, start = 0}) => {
  const f = useCurrentFrame();
  const tf = Math.max(0, f - start);
  const u = clamp01((tf / FPS) * rate / clipSec);
  const us = keys.map((k) => k.u);
  const ip = (arr: number[]) => interpolate(u, us, arr, {easing: eio, extrapolateLeft: 'clamp', extrapolateRight: 'clamp'});
  const s = ip(keys.map((k) => k.s));
  const cx = ip(keys.map((k) => k.cx)) * vw;
  const cy = ip(keys.map((k) => k.cy)) * vh;
  const tx = Math.min(0, Math.max(w - vw * s, w / 2 - cx * s));
  const ty = Math.min(0, Math.max(h - vh * s, h / 2 - cy * s));
  const lastFrame = Math.max(1, Math.floor((clipSec - 0.15) * FPS / rate));
  const vid = <OffthreadVideo src={staticFile(src)} muted playbackRate={rate} style={{width: vw, height: vh, display: 'block'}} />;
  return (
    <div style={{position: 'absolute', left: 0, top: 0, width: w, height: h, overflow: 'hidden'}}>
      <div style={{position: 'absolute', left: 0, top: 0, width: vw, height: vh, transformOrigin: '0 0', transform: `translate(${tx}px, ${ty}px) scale(${s})`}}>
        {tf > lastFrame ? <Freeze frame={lastFrame + start}>{vid}</Freeze> : vid}
        {keys.map((k, i) => {
          if (!k.ring) return null;
          const arrive = start + (k.u * clipSec / rate) * FPS;
          const a = (f - arrive) / 12;
          if (a < 0 || a > 1) return null;
          const r = 18 + 70 * eo(a);
          return <div key={i} style={{position: 'absolute', left: k.ring[0] * vw - r, top: k.ring[1] * vh - r, width: r * 2, height: r * 2, borderRadius: '50%', background: `rgba(59,130,246,${0.45 * (1 - a)})`, border: `3px solid rgba(59,130,246,${0.9 * (1 - a)})`}} />;
        })}
      </div>
    </div>
  );
};

export const Phone: React.FC<{x: number; y: number; w: number; h: number; children: React.ReactNode}> = ({x, y, w, h, children}) => (
  <div style={{position: 'absolute', left: x, top: y, width: w + 32, height: h + 32, background: '#000', borderRadius: 54, padding: 16, boxSizing: 'border-box', boxShadow: '0 30px 80px rgba(0,0,0,.6)', border: '2px solid #1f2937'}}>
    <div style={{width: w, height: h, borderRadius: 40, overflow: 'hidden', background: '#000', position: 'relative'}}>{children}</div>
  </div>
);

// ゆっくり動く背景(止まらない画面用)
export const Drift: React.FC<{frames: number; hue?: string}> = ({frames, hue = 'rgba(59,130,246,0.22)'}) => {
  const f = useCurrentFrame();
  const p = f / Math.max(1, frames);
  return (
    <Fill style={{overflow: 'hidden'}}>
      <Fill style={{background: `radial-gradient(900px 700px at ${25 + 50 * p}% ${70 - 30 * p}%, ${hue}, rgba(11,18,32,0) 70%)`}} />
      <Fill style={{opacity: 0.35, backgroundImage: `linear-gradient(${C.line} 1px, transparent 1px), linear-gradient(90deg, ${C.line} 1px, transparent 1px)`, backgroundSize: '96px 96px', backgroundPosition: `${-p * 96}px ${-p * 48}px`}} />
    </Fill>
  );
};
