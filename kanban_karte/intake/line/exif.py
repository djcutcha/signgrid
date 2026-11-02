"""写真の EXIF から GPS(十進度)を読む。無ければ None。"""
from __future__ import annotations

import io

from PIL import Image

_GPS_IFD = 0x8825


def _deg(dms, ref) -> float | None:
    try:
        d, m, s = (float(x) for x in dms)
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    v = d + m / 60.0 + s / 3600.0
    if isinstance(ref, bytes):
        ref = ref.decode("ascii", "ignore")
    if str(ref).strip().upper() in ("S", "W"):
        v = -v
    return v


def gps_from_bytes(data: bytes) -> tuple[float, float] | None:
    """画像のバイト列から (緯度, 経度)。EXIF に GPS が無い・壊れている・範囲外は None。"""
    try:
        with Image.open(io.BytesIO(data)) as im:
            gps = im.getexif().get_ifd(_GPS_IFD)
    except Exception:
        return None
    if not gps or 2 not in gps or 4 not in gps:
        return None
    lat, lng = _deg(gps[2], gps.get(1)), _deg(gps[4], gps.get(3))
    if lat is None or lng is None or not (-90 <= lat <= 90 and -180 <= lng <= 180):
        return None
    if lat == 0 and lng == 0:  # 位置が取れなかった端末が 0,0 を書くことがある
        return None
    return lat, lng


def image_format(data: bytes) -> str | None:
    """画像として開ければ形式名(JPEG/PNG/...)。開けなければ None。"""
    try:
        with Image.open(io.BytesIO(data)) as im:
            im.verify()
            return im.format
    except Exception:
        return None
