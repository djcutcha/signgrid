"""スマホからの投入: 写真(縮小済みの JPEG など)と、端末側で決めた位置を受け取り、位置で候補看板に束ねて gov_ の表に足す。

- 位置は呼び出し側(ブラウザ)が写真の EXIF か端末の測位から決めて、写真ごとに渡す。縮小で EXIF は落ちているので、ここでは読まない。
- 束ね方と保存先の表は gov.batch_intake と同じ(bundle_photos / gov_ の表)。写真は <保存先>/<束id>/NNN.<拡張子> に置く。
- 内容が同じ写真(sha256)は取り込み済みとして飛ばす。全部が取り込み済みなら束は作らない。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import sqlite3
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from .batch_intake import PhotoMeta, bundle_photos, ensure_gov_schema, valid_position
from .geocode import Geocoder, geocode_candidates

MAX_UPLOAD_PHOTOS = 30
MAX_PHOTO_BYTES = 12_000_000
POSITION_SOURCES = ("exif", "device")   # 写真の EXIF / 端末の測位。それ以外(申告なし)は "given"
_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}


class UploadError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def _number(v, name: str) -> float | None:
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise UploadError(400, f"{name} は数値")
    return float(v)


def _taken(v) -> str | None:
    """ISO 8601 → タイムゾーン無しの ISO(束ねの時間差は端末の現地時刻のまま比べる)。読めなければ None。"""
    if not isinstance(v, str) or not v.strip():
        return None
    try:
        return datetime.fromisoformat(v.strip().replace("Z", "+00:00")).replace(tzinfo=None).isoformat(timespec="seconds")
    except ValueError:
        return None


def parse_photos(body: dict) -> list[dict]:
    """検証して [{bytes, ext, sha256, taken_at, lat, lng, source}] にする。1枚でも不正なら全体を断る。"""
    raw = body.get("photos")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_UPLOAD_PHOTOS:
        raise UploadError(400, f"photos は 1〜{MAX_UPLOAD_PHOTOS} 枚の配列")
    out = []
    for i, x in enumerate(raw, 1):
        if not isinstance(x, dict) or not isinstance(x.get("data"), str):
            raise UploadError(400, f"{i} 枚目: data は base64 の文字列")
        try:
            b = base64.b64decode(x["data"].split(",", 1)[-1], validate=True)
        except (ValueError, binascii.Error):
            raise UploadError(400, f"{i} 枚目: data が base64 ではありません")
        if not b or len(b) > MAX_PHOTO_BYTES:
            raise UploadError(400, f"{i} 枚目: 写真が空か大きすぎます(1枚 {MAX_PHOTO_BYTES // 1_000_000}MB まで)")
        try:
            with Image.open(io.BytesIO(b)) as im:
                im.verify()
                fmt = im.format
        except (UnidentifiedImageError, OSError, SyntaxError):
            raise UploadError(400, f"{i} 枚目: 画像として開けません")
        if fmt not in _EXT:
            raise UploadError(400, f"{i} 枚目: JPEG / PNG / WEBP だけ送れます")
        lat, lng = _number(x.get("lat"), f"{i} 枚目の lat"), _number(x.get("lng"), f"{i} 枚目の lng")
        if (lat is None) != (lng is None):
            raise UploadError(400, f"{i} 枚目: lat と lng は両方か、両方なし")
        if lat is not None and not valid_position(lat, lng):
            lat = lng = None    # 範囲外と (0,0) は位置なし(EXIF の扱いと同じ)
        src = x.get("position_source")
        taken = _taken(x.get("taken_at"))
        # 重複判定は「内容 + 位置 + 撮影時刻」。縮小で EXIF が落ちるので、見た目が同じでも別の場所・時刻の写真は別物として扱う
        key = hashlib.sha256(b + f"|{lat}|{lng}|{taken}".encode()).hexdigest()
        out.append({"bytes": b, "ext": _EXT[fmt], "sha256": key, "taken_at": taken,
                    "lat": lat, "lng": lng, "source": (src if src in POSITION_SOURCES else "given") if lat is not None else None})
    return out


def upload(conn: sqlite3.Connection, body: dict, store: Path, geocoder: Geocoder | None) -> dict:
    """検証 → 束ねる → 保存 → 自治体の判定。geocoder が None なら判定しない(あとで gov.cli geocode)。"""
    parsed = parse_photos(body)
    name = str(body.get("batch_name") or "").strip()[:80] or None
    ensure_gov_schema(conn)
    known = {r[0] for r in conn.execute("SELECT sha256 FROM gov_photos")}
    fresh, seen = [], set()
    for p in parsed:
        if p["sha256"] not in known and p["sha256"] not in seen:
            seen.add(p["sha256"])
            fresh.append(p)
    skipped = len(parsed) - len(fresh)
    if not fresh:
        raise UploadError(409, "送った写真はすべて取り込み済みです")
    cur = conn.execute("INSERT INTO gov_batches(dir, name, n_files, n_skipped) VALUES ('', ?, ?, ?)", (name, len(fresh), skipped))
    bid = cur.lastrowid
    folder = Path(store) / str(bid)
    folder.mkdir(parents=True, exist_ok=True)
    metas = []
    for n, p in enumerate(fresh, 1):
        rel = f"{n:03d}{p['ext']}"
        (folder / rel).write_bytes(p["bytes"])
        metas.append(PhotoMeta(str((folder / rel).resolve()), rel, p["sha256"], p["taken_at"], p["lat"], p["lng"]))
    cands, no_pos = bundle_photos(metas)
    conn.execute("UPDATE gov_batches SET dir=?, n_gps=?, n_nogps=? WHERE id=?",
                 (str(folder.resolve()), len(metas) - len(no_pos), len(no_pos), bid))
    ins = "INSERT INTO gov_photos(batch_id, candidate_id, path, rel, sha256, taken_at, lat, lng) VALUES (?,?,?,?,?,?,?,?)"
    for c in cands:
        cc = conn.execute("INSERT INTO gov_candidates(batch_id, lat, lng, n_photos, first_taken, last_taken) VALUES (?,?,?,?,?,?)",
                          (bid, c.lat, c.lng, len(c.photos), c.first_taken, c.last_taken))
        for m in c.photos:
            conn.execute(ins, (bid, cc.lastrowid, m.path, m.rel, m.sha256, m.taken_at, m.lat, m.lng))
    for m in no_pos:
        conn.execute(ins, (bid, None, m.path, m.rel, m.sha256, m.taken_at, None, None))
    conn.commit()
    pending = 0
    if geocoder is not None and cands:
        _, pending = geocode_candidates(conn, geocoder)
    srcs = {"exif": 0, "device": 0, "given": 0, "none": len(no_pos)}
    for p in fresh:
        if p["source"]:
            srcs[p["source"]] += 1
    return {"batch_id": bid, "candidates": len(cands), "no_position": len(no_pos), "photos": len(fresh), "skipped": skipped,
            "position_sources": srcs, "geocode_pending": pending}
