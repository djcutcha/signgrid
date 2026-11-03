import narrationRaw from './narration.json';
import {FPS} from '../theme';

export type CalloutDef = {big?: string; text: string; sub?: string; warn?: boolean};
export type SceneDef = {
  id: string;
  title: string;
  slot: number; // 台本の枠(秒)
  callouts: CalloutDef[];
  placeholder?: string;
  fallbackText?: string; // ナレーションが index.json に無い時の字幕
};

export const SCENES: SceneDef[] = [
  {id: '01', title: '社会課題+本人', slot: 16, placeholder: '[実写: 街の看板と本人(11-06 撮影)]',
    callouts: [
      {big: '89件中73件', text: '事故報告のうち 自家用43+無許可30', warn: true},
      {text: '国交省報告分 2021年4月〜2024年12月(一部火災を含む)', sub: '出典: 国土交通省 報告'},
    ]},
  {id: '02', title: '現場の実話', slot: 20, placeholder: '[実写: はしご・手書き点検票(11-06 撮影)]',
    callouts: [
      {text: '客先の1店舗: 看板66基・写真773枚', sub: '2026-07'},
      {text: '別の客先 45店舗・1ラウンド: 事務作業 80時間超'},
    ]},
  {id: '03', title: 'さらに重なる負担', slot: 14,
    callouts: [
      {text: '報告書は客先ごとに違う'},
      {text: '規制は自治体ごとに違う', sub: '4自治体で規則が違う'},
    ]},
  {id: '04', title: 'なぜ難しいか', slot: 15,
    callouts: [
      {text: '現場の記録 + 看板の寸法 + 場所の規制'},
      {text: '4自治体の条例原文から抽出した規則 / 国交省の用途地域データ', sub: '規則と地図データの出典'},
    ]},
  {id: '05', title: '一手で始まるデモ', slot: 20,
    callouts: [
      {big: '約9分', text: '一周(人の確認を除く)'},
      {text: '写真1,398枚・点検票140頁・看板69基'},
      {text: 'API $7.44', sub: '登録5.4秒 / 転記247秒 / 下書き276秒 / 様式9秒'},
    ]},
  {id: '06a', title: 'Claude の働き', slot: 5,
    fallbackText: 'Claudeが担うのは、写真と看板の対応付け、手書きの読み取り、帳票の凡例の解釈、そして規制との突き合わせです。',
    callouts: [{big: '95.1%', text: '転記の照合(D/C/B/A の並び)', sub: '頁の最悪値 15/15'}]},
  {id: '06b', title: '受け継ぐ一周', slot: 20,
    callouts: [
      {text: '訂正'}, {text: '聞き返し'}, {text: '規則候補'}, {text: '矛盾'}, {text: '承認'}, {text: '次に効く'},
    ]},
  {id: '07', title: '検証と改善', slot: 18,
    callouts: [
      {big: '5/9', text: '要対応の見逃し'},
      {big: '1/38', text: '誤警報'},
      {big: '70.2%', text: '完全一致', sub: '33/47'},
      {big: '16.7% / 17.1%', text: '寸法の相対誤差 中央値', sub: '20基'},
      {big: '76.7% / 4.9%', text: '全量829基: Opus 見逃し / 誤警報'},
    ]},
  {id: '08', title: '使う人の実声', slot: 17, placeholder: '[実声: 職人・事務(11-04 収録)]',
    callouts: [
      {text: '人の確認込みの時間', sub: '[未測: 11-04 の試用で計る]'},
      {text: '従来: 事務 80時間超 / 45店舗'},
    ]},
  {id: '09', title: '行政側への反転', slot: 20,
    callouts: [
      {big: '写真10枚 → 候補6基', text: '見本'},
      {text: '許可台帳は施工台帳で代用(見本)'},
      {text: '役所システム連携は将来展開'},
      {text: '見つからないことを違法とは断定しない。職員が確認する候補を絞る', warn: true},
    ]},
  {id: '10', title: '社会への効果', slot: 15, placeholder: '[実写: 本人+街(11-06 撮影)]',
    fallbackText: '現場と行政、その両側から、看板事故を減らしたい。',
    callouts: [{text: '作業削減 → 事故低減は仮説', sub: '検証はこれから'}]},
];

type Narr = {scene: string; file: string; text: string; duration: number};
const NARR = narrationRaw as Narr[];

export const NARR_OFFSET = 0.3; // 秒
export const TAIL = 0.8; // 秒

export type TimelineItem = {
  def: SceneDef;
  from: number; // frame
  frames: number;
  narr?: Narr;
  text: string;
  textStart: number; // 秒(場面内)
  textDur: number; // 秒
};

export function buildTimeline(): TimelineItem[] {
  let from = 0;
  return SCENES.map((def) => {
    const narr = NARR.find((n) => n.scene === def.id);
    const sec = Math.max(def.slot, narr ? narr.duration + TAIL : 0);
    const frames = Math.round(sec * FPS);
    const item: TimelineItem = {
      def, from, frames, narr,
      text: narr ? narr.text : def.fallbackText ?? '',
      textStart: narr ? NARR_OFFSET : 0.5,
      textDur: narr ? narr.duration : Math.max(1, def.slot - 1.5),
    };
    from += frames;
    return item;
  });
}

export const TIMELINE = buildTimeline();
export const TOTAL_FRAMES = TIMELINE.reduce((a, t) => a + t.frames, 0);

// 句点で文に分け、長い文は読点で最大 MAXC 文字にまとめて字幕にする
const MAXC = 56;
export function splitCaptions(text: string): string[] {
  const sentences = text.split(/(?<=。)/).map((s) => s.trim()).filter(Boolean);
  const out: string[] = [];
  for (const s of sentences) {
    if (s.length <= MAXC) { out.push(s); continue; }
    const parts = s.split(/(?<=、)/);
    let cur = '';
    for (const p of parts) {
      if ((cur + p).length > MAXC && cur) { out.push(cur); cur = p; } else cur += p;
    }
    if (cur) out.push(cur);
  }
  return out;
}
