import React from 'react';
import {useCurrentFrame} from 'remotion';
import {TItem, cue} from '../data/timeline';
import {C, FPS} from '../theme';
import {Drift, Shell, Word, clamp01, eo} from '../lib';

const CARDS = [
  {k: '写真と看板の対応付け', t: '写真と看板の対応付け', s: '索引 69基'},
  {k: '手書きの読み取り', t: '手書きの読み取り', s: '転記 140頁'},
  {k: '凡例の解釈', t: '凡例の解釈', s: 'D=◎ … A=× と a/b/c'},
  {k: '規制との突き合わせ', t: '規制との突き合わせ', s: '4自治体の規則'},
];
export const S06a: React.FC<{t: TItem}> = ({t}) => {
  const f = useCurrentFrame();
  const fl = Math.sin(f / 22) * 5;
  return (
    <Shell t={t}>
      <Drift frames={t.frames} />
      <Word at={0.4} style={{left: 0, width: 1920, top: 130, textAlign: 'center', fontSize: 72}}>裏で Claude がやっているのは<span style={{color: C.accent}}>4つ</span></Word>
      {CARDS.map((c, i) => {
        const at = 0.9 + i * 0.25;
        const p = eo(clamp01((f / FPS - at) / 0.3));
        return (
          <div key={i} style={{position: 'absolute', left: 110 + i * 440, top: 340 + (1 - p) * 24 + (p > 0.99 ? fl * (i % 2 ? 1 : -1) : 0), width: 400, height: 460, opacity: p, background: C.panel, borderRadius: 16, borderTop: `6px solid ${C.accent}`, padding: '36px 32px', boxSizing: 'border-box'}}>
            <div style={{fontSize: 40, color: C.accent, fontWeight: 800}}>0{i + 1}</div>
            <div style={{fontSize: 56, fontWeight: 800, lineHeight: 1.3, marginTop: 24}}>{c.t}</div>
            <div style={{fontSize: 30, color: C.sub, marginTop: 28, fontWeight: 600}}>{c.s}</div>
          </div>
        );
      })}
    </Shell>
  );
};
