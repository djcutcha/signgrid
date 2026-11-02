"""様式出力のコマンド。

  python3 -m kanban_karte.output.cli tokyo    --site <現場ID> --out out.xlsx   東京都 第2号様式
  python3 -m kanban_karte.output.cli client-a --site <現場ID> --out out.xlsx   客先A型(センター単位)
台帳は --db(既定 data/private/ledger.sqlite、環境変数 KANBAN_KARTE_DB)。見本データなら
  --from-json data/sample/ledger.json  で、JSON を一時の台帳に読み込んで出す(DB には書かない)。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..ledger.db import Ledger
from .client_a_form import write_client_a
from .tokyo_form import write_tokyo_form

REPO_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kanban_karte.output.cli", description="台帳から様式(xlsx)を出す。出力は常に下書き。")
    # 台帳の指定は、サブコマンドの前でも後でも書ける(未指定は SUPPRESS にして、parse の後で既定値を入れる)
    def add_ledger_args(p):
        p.add_argument("--db", default=argparse.SUPPRESS, help="台帳 SQLite(既定 data/private/ledger.sqlite、環境変数 KANBAN_KARTE_DB)")
        p.add_argument("--data-dir", default=argparse.SUPPRESS, help="台帳の画像パス(相対)の基準フォルダ")
        p.add_argument("--from-json", default=argparse.SUPPRESS, help="台帳の代わりにこの JSON(見本データ)を一時の台帳に読み込む")

    add_ledger_args(ap)
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name, help_ in (("tokyo", "東京都 屋外広告物安全点検報告書(第2号様式)"), ("client-a", "客先A型 安全点検報告書(センター単位)")):
        p = sub.add_parser(name, help=help_)
        add_ledger_args(p)
        p.add_argument("--site", required=True, help="現場ID")
        p.add_argument("--out", required=True, help="出力する xlsx のパス")
    return ap


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    a = build_parser().parse_args(argv)
    a.db = getattr(a, "db", None) or os.environ.get("KANBAN_KARTE_DB") or str(REPO_ROOT / "data" / "private" / "ledger.sqlite")
    a.data_dir = getattr(a, "data_dir", None) or os.environ.get("KANBAN_KARTE_DATA") or str(REPO_ROOT / "data" / "private" / "intake")
    a.from_json = getattr(a, "from_json", None)
    if a.from_json:
        ledger = Ledger(":memory:", data_dir=a.data_dir)
        ledger.import_json(a.from_json)
    else:
        if not Path(a.db).exists():
            print(f"台帳が見つかりません: {a.db}", file=sys.stderr)
            return 1
        ledger = Ledger(a.db, data_dir=a.data_dir)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    try:
        fn = write_tokyo_form if a.cmd == "tokyo" else write_client_a
        sheets = fn(ledger, a.site, a.out)
    except (LookupError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 1
    finally:
        ledger.close()
    print(f"{a.out} を書きました(シート {len(sheets)}: {', '.join(sheets)})。下書きです。有資格者の確認・署名が要ります。", file=out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
