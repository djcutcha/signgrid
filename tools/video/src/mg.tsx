import React from 'react';
import {interpolate, useCurrentFrame} from 'remotion';
import {C, FPS, H, W} from './theme';
import {clamp01, eo, eio, Fill, Odo, Word} from './lib';

// ---- タイミング ----
export const win = (s: number, a: number, b = Infinity, f = 0.25) => clamp01((s - a) / f) * clamp01((b - s) / f);
export const back = (p: number) => {
  const c1 = 1.70158, c3 = c1 + 1;
  const x = clamp01(p);
  return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2);
};
// 弾んで入る(0 → 1、少し行き過ぎて戻る)
export const pop = (s: number, a: number, d = 0.5) => (s < a ? 0 : back((s - a) / d));
export const useS = () => useCurrentFrame() / FPS;

export const COL = {
  bg: C.bg, panel: C.panel, line: C.line, text: C.text, sub: C.sub, blue: C.accent, red: C.warn,
  amber: '#f5a524', green: '#22c55e', yellow: '#facc15', ink: '#0b1220', paper: '#e8edf5', skin: '#f2c9a0',
};

export const Svg: React.FC<{children: React.ReactNode; style?: React.CSSProperties}> = ({children, style}) => (
  <svg width={W} height={H} viewBox={`0 0 ${W} ${H}`} style={{position: 'absolute', left: 0, top: 0, ...style}}>{children}</svg>
);

// ---- キネティック文字 ----
// 要点語: 弾んで入り、until で縮んで消える。(x,y)= 中心
export const KW: React.FC<{s: number; at: number; until?: number; x: number; y: number; size: number; color?: string; children: React.ReactNode; weight?: number; shake?: number}> = ({s, at, until, x, y, size, color = '#fff', children, weight = 800, shake = 0}) => {
  const p = pop(s, at, 0.45);
  const o = until === undefined ? 1 : 1 - clamp01((s - until) / 0.25);
  if (p <= 0 || o <= 0) return null;
  const sx = shake ? Math.sin(s * 60) * shake * clamp01(1 - (s - at) / 0.8) : 0;
  return (
    <div style={{position: 'absolute', left: x - 1200 + sx, width: 2400, top: y - size * 0.62, textAlign: 'center', fontSize: size, fontWeight: weight, color, lineHeight: 1.2, opacity: Math.min(1, p * 1.5) * o, transform: `scale(${0.6 + 0.4 * p})`, textShadow: '0 4px 28px rgba(0,0,0,.55)', whiteSpace: 'nowrap'}}>{children}</div>
  );
};

// ---- 背景 ----
export const Bg: React.FC<{hue?: string; grid?: boolean}> = ({hue = 'rgba(59,130,246,0.18)', grid = true}) => {
  const s = useS();
  return (
    <Fill>
      <Fill style={{background: `radial-gradient(1000px 700px at ${30 + 40 * Math.sin(s / 6)}% ${60 - 10 * Math.cos(s / 5)}%, ${hue}, rgba(11,18,32,0) 70%)`}} />
      {grid && <Fill style={{opacity: 0.22, backgroundImage: `linear-gradient(${C.line} 1px, transparent 1px), linear-gradient(90deg, ${C.line} 1px, transparent 1px)`, backgroundSize: '96px 96px', backgroundPosition: `${-s * 10}px ${-s * 5}px`}} />}
    </Fill>
  );
};

// ---- イラスト部品(SVG)。座標は呼び出し側で translate ----
export const Person: React.FC<{x: number; y: number; k?: number; walk?: number; color?: string; helmet?: boolean; q?: boolean}> = ({x, y, k = 1, walk = 0, color = '#9fb0c8', helmet}) => {
  const a = Math.sin(walk) * 22;
  return (
    <g transform={`translate(${x},${y}) scale(${k})`}>
      <line x1={0} y1={-62} x2={0} y2={-6} stroke={color} strokeWidth={14} strokeLinecap="round" transform={`rotate(${a} 0 -62)`} />
      <line x1={0} y1={-62} x2={0} y2={-6} stroke={color} strokeWidth={14} strokeLinecap="round" transform={`rotate(${-a} 0 -62)`} />
      <rect x={-20} y={-136} width={40} height={80} rx={16} fill={color} />
      <circle cx={0} cy={-160} r={19} fill={color} />
      {helmet && <path d="M-22 -164 a22 22 0 0 1 44 0 z" fill={COL.yellow} />}
    </g>
  );
};

export const SignBoard: React.FC<{x: number; y: number; w: number; h: number; rot?: number; color?: string; label?: string; pole?: number; stroke?: string; dashed?: boolean; fs?: number; op?: number}> = ({x, y, w, h, rot = 0, color = '#f8fafc', label, pole = 0, stroke, dashed, fs = 36, op = 1}) => (
  <g transform={`translate(${x},${y}) rotate(${rot})`} opacity={op}>
    {pole > 0 && <rect x={-6} y={h / 2} width={12} height={pole} fill="#64748b" />}
    <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={8} fill={dashed ? 'none' : color} stroke={stroke ?? '#475569'} strokeWidth={dashed ? 5 : 4} strokeDasharray={dashed ? '14 10' : undefined} />
    {label && <text x={0} y={fs * 0.35} textAnchor="middle" fontSize={fs} fontWeight={800} fill={dashed ? (stroke ?? '#94a3b8') : '#0b1220'} fontFamily="inherit">{label}</text>}
  </g>
);

// 高所作業車(バケットにヘルメットの作業員)
export const LiftWorker: React.FC<{x: number; y: number; k?: number; raise: number; s: number}> = ({x, y, k = 1, raise, s}) => {
  const px = 150, py = -70, L = 120 + 260 * raise, ang = (-20 - 45 * raise) * (Math.PI / 180);
  const bx = px + L * Math.cos(ang), by = py + L * Math.sin(ang) + Math.sin(s * 3) * 3 * raise;
  return (
    <g transform={`translate(${x},${y}) scale(${k})`}>
      <rect x={-120} y={-80} width={300} height={56} rx={10} fill="#e2e8f0" />
      <path d="M-120 -80 h80 l30 -40 h-110 z" fill="#cbd5e1" />
      <rect x={-98} y={-114} width={60} height={30} fill="#7dd3fc" />
      <rect x={30} y={-96} width={140} height={16} fill={COL.amber} />
      <circle cx={-70} cy={-22} r={30} fill="#1e293b" /><circle cx={-70} cy={-22} r={12} fill="#94a3b8" />
      <circle cx={120} cy={-22} r={30} fill="#1e293b" /><circle cx={120} cy={-22} r={12} fill="#94a3b8" />
      <line x1={px} y1={py} x2={bx} y2={by} stroke={COL.amber} strokeWidth={22} strokeLinecap="round" />
      <circle cx={px} cy={py} r={16} fill="#475569" />
      <g transform={`translate(${bx},${by})`}>
        <rect x={-46} y={-14} width={92} height={56} rx={6} fill="#64748b" stroke="#94a3b8" strokeWidth={4} />
        <rect x={-18} y={-58} width={36} height={52} rx={14} fill={COL.blue} />
        <circle cx={0} cy={-74} r={16} fill={COL.skin} />
        <path d="M-19 -77 a19 19 0 0 1 38 0 z" fill={COL.yellow} />
      </g>
    </g>
  );
};

export const Building: React.FC<{x: number; y: number; w: number; h: number; cols?: number; rows?: number; lit?: number; color?: string}> = ({x, y, w, h, cols = 6, rows = 4, lit = 0.5, color = '#1e2a44'}) => (
  <g transform={`translate(${x},${y})`}>
    <rect x={0} y={0} width={w} height={h} rx={6} fill={color} stroke="#334766" strokeWidth={3} />
    {Array.from({length: rows * cols}, (_, i) => {
      const c = i % cols, r = Math.floor(i / cols);
      const on = ((i * 7919) % 100) / 100 < lit;
      return <rect key={i} x={20 + c * ((w - 40) / cols)} y={24 + r * ((h - 70) / rows)} width={(w - 40) / cols - 14} height={(h - 70) / rows - 14} rx={3} fill={on ? '#fde68a' : '#2b3a5c'} />;
    })}
  </g>
);

export const Cylinder: React.FC<{x: number; y: number; w: number; h: number; fill?: number; color?: string}> = ({x, y, w, h, fill = 0.5, color = COL.blue}) => {
  const bh = h - 40, lv = bh * fill;
  return (
    <g transform={`translate(${x},${y})`}>
      <ellipse cx={w / 2} cy={20 + bh} rx={w / 2} ry={20} fill="#16223d" stroke={color} strokeWidth={5} />
      <rect x={0} y={20} width={w} height={bh} fill="#16223d" />
      <ellipse cx={w / 2} cy={20 + bh} rx={w / 2} ry={20} fill="#16223d" />
      <rect x={0} y={20 + bh - lv} width={w} height={lv} fill={color} opacity={0.5} />
      <ellipse cx={w / 2} cy={20 + bh} rx={w / 2} ry={20} fill={color} opacity={0.5} />
      <line x1={0} y1={20} x2={0} y2={20 + bh} stroke={color} strokeWidth={5} /><line x1={w} y1={20} x2={w} y2={20 + bh} stroke={color} strokeWidth={5} />
      <ellipse cx={w / 2} cy={20} rx={w / 2} ry={20} fill="#1b2a4d" stroke={color} strokeWidth={5} />
    </g>
  );
};

export const Clock: React.FC<{x: number; y: number; r: number; t: number; color?: string}> = ({x, y, r, t, color = COL.red}) => (
  <g transform={`translate(${x},${y})`}>
    <circle r={r} fill="#16223d" stroke={color} strokeWidth={8} />
    {Array.from({length: 12}, (_, i) => <line key={i} x1={0} y1={-r + 8} x2={0} y2={-r + 20} stroke="#9fb0c8" strokeWidth={4} transform={`rotate(${i * 30})`} />)}
    <line x1={0} y1={0} x2={0} y2={-r * 0.78} stroke="#fff" strokeWidth={7} strokeLinecap="round" transform={`rotate(${t * 360})`} />
    <line x1={0} y1={0} x2={0} y2={-r * 0.5} stroke={color} strokeWidth={9} strokeLinecap="round" transform={`rotate(${t * 30})`} />
    <circle r={9} fill="#fff" />
  </g>
);

export const Head: React.FC<{x: number; y: number; k?: number; glow?: number; color?: string}> = ({x, y, k = 1, glow = 0, color = '#9fb0c8'}) => (
  <g transform={`translate(${x},${y}) scale(${k})`}>
    {glow > 0 && <circle cx={0} cy={-90} r={150} fill={`rgba(250,204,21,${0.22 * glow})`} />}
    <path d="M-120 100 q0 -90 120 -100 q120 10 120 100 z" fill={color} />
    <circle cx={0} cy={-70} r={70} fill={color} />
  </g>
);

export const Book: React.FC<{x: number; y: number; k?: number; label?: string}> = ({x, y, k = 1, label = '台帳'}) => (
  <g transform={`translate(${x},${y}) scale(${k})`}>
    <rect x={-110} y={-80} width={220} height={160} rx={12} fill="#1d4ed8" stroke="#93c5fd" strokeWidth={5} />
    <rect x={-96} y={-66} width={22} height={132} fill="#1e3a8a" />
    <text x={20} y={14} textAnchor="middle" fontSize={50} fontWeight={800} fill="#fff" fontFamily="inherit">{label}</text>
  </g>
);

export const Gov: React.FC<{x: number; y: number; k?: number}> = ({x, y, k = 1}) => (
  <g transform={`translate(${x},${y}) scale(${k})`}>
    <path d="M-150 -60 L0 -130 L150 -60 z" fill="#334766" stroke="#93c5fd" strokeWidth={4} />
    {[-110, -55, 0, 55, 110].map((cx) => <rect key={cx} x={cx - 12} y={-50} width={24} height={110} fill="#cbd5e1" />)}
    <rect x={-160} y={60} width={320} height={22} fill="#93c5fd" />
    <rect x={-170} y={82} width={340} height={18} fill="#64748b" />
  </g>
);

export const PhoneSvg: React.FC<{x: number; y: number; w?: number; h?: number; children?: React.ReactNode}> = ({x, y, w = 260, h = 520, children}) => (
  <g transform={`translate(${x},${y})`}>
    <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={40} fill="#020617" stroke="#475569" strokeWidth={6} />
    <rect x={-w / 2 + 14} y={-h / 2 + 24} width={w - 28} height={h - 48} rx={22} fill="#0f1a33" />
    {children}
  </g>
);

export const Pill: React.FC<{x: number; y: number; w: number; h?: number; text: string; color?: string; lit?: number; size?: number}> = ({x, y, w, h = 84, text, color = COL.blue, lit = 1, size = 40}) => (
  <g transform={`translate(${x},${y})`}>
    <rect x={-w / 2} y={-h / 2} width={w} height={h} rx={h / 2} fill={lit > 0.5 ? color : '#16223d'} stroke={color} strokeWidth={4} opacity={0.35 + 0.65 * lit} />
    <text x={0} y={size * 0.35} textAnchor="middle" fontSize={size} fontWeight={800} fill="#fff" fontFamily="inherit">{text}</text>
  </g>
);

export const Arrow: React.FC<{x1: number; y1: number; x2: number; y2: number; p: number; color?: string; w?: number}> = ({x1, y1, x2, y2, p, color = COL.blue, w = 8}) => {
  const ex = x1 + (x2 - x1) * p, ey = y1 + (y2 - y1) * p;
  const a = Math.atan2(y2 - y1, x2 - x1);
  return (
    <g opacity={p > 0 ? 1 : 0}>
      <line x1={x1} y1={y1} x2={ex} y2={ey} stroke={color} strokeWidth={w} strokeLinecap="round" />
      {p > 0.95 && <path d="M0 0 l-30 -16 v32 z" fill={color} transform={`translate(${x2},${y2}) rotate(${(a * 180) / Math.PI})`} />}
    </g>
  );
};

export const Sprout: React.FC<{x: number; y: number; k?: number; p: number}> = ({x, y, k = 1, p}) => {
  const h = 260 * eo(clamp01(p / 0.7));
  const lf = eo(clamp01((p - 0.5) / 0.4));
  return (
    <g transform={`translate(${x},${y}) scale(${k})`}>
      <ellipse cx={0} cy={6} rx={120} ry={22} fill="#3b2f23" />
      <path d={`M0 0 q-14 ${-h / 2} 6 ${-h}`} stroke="#22c55e" strokeWidth={14} fill="none" strokeLinecap="round" />
      <g transform={`translate(6,${-h})`}>
        <path d={`M0 0 q${-100 * lf} ${-20 * lf} ${-110 * lf} ${-90 * lf} q${90 * lf} ${10 * lf} ${110 * lf} ${90 * lf}`} fill="#4ade80" />
        <path d={`M0 0 q${100 * lf} ${-20 * lf} ${110 * lf} ${-90 * lf} q${-90 * lf} ${10 * lf} ${-110 * lf} ${90 * lf}`} fill="#22c55e" />
      </g>
    </g>
  );
};

export const MapBg: React.FC<{x: number; y: number; w: number; h: number}> = ({x, y, w, h}) => (
  <g transform={`translate(${x},${y})`}>
    <rect width={w} height={h} rx={20} fill="#12203a" stroke="#334766" strokeWidth={4} />
    {[0.2, 0.45, 0.7, 0.9].map((r, i) => <line key={i} x1={0} y1={h * r} x2={w} y2={h * (r + (i % 2 ? -0.08 : 0.1))} stroke="#2a3d63" strokeWidth={i % 2 ? 14 : 22} />)}
    {[0.15, 0.4, 0.62, 0.85].map((r, i) => <line key={i} x1={w * r} y1={0} x2={w * (r + (i % 2 ? 0.08 : -0.06))} y2={h} stroke="#2a3d63" strokeWidth={i % 2 ? 22 : 12} />)}
    <path d={`M0 ${h * 0.75} C ${w * 0.3} ${h * 0.6}, ${w * 0.5} ${h * 0.9}, ${w} ${h * 0.55}`} stroke="#1d4ed8" strokeWidth={18} fill="none" opacity={0.5} />
  </g>
);

export const Pin: React.FC<{x: number; y: number; color?: string; k?: number}> = ({x, y, color = COL.red, k = 1}) => (
  <g transform={`translate(${x},${y}) scale(${k})`}>
    <path d="M0 0 C-34 -42 -34 -86 0 -86 C34 -86 34 -42 0 0 z" fill={color} stroke="#fff" strokeWidth={4} />
    <circle cx={0} cy={-58} r={11} fill="#fff" />
  </g>
);

export const Check: React.FC<{x: number; y: number; p: number; color?: string; k?: number}> = ({x, y, p, color = COL.green, k = 1}) => (
  <g transform={`translate(${x},${y}) scale(${k})`}>
    <rect x={-22} y={-22} width={44} height={44} rx={8} fill="none" stroke="#64748b" strokeWidth={4} />
    <path d="M-12 0 l9 11 l17 -22" stroke={color} strokeWidth={8} fill="none" strokeLinecap="round" strokeLinejoin="round" strokeDasharray={60} strokeDashoffset={60 * (1 - clamp01(p))} />
  </g>
);

export const Sheet: React.FC<{x: number; y: number; rot?: number; label: string; color: string; k?: number}> = ({x, y, rot = 0, label, color, k = 1}) => (
  <g transform={`translate(${x},${y}) rotate(${rot}) scale(${k})`}>
    <rect x={-90} y={-120} width={180} height={240} rx={8} fill="#f8fafc" stroke="#94a3b8" strokeWidth={3} />
    <rect x={-90} y={-120} width={180} height={44} rx={8} fill={color} />
    <text x={0} y={-90} textAnchor="middle" fontSize={26} fontWeight={800} fill="#fff" fontFamily="inherit">{label}</text>
    {[-50, -20, 10, 40, 70].map((yy) => <line key={yy} x1={-68} y1={yy} x2={68} y2={yy} stroke="#cbd5e1" strokeWidth={5} />)}
  </g>
);

export const Spark: React.FC<{x: number; y: number; r: number; p: number; color?: string}> = ({x, y, r, p, color = '#facc15'}) => (
  <g transform={`translate(${x},${y}) rotate(${p * 90}) scale(${p})`} opacity={1 - clamp01((p - 0.7) / 0.3) * 0.0}>
    <path d={`M0 ${-r} L${r * 0.18} ${-r * 0.18} L${r} 0 L${r * 0.18} ${r * 0.18} L0 ${r} L${-r * 0.18} ${r * 0.18} L${-r} 0 L${-r * 0.18} ${-r * 0.18} z`} fill={color} />
  </g>
);

export {interpolate, eo, eio, Odo, Word, clamp01, Fill};
