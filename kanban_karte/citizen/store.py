"""市民からの報告の保存(data/private/citizen_reports.sqlite、環境変数 KK_CITIZEN_DB)と、台帳の看板との照合。

- 写真は data/private/citizen_photos/<受付番号>.jpg に、長辺 1600px の JPEG で保存し直す(EXIF は書かない= 端末の位置や機種は残らない)。
  写真は公開しない(地図・状態の確認には出さない)。顔やナンバーの自動処理はしない(公開しないため)。
- 位置はブラウザの位置情報(写真の EXIF の位置は使わない)。地図に出すのは小数 3 桁に丸めた位置(約 100 m)だけ。
- 状態: 受付(位置が無く照合できない)/ 把握済み(台帳の看板が 30 m 以内)/ 未把握(台帳に近い看板が無い)/ 点検済み
  (一致した台帳の看板の最終点検日が報告の日以降)。照合は gov.match.nearest_sign(職員の巡回の候補照合と同じ 30 m)。
"""
from __future__ import annotations

import io
import json
import os
import secrets
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from PIL import Image

from ..gov.match import MATCH_RADIUS_M, nearest_sign

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = Path(os.environ.get("KK_CITIZEN_DB") or REPO_ROOT / "data" / "private" / "citizen_reports.sqlite")

RECEIVED, KNOWN, UNKNOWN, INSPECTED = "受付", "把握済み", "未把握", "点検済み"
STATUSES = (RECEIVED, KNOWN, UNKNOWN, INSPECTED)
SIGNS = ("錆", "傾き", "剥がれ", "ぐらつき", "その他")      # 「気になる所」のチェック
STATUS_TEXT = {   # 市民に返す文(無許可の疑いなどの判断は書かない)
    RECEIVED: "受け付けました。位置が無いため、担当の窓口が住所から看板を確かめます。",
    KNOWN: "受け付けました。台帳に載っている看板です。点検の予定を確かめます。",
    UNKNOWN: "受け付けました。担当の窓口が、どの看板か確かめます。",
    INSPECTED: "この看板は、報告の後に点検されました。",
}
CODE_ALPHABET = "ACDEFGHJKLMNPQRTUVWXY34679"   # 読み違えやすい 0/O・1/I/L・2/Z・5/S・8/B を外す
MAX_PHOTO_BYTES = 8_000_000
MAX_NOTE = 200
MAX_ADDRESS = 120
LONG_EDGE = 1600
PUBLIC_DIGITS = 3

SCHEMA = """
CREATE TABLE IF NOT EXISTS citizen_reports (
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  code            TEXT NOT NULL UNIQUE,      -- 受付番号(合言葉)。状態の確認に使う
  created_at      TEXT NOT NULL,             -- 端末ではなくサーバの現地時刻(秒まで)
  lat REAL, lng REAL,                        -- ブラウザの位置情報(写真の EXIF は使わない)
  lat_pub REAL, lng_pub REAL,                -- 地図に出す位置(小数 3 桁に丸め)
  address         TEXT NOT NULL DEFAULT '',  -- 位置が取れない時の手入力
  photo_path      TEXT NOT NULL DEFAULT '',  -- citizen_photos/ からの相対(公開しない)
  note            TEXT NOT NULL DEFAULT '',  -- 一言(公開しない)
  signs           TEXT NOT NULL DEFAULT '[]',-- JSON: 気になる所
  status          TEXT NOT NULL DEFAULT '受付',
  matched_sign_id TEXT                       -- 一致した台帳の看板
);
"""


class ReportError(ValueError):
    """入力の誤り(画面に出す文)。"""


@dataclass
class Report:
    id: int
    code: str
    created_at: str
    lat: float | None
    lng: float | None
    lat_pub: float | None
    lng_pub: float | None
    address: str
    photo_path: str
    note: str
    signs: list
    status: str
    matched_sign_id: str | None

    def public(self) -> dict:
        """状態の確認で返してよい物だけ(写真・一言・位置の詳細は返さない)。"""
        return {"code": self.code, "status": self.status, "text": STATUS_TEXT[self.status], "date": self.created_at[:10]}

    def pin(self) -> dict | None:
        """地図のピン(丸めた位置・状態・日付だけ)。位置が無ければ None。"""
        if self.lat_pub is None or self.lng_pub is None:
            return None
        return {"lat": self.lat_pub, "lng": self.lng_pub, "status": self.status, "date": self.created_at[:10]}


def new_code() -> str:
    s = "".join(secrets.choice(CODE_ALPHABET) for _ in range(8))
    return f"{s[:4]}-{s[4:]}"


def normalize_code(code: str) -> str:
    c = "".join(ch for ch in str(code or "").upper() if ch.isalnum())
    return f"{c[:4]}-{c[4:]}" if len(c) == 8 else ""


def clean_photo(raw: bytes) -> bytes:
    """写真を開けるか確かめ、長辺 1600px の JPEG に保存し直す(EXIF は捨てる)。"""
    if not raw or len(raw) > MAX_PHOTO_BYTES:
        raise ReportError(f"写真が空か大きすぎます(上限 {MAX_PHOTO_BYTES // 1_000_000} MB)")
    try:
        im = Image.open(io.BytesIO(raw))
        if im.format not in ("JPEG", "PNG", "WEBP"):
            raise ReportError("写真は JPEG / PNG / WebP にしてください")
        im.load()
    except ReportError:
        raise
    except Exception:
        raise ReportError("写真を開けませんでした")
    im = im.convert("RGB")
    im.thumbnail((LONG_EDGE, LONG_EDGE))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=85)   # exif を渡さない= 位置・機種・時刻は残らない
    return buf.getvalue()


def match_status(lat, lng, address: str, created_at: str, signs: list) -> tuple[str, str | None]:
    """(状態, 一致した台帳の看板 id)。位置があれば 30 m 以内の最近傍、無ければ住所の一致。"""
    sign = None
    if lat is not None and lng is not None:
        sign, _ = nearest_sign(lat, lng, signs, MATCH_RADIUS_M)
        if sign is None:
            return UNKNOWN, None
    else:
        a = "".join((address or "").split())
        sign = next((s for s in signs if a and s.address and "".join(s.address.split()) == a), None)
        if sign is None:
            return RECEIVED, None
    if sign.last_inspected and sign.last_inspected[:10] >= created_at[:10]:
        return INSPECTED, sign.id
    return KNOWN, sign.id


class Store:
    def __init__(self, path: str | Path = DEFAULT_DB, photo_dir: str | Path | None = None):
        self.path = Path(path)
        self.photo_dir = Path(photo_dir) if photo_dir else self.path.parent / "citizen_photos"
        self._lock = threading.Lock()

    def _conn(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        c = sqlite3.connect(self.path)
        c.row_factory = sqlite3.Row
        c.executescript(SCHEMA)
        return c

    @staticmethod
    def _row(r) -> Report:
        d = dict(r)
        d["signs"] = json.loads(d["signs"] or "[]")
        return Report(**d)

    def add(self, photo: bytes, *, lat=None, lng=None, address: str = "", note: str = "", signs=(),
            ledger_signs: list | None = None, created_at: str | None = None, status: str | None = None) -> Report:
        """1 件を保存する。lat/lng(ブラウザの位置)か address のどちらかが要る。ledger_signs があれば照合して状態を付ける。
        status は見本データ用(照合の結果を上書きする)。"""
        lat, lng = _coord(lat, -90, 90, "lat"), _coord(lng, -180, 180, "lng")
        if (lat is None) != (lng is None):
            raise ReportError("位置は緯度と経度の両方が要ります")
        address = str(address or "").strip()[:MAX_ADDRESS]
        if lat is None and not address:
            raise ReportError("位置が取れない時は、住所(だいたいの場所)を入れてください")
        note = str(note or "").strip()[:MAX_NOTE]
        if not isinstance(signs, (list, tuple)) or any(s not in SIGNS for s in signs):
            raise ReportError(f"気になる所は {'・'.join(SIGNS)} から選んでください")
        jpeg = clean_photo(photo)
        created = created_at or datetime.now().isoformat(timespec="seconds")
        st, sid = match_status(lat, lng, address, created, ledger_signs or [])
        if status is not None:
            if status not in STATUSES:
                raise ReportError(f"status は {STATUSES}")
            st = status
        pub = (None, None) if lat is None else (round(lat, PUBLIC_DIGITS), round(lng, PUBLIC_DIGITS))
        self.photo_dir.mkdir(parents=True, exist_ok=True)
        with self._lock, self._conn() as c:
            for _ in range(5):
                code = new_code()
                if c.execute("SELECT 1 FROM citizen_reports WHERE code=?", (code,)).fetchone() is None:
                    break
            rel = f"{code}.jpg"
            (self.photo_dir / rel).write_bytes(jpeg)
            cur = c.execute("INSERT INTO citizen_reports (code, created_at, lat, lng, lat_pub, lng_pub, address, photo_path, note, signs, status, matched_sign_id) "
                            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                            (code, created, lat, lng, pub[0], pub[1], address, rel, note, json.dumps(list(signs), ensure_ascii=False), st, sid))
            rid = cur.lastrowid
        return self.get(rid)

    def get(self, rid: int) -> Report | None:
        with self._conn() as c:
            r = c.execute("SELECT * FROM citizen_reports WHERE id=?", (rid,)).fetchone()
        return self._row(r) if r else None

    def by_code(self, code: str) -> Report | None:
        code = normalize_code(code)
        if not code:
            return None
        with self._conn() as c:
            r = c.execute("SELECT * FROM citizen_reports WHERE code=?", (code,)).fetchone()
        return self._row(r) if r else None

    def all(self) -> list[Report]:
        if not self.path.exists():
            return []
        with self._conn() as c:
            return [self._row(r) for r in c.execute("SELECT * FROM citizen_reports ORDER BY id")]

    def rematch(self, ledger_signs: list) -> int:
        """台帳の看板と照らし直す(台帳に看板が増えた・点検された時)。点検済みは戻さない。変わった件数を返す。"""
        n = 0
        with self._lock, self._conn() as c:
            for r in [self._row(x) for x in c.execute("SELECT * FROM citizen_reports WHERE status != ?", (INSPECTED,))]:
                st, sid = match_status(r.lat, r.lng, r.address, r.created_at, ledger_signs)
                if (st, sid) != (r.status, r.matched_sign_id):
                    c.execute("UPDATE citizen_reports SET status=?, matched_sign_id=? WHERE id=?", (st, sid, r.id))
                    n += 1
        return n

    def summary(self) -> dict:
        """地図の層と /gov/ の件数(ピンと件数だけ。写真・一言・住所は出さない)。"""
        rows = self.all()
        by = {s: 0 for s in STATUSES}
        for r in rows:
            by[r.status] += 1
        pins = [p for p in (r.pin() for r in rows) if p]
        return {"total": len(rows), "by_status": by, "pins": pins, "no_position": len(rows) - len(pins)}


def _coord(v, lo: float, hi: float, name: str) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, bool):
        raise ReportError(f"{name} は数値")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ReportError(f"{name} は数値")
    if not lo <= f <= hi:
        raise ReportError(f"{name} が範囲の外")
    return f
