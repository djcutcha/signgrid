"""住所 → 設置場所の区分(用途地域)つきの判定。web・寸法役・impact が同じ入口を使う。

make_placer   GIS(data/private/gis/)と国土地理院の住所検索で ZonePlacer を作る。GIS が無ければ None(落ちない)。
judge_at      住所から自治体 → 用途地域を引き、引けたら judge の place に渡す。引けなかったとき
              (outside / no_data / not_found / error / mismatch / placer なし)は区分なしで判定する。

用途地域ベースの暫定(docs/GIS.md): 禁止区域のうち用途地域だけで決まらないもの(風致地区 等)は未判定。
"""
from __future__ import annotations

from pathlib import Path

from . import judge as J
from .municipality import AddressResolver

PLACE_SOURCE = "国土交通省 都市計画決定GIS(用途地域ベースの暫定)"


def make_placer(gis_root=None, geocode_cache=None, reverse_cache=None):
    """ZonePlacer を作る。用途地域の GIS が無い・読めないときは None。"""
    from ..geo import geocode as GC
    from ..geo import zoning as Z
    from ..gov import geocode as GG
    from ..impact.place import ZonePlacer
    root = Path(gis_root or Z.DEFAULT_ROOT)
    if not any(root.rglob("*_youto.shp")):
        return None
    try:
        index = Z.ZoningIndex.load(root)
    except Exception:   # pyshp が無い・壊れた shp。区分なしで動き続ける
        return None
    private = Path(__file__).resolve().parents[2] / "data" / "private"
    geocoder = GC.AddressGeocoder(geocode_cache or GC.DEFAULT_CACHE)
    reverse = GG.Geocoder(reverse_cache or private / "reverse_cache.json")
    return ZonePlacer(geocoder, reverse, index)


def judge_at(address: str | None, *, use: str, area_m2: float | None, height_m: float | None = None,
             kind: str | None = None, placer=None):
    """(JudgeResult, Placed | None)。Placed は placer を使って場所を引こうとしたときだけ(住所なし・placer なしは None)。"""
    muni = AddressResolver().resolve(address) if address else None
    placed = placer.place(address, muni) if (placer is not None and address) else None
    place = placed.vocab if placed is not None and placed.status == "zone" else None
    r = J.judge(address, municipality=muni, use=use, area_m2=area_m2, height_m=height_m, place=place, kind=kind)
    return r, placed


def place_info(placed) -> dict:
    """画面用 {"status", "zone", "source"}。placed が None(引こうとしなかった)は status='none'。"""
    if placed is None:
        return {"status": "none", "zone": None, "source": PLACE_SOURCE}
    zone = placed.vocab if placed.status == "zone" else None
    return {"status": placed.status, "zone": zone, "source": PLACE_SOURCE}
