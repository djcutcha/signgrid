"""gov モジュールの CLI。

  python3 -m kanban_karte.gov.cli intake <写真フォルダ> --db <sqlite> [--no-geocode]
  python3 -m kanban_karte.gov.cli geocode --db <sqlite>          # 自治体が未判定の候補をやり直す
  python3 -m kanban_karte.gov.cli match --db <sqlite> [--today YYYY-MM-DD]
  python3 -m kanban_karte.gov.cli map --db <sqlite> --out map.html [--today YYYY-MM-DD]

--db は台帳の SQLite(signs が入っているもの)。巡回写真の表(gov_*)は同じファイルに足す。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

from ..ledger import Ledger
from .batch_intake import BUNDLE_GAP_SEC, BUNDLE_RADIUS_M, intake
from .geocode import Geocoder, geocode_candidates
from .map import write_map
from .match import MATCH_RADIUS_M, WARN_DAYS, load_candidates, match_candidates


def _geocoder(args) -> Geocoder:
    cache = Path(args.geocache) if args.geocache else Path(str(args.db) + ".geocache.json")
    return Geocoder(cache)


def _today(args) -> date:
    return date.fromisoformat(args.today) if args.today else date.today()


def _results(lg: Ledger, args):
    return match_candidates(load_candidates(lg.conn), lg.list_signs(), _today(args),
                            radius_m=args.match_radius, warn_days=args.warn_days)


def cmd_intake(args) -> int:
    folder = Path(args.dir)
    if not folder.is_dir():
        print(f"フォルダが無い: {folder}", file=sys.stderr)
        return 2
    with Ledger(args.db) as lg:
        rep = intake(lg.conn, folder, radius_m=args.radius, gap_sec=args.gap)
        print(f"取り込み: 新規 {rep.new_photos} 枚(位置あり {rep.with_gps} / 位置なし {rep.no_gps})、"
              f"取り込み済みで飛ばした {rep.skipped_known} 枚 → 看板の候補 {rep.candidates} 基")
        for rel, why in rep.unreadable:
            print(f"  読めない: {rel} ({why})", file=sys.stderr)
        if not args.no_geocode and rep.candidates:
            ok, ng = geocode_candidates(lg.conn, _geocoder(args))
            print(f"自治体の判定: {ok} 基" + (f"、通信できず未判定 {ng} 基(geocode で再実行)" if ng else ""))
    return 0 if not rep.unreadable else 1


def cmd_geocode(args) -> int:
    with Ledger(args.db) as lg:
        ok, ng = geocode_candidates(lg.conn, _geocoder(args))
    print(f"自治体の判定: {ok} 基" + (f"、通信できず未判定 {ng} 基" if ng else ""))
    return 0 if not ng else 1


def cmd_match(args) -> int:
    with Ledger(args.db) as lg:
        for r in _results(lg, args):
            c = r.cand
            pos = "位置なし" if c.lat is None else f"{c.lat:.5f},{c.lng:.5f}"
            who = f"{r.sign.id} {r.sign.name or ''}" if r.sign else "-"
            print(f"{r.color:6} {r.status:18} {pos:20} 写真{len(c.photos):2} {who}  {r.note}")
    return 0


def cmd_map(args) -> int:
    if not Path(args.db).exists():
        print(f"台帳が無い: {args.db}", file=sys.stderr)
        return 2
    with Ledger(args.db) as lg:
        results = _results(lg, args)
        sm = write_map(args.out, results, today=_today(args), ledger_signs=lg.list_signs(), warn_days=args.warn_days)
    bc = sm["by_color"]
    print(f"{args.out}: 赤 {bc['red']} / 橙 {bc['orange']} / 緑 {bc['green']} / 灰 {bc['gray']}(計 {sm['total']})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.gov.cli", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, geo=False, matching=False):
        p.add_argument("--db", required=True, help="台帳の SQLite")
        if geo:
            p.add_argument("--geocache", help="逆ジオコードのキャッシュ JSON(既定: <db>.geocache.json)")
        if matching:
            p.add_argument("--today", help="期限の判定に使う基準日 YYYY-MM-DD(既定: 今日)")
            p.add_argument("--match-radius", type=float, default=MATCH_RADIUS_M, help="台帳との一致半径 m")
            p.add_argument("--warn-days", type=int, default=WARN_DAYS, help="期限が近いとみなす日数")

    p = sub.add_parser("intake", help="写真フォルダを取り込み、位置で候補看板に束ねる")
    p.add_argument("dir")
    common(p, geo=True)
    p.add_argument("--radius", type=float, default=BUNDLE_RADIUS_M, help="束ねる半径 m")
    p.add_argument("--gap", type=float, default=BUNDLE_GAP_SEC, help="同じ看板とみなす撮影間隔の上限 秒")
    p.add_argument("--no-geocode", action="store_true", help="自治体の判定(通信)をしない")
    p.set_defaults(fn=cmd_intake)

    p = sub.add_parser("geocode", help="自治体が未判定の候補をやり直す")
    common(p, geo=True)
    p.set_defaults(fn=cmd_geocode)

    p = sub.add_parser("match", help="台帳との突き合わせ結果を表示")
    common(p, matching=True)
    p.set_defaults(fn=cmd_match)

    p = sub.add_parser("map", help="結果を HTML 地図に出す")
    common(p, matching=True)
    p.add_argument("--out", default="map.html")
    p.set_defaults(fn=cmd_map)

    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
