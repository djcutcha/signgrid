import React from 'react';
import {Audio, Composition, Sequence, staticFile} from 'remotion';
import {TL, TOTAL, cue, NARR_OFFSET} from './data/timeline';
import {FPS} from './theme';
import {SCENES} from './scenes';
import {hitSec} from './scenes/S04';
import {clamp01} from './lib';

const VOICE = TL.filter((t) => t.narr).map((t) => [t.from + Math.round(NARR_OFFSET * FPS), t.from + Math.round((NARR_OFFSET + t.dur) * FPS)] as const);
const t4 = TL.find((t) => t.id === '04')!;
const t11 = TL.find((t) => t.id === '11')!;
const hitAt = (t: typeof t4) => t.from + Math.round(hitSec(t) * FPS);
// BGM を一瞬上げる所: 冒頭と、ロゴの出る場面 04 と 11
const HITS = [[0, 40], [hitAt(t4) - 3, hitAt(t4) + 36], [hitAt(t11) - 3, hitAt(t11) + 36]] as const;
export const G_GAP = 0.316; // -10dB
export const G_VOICE = 0.158; // -16dB
export const G_HIT = 0.8;

// ダッキング: 声の区間で BGM を下げる。冒頭と場面04・11のロゴで一瞬上げる。末尾 2 秒でフェード
export const bgmGain = (f: number): number => {
  let v = 0;
  for (const [a, b] of VOICE) {
    const d = f < a ? a - f : f > b ? f - b : 0;
    v = Math.max(v, clamp01(1 - d / 8));
  }
  let g = G_GAP + (G_VOICE - G_GAP) * v;
  for (const [a, b] of HITS) {
    const up = clamp01((f - a) / 4), down = clamp01(1 - (f - (a + 14)) / (b - a - 14));
    g = Math.max(g, G_HIT * Math.min(up, f < a + 14 ? 1 : down));
  }
  const fade = clamp01((TOTAL - f) / (2 * FPS));
  return g * fade;
};

const Submission: React.FC = () => (
  <>
    <Audio src={staticFile('assets/music/bgm_v2.wav')} volume={bgmGain} loop />
    {TL.map((t) => {
      const S = SCENES[t.id];
      return (
        <Sequence key={t.id} from={t.from} durationInFrames={t.frames} name={`${t.id}`}>
          <S t={t} />
        </Sequence>
      );
    })}
  </>
);

export const Root: React.FC = () => (
  <Composition id="Submission" component={Submission} durationInFrames={TOTAL} fps={FPS} width={1920} height={1080} />
);
