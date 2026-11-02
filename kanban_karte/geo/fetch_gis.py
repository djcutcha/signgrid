"""国土交通省「都市計画決定GISデータ」(埼玉県・東京都)の再取得。出典・使用許諾は docs/GIS.md。

  python3 -m kanban_karte.geo.fetch_gis [--root data/private/gis]

zip を <root>/raw/ に保存 → <root>/saitama/ <root>/tokyo/ に展開 → *_youto.shp の本数を数える。
既にあるものはスキップ(取り直すなら該当フォルダを消す)。標準ライブラリだけ(ポリゴン数を数えるときだけ pyshp)。
"""
from __future__ import annotations

import argparse
import sys
import urllib.request
import zipfile
from pathlib import Path

from ..gov import geocode as GG
from .zoning import DEFAULT_ROOT

BASE = "https://www.mlit.go.jp"
# (展開先フォルダ, zip のパス, docs/GIS.md が書く *_youto.shp の本数)
SOURCES = (
    ("saitama", "/toshi/tosiko/content/001752729.zip", 58),
    ("tokyo", "/toshi/tosiko/content/001752732.zip", 52),
)
EXPECT_POLYGONS = 15_783


def _http_get(url: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": GG.USER_AGENT})
    tmp = dest.with_name(dest.name + ".part")
    with urllib.request.urlopen(req, timeout=120) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    tmp.replace(dest)


def extract_zip(zip_path: Path, dest: Path) -> None:
    """zip を dest に展開。dest の外に出るパスは弾く。"""
    root = dest.resolve()
    with zipfile.ZipFile(zip_path) as z:
        for info in z.infolist():
            if not (root / info.filename).resolve().is_relative_to(root):
                raise ValueError(f"zip に展開先の外へ出るパスがある: {info.filename!r}")
        z.extractall(dest)


def count_shp(folder: Path) -> int:
    return len(list(folder.rglob("*_youto.shp")))


def count_polygons(root: Path) -> int | None:
    """root 以下の用途地域ポリゴンの総数。pyshp が無ければ None。"""
    try:
        import shapefile
    except ImportError:
        return None
    n = 0
    for p in sorted(root.rglob("*_youto.shp")):
        with shapefile.Reader(str(p), encoding="utf-8") as r:
            n += len(r)
    return n


def fetch(root: Path, *, download=_http_get, out=print) -> dict[str, int]:
    """戻り値= {展開先: *_youto.shp の本数}。download(url, dest) は試験で差し替える。"""
    raw = root / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for name, path, _ in SOURCES:
        dest = root / name
        if count_shp(dest):
            out(f"{name}: 展開済み(スキップ)")
        else:
            z = raw / Path(path).name
            if z.exists():
                out(f"{name}: zip あり {z.name}({z.stat().st_size / 1e6:.1f} MB)")
            else:
                out(f"{name}: ダウンロード {BASE}{path}")
                download(BASE + path, z)
                out(f"{name}: {z.name} {z.stat().st_size / 1e6:.1f} MB")
            extract_zip(z, dest)
        counts[name] = count_shp(dest)
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.geo.fetch_gis", description="用途地域GISの再取得")
    ap.add_argument("--root", default=str(DEFAULT_ROOT), help="置き場(既定 data/private/gis)")
    a = ap.parse_args(argv)
    root = Path(a.root)
    counts = fetch(root)
    ok = True
    for name, _, expect in SOURCES:
        flag = "一致" if counts[name] == expect else f"docs は {expect} 本"
        ok &= counts[name] == expect
        print(f"{name}: *_youto.shp {counts[name]} 本({flag})")
    print(f"合計 {sum(counts.values())} 本")
    n = count_polygons(root)
    if n is None:
        print("ポリゴン数: pyshp が無いので数えていない(pip install -r requirements.txt)")
    else:
        flag = "一致" if n == EXPECT_POLYGONS else f"docs は {EXPECT_POLYGONS}"
        ok &= n == EXPECT_POLYGONS
        print(f"ポリゴン数: {n:,}({flag})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
