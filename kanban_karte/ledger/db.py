"""台帳の SQLite(標準 sqlite3)。スキーマは schema.sql 1本。

法規の列(is_self_use / permit_required / permit_expires / next_inspection)は、この段階では空でよい。
安全の列(rank / items / remark / photos ...)は有資格者が確定する。AI は確定しない。
"""
from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field, fields
from pathlib import Path

from .items import validate_items

SCHEMA_VERSION = 2
SCHEMA_SQL = Path(__file__).with_name("schema.sql")
SIGN_STATUSES = ("new", "needs_photos", "draft", "confirmed")
RULE_STATUSES = ("candidate", "approved", "rejected")
FIGURE_TAG = "図面"  # pages.tag: 職人の手描きスケッチ・印刷案の頁。看板の写真ではないので、下書き・様式の写真には入れない
MEASURE_TAG = "実測"  # pages.tag: 巻尺を当てた写真(ガイドの寸法の実測)。寸法の証拠で、下書き・様式の写真には入れない
SIZE_SOURCES = ("measured", "estimated", "drawing")  # signs.size_source: 実測 / 推定(AI)/ 図面(スケッチ)。None=未入力か出どころ不明
PAGE_KINDS = ("指示書", "完成報告書", "点検表", "写真", "地図", "危険予知", "その他", "不明")


@dataclass
class Site:
    id: str
    name: str
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    municipality: str | None = None


@dataclass
class Sign:
    id: str
    site_id: str | None = None
    name: str | None = None
    type: str | None = None
    size_w: int | None = None  # mm
    size_h: int | None = None
    address: str | None = None
    lat: float | None = None
    lng: float | None = None
    municipality: str | None = None
    # 法規の列
    is_self_use: bool | None = None
    permit_required: bool | None = None
    permit_expires: str | None = None
    next_inspection: str | None = None
    # 安全の列
    rank: int | None = None
    items: dict[str, str] = field(default_factory=dict)
    remark: str | None = None
    photos: list[str] = field(default_factory=list)
    last_inspected: str | None = None
    prev_rank: int | None = None
    source_bundle: int | None = None
    # 下書きと確定
    status: str = "new"
    draft_rank: int | None = None
    confidence: float | None = None
    evidence: dict[str, dict] = field(default_factory=dict)
    confirmed_at: str | None = None
    confirmed_by: str | None = None
    height_m: float | None = None  # 看板の全高(m)。size は表示面の寸法で、全高は別。法規の判定に渡す
    draft_by: str | None = None    # 下書きの出どころ。ガイド付き点検は 'line_guide:<末尾4桁>'
    # 寸法の出どころと実測の付帯(size_w / size_h の出どころ。GL は地面からの高さ mm、gl_edge='bottom' は看板の下端まで)
    size_source: str | None = None
    size_by: str | None = None
    size_at: str | None = None     # YYYY-MM-DD
    gl_mm: int | None = None
    gl_edge: str | None = None
    mount: str | None = None       # ビス / バンセン / 吊り / 接着 / その他
    post: str | None = None        # 支柱(例 □50)。「無し」も値

    def validate(self) -> None:
        if self.status not in SIGN_STATUSES:
            raise ValueError(f"status は {SIGN_STATUSES} のどれか: {self.status!r}")
        for name in ("rank", "prev_rank", "draft_rank"):
            v = getattr(self, name)
            if v is not None and (isinstance(v, bool) or v not in (1, 2, 3, 4)):
                raise ValueError(f"{name} は 1〜4 か None: {v!r}")
        self.items = validate_items(self.items)
        if self.size_source is not None and self.size_source not in SIZE_SOURCES:
            raise ValueError(f"size_source は {SIZE_SOURCES} か None: {self.size_source!r}")
        if not isinstance(self.photos, list):
            raise ValueError("photos はリスト")


_SIGN_COLS = [f.name for f in fields(Sign)]
_BOOL_COLS = ("is_self_use", "permit_required")


def _tri(v):
    return None if v is None else int(bool(v))


class Ledger:
    """台帳への入口。Ledger(path) か Ledger(":memory:")。"""

    def __init__(self, path: str | Path = ":memory:", data_dir: str | Path | None = None):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.data_dir = Path(data_dir) if data_dir is not None else None
        self.ensure_schema()

    def resolve_path(self, p: str | Path) -> Path:
        """台帳に入っている画像のパスを、開けるパスにする。相対パスは data_dir 基準(取り込みが書く形)、
        絶対パス(見本データなど)はそのまま。data_dir が無いときの相対パスは実行時のカレント基準。"""
        p = Path(p)
        return p if p.is_absolute() or self.data_dir is None else self.data_dir / p

    def rel_path(self, p: str | Path) -> str:
        """data_dir の下にある画像のパスを、台帳に入れる相対パス(/ 区切り)にする。下になければそのまま。"""
        p = Path(p)
        if self.data_dir is not None:
            try:
                return p.relative_to(self.data_dir).as_posix()
            except ValueError:
                pass
        return str(p)

    def close(self) -> None:
        self.conn.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    # ---- スキーマ ----
    def ensure_schema(self) -> None:
        ver = self.conn.execute("PRAGMA user_version").fetchone()[0]
        if ver > SCHEMA_VERSION:
            raise RuntimeError(f"台帳の版 {ver} は、このプログラムが扱える版 {SCHEMA_VERSION} より新しい")
        self.conn.executescript(SCHEMA_SQL.read_text(encoding="utf-8"))
        if "site_id" not in {r[1] for r in self.conn.execute("PRAGMA table_info(bundles)")}:
            self.conn.execute("ALTER TABLE bundles ADD COLUMN site_id TEXT REFERENCES sites(id)")
        # v2: signs に下書き・確定の列(古い台帳に足す。新しい台帳は schema.sql の CREATE が持つ)
        sign_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(signs)")}
        for col, ddl in (("status", "TEXT NOT NULL DEFAULT 'new'"), ("draft_rank", "INTEGER"),
                         ("confidence", "REAL"), ("evidence", "TEXT NOT NULL DEFAULT '{}'"),
                         ("confirmed_at", "TEXT"), ("confirmed_by", "TEXT"), ("height_m", "REAL"),
                         ("draft_by", "TEXT"), ("size_source", "TEXT"), ("size_by", "TEXT"), ("size_at", "TEXT"),
                         ("gl_mm", "INTEGER"), ("gl_edge", "TEXT"), ("mount", "TEXT"), ("post", "TEXT")):
            if col not in sign_cols:
                self.conn.execute(f"ALTER TABLE signs ADD COLUMN {col} {ddl}")
        if "tag" not in {r[1] for r in self.conn.execute("PRAGMA table_info(pages)")}:
            self.conn.execute("ALTER TABLE pages ADD COLUMN tag TEXT")  # ガイド付き点検: どの部位の写真か
        # 規則の承認の列(古い台帳に足す。ALTER の ADD COLUMN は CHECK を付けても既存行は既定値 candidate になる)
        rule_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(rules)")}
        for col, ddl in (("status", "TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'approved', 'rejected'))"),
                         ("approved_by", "TEXT"), ("approved_at", "TEXT"), ("resolution", "TEXT"), ("structured", "TEXT")):
            if col not in rule_cols:
                self.conn.execute(f"ALTER TABLE rules ADD COLUMN {col} {ddl}")
        self.conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        self.conn.commit()

    # ---- 現場 ----
    def upsert_site(self, s: Site) -> None:
        self.conn.execute(
            "INSERT INTO sites(id, name, address, lat, lng, municipality) VALUES (?,?,?,?,?,?) "
            "ON CONFLICT(id) DO UPDATE SET name=excluded.name, address=excluded.address, lat=excluded.lat, "
            "lng=excluded.lng, municipality=excluded.municipality",
            (s.id, s.name, s.address, s.lat, s.lng, s.municipality))
        self.conn.commit()

    def get_site(self, site_id: str) -> Site | None:
        r = self.conn.execute("SELECT * FROM sites WHERE id=?", (site_id,)).fetchone()
        return Site(r["id"], r["name"], r["address"], r["lat"], r["lng"], r["municipality"]) if r else None

    # ---- 看板 ----
    def upsert_sign(self, s: Sign) -> None:
        s.validate()
        vals = {c: getattr(s, c) for c in _SIGN_COLS}
        for c in _BOOL_COLS:
            vals[c] = _tri(vals[c])
        vals["items"] = json.dumps(s.items, ensure_ascii=False)
        vals["photos"] = json.dumps(s.photos, ensure_ascii=False)
        vals["evidence"] = json.dumps(s.evidence, ensure_ascii=False)
        cols = ", ".join(_SIGN_COLS)
        marks = ", ".join("?" for _ in _SIGN_COLS)
        sets = ", ".join(f"{c}=excluded.{c}" for c in _SIGN_COLS if c != "id")
        self.conn.execute(
            f"INSERT INTO signs({cols}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {sets}, "
            f"updated=datetime('now')", [vals[c] for c in _SIGN_COLS])
        self.conn.commit()

    @staticmethod
    def _sign_from_row(r: sqlite3.Row) -> Sign:
        d = {c: r[c] for c in _SIGN_COLS}
        for c in _BOOL_COLS:
            d[c] = None if d[c] is None else bool(d[c])
        d["items"] = json.loads(d["items"])
        d["photos"] = json.loads(d["photos"])
        d["evidence"] = json.loads(d["evidence"])
        return Sign(**d)

    def get_sign(self, sign_id: str) -> Sign | None:
        r = self.conn.execute("SELECT * FROM signs WHERE id=?", (sign_id,)).fetchone()
        return self._sign_from_row(r) if r else None

    def list_signs(self, site_id: str | None = None, bundle_id: int | None = None) -> list[Sign]:
        q, args = "SELECT * FROM signs", []
        conds = []
        if site_id is not None:
            conds.append("site_id=?")
            args.append(site_id)
        if bundle_id is not None:
            conds.append("source_bundle=?")
            args.append(bundle_id)
        if conds:
            q += " WHERE " + " AND ".join(conds)
        return [self._sign_from_row(r) for r in self.conn.execute(q + " ORDER BY id", args)]

    # ---- 束とページ(入口2) ----
    def find_bundle_by_sha(self, sha256: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM bundles WHERE sha256=?", (sha256,)).fetchone()

    def add_bundle(self, file: str, sha256: str, pages: int = 0) -> int:
        cur = self.conn.execute(
            "INSERT INTO bundles(file, sha256, pages, status) VALUES (?,?,?, 'new')", (file, sha256, pages))
        self.conn.commit()
        return cur.lastrowid

    def set_bundle(self, bundle_id: int, *, status: str | None = None, pages: int | None = None,
                   kind_counts: dict | None = None, error: str | None = None,
                   site_id: str | None = None) -> None:
        sets, args = [], []
        for col, v in (("status", status), ("pages", pages), ("error", error), ("site_id", site_id)):
            if v is not None:
                sets.append(f"{col}=?")
                args.append(v)
        if kind_counts is not None:
            sets.append("kind_counts=?")
            args.append(json.dumps(kind_counts, ensure_ascii=False))
        if sets:
            self.conn.execute(f"UPDATE bundles SET {', '.join(sets)} WHERE id=?", [*args, bundle_id])
            self.conn.commit()

    def get_bundle(self, bundle_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM bundles WHERE id=?", (bundle_id,)).fetchone()

    def list_bundles(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM bundles ORDER BY id"))

    def upsert_page(self, bundle_id: int, page_no: int, *, kind: str = "不明", kind_basis: str | None = None,
                    image_path: str | None = None, image_source: str | None = None, src_rotate: int = 0,
                    rotation: int = 0, width: int | None = None, height: int | None = None,
                    dpi: float | None = None) -> None:
        if kind not in PAGE_KINDS:
            raise ValueError(f"ページ種別は {PAGE_KINDS} のどれか: {kind!r}")
        self.conn.execute(
            "INSERT INTO pages(bundle_id, page_no, kind, kind_basis, image_path, image_source, src_rotate, "
            "rotation, width, height, dpi) VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(bundle_id, page_no) DO UPDATE SET kind=excluded.kind, kind_basis=excluded.kind_basis, "
            "image_path=excluded.image_path, image_source=excluded.image_source, src_rotate=excluded.src_rotate, "
            "rotation=excluded.rotation, width=excluded.width, height=excluded.height, dpi=excluded.dpi",
            (bundle_id, page_no, kind, kind_basis, image_path, image_source, src_rotate, rotation, width, height, dpi))
        self.conn.commit()

    def set_page_kind(self, bundle_id: int, page_no: int, kind: str, basis: str | None = None) -> None:
        if kind not in PAGE_KINDS:
            raise ValueError(f"ページ種別は {PAGE_KINDS} のどれか: {kind!r}")
        self.conn.execute("UPDATE pages SET kind=?, kind_basis=? WHERE bundle_id=? AND page_no=?",
                          (kind, basis, bundle_id, page_no))
        self.conn.commit()

    def set_page_tag(self, bundle_id: int, page_no: int, tag: str | None) -> None:
        """ガイド付き点検で、そのページがどの部位の写真か(overview / 部位名)を残す。"""
        self.conn.execute("UPDATE pages SET tag=? WHERE bundle_id=? AND page_no=?", (tag, bundle_id, page_no))
        self.conn.commit()

    def figure_paths(self) -> set[str]:
        """タグ「図面」の頁の画像(台帳の相対パス)。"""
        return {r["image_path"] for r in self.conn.execute(
            "SELECT image_path FROM pages WHERE tag=? AND image_path IS NOT NULL", (FIGURE_TAG,))}

    def measure_paths(self) -> set[str]:
        """タグ「実測」の頁の画像(台帳の相対パス)。"""
        return {r["image_path"] for r in self.conn.execute(
            "SELECT image_path FROM pages WHERE tag=? AND image_path IS NOT NULL", (MEASURE_TAG,))}

    def split_photos(self, sign: Sign) -> tuple[list[str], list[str]]:
        """看板の画像を (写真, 図面) に分ける(順序は保つ)。下書き・様式の写真は前者だけ(図面も実測写真も入れない)。"""
        fig, meas = self.figure_paths(), self.measure_paths()
        return [p for p in sign.photos if p not in fig and p not in meas], [p for p in sign.photos if p in fig]

    def list_pages(self, bundle_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM pages WHERE bundle_id=? ORDER BY page_no", (bundle_id,)))

    def kind_counts(self, bundle_id: int) -> dict[str, int]:
        return {r["kind"]: r["n"] for r in self.conn.execute(
            "SELECT kind, COUNT(*) n FROM pages WHERE bundle_id=? GROUP BY kind ORDER BY kind", (bundle_id,))}

    # ---- 規則(有資格者の訂正から育つ) ----
    def add_rule(self, text: str, source: str | None = None, conflicts_with: list[int] | None = None,
                 status: str = "candidate", structured: dict | None = None) -> int:
        if status not in RULE_STATUSES:
            raise ValueError(f"規則の status は {RULE_STATUSES} のどれか: {status!r}")
        cur = self.conn.execute(
            "INSERT INTO rules(text, source, conflicts_with, status, structured) VALUES (?,?,?,?,?)",
            (text, source, json.dumps(conflicts_with or []), status,
             None if structured is None else json.dumps(structured, ensure_ascii=False)))
        self.conn.commit()
        return cur.lastrowid

    def update_rule(self, rule_id: int, text: str, structured: dict, conflicts_with: list[int]) -> None:
        """規則の文・構造・矛盾の相手を書き直す(聞き返しの答えを受けたとき)。承認の状態は変えない。"""
        self.conn.execute("UPDATE rules SET text=?, structured=?, conflicts_with=? WHERE id=?",
                          (text, json.dumps(structured, ensure_ascii=False), json.dumps(conflicts_with), rule_id))
        self.conn.commit()

    def list_rules(self, status: str | None = None) -> list[sqlite3.Row]:
        if status is None:
            return list(self.conn.execute("SELECT * FROM rules ORDER BY id"))
        return list(self.conn.execute("SELECT * FROM rules WHERE status=? ORDER BY id", (status,)))

    def get_rule(self, rule_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM rules WHERE id=?", (rule_id,)).fetchone()

    def set_rule_status(self, rule_id: int, status: str, by: str, resolution: str | None = None, *,
                        commit: bool = True) -> None:
        """承認・却下を書く。resolution は None なら今の値を残す。"""
        if status not in RULE_STATUSES:
            raise ValueError(f"規則の status は {RULE_STATUSES} のどれか: {status!r}")
        self.conn.execute(
            "UPDATE rules SET status=?, approved_by=?, approved_at=datetime('now'), resolution=COALESCE(?, resolution) "
            "WHERE id=?", (status, by, resolution, rule_id))
        if commit:
            self.conn.commit()

    # ---- 訂正(有資格者が下書きを直した記録) ----
    def add_correction(self, sign_id: str, item: str, old_value: str | None, new_value: str, by: str, *,
                       rank_before: int | None = None, rank_after: int | None = None,
                       note: str | None = None, rule_id: int | None = None) -> int:
        cur = self.conn.execute(
            "INSERT INTO corrections(sign_id, item, old_value, new_value, rank_before, rank_after, "
            "corrected_by, note, rule_id) VALUES (?,?,?,?,?,?,?,?,?)",
            (sign_id, item, old_value, new_value, rank_before, rank_after, by, note, rule_id))
        self.conn.commit()
        return cur.lastrowid

    def list_corrections(self, sign_id: str | None = None) -> list[sqlite3.Row]:
        if sign_id is None:
            return list(self.conn.execute("SELECT * FROM corrections ORDER BY id"))
        return list(self.conn.execute("SELECT * FROM corrections WHERE sign_id=? ORDER BY id", (sign_id,)))

    def recent_rules(self, n: int = 20) -> list[str]:
        """承認済み(approved)の最新 n 件の規則の文(古い順)。ClaudeJudge の set_rules に渡す。候補・却下は入れない。"""
        rows = self.conn.execute("SELECT text FROM rules WHERE status='approved' ORDER BY id DESC LIMIT ?", (n,)).fetchall()
        return [r["text"] for r in reversed(rows)]

    # ---- JSON の読み込み(見本データ) ----
    def import_json(self, path: str | Path) -> tuple[int, int]:
        """{"sites": [...], "signs": [...]} を読み込む。戻り値は (現場数, 看板数)。写真パスは JSON のあるフォルダ基準の絶対パスで保存。"""
        p = Path(path).resolve()
        doc = json.loads(p.read_text(encoding="utf-8"))
        for s in doc.get("sites", []):
            self.upsert_site(Site(**s))
        for s in doc.get("signs", []):
            s = dict(s)
            s["photos"] = [str(p.parent / x) for x in s.get("photos", [])]
            self.upsert_sign(Sign(**s))
        return len(doc.get("sites", [])), len(doc.get("signs", []))
