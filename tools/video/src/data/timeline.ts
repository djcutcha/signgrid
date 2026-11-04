import narrationRaw from './narration.json';
import {FPS} from '../theme';

export type Narr = {scene: string; file: string; text: string; duration: number};
const NARR = narrationRaw as Narr[];

export const SCENE_IDS = ['01', '02', '03', '04', '04b', '05', '06', '07', '08', '09', '10'];
export const NARR_OFFSET = 0.4; // 秒: 場面頭からナレーション開始
export const TAIL = 0.3; // 秒: 声の後ろの余白(既定)。合計が MAX_TOTAL を超えるときは 0.4 まで縮める
export const MAX_TOTAL = 179.5;
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
  const durs = SCENE_IDS.map((id) => NARR.find((n) => n.scene === id)?.duration ?? 6);
  const need = durs.reduce((a, d) => a + NARR_OFFSET + d, 0);
  const tail = Math.min(TAIL, Math.max(0.2, (MAX_TOTAL - need) / SCENE_IDS.length));
  return SCENE_IDS.map((id) => {
    const narr = NARR.find((n) => n.scene === id);
    const dur = narr ? narr.duration : 6;
    const sec = Math.max(MIN_SEC, dur + NARR_OFFSET + tail);
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
