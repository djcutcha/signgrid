"""緯度経度 → 用途地域。国土交通省「都市計画決定GISデータ」の用途地域(*_youto.shp)を読む。

出典・取得日・使用許諾・座標系は docs/GIS.md。データは data/private/gis/(未追跡)に置く:
  data/private/gis/<東京都|埼玉県の展開先>/<県コード_県名>/<市区町村コード_名>/<市区町村コード>_youto.shp
座標系は JGD2011 の経緯度(.prj: GCS_JGD_2011)。WGS84 とのずれは数cm〜数十cm で、用途地域の判定には無視できる。

- 読み込みは pyshp(純 Python。requirements に固定)。import は読み込み時だけで、点の判定(ray casting)は標準ライブラリのみ。
- 1つの用途地域ポリゴンは複数の輪(外周と穴)を持つ。輪の向きに頼らず、全輪に対する even-odd 規則で内外を決める。
- 属性 YoutoName は『第１種低層住居専用地域』のように全角数字・『種』表記。legal の語彙(schema.ZONE_VOCAB、
  『第一種…』)に直して vocab に入れる。語彙に無い名前は vocab=None(判定には渡さない)。
- 結果の status: 'zone'(用途地域に入った) / 'outside'(その市区町村のデータはあるが、どの用途地域にも入らない
  = 市街化調整区域など) / 'no_data'(その市区町村の用途地域データを持っていない、または muni_cd 不明で判別できない)。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from ..legal.schema import ZONE_VOCAB

DEFAULT_ROOT = Path(__file__).resolve().parents[2] / "data" / "private" / "gis"
_KANSUJI = {"1": "一", "2": "二"}

Point = tuple[float, float]       # (経度 x, 緯度 y)
Ring = list[Point]


def normalize_zone(name: str | None) -> str | None:
    """YoutoName -> ZONE_VOCAB の語。『第１種低層住居専用地域』 -> 『第一種低層住居専用地域』。語彙に無ければ None。"""
    s = unicodedata.normalize("NFKC", name or "").strip()
    s = re.sub(r"第([12])種", lambda m: f"第{_KANSUJI[m.group(1)]}種", s)
    return s if s in ZONE_VOCAB else None


def file_code(muni_cd: str) -> str:
    """市区町村コード -> データのファイル名の先頭コード。さいたま市の区(11101〜11110)は市(11100)の1ファイルにまとまっている。"""
    cd = str(muni_cd).zfill(5)
    return "11100" if cd[:3] == "111" and "01" <= cd[3:] <= "10" else cd


def point_in_ring(x: float, y: float, ring: Ring) -> bool:
    """ray casting(+x 方向への半直線が辺と交わる回数の偶奇)。ring は閉じていてもいなくてもよい。"""
    inside = False
    n = len(ring)
    if n < 3:
        return False
    x1, y1 = ring[-1]
    for x2, y2 in ring:
        if (y1 > y) != (y2 > y):
            if x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
                inside = not inside
        x1, y1 = x2, y2
    return inside


@dataclass(frozen=True)
class ZonePolygon:
    name: str                  # 原データの YoutoName
    rings: tuple[tuple[Point, ...], ...]
    muni_cd: str | None = None
    bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)

    @staticmethod
    def make(name: str, rings, muni_cd: str | None = None) -> "ZonePolygon":
        rs = tuple(tuple((float(x), float(y)) for x, y in r) for r in rings)
        xs = [p[0] for r in rs for p in r]
        ys = [p[1] for r in rs for p in r]
        bbox = (min(xs), min(ys), max(xs), max(ys)) if xs else (0.0, 0.0, 0.0, 0.0)
        return ZonePolygon(name, rs, muni_cd, bbox)

    def contains(self, x: float, y: float) -> bool:
        x0, y0, x1, y1 = self.bbox
        if not (x0 <= x <= x1 and y0 <= y <= y1):
            return False
        return sum(point_in_ring(x, y, list(r)) for r in self.rings) % 2 == 1   # even-odd


@dataclass(frozen=True)
class ZoneResult:
    status: str                       # zone / outside / no_data
    name: str | None = None           # 原データの名前(第１種低層住居専用地域)
    vocab: str | None = None          # legal の語彙に直した名前(judge の place に渡せる)
    others: tuple[str, ...] = ()      # 同じ点が別の用途地域にも入っていたときのその名前(境界・データの重なり)


class ZoningIndex:
    def __init__(self, polygons: list[ZonePolygon] | None = None):
        self.polygons: list[ZonePolygon] = list(polygons or [])
        self.covered: set[str] = {p.muni_cd for p in self.polygons if p.muni_cd}

    @classmethod
    def from_polygons(cls, polygons: list[ZonePolygon], covered: set[str] | None = None) -> "ZoningIndex":
        idx = cls(polygons)
        if covered:
            idx.covered |= set(covered)
        return idx

    @classmethod
    def load(cls, root: Path | str = DEFAULT_ROOT, *, encoding: str = "utf-8") -> "ZoningIndex":
        """root 以下の *_youto.shp をすべて読む(ファイル名の先頭5桁が市区町村コード)。"""
        try:
            import shapefile  # pyshp
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("用途地域の読み込みには pyshp が要る(requirements.txt)") from e
        files = sorted(Path(root).rglob("*_youto.shp"))
        if not files:
            raise FileNotFoundError(f"用途地域のデータが無い: {root}(docs/GIS.md の手順で data/private/gis/ に置く)")
        idx = cls()
        for f in files:
            cd = f.name.split("_")[0]
            idx.covered.add(cd)
            with shapefile.Reader(str(f), encoding=encoding) as r:
                names = [fld[0] for fld in r.fields[1:]]
                col = names.index("YoutoName")
                for sr in r.iterShapeRecords():
                    pts, parts = sr.shape.points, list(sr.shape.parts) + [len(sr.shape.points)]
                    rings = [pts[parts[i]:parts[i + 1]] for i in range(len(parts) - 1)]
                    if rings:
                        idx.polygons.append(ZonePolygon.make(str(sr.record[col]), rings, cd))
        return idx

    def lookup(self, lat: float, lng: float, muni_cd: str | None = None) -> ZoneResult:
        hits = [p for p in self.polygons if p.contains(lng, lat)]
        if hits:
            first = hits[0]
            others = tuple(dict.fromkeys(p.name for p in hits[1:] if p.name != first.name))
            return ZoneResult("zone", first.name, normalize_zone(first.name), others)
        if muni_cd is not None and file_code(muni_cd) in self.covered:
            return ZoneResult("outside")
        return ZoneResult("no_data")
