import narrationRaw from './narration.json';
import {FPS} from '../theme';

export type Narr = {scene: string; file: string; text: string; duration: number};
const NARR = narrationRaw as Narr[];

export const SCENE_IDS = ['01', '02', '03', '04', '05', '06', '07', '08', '09', '10', '11'];
export const NARR_OFFSET = 0.4; // 秒: 場面頭からナレーション開始
export const TAIL = 1.0; // 秒: 声の後ろの余白
export const MIN_SEC = 4;

export type TItem = {
  id: string;
  from: number;
  frames: number;
  sec: number;
  narr?: Narr;
  text: string;
  dur: number; // 声の秒
};

export function build(): TItem[] {
  let from = 0;
  return SCENE_IDS.map((id) => {
    const narr = NARR.find((n) => n.scene === id);
    const dur = narr ? narr.duration : 6;
    const sec = Math.max(MIN_SEC, dur + TAIL + (narr ? 0 : 0));
    const frames = Math.round(sec * FPS);
    const it: TItem = {id, from, frames, sec: frames / FPS, narr, text: narr?.text ?? '', dur};
    from += frames;
    return it;
  });
}
export const TL = build();
export const TOTAL = TL.reduce((a, t) => a + t.frames, 0);

// 台詞の中の語句が読まれる時刻(場面内の秒)。見つからなければ fb(0〜1)の位置
export function cue(t: TItem, phrase: string, fb: number, lead = 0.15): number {
  const i = t.text.indexOf(phrase);
  const frac = i >= 0 ? i / Math.max(1, t.text.length) : fb;
  return Math.max(0.2, NARR_OFFSET + t.dur * frac - lead);
}
