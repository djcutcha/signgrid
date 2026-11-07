"""有資格者のアノテーション画面(/expert/)。写真を見て、危なさの直感・根拠・気になる点・写真では分からない点を言葉にする。

  python3 -m kanban_karte.web.expert export [--out DIR] [--db PATH]   CSV と JSONL を書き出す
  python3 -m kanban_karte.web.expert stats  [--db PATH]               件数だけ見る
  python3 -m kanban_karte.web.expert sources                          写真の出どころの件数(基・枚)
  python3 -m kanban_karte.web.expert nas-meta [--out PATH]            点検記録(nas)の key → 記録済みランク・現場の表(TSV)

この言葉が安全判定の正解データになり、後で構造化して規則にする。台帳 DB は触らない。
保存先は data/private/expert_notes.sqlite(環境変数 KK_EXPERT_DB)。写真は読み取り専用で、出どころは 3 つ:
  eval   docs/EVAL.md の評価セット(有資格者ランク付き。--limit 34 --seed 1 の 3 ソース= 102 基)。正解ランクは画面に出さない
  field  data/private/field_intake/photos/ の実データ(1 フォルダ= 1 基)と pages/(報告書ページ。1 頁= 1 件)
  nas    fkogei-estimate-data/eval/nas/<組>/items.tsv の点検記録(1 行= 1 基、写真は最大 6 枚)。組は nas/expert_sets.txt(1 行 1 組)か
         環境変数 KK_EXPERT_NAS_SETS(カンマ区切り)。組の名前はデータ側にだけ置く。記録済みのランクは画面に出さない
  insp2017  fkogei-estimate-data/eval/insp2017/items.tsv(2017 年の点検報告書と写真。kanban_karte.eval.insp2017_build が作る)。並びは nas の後
  scan   fkogei-estimate-data/scans/ の点検指示書スキャン PDF(1 PDF= 1 件。ページは pdftoppm で必要なときだけ描画)
写真の実物(社名・個人情報)は画面にだけ出す。ID と表示名には現場名・社名を入れない。

判定役の下書き(評価の結果 JSON、環境変数 KK_EXPERT_DRAFTS)があれば、評価セットの各件に「所見の候補」を付け、
評価セットの未入力の先頭 EVAL_HEAD 件を種類・所見・要対応になりそうな物が散らばる順にする。下書きのランクは画面に出さない(先入観になる)。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import random
import re
import sqlite3
import subprocess
import sys
import threading
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).with_name("static")
PRIVATE = Path(os.environ.get("KK_EXPERT_PRIVATE") or REPO_ROOT / "data" / "private")
SCANS = Path(os.environ.get("KK_EXPERT_SCANS") or Path.home() / "Projects" / "fkogei-estimate-data" / "scans")
DEFAULT_DB = Path(os.environ.get("KK_EXPERT_DB") or PRIVATE / "expert_notes.sqlite")

RANKS = ("良好", "経過観察", "要改善", "即時対応")
SIGNS = ("錆", "腐食", "塗装の剥がれ", "パネルの浮き", "傾き", "ボルトの変色", "取付部のすき間", "基礎のひび", "その他")
KINDS = ("壁面", "袖", "自立", "屋上", "その他")
WEB_FIRST = 50                          # 外の写真(web)は先頭 50 件を状態が混ざるように種固定でシャッフル
SOURCE_LABEL = {"web": "外の写真", "eval": "評価セット", "field": "実データ", "nas": "点検記録", "insp2017": "点検記録(2017)", "page": "報告書ページ", "scan": "点検指示書スキャン"}
EVAL_LIMIT, EVAL_SEED = 34, 1           # docs/EVAL.md の 102 基(ソースごとに 34、seed 1)
SCAN_QUEUE = 500                        # スキャンは 34,000 件超。キューには種をそろえた順で先頭からこの数だけ載せる
SCAN_PAGES = 4                          # 1 PDF につき画面に出す最大ページ数
OUTDOOR = ("壁面", "袖", "自立", "屋上")   # 屋外広告物の点検義務に近い種類。残りのキューはこれだけを先に出す(2026-11-06 Cutcha)
# 種類・屋内の手がかり(看板名 → 判定役の下書きの文の順に見る)。除く物は出さない、分からない物は後ろへ(消さない)
EXCLUDE_WORDS = (("カッティングシート", r"カッティング|カッテイング"), ("室内", r"屋内|室内|店内|館内|窓の内側|ドアガラス"))
KIND_WORDS = (("屋上", r"屋上|塔屋"), ("袖", r"袖|突出"), ("自立", r"自立|支柱|ポール|塔看板|門型"), ("壁面", r"壁面|外壁"))
OUTDOOR_FILTER = os.environ.get("KK_EXPERT_OUTDOOR", "1") != "0"
EVAL_HEAD = 40                          # 評価セットの未入力のうち、散らばらせて先頭に置く件数(41 件目以降は元の順)
NAS_PHOTOS = 6                           # 1 基に出す写真の上限(既存の画面と同じ)
# 看板名の語で屋外か決める(依頼 14)。後ろの語が先: 屋内・置き型は後ろへ、屋外の語は先へ、どちらでもない物も後ろへ
NAS_BACK = r"写真の束|シート|ガラス|ウィンド|スタンド|フロア案内|室内|内部|ポスターパネル|サイネージ|EV|エレベ|受付|カウンター|パンフレット|メニュー|黒板|A型|ポスト"
NAS_OUT = r"屋上|塔屋|自立|袖|突出|壁面|ポール|野立|欄間|フェンス|沿道|切り?文字|チャンネル|パラペット|ファサード|看板|サイン"
NAS_KIND = (("屋上", r"屋上|塔屋"), ("袖", r"袖|突出"), ("自立", r"自立|ポール|野立|塔看板"), ("壁面", r"壁面|外壁|欄間|切り?文字|チャンネル|パラペット|ファサード"))
DEFAULT_DRAFTS = Path(os.environ.get("KK_EXPERT_DRAFTS") or REPO_ROOT / "runs" / "2026-11-02_day2_ex6_opus_limit34.json")
IMG_EXT = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}

SCHEMA = """
CREATE TABLE IF NOT EXISTS expert_notes (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  source      TEXT NOT NULL,
  sign_key    TEXT NOT NULL,
  photos      TEXT NOT NULL DEFAULT '[]',   -- JSON: 見せた写真のパス(読み取り専用の出どころ)
  score       INTEGER CHECK (score BETWEEN 0 AND 100),   -- 危なさの直感(0 安全〜100 危険)。触っていなければ NULL
  rank        INTEGER CHECK (rank BETWEEN 1 AND 4),      -- 1良好 2経過観察 3要改善 4即時対応
  reasons     TEXT NOT NULL DEFAULT '',     -- 根拠(自由記述)
  unknowns    TEXT NOT NULL DEFAULT '',     -- 写真では分からない点(自由記述)
  signs       TEXT NOT NULL DEFAULT '[]',   -- JSON: 見えている劣化のサイン
  kind        TEXT NOT NULL DEFAULT '',     -- 壁面/袖/自立/屋上/その他
  skipped     INTEGER NOT NULL DEFAULT 0,   -- 1 = 「分からない・飛ばす」
  source_type TEXT NOT NULL DEFAULT '',     -- web の出どころ種別(gov/cc/other)。web 以外は空
  license     TEXT NOT NULL DEFAULT '',     -- web の写真のライセンス表記。web 以外は空
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL,
  UNIQUE (source, sign_key)
);
"""


def now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")   # localtime


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(SCHEMA)
            have = {r["name"] for r in c.execute("PRAGMA table_info(expert_notes)")}
            for col in ("source_type", "license"):       # 旧 DB(列なし)への足し込み
                if col not in have:
                    c.execute(f"ALTER TABLE expert_notes ADD COLUMN {col} TEXT NOT NULL DEFAULT ''")

    def _conn(self) -> sqlite3.Connection:
        c = sqlite3.connect(self.path, timeout=10)
        c.row_factory = sqlite3.Row
        return c

    @staticmethod
    def _row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["photos"], d["signs"] = json.loads(d["photos"]), json.loads(d["signs"])
        return d

    def get(self, source: str, sign_key: str) -> dict | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM expert_notes WHERE source=? AND sign_key=?", (source, sign_key)).fetchone()
        return self._row(r) if r else None

    def all(self) -> list[dict]:
        with self._conn() as c:
            return [self._row(r) for r in c.execute("SELECT * FROM expert_notes ORDER BY id")]

    def save(self, source: str, sign_key: str, photos: list[str], note: dict, extra: dict | None = None) -> dict:
        extra = extra or {}
        score, rank = note.get("score"), note.get("rank")
        if score is not None and not (isinstance(score, int) and not isinstance(score, bool) and 0 <= score <= 100):
            raise ValueError("score は 0〜100 の整数")
        if rank is not None and rank not in (1, 2, 3, 4):
            raise ValueError("rank は 1〜4")
        signs = [s for s in (note.get("signs") or []) if s in SIGNS]
        kind = note.get("kind") or ""
        if kind and kind not in KINDS:
            raise ValueError("kind は " + "/".join(KINDS))
        skipped = 1 if note.get("skipped") else 0
        if skipped:
            score = rank = None
        t = now()
        with self._lock, self._conn() as c:
            c.execute(
                """INSERT INTO expert_notes (source, sign_key, photos, score, rank, reasons, unknowns, signs, kind, skipped, source_type, license, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source, sign_key) DO UPDATE SET photos=excluded.photos, score=excluded.score, rank=excluded.rank,
                     reasons=excluded.reasons, unknowns=excluded.unknowns, signs=excluded.signs, kind=excluded.kind,
                     skipped=excluded.skipped, source_type=excluded.source_type, license=excluded.license, updated_at=excluded.updated_at""",
                (source, sign_key, json.dumps(photos, ensure_ascii=False), score, rank, str(note.get("reasons") or "").strip(),
                 str(note.get("unknowns") or "").strip(), json.dumps(signs, ensure_ascii=False), kind, skipped,
                 extra.get("source_type", ""), extra.get("license", ""), t, t))
        return self.get(source, sign_key)


# ---------- 写真の出どころ(読み取り専用) ----------

def guess_kind(name: str) -> str:
    """台帳の看板名(評価セットの sign_name)に語があれば種類の初期値にする。語は画面に出さない。"""
    for word, kind in (("屋上", "屋上"), ("自立", "自立"), ("袖", "袖"), ("壁面", "壁面"), ("壁", "壁面"), ("塔", "自立")):
        if word in (name or ""):
            return kind
    return ""


# 下書きの理由の 1 文ずつに語があれば、その所見を候補にする。否定の文(「見られない」等)は数えない
SIGN_WORDS = (("錆", r"錆|サビ|さび"), ("腐食", r"腐食"), ("塗装の剥がれ", r"塗装.{0,6}(剥|はが|浮)|剥離|塗膜"),
              ("パネルの浮き", r"(パネル|面板|板面|表示面).{0,8}(浮|反り|たわ|変形)"), ("傾き", r"傾"),
              ("ボルトの変色", r"(ボルト|ナット|ビス).{0,8}(錆|変色|サビ)|(錆|変色|サビ).{0,8}(ボルト|ナット|ビス)"),
              ("取付部のすき間", r"すき間|隙間|ゆるみ|緩み|がたつ"), ("基礎のひび", r"基礎|ひび|亀裂|クラック"),
              ("その他", r"汚れ|黄変|黄ば|退色|色あせ|欠け|割れ"))
# 並べ方に使う所見の種類(依頼 10: 錆・ボルト・板面・照明・取付)
CAT_WORDS = (("錆", r"錆|サビ|腐食"), ("ボルト", r"ボルト|ナット|ビス"), ("板面", r"板面|面板|パネル|表示面|割れ|退色|破損"),
             ("照明", r"照明|蛍光|LED|点灯|球|安定器|内照|配線"), ("取付", r"取付|取り付け|金具|アンカー|支柱|ポール|基礎|傾"))
NEG = re.compile(r"見られ(ない|ず)|見当たら|確認でき(ない|ず)|見え(ない|ず)|認められ(ない|ず)|ありません|はない|もない|無い|(判定|判断)でき|写っておらず|該当なし")


def _sentences(reasons) -> list[str]:
    out = []
    for r in reasons if isinstance(reasons, list) else [reasons or ""]:
        out += [x for x in re.split(r"[。\n]", str(r)) if x.strip()]
    return [x for x in out if not NEG.search(x)]


def parse_draft(rec: dict) -> dict:
    """評価の結果 JSON の 1 件 → {rank, suggest, cats}。rank は並べ方にだけ使い、画面に出さない。"""
    sents = _sentences(rec.get("reasons"))
    text = "。".join(sents)
    try:
        rank = int(rec.get("rank_pred"))
    except (TypeError, ValueError):
        rank = None
    raw = " ".join(str(r) for r in rec.get("reasons")) if isinstance(rec.get("reasons"), list) else str(rec.get("reasons") or "")
    return {"rank": rank, "suggest": [n for n, w in SIGN_WORDS if re.search(w, text)],
            "cats": [n for n, w in CAT_WORDS if re.search(w, text)], "text": raw}


def outdoor_class(kind: str, text: str) -> tuple[str, str]:
    """('outdoor' | 'exclude' | 'unknown', 理由)。除く語が先、次に種類(看板名の種類 → 文の語)。"""
    for label, w in EXCLUDE_WORDS:
        if re.search(w, text or ""):
            return "exclude", label
    if kind in OUTDOOR:
        return "outdoor", kind
    for k, w in KIND_WORDS:
        if re.search(w, text or ""):
            return "outdoor", k
    return "unknown", ""


def load_drafts(path: str | Path | None) -> dict[str, dict]:
    """sign_id("xlsx_center:0019" / "0000005930-007")→ 下書き。ファイルが無ければ空(画面は候補なしで動く)。"""
    try:
        recs = json.loads(Path(path).read_text(encoding="utf-8"))["records"]
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return {str(r.get("sign_id", "")).replace(":", "-"): parse_draft(r) for r in recs if r.get("sign_id")}


def spread(items: list, drafts: dict[str, dict], n: int, seed: str = "expert-eval-v1") -> list:
    """先頭 n 件を、種類・所見の種類・出どころ・要対応になりそうか(下書き 3〜4)が偏らない順に選ぶ。残りは元の順で後ろへ。
    貪欲法: まだ少ない特徴を多く持つ件を先に取る(同点は種固定の乱数)。"""
    rng = random.Random(seed)
    tie = {id(it): rng.random() for it in items}
    feats = {}
    for it in items:
        d = drafts.get(it.key) or {}
        f = ["kind:" + (it.kind or "不明"), "grp:" + (it.group or "")] + ["cat:" + c for c in d.get("cats", [])]
        f.append("risk:" + ("高" if (d.get("rank") or 0) >= 3 else "低"))
        feats[id(it)] = f
    seen: dict[str, int] = {}
    pool, head = list(items), []
    while pool and len(head) < n:
        best = min(pool, key=lambda it: (sum(seen.get(x, 0) for x in feats[id(it)]) / len(feats[id(it)]) - 0.15 * len(feats[id(it)]), tie[id(it)]))
        head.append(best)
        pool.remove(best)
        for x in feats[id(best)]:
            seen[x] = seen.get(x, 0) + 1
    taken = {id(i) for i in head}
    return head + [i for i in items if id(i) not in taken]


def _mix_ranks(items: list, meta: dict[str, dict], seed: str = "expert-nas-v1") -> list:
    """記録済みランクで 3 つに分け(要対応 3〜4 / 経過観察 2 / 良好 1・不明)、要対応→良好→経過観察→良好… の順に交互に取る。
    良好ばかり続かないように(依頼 14)。各組の中は種固定でシャッフル。尽きた組は飛ばす。"""
    rng = random.Random(seed)
    groups: dict[str, list] = {"bad": [], "watch": [], "good": []}
    for it in items:
        r = (meta.get(it.key) or {}).get("rank") or 1
        groups["bad" if r >= 3 else "watch" if r == 2 else "good"].append(it)
    for g in groups.values():
        rng.shuffle(g)
    out, cycle = [], ("bad", "good", "watch", "good")
    while any(groups.values()):
        for k in cycle:
            if groups[k]:
                out.append(groups[k].pop(0))
    return out


class Item:
    def __init__(self, source: str, key: str, files: list[Path], kind: str = "", pdf: Path | None = None, web: dict | None = None,
                 group: str = "", cls: str = ""):
        self.source, self.key, self.files, self.kind, self.pdf, self.web, self.group = source, key, files, kind, pdf, web, group
        self.cls = cls   # 屋外の絞り込みの区分を出どころ側で決めた物(nas)。空なら outdoor_class で決める

    @property
    def id(self) -> str:
        return f"{self.source}/{self.key}"


def nas_sets(nas: Path) -> list[str]:
    """点検記録の組(nas の下のフォルダ名)。KK_EXPERT_NAS_SETS か nas/expert_sets.txt。どちらも無ければ空(社名はコードに書かない)。"""
    env = os.environ.get("KK_EXPERT_NAS_SETS")
    if env is not None:
        return [x.strip() for x in env.split(",") if x.strip()]
    f = Path(nas) / "expert_sets.txt"
    return [x.strip() for x in f.read_text(encoding="utf-8").splitlines() if x.strip() and not x.startswith("#")] if f.is_file() else []


class Catalog:
    """写真の出どころを 1 本の順番にする。評価セット→実データ→点検記録(NAS)→報告書ページ→スキャン(種つきの並び)。"""

    def __init__(self, private: Path = PRIVATE, scans: Path = SCANS, cache: Path | None = None, with_eval: bool = True,
                 sources_config: str | None = None, scan_queue: int = SCAN_QUEUE, drafts: str | Path | None = DEFAULT_DRAFTS,
                 nas: Path | None = None):
        self.private, self.scans, self.scan_queue = Path(private), Path(scans), scan_queue
        # NAS の点検記録は scans の隣(fkogei-estimate-data/eval/nas)。試験は scans を一時フォルダにするので自然に空になる
        self.nas = Path(nas or os.environ.get("KK_EXPERT_NAS") or Path(scans).parent / "eval" / "nas")
        self.insp2017 = Path(os.environ.get("KK_EXPERT_INSP2017") or Path(scans).parent / "eval" / "insp2017")
        self.nas_meta: dict[str, dict] = {}   # key → 記録済みのランク・現場など(画面・API には出さない。一致の数え直し用)
        self.drafts = load_drafts(drafts) if drafts else {}
        self.cache = Path(cache) if cache else self.private / "expert_cache"
        self.items: dict[str, Item] = {}
        self.order: list[str] = []
        self.counts: dict[str, dict] = {}
        self.warnings: list[str] = []
        self._pdf_names: list[str] = []
        self.hints: dict[str, str] = {}   # 項目 id → 種類の手がかりの文(下書きの理由など)
        self._load_web()
        self._load_eval(sources_config) if with_eval else None
        self._load_field()
        self._load_nas()
        self._load_nas("insp2017", [("insp2017", self.insp2017)])
        self._load_pages()
        self._load_scans()

    def _add(self, it: Item) -> None:
        self.items[it.id] = it
        self.order.append(it.id)

    def _load_web(self) -> None:
        """外の写真: data/private/expert_web/index.json(配列)。キュー先頭に置き、先頭 WEB_FIRST 件は状態が混ざる順にする。"""
        root = self.private / "expert_web"
        try:
            rows = json.loads((root / "index.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            rows = []
        except (ValueError, OSError):
            self.warnings.append("expert_web/index.json を読めません")
            rows = []
        items = []
        for r in rows if isinstance(rows, list) else []:
            f = root / str(r.get("file") or "")
            if r.get("file") and f.is_file() and f.suffix.lower() in IMG_EXT:
                web = {k: str(r.get(k) or "") for k in ("source_type", "title", "url", "document", "page", "license", "author", "fetched")}
                web["condition_guess"] = str(r.get("condition_guess") or "不明")   # 画面には出さない(先入観になる)
                items.append(Item("web", f.stem, [f], guess_kind(str(r.get("kind_guess") or "")) or (
                    str(r.get("kind_guess")) if r.get("kind_guess") in KINDS else ""), web=web))
        by: dict[str, list[Item]] = {}
        for it in items:
            by.setdefault(it.web["condition_guess"], []).append(it)
        rng = random.Random("expert-web-v1")
        groups = [by[k] for k in ("危ない側", "中間", "良好", "不明") if k in by] + [v for k, v in by.items() if k not in ("危ない側", "中間", "良好", "不明")]
        for g in groups:
            rng.shuffle(g)
        head: list[Item] = []
        while len(head) < min(WEB_FIRST, len(items)):
            for g in groups:
                if g and len(head) < WEB_FIRST:
                    head.append(g.pop(0))
        taken = {id(i) for i in head}
        for it in head + [i for i in items if id(i) not in taken]:
            self._add(it)
        self.counts["web"] = {"signs": len(items), "photos": len(items)}

    def _load_eval(self, cfg: str | None) -> None:
        try:
            from ..eval import dataset
            from ..eval.run_eval import select
            cfg = cfg or os.environ.get("KANBAN_KARTE_SOURCES") or str(self.private / "sources.toml")
            items = dataset.load_all(cfg, sources=dataset.AGGREGATE_SOURCES)
        except Exception as e:   # 評価セットの実体が無い環境でも画面は動かす
            self.warnings.append(f"評価セットを読めません: {type(e).__name__}")
            return
        n = p = 0
        for s in dataset.AGGREGATE_SOURCES:
            pool = sorted((i for i in items if i.source == s and i.split == "eval" and i.rank_true is not None and i.photo_paths),
                          key=lambda i: i.sign_id)
            for i in select(pool, EVAL_LIMIT, EVAL_SEED, s):
                files = [Path(x) for x in i.photo_paths if Path(x).is_file()]
                if files:
                    # sign_id は "xlsx_center:0019" のような連番で社名を含まない。正解ランクは持ち込まない
                    self._add(Item("eval", i.sign_id.replace(":", "-"), files, guess_kind(i.meta.get("sign_name", "")), group=s))
                    n, p = n + 1, p + len(files)
        self.counts["eval"] = {"signs": n, "photos": p}

    def _load_field(self) -> None:
        root = self.private / "field_intake"
        remarks: dict[str, str] = {}
        try:   # 現場の判定役の下書き(看板名+所見の文)。種類と屋内かどうかの手がかりにだけ使う
            c = sqlite3.connect(f"file:{self.private / 'field_site.sqlite'}?mode=ro", uri=True)
            remarks = {r[0]: f"{r[1] or ''} {r[2] or ''} {r[3] or ''}" for r in c.execute("SELECT id, name, type, remark FROM signs")}
            c.close()
        except sqlite3.Error:
            pass
        n = p = 0
        for d in sorted((root / "photos").glob("*")) if (root / "photos").is_dir() else []:
            files = sorted(f for f in d.iterdir() if f.suffix.lower() in IMG_EXT)
            if files:
                self._add(Item("field", d.name, files))
                self.hints["field/" + d.name] = remarks.get(d.name, "")
                n, p = n + 1, p + len(files)
        self.counts["field"] = {"signs": n, "photos": p}

    def _load_nas(self, source: str = "nas", sets: list[tuple[str, Path]] | None = None) -> None:
        """NAS の点検記録(items.tsv)。屋外の語の看板を先に、要対応・経過観察と良好が交互に近くなる順にする(種固定)。
        key は 社名・現場名・組の名前を入れない "<ハッシュ 10 桁>"(元の行は nas_meta で引ける)。カッティングシートは出さない。"""
        import hashlib
        out, back = [], []
        n = p = dup = 0
        digest = lambda x: hashlib.sha1(x.read_bytes()).hexdigest()
        # 評価セット・実データと同じ写真を持つ看板は同じ看板の重複(評価セットの xlsx は NAS 由来)= 載せない。先に読み込んだ物と比べる
        seen = {digest(f) for i in self.order if i.startswith(("eval/", "field/")) for f in self.items[i].files if f.is_file()}
        for s, base in sets if sets is not None else [(x, self.nas / x) for x in nas_sets(self.nas)]:
            f = base / "items.tsv"
            if not f.is_file():
                continue
            with open(f, encoding="utf-8", newline="") as fh:
                rows = list(csv.DictReader(fh, delimiter="\t"))
            for r in rows:
                try:
                    files = [base / x for x in json.loads(r.get("photo_files") or "[]")]
                except ValueError:
                    files = []
                files = [x for x in files if x.is_file() and x.suffix.lower() in IMG_EXT][:NAS_PHOTOS]
                if not files:
                    continue
                if seen and any(digest(x) in seen for x in files):
                    dup += 1
                    continue
                key = hashlib.sha1("|".join((s, r.get("source_file", ""), r.get("sheet", ""), r.get("sign_no", ""),
                                                      r.get("sign_name", ""))).encode()).hexdigest()[:10]
                name = r.get("sign_name", "")
                rank = r.get("sign_rank_normalized") or r.get("overall_rank_normalized") or ""
                self.nas_meta[key] = {"set": s, "site": r.get("site", ""), "sheet": r.get("sheet", ""), "sign_no": r.get("sign_no", ""),
                                      "sign_name": name, "inspection_date": r.get("inspection_date", ""), "rank": int(rank) if rank.isdigit() else None}
                if re.search(EXCLUDE_WORDS[0][1], name):
                    cls = "exclude"
                elif re.search(NAS_BACK, name) or not re.search(NAS_OUT, name):
                    cls = "unknown"
                else:
                    cls = "outdoor"
                kind = next((k for k, w in NAS_KIND if re.search(w, name)), "") if cls == "outdoor" else ""
                it = Item(source, key, files, kind, group=s, cls=cls)
                (out if cls == "outdoor" else back).append(it)
                n, p = n + 1, p + len(files)
        for it in _mix_ranks(out, self.nas_meta) + back:
            self._add(it)
        self.counts[source] = {"signs": n, "photos": p, "dup_skipped": dup}

    def _load_pages(self) -> None:
        root = self.private / "field_intake"
        n = p = 0
        for d in sorted((root / "pages").glob("*")) if (root / "pages").is_dir() else []:
            for f in sorted(d.iterdir()):
                if f.suffix.lower() in IMG_EXT:
                    self._add(Item("page", f"{d.name}-{f.stem}", [f]))
                    n, p = n + 1, p + 1
        self.counts["page"] = {"signs": n, "photos": p}

    def _load_scans(self) -> None:
        names = sorted(f.name[:-4] for f in os.scandir(self.scans) if f.name.endswith(".pdf")) if self.scans.is_dir() else []
        self._pdf_names = names
        rng = random.Random("expert-scan-v1")
        shuffled = names[:]
        rng.shuffle(shuffled)
        for stem in shuffled[: self.scan_queue]:
            self._add(Item("scan", stem, [], pdf=self.scans / f"{stem}.pdf"))
        self.counts["scan"] = {"signs": len(names), "photos": None, "queued": min(len(names), self.scan_queue)}

    # ---- 写真ファイルの解決 ----
    def files(self, it: Item) -> list[Path]:
        """見せる写真。スキャン PDF は先頭から SCAN_PAGES ページを JPEG にして cache に置く(初回だけ描画)。"""
        if it.source != "scan":
            return it.files
        if not it.files:
            out = self.cache / "scan" / it.key
            out.mkdir(parents=True, exist_ok=True)
            if not list(out.glob("p-*.jpg")):
                subprocess.run(["pdftoppm", "-jpeg", "-r", "80", "-l", str(SCAN_PAGES), str(it.pdf), str(out / "p")],
                               check=False, capture_output=True, timeout=120)
            it.files = sorted(out.glob("p-*.jpg"))
        return it.files


# ---------- HTTP ----------

class Expert:
    def __init__(self, store: Store, catalog: Catalog):
        self.store, self.catalog = store, catalog
        self._order: list[str] | None = None
        self.filter_counts: dict = {}

    def order(self, done: dict) -> list[str]:
        """評価セットの区間だけ並べ替える: 未入力を散らばらせた先頭 EVAL_HEAD 件 → 残りの未入力(元の順)→ 入力済み。
        プロセスで 1 回だけ決める(保存のたびに並びが動かないように)。"""
        if self._order is None:
            ids = self.catalog.order
            ev = [i for i in ids if i.startswith("eval/")]
            if ev and self.catalog.drafts:
                todo = [self.catalog.items[i] for i in ev if i not in done]
                new = [it.id for it in spread(todo, self.catalog.drafts, EVAL_HEAD)] + [i for i in ev if i in done]
                at = ids.index(ev[0])
                rest = [i for i in ids if not i.startswith("eval/")]
                ids = rest[:at] + new + rest[at:]
            self._order = self._outdoor(ids, done) if OUTDOOR_FILTER else ids
        return self._order

    def _outdoor(self, ids: list[str], done: dict) -> list[str]:
        """未入力だけを屋外の看板に絞る: 屋外(元の順)→ 分からない物は後ろ。除く物(カッティングシート・室内)は出さない。
        入力済みは最後尾に回す(消さない)。内訳は self.filter_counts に。"""
        keep, back, fin, web = [], [], [], []
        cnt = {"outdoor": 0, "unknown": 0, "exclude": {}}
        for i in ids:
            if i in done:
                fin.append(i)   # 入力済みは最後尾へ(画面の「次へ」は位置で進むので、途中に残すと済みの物が出る。2026-11-06 15:55 Cutcha 指摘)
                continue
            it = self.catalog.items[i]
            if it.source == "web":
                web.append(i)   # 外のサイトの写真(1 基 1 枚、点検結果なし)はアノテーションの対象外= 入力済みより後ろ(2026-11-06 16:01 Cutcha 指摘)
                continue
            text = self.catalog.hints.get(i) or (self.catalog.drafts.get(it.key) or {}).get("text", "") if it.source == "eval" else self.catalog.hints.get(i, "")
            cls, why = (it.cls, "カッティングシート") if it.cls else outdoor_class(it.kind, text)
            if cls == "exclude":
                cnt["exclude"][why] = cnt["exclude"].get(why, 0) + 1
            elif cls == "outdoor":
                cnt["outdoor"] += 1
                keep.append(i)
            else:
                cnt["unknown"] += 1
                back.append(i)
        self.filter_counts = cnt
        return keep + back + fin + web

    def queue(self) -> dict:
        done = {(r["source"] + "/" + r["sign_key"]): r for r in self.store.all()}
        ids = self.order(done)
        rows = [[i, 0 if i not in done else (2 if done[i]["skipped"] else 1)] for i in ids]   # 0 未 1 済 2 飛ばし
        return {"queue": rows, "total": len(ids),
                "done": sum(1 for r in rows if r[1] == 1), "skipped": sum(1 for r in rows if r[1] == 2),
                "counts": self.catalog.counts, "warnings": self.catalog.warnings, "outdoor": self.filter_counts,
                "ranks": RANKS, "signs": SIGNS, "kinds": KINDS}

    def item(self, iid: str) -> dict | None:
        it = self.catalog.items.get(iid)
        if not it:
            return None
        files = self.catalog.files(it)
        saved = self.store.get(it.source, it.key)
        return {"id": it.id, "source": it.source, "source_label": SOURCE_LABEL[it.source], "key": it.key,
                "n_photos": len(files), "kind_default": it.kind, "saved": saved,
                "suggest_signs": (self.catalog.drafts.get(it.key) or {}).get("suggest", []) if it.source == "eval" else [],
                "web": {k: v for k, v in it.web.items() if k != "condition_guess"} if it.web else None}

    def image(self, iid: str, n: int) -> tuple[bytes, str] | None:
        it = self.catalog.items.get(iid)
        if not it:
            return None
        files = self.catalog.files(it)
        if not 0 <= n < len(files):
            return None
        p = files[n]
        return p.read_bytes(), IMG_EXT.get(p.suffix.lower(), "image/jpeg")

    def save(self, body: dict) -> dict | None:
        it = self.catalog.items.get(str(body.get("id") or ""))
        if not it:
            return None
        files = self.catalog.files(it)
        return self.store.save(it.source, it.key, [self._rel(f) for f in files], body,
                               {"source_type": it.web["source_type"], "license": it.web["license"]} if it.web else None)

    def _rel(self, f: Path) -> str:
        """web の写真は private からの相対パス(expert_web/12.jpg)、他は絶対パス。"""
        try:
            return str(f.relative_to(self.catalog.private)) if f.is_relative_to(self.catalog.private) and f.parent.name == "expert_web" else str(f)
        except ValueError:
            return str(f)


def get(app) -> Expert:
    """App に 1 つだけ持たせる(テストは app._expert を先に差し込める)。"""
    ex = getattr(app, "_expert", None)
    if ex is None:
        ex = Expert(Store(DEFAULT_DB), Catalog())
        app._expert = ex
    return ex


def wants(path: str) -> bool:
    return path in ("/expert", "/expert/") or path.startswith("/api/expert/")


def serve(h, app, method: str, path: str, q: dict, body: dict | None) -> None:
    """server.py の Handler から呼ぶ。h は Handler(_send/_json/_error/_file を使う)。"""
    if path == "/expert":
        return h._send(308, b"", "text/plain", {"Location": "/expert/"})
    if path == "/expert/":
        return h._file(STATIC / "expert.html", "text/html; charset=utf-8")
    ex = get(app)
    try:
        if method == "GET":
            if path == "/api/expert/queue":
                return h._json(ex.queue())
            if path == "/api/expert/item":
                r = ex.item((q.get("id") or [""])[0])
                return h._json(r) if r else h._error(404, "その件はありません")
            if path == "/api/expert/img":
                try:
                    n = int((q.get("n") or ["0"])[0])
                except ValueError:
                    return h._error(400, "n は整数")
                r = ex.image((q.get("id") or [""])[0], n)
                if not r:
                    return h._error(404, "写真がありません")
                return h._send(200, r[0], r[1], {"Cache-Control": "max-age=300"})
        elif path == "/api/expert/save":
            r = ex.save(body or {})
            return h._json(r) if r else h._error(404, "その件はありません")
    except ValueError as e:
        return h._error(400, str(e))
    h._error(404, "見つかりません")


# ---------- CLI ----------

COLS = ["id", "source", "sign_key", "photos", "score", "rank", "reasons", "unknowns", "signs", "kind", "skipped", "source_type", "license", "created_at", "updated_at"]


def export(db: str | Path, out: str | Path) -> dict:
    rows = Store(db).all()
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M")
    jl, cv = out / f"expert_notes_{stamp}.jsonl", out / f"expert_notes_{stamp}.csv"
    with open(jl, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps({k: r[k] for k in COLS}, ensure_ascii=False) + "\n")
    with open(cv, "w", encoding="utf-8-sig", newline="") as f:     # BOM つき(Excel で文字化けしない)
        w = csv.writer(f)
        w.writerow(COLS)
        for r in rows:
            w.writerow([json.dumps(r[k], ensure_ascii=False) if k in ("photos", "signs") else r[k] for k in COLS])
    return {"rows": len(rows), "csv": str(cv), "jsonl": str(jl)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.web.expert", description="有資格者のアノテーション(expert_notes)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="CSV と JSONL に書き出す")
    e.add_argument("--db", default=str(DEFAULT_DB))
    e.add_argument("--out", default=str(REPO_ROOT / "runs" / "expert"))
    s = sub.add_parser("stats", help="保存済みの件数")
    s.add_argument("--db", default=str(DEFAULT_DB))
    sub.add_parser("sources", help="写真の出どころごとの件数")
    m = sub.add_parser("nas-meta", help="点検記録(nas)の key → 記録済みランク・現場(Cutcha のランクとの一致を数える元)")
    m.add_argument("--out", default=str(REPO_ROOT / "runs" / "expert" / "nas_meta.tsv"))
    a = ap.parse_args(argv)
    if a.cmd == "export":
        print(json.dumps(export(a.db, a.out), ensure_ascii=False))
    elif a.cmd == "stats":
        rows = Store(a.db).all()
        print(json.dumps({"saved": len(rows), "skipped": sum(r["skipped"] for r in rows),
                          "by_source": {s: sum(1 for r in rows if r["source"] == s) for s in SOURCE_LABEL}}, ensure_ascii=False))
    elif a.cmd == "nas-meta":
        c = Catalog()
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        cols = ["set", "site", "sheet", "sign_no", "sign_name", "inspection_date", "rank"]
        with open(out, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f, delimiter="\t")
            w.writerow(["sign_key"] + cols)
            for k, v in c.nas_meta.items():
                w.writerow([k] + ["" if v[x] is None else v[x] for x in cols])
        print(json.dumps({"rows": len(c.nas_meta), "out": str(out)}, ensure_ascii=False))
    else:
        c = Catalog()
        print(json.dumps({"counts": c.counts, "queue": len(c.order), "warnings": c.warnings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
