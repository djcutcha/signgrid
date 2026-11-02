"""巡回写真の一括取り込み: EXIF の GPS と撮影時刻を読み、位置で看板ごと(候補看板)に束ねる。

- JPEG / HEIC を対象にする。HEIC は pillow-heif が入っているときだけ読める(無ければ「読めない」として報告し、黙って捨てない)。
- GPS が無い(または 0,0 など不正な)写真は「位置なし」で別扱い。候補看板にはせず、1枚ずつ一覧に出す。
- 束ね方: 撮影時刻順に1枚ずつ見て、既存の束の重心から半径 15m 以内、かつ直前の撮影から 10 分以内なら同じ束に入れる。
  両方に合う束が複数あれば近い方。合う束が無ければ新しい束(=仮置きの看板1基)。
  撮影時刻が無い写真は距離だけで判定する。
"""
from __future__ import annotations

import hashlib
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

BUNDLE_RADIUS_M = 15.0
BUNDLE_GAP_SEC = 600  # 同じ看板とみなす撮影間隔の上限

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".heic", ".heif"}

_TAG_GPS_IFD = 0x8825
_TAG_EXIF_IFD = 0x8769
_TAG_DATETIME_ORIGINAL = 0x9003
_TAG_DATETIME_DIGITIZED = 0x9004
_TAG_DATETIME = 0x0132

try:  # HEIC は任意依存(requirements-gov.txt)
    import pillow_heif  # type: ignore

    pillow_heif.register_heif_opener()
    HEIC_AVAILABLE = True
except Exception:  # noqa: BLE001  未導入・読み込み失敗のどちらも「使えない」で同じ
    HEIC_AVAILABLE = False


@dataclass
class PhotoMeta:
    path: str                      # 絶対パス
    rel: str                       # 取り込みフォルダからの相対パス
    sha256: str
    taken_at: str | None = None    # ISO 8601(タイムゾーン無し・端末の現地時刻のまま)
    lat: float | None = None
    lng: float | None = None

    @property
    def has_gps(self) -> bool:
        return self.lat is not None and self.lng is not None


@dataclass
class Candidate:
    """候補看板=位置で束ねた写真の群れ(1束=1基の仮置き)。"""
    photos: list[PhotoMeta] = field(default_factory=list)
    lat: float = 0.0
    lng: float = 0.0

    def add(self, p: PhotoMeta) -> None:
        n = len(self.photos)
        self.lat = (self.lat * n + p.lat) / (n + 1)
        self.lng = (self.lng * n + p.lng) / (n + 1)
        self.photos.append(p)

    @property
    def first_taken(self) -> str | None:
        ts = [p.taken_at for p in self.photos if p.taken_at]
        return min(ts) if ts else None

    @property
    def last_taken(self) -> str | None:
        ts = [p.taken_at for p in self.photos if p.taken_at]
        return max(ts) if ts else None


@dataclass
class ScanResult:
    photos: list[PhotoMeta] = field(default_factory=list)
    unreadable: list[tuple[str, str]] = field(default_factory=list)  # (相対パス, 理由)


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """2点間の距離(m)。"""
    r = 6371008.8
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = p2 - p1
    dl = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(a)))


def _dms_to_deg(dms, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in dms)
    except (TypeError, ValueError):
        return None
    v = d + m / 60 + s / 3600
    if isinstance(ref, bytes):
        ref = ref.decode("ascii", "ignore")
    if str(ref).strip().upper() in ("S", "W"):
        v = -v
    return v


def _parse_exif_time(s) -> str | None:
    if isinstance(s, bytes):
        s = s.decode("ascii", "ignore")
    if not isinstance(s, str):
        return None
    s = s.strip().strip("\x00")
    for fmt in ("%Y:%m:%d %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).isoformat(timespec="seconds")
        except ValueError:
            continue
    return None


def valid_position(lat: float, lng: float) -> bool:
    """範囲外と (0,0)(GPS が測位前に書く既定値)は位置なし扱い。"""
    if not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return False
    return not (abs(lat) < 1e-9 and abs(lng) < 1e-9)


def read_exif(path: Path) -> tuple[str | None, float | None, float | None]:
    """(撮影時刻, 緯度, 経度)。読めない項目は None。画像が開けなければ例外(呼び出し側で「読めない」にする)。"""
    with Image.open(path) as im:
        ex = im.getexif()
        taken = None
        try:
            sub = ex.get_ifd(_TAG_EXIF_IFD)
        except Exception:  # noqa: BLE001
            sub = {}
        for raw in (sub.get(_TAG_DATETIME_ORIGINAL), sub.get(_TAG_DATETIME_DIGITIZED), ex.get(_TAG_DATETIME)):
            taken = _parse_exif_time(raw)
            if taken:
                break
        lat = lng = None
        try:
            gps = ex.get_ifd(_TAG_GPS_IFD)
        except Exception:  # noqa: BLE001
            gps = {}
        if gps and 2 in gps and 4 in gps:
            lat = _dms_to_deg(gps.get(2), gps.get(1))
            lng = _dms_to_deg(gps.get(4), gps.get(3))
        if lat is None or lng is None or not valid_position(lat, lng):
            lat = lng = None
    return taken, lat, lng


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_folder(folder: str | Path) -> ScanResult:
    """フォルダ(下位も)の JPEG/HEIC を読む。画像でない拡張子は無視、読めない画像は unreadable に理由つきで残す。"""
    root = Path(folder).resolve()
    res = ScanResult()
    for p in sorted(root.rglob("*")):
        if not p.is_file() or p.suffix.lower() not in IMAGE_SUFFIXES or p.name.startswith("."):
            continue
        rel = p.relative_to(root).as_posix()
        if p.suffix.lower() in (".heic", ".heif") and not HEIC_AVAILABLE:
            res.unreadable.append((rel, "HEIC を読むには pillow-heif が要る(requirements-gov.txt)"))
            continue
        try:
            taken, lat, lng = read_exif(p)
            res.photos.append(PhotoMeta(str(p), rel, _sha256(p), taken, lat, lng))
        except (UnidentifiedImageError, OSError, ValueError) as e:
            res.unreadable.append((rel, f"画像として開けない: {e}"))
    return res


def _gap_sec(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return abs((datetime.fromisoformat(a) - datetime.fromisoformat(b)).total_seconds())


def bundle_photos(photos: list[PhotoMeta], radius_m: float = BUNDLE_RADIUS_M,
                  gap_sec: float = BUNDLE_GAP_SEC) -> tuple[list[Candidate], list[PhotoMeta]]:
    """位置のある写真を候補看板に束ねる。戻り値は (候補看板のリスト, 位置なしの写真のリスト)。"""
    with_pos = [p for p in photos if p.has_gps]
    no_pos = sorted((p for p in photos if not p.has_gps), key=lambda p: (p.taken_at or "", p.rel))
    # 撮影時刻順。時刻の無い写真は最後(相対パス順)
    with_pos.sort(key=lambda p: (p.taken_at is None, p.taken_at or "", p.rel))
    cands: list[Candidate] = []
    last_taken: list[str | None] = []  # 各束の直近の撮影時刻
    for p in with_pos:
        best, best_d = None, None
        for i, c in enumerate(cands):
            d = haversine_m(c.lat, c.lng, p.lat, p.lng)
            if d > radius_m:
                continue
            g = _gap_sec(last_taken[i], p.taken_at)
            if g is not None and g > gap_sec:
                continue
            if best_d is None or d < best_d:
                best, best_d = i, d
        if best is None:
            c = Candidate()
            c.add(p)
            cands.append(c)
            last_taken.append(p.taken_at)
        else:
            cands[best].add(p)
            if p.taken_at:
                last_taken[best] = p.taken_at
    return cands, no_pos


# ---- 保存(台帳と同じ SQLite に gov_ 接頭辞の表を足す。台帳のスキーマには触らない) ----
GOV_SCHEMA = """
CREATE TABLE IF NOT EXISTS gov_batches (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    dir         TEXT NOT NULL,
    n_files     INTEGER NOT NULL DEFAULT 0,   -- 新しく取り込んだ写真の枚数
    n_gps       INTEGER NOT NULL DEFAULT 0,
    n_nogps     INTEGER NOT NULL DEFAULT 0,
    n_skipped   INTEGER NOT NULL DEFAULT 0,   -- 取り込み済み(同じ内容)で飛ばした枚数
    n_unreadable INTEGER NOT NULL DEFAULT 0,
    created     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS gov_candidates (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES gov_batches(id),
    lat         REAL NOT NULL,
    lng         REAL NOT NULL,
    n_photos    INTEGER NOT NULL,
    first_taken TEXT,
    last_taken  TEXT,
    geocoded    INTEGER NOT NULL DEFAULT 0,   -- 逆ジオコードで答えを得たか(圏外の「答えなし」も1)
    muni_cd     TEXT,
    pref        TEXT,
    city        TEXT
);
CREATE TABLE IF NOT EXISTS gov_photos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    batch_id    INTEGER NOT NULL REFERENCES gov_batches(id),
    candidate_id INTEGER REFERENCES gov_candidates(id),  -- 位置なしは NULL
    path        TEXT NOT NULL,
    rel         TEXT NOT NULL,
    sha256      TEXT NOT NULL UNIQUE,         -- 同じ写真の二重取り込みを防ぐ
    taken_at    TEXT,
    lat         REAL,
    lng         REAL
);
CREATE INDEX IF NOT EXISTS idx_gov_photos_cand ON gov_photos(candidate_id);
"""


def ensure_gov_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(GOV_SCHEMA)
    if "name" not in {r[1] for r in conn.execute("PRAGMA table_info(gov_batches)")}:   # スマホ投入の束の名前(あとから足した列)
        conn.execute("ALTER TABLE gov_batches ADD COLUMN name TEXT")
    conn.commit()


@dataclass
class IntakeReport:
    batch_id: int
    new_photos: int
    skipped_known: int
    with_gps: int
    no_gps: int
    candidates: int
    unreadable: list[tuple[str, str]]


def intake(conn: sqlite3.Connection, folder: str | Path, *, radius_m: float = BUNDLE_RADIUS_M,
           gap_sec: float = BUNDLE_GAP_SEC) -> IntakeReport:
    """フォルダを読み、新しい写真だけを束ねて保存する(内容が同じ写真は取り込み済みとして飛ばす)。
    逆ジオコード(自治体の判定)はここでは行わない。geocode.geocode_candidates を後で呼ぶ。"""
    ensure_gov_schema(conn)
    scan = scan_folder(folder)
    known = {r[0] for r in conn.execute("SELECT sha256 FROM gov_photos")}
    fresh, seen = [], set()
    for p in scan.photos:
        if p.sha256 in known or p.sha256 in seen:
            continue
        seen.add(p.sha256)
        fresh.append(p)
    skipped = len(scan.photos) - len(fresh)
    cands, no_pos = bundle_photos(fresh, radius_m, gap_sec)
    cur = conn.execute(
        "INSERT INTO gov_batches(dir, n_files, n_gps, n_nogps, n_skipped, n_unreadable) VALUES (?,?,?,?,?,?)",
        (str(Path(folder).resolve()), len(fresh), len(fresh) - len(no_pos), len(no_pos), skipped, len(scan.unreadable)))
    bid = cur.lastrowid
    ins = ("INSERT INTO gov_photos(batch_id, candidate_id, path, rel, sha256, taken_at, lat, lng) "
           "VALUES (?,?,?,?,?,?,?,?)")
    for c in cands:
        cc = conn.execute(
            "INSERT INTO gov_candidates(batch_id, lat, lng, n_photos, first_taken, last_taken) VALUES (?,?,?,?,?,?)",
            (bid, c.lat, c.lng, len(c.photos), c.first_taken, c.last_taken))
        for p in c.photos:
            conn.execute(ins, (bid, cc.lastrowid, p.path, p.rel, p.sha256, p.taken_at, p.lat, p.lng))
    for p in no_pos:
        conn.execute(ins, (bid, None, p.path, p.rel, p.sha256, p.taken_at, None, None))
    conn.commit()
    return IntakeReport(bid, len(fresh), skipped, len(fresh) - len(no_pos), len(no_pos), len(cands), scan.unreadable)
