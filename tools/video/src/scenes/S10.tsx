import React from 'react';
import {Img, staticFile} from 'remotion';
import {TItem, cue} from '../data/timeline';
import {C} from '../theme';
import {Chars, Fill, Photo, Shell, Word, clamp01, eo, useSec} from '../lib';

// 現場の手元(生成写真。実写があれば差し替え)→ 公開リポジトリの画面(repo_readme.png)
export const S10: React.FC<{t: TItem}> = ({t}) => {
  const s = useSec();
  const sw = cue(t, '写真の判定', 0.7, 0.2);
  const o2 = eo(clamp01((s - sw) / 0.4));
  const pan = -400 * eo(clamp01((s - sw - 0.5) / 5));
  return (
    <Shell t={t}>
      <Photo src="assets/images/08_office_hands.png" frames={Math.round(sw * 30)} z0={1.0} z1={1.14} ox={40} oy={65} pan={-70} grad={0.9} opacity={1 - o2} />
      <div style={{position: 'absolute', left: 110, top: 540, opacity: 1 - o2}}>
        <Chars text="11月5日、" at={cue(t, '11月5日', 0.0, 0.1)} size={150} step={0.07} />
      </div>
      <div style={{position: 'absolute', left: 110, top: 710, opacity: 1 - o2}}>
        <Chars text="自分たちの現場で使い始めた" at={cue(t, '私たちの会社', 0.4, 0.1)} size={92} step={0.05} color="#7fb0ff" />
      </div>
      <Fill style={{opacity: o2, overflow: 'hidden', background: '#fff'}}>
        <Img src={staticFile('assets/images/repo_readme.png')} style={{width: 1920, height: 1500, transform: `translateY(${pan}px)`}} />
      </Fill>
      <Word at={sw + 0.3} style={{left: 0, width: 1920, top: 70, textAlign: 'center'}}>
        <span style={{display: 'inline-block', background: 'rgba(11,18,32,.92)', padding: '16px 56px', borderRadius: 16, fontSize: 80}}>コードは<span style={{color: C.accent}}>公開リポジトリ</span>に</span>
      </Word>
    </Shell>
  );
};
