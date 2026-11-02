"""python3 -m kanban_karte.impact.cli count --db <signjobz.db>"""
from __future__ import annotations

import argparse
import sys

from . import count as C


def make_placer(gis_root=None, geocode_cache=None, reverse_cache=None):
    from ..geo import geocode as GC
    from ..geo import zoning as Z
    from ..gov import geocode as GG
    from .place import ZonePlacer
    private = C.Path(__file__).resolve().parents[2] / "data" / "private"
    index = Z.ZoningIndex.load(gis_root or Z.DEFAULT_ROOT)
    geocoder = GC.AddressGeocoder(geocode_cache or GC.DEFAULT_CACHE)
    reverse = GG.Geocoder(reverse_cache or private / "reverse_cache.json")
    return ZonePlacer(geocoder, reverse, index)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.impact.cli", description="自社版『把握の数字』")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("count", help="自家用・許可不要の看板を自治体別に数える(DB は読むだけ)")
    c.add_argument("--db", required=True, help="SignJOBZ 複製の sqlite")
    c.add_argument("--runs-dir", default="runs", help="結果の置き場(件数と率のみ。未追跡)")
    c.add_argument("--agencies", help="代理店リスト(1行1語。既定 data/private/agencies.txt)")
    c.add_argument("--with-zoning", action="store_true",
                   help="直取引の住所を座標化(国土地理院・1秒1回・キャッシュ)→用途地域(国土交通省GIS)→ judge の place に渡して再集計。"
                        "用途地域ベースの暫定(禁止地域は用途地域だけで決まらない)。初回は通信で数分かかる")
    c.add_argument("--gis-root", help="用途地域GISの置き場(既定 data/private/gis)")
    c.add_argument("--geocode-cache", help="住所→座標のキャッシュ(既定 data/private/geocode_cache.json)")
    c.add_argument("--reverse-cache", help="座標→市区町村のキャッシュ(既定 data/private/reverse_cache.json)")
    args = ap.parse_args(argv)
    words = C.load_agency_words(C.Path(args.agencies)) if args.agencies else None
    placer = None
    if args.with_zoning:
        placer = make_placer(args.gis_root, args.geocode_cache, args.reverse_cache)
    rep = C.run(args.db, agency_words=words, placer=placer)
    path = C.write_run(rep, args.runs_dir)
    print(C.render(rep))
    print(f"-> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
