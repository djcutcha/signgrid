"""有資格者の言葉づけ画面(/expert/)。写真を見て、危なさの直感・根拠・気になる点・写真では分からない点を言葉にする。

  python3 -m kanban_karte.web.expert export [--out DIR] [--db PATH]   CSV と JSONL を書き出す
  python3 -m kanban_karte.web.expert stats  [--db PATH]               件数だけ見る
  python3 -m kanban_karte.web.expert sources                          写真の出どころの件数(基・枚)

この言葉が安全判定の正解データになり、後で構造化して規則にする。台帳 DB は触らない。
保存先は data/private/expert_notes.sqlite(環境変数 KK_EXPERT_DB)。写真は読み取り専用で、出どころは 3 つ:
  eval   docs/EVAL.md の評価セット(有資格者ランク付き。--limit 34 --seed 1 の 3 ソース= 102 基)。正解ランクは画面に出さない
  field  data/private/field_intake/photos/ の実データ(1 フォルダ= 1 基)と pages/(報告書ページ。1 頁= 1 件)
  scan   fkogei-estimate-data/scans/ の点検指示書スキャン PDF(1 PDF= 1 件。ページは pdftoppm で必要なときだけ描画)
写真の実物(社名・個人情報)は画面にだけ出す。ID と表示名には現場名・社名を入れない。
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
SOURCE_LABEL = {"eval": "評価セット", "field": "実データ", "page": "報告書ページ", "scan": "点検指示書スキャン"}
EVAL_LIMIT, EVAL_SEED = 34, 1           # docs/EVAL.md の 102 基(ソースごとに 34、seed 1)
SCAN_QUEUE = 500                        # スキャンは 34,000 件超。キューには種をそろえた順で先頭からこの数だけ載せる
SCAN_PAGES = 4                          # 1 PDF につき画面に出す最大ページ数
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

    def save(self, source: str, sign_key: str, photos: list[str], note: dict) -> dict:
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
                """INSERT INTO expert_notes (source, sign_key, photos, score, rank, reasons, unknowns, signs, kind, skipped, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(source, sign_key) DO UPDATE SET photos=excluded.photos, score=excluded.score, rank=excluded.rank,
                     reasons=excluded.reasons, unknowns=excluded.unknowns, signs=excluded.signs, kind=excluded.kind,
                     skipped=excluded.skipped, updated_at=excluded.updated_at""",
                (source, sign_key, json.dumps(photos, ensure_ascii=False), score, rank, str(note.get("reasons") or "").strip(),
                 str(note.get("unknowns") or "").strip(), json.dumps(signs, ensure_ascii=False), kind, skipped, t, t))
        return self.get(source, sign_key)


# ---------- 写真の出どころ(読み取り専用) ----------

def guess_kind(name: str) -> str:
    """台帳の看板名(評価セットの sign_name)に語があれば種類の初期値にする。語は画面に出さない。"""
    for word, kind in (("屋上", "屋上"), ("自立", "自立"), ("袖", "袖"), ("壁面", "壁面"), ("壁", "壁面"), ("塔", "自立")):
        if word in (name or ""):
            return kind
    return ""


class Item:
    def __init__(self, source: str, key: str, files: list[Path], kind: str = "", pdf: Path | None = None):
        self.source, self.key, self.files, self.kind, self.pdf = source, key, files, kind, pdf

    @property
    def id(self) -> str:
        return f"{self.source}/{self.key}"


class Catalog:
    """写真の出どころ 3 つを 1 本の順番にする。評価セット→実データ→報告書ページ→スキャン(種つきの並び)。"""

    def __init__(self, private: Path = PRIVATE, scans: Path = SCANS, cache: Path | None = None, with_eval: bool = True,
                 sources_config: str | None = None, scan_queue: int = SCAN_QUEUE):
        self.private, self.scans, self.scan_queue = Path(private), Path(scans), scan_queue
        self.cache = Path(cache) if cache else self.private / "expert_cache"
        self.items: dict[str, Item] = {}
        self.order: list[str] = []
        self.counts: dict[str, dict] = {}
        self.warnings: list[str] = []
        self._pdf_names: list[str] = []
        self._load_eval(sources_config) if with_eval else None
        self._load_field()
        self._load_scans()

    def _add(self, it: Item) -> None:
        self.items[it.id] = it
        self.order.append(it.id)

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
                    self._add(Item("eval", i.sign_id.replace(":", "-"), files, guess_kind(i.meta.get("sign_name", ""))))
                    n, p = n + 1, p + len(files)
        self.counts["eval"] = {"signs": n, "photos": p}

    def _load_field(self) -> None:
        root = self.private / "field_intake"
        n = p = 0
        for d in sorted((root / "photos").glob("*")) if (root / "photos").is_dir() else []:
            files = sorted(f for f in d.iterdir() if f.suffix.lower() in IMG_EXT)
            if files:
                self._add(Item("field", d.name, files))
                n, p = n + 1, p + len(files)
        self.counts["field"] = {"signs": n, "photos": p}
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

    def queue(self) -> dict:
        done = {(r["source"] + "/" + r["sign_key"]): r for r in self.store.all()}
        ids = self.catalog.order
        rows = [[i, 0 if i not in done else (2 if done[i]["skipped"] else 1)] for i in ids]   # 0 未 1 済 2 飛ばし
        return {"queue": rows, "total": len(ids),
                "done": sum(1 for r in rows if r[1] == 1), "skipped": sum(1 for r in rows if r[1] == 2),
                "counts": self.catalog.counts, "warnings": self.catalog.warnings,
                "ranks": RANKS, "signs": SIGNS, "kinds": KINDS}

    def item(self, iid: str) -> dict | None:
        it = self.catalog.items.get(iid)
        if not it:
            return None
        files = self.catalog.files(it)
        saved = self.store.get(it.source, it.key)
        return {"id": it.id, "source": it.source, "source_label": SOURCE_LABEL[it.source], "key": it.key,
                "n_photos": len(files), "kind_default": it.kind, "saved": saved}

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
        return self.store.save(it.source, it.key, [str(f) for f in files], body)


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

COLS = ["id", "source", "sign_key", "photos", "score", "rank", "reasons", "unknowns", "signs", "kind", "skipped", "created_at", "updated_at"]


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
    ap = argparse.ArgumentParser(prog="kanban_karte.web.expert", description="有資格者の言葉づけ(expert_notes)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export", help="CSV と JSONL に書き出す")
    e.add_argument("--db", default=str(DEFAULT_DB))
    e.add_argument("--out", default=str(REPO_ROOT / "runs" / "expert"))
    s = sub.add_parser("stats", help="保存済みの件数")
    s.add_argument("--db", default=str(DEFAULT_DB))
    sub.add_parser("sources", help="写真の出どころごとの件数")
    a = ap.parse_args(argv)
    if a.cmd == "export":
        print(json.dumps(export(a.db, a.out), ensure_ascii=False))
    elif a.cmd == "stats":
        rows = Store(a.db).all()
        print(json.dumps({"saved": len(rows), "skipped": sum(r["skipped"] for r in rows),
                          "by_source": {s: sum(1 for r in rows if r["source"] == s) for s in SOURCE_LABEL}}, ensure_ascii=False))
    else:
        c = Catalog()
        print(json.dumps({"counts": c.counts, "queue": len(c.order), "warnings": c.warnings}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
