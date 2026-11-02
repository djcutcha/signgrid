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

SCHEMA_VERSION = 1
SCHEMA_SQL = Path(__file__).with_name("schema.sql")
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

    def validate(self) -> None:
        for name in ("rank", "prev_rank"):
            v = getattr(self, name)
            if v is not None and (isinstance(v, bool) or v not in (1, 2, 3, 4)):
                raise ValueError(f"{name} は 1〜4 か None: {v!r}")
        self.items = validate_items(self.items)
        if not isinstance(self.photos, list):
            raise ValueError("photos はリスト")


_SIGN_COLS = [f.name for f in fields(Sign)]
_BOOL_COLS = ("is_self_use", "permit_required")


def _tri(v):
    return None if v is None else int(bool(v))


class Ledger:
    """台帳への入口。Ledger(path) か Ledger(":memory:")。"""

    def __init__(self, path: str | Path = ":memory:"):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.ensure_schema()

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
                   kind_counts: dict | None = None, error: str | None = None) -> None:
        sets, args = [], []
        for col, v in (("status", status), ("pages", pages), ("error", error)):
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

    def list_pages(self, bundle_id: int) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM pages WHERE bundle_id=? ORDER BY page_no", (bundle_id,)))

    def kind_counts(self, bundle_id: int) -> dict[str, int]:
        return {r["kind"]: r["n"] for r in self.conn.execute(
            "SELECT kind, COUNT(*) n FROM pages WHERE bundle_id=? GROUP BY kind ORDER BY kind", (bundle_id,))}

    # ---- 規則(有資格者の訂正から育つ) ----
    def add_rule(self, text: str, source: str | None = None, conflicts_with: list[int] | None = None) -> int:
        cur = self.conn.execute("INSERT INTO rules(text, source, conflicts_with) VALUES (?,?,?)",
                                (text, source, json.dumps(conflicts_with or [])))
        self.conn.commit()
        return cur.lastrowid

    def list_rules(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM rules ORDER BY id"))

    # ---- JSON の読み込み(見本データ) ----
    def import_json(self, path: str | Path) -> tuple[int, int]:
        """{"sites": [...], "signs": [...]} を読み込む。戻り値は (現場数, 看板数)。写真パスは JSON のあるフォルダ基準で保存。"""
        p = Path(path)
        doc = json.loads(p.read_text(encoding="utf-8"))
        for s in doc.get("sites", []):
            self.upsert_site(Site(**s))
        for s in doc.get("signs", []):
            s = dict(s)
            s["photos"] = [str(p.parent / x) for x in s.get("photos", [])]
            self.upsert_sign(Sign(**s))
        return len(doc.get("sites", [])), len(doc.get("signs", []))
