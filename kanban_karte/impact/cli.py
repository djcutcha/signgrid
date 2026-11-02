"""python3 -m kanban_karte.impact.cli count --db <signjobz.db>"""
from __future__ import annotations

import argparse
import sys

from . import count as C


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.impact.cli", description="自社版『把握の数字』")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("count", help="自家用・許可不要の看板を自治体別に数える(DB は読むだけ)")
    c.add_argument("--db", required=True, help="SignJOBZ 複製の sqlite")
    c.add_argument("--runs-dir", default="runs", help="結果の置き場(件数と率のみ。未追跡)")
    c.add_argument("--agencies", help="代理店リスト(1行1語。既定 data/private/agencies.txt)")
    args = ap.parse_args(argv)
    words = C.load_agency_words(C.Path(args.agencies)) if args.agencies else None
    rep = C.run(args.db, agency_words=words)
    path = C.write_run(rep, args.runs_dir)
    print(C.render(rep))
    print(f"-> {path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
