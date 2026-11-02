"""コマンド `kanban-karte`(python3 -m kanban_karte でも同じ)。

  kanban-karte intake <pdf>            紙の束(スキャンした PDF)を台帳に取り込む(入口2)
  kanban-karte ledger list             台帳の看板一覧
  kanban-karte ledger show <id>        看板1基の台帳の1行(法規の列・安全の列)
  kanban-karte ledger bundles          取り込んだ束の一覧
  kanban-karte ledger import <json>    JSON(見本データなど)を台帳に入れる
台帳の置き場は --db(既定 data/private/ledger.sqlite、環境変数 KANBAN_KARTE_DB)。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from .ledger.db import Ledger, Sign
from .ledger.items import ITEM_KEYS, ITEM_LABELS

REPO_ROOT = Path(__file__).resolve().parents[1]
RANK_LABEL = {1: "良好", 2: "経過観察", 3: "要改善", 4: "即時対応"}


def _yn(v, yes: str, no: str) -> str:
    return "未判定" if v is None else (yes if v else no)


def _rank(v) -> str:
    return "未確定" if v is None else f"{v} {RANK_LABEL[v]}"


def _diff(prev, cur) -> str:
    if prev is None:
        return "前回なし"
    if cur is None:
        return f"前回 {_rank(prev)}"
    if cur == prev:
        return f"前回 {_rank(prev)} から変化なし"
    return f"前回 {_rank(prev)} から" + ("悪化" if cur > prev else "改善")


def format_sign(s: Sign, site_name: str | None = None, site_municipality: str | None = None,
                resolve=None) -> str:
    L = [f"看板 {s.id}  {s.name or '(名前未確定)'}"]
    where = site_name or s.site_id or "未確定"
    addr = s.address or ""
    muni = s.municipality or site_municipality or ""
    L.append(f"  現場: {where}  {addr}  {muni}".rstrip())
    size = "未確定" if s.size_w is None and s.size_h is None else \
        f"W{s.size_w if s.size_w is not None else '?'} × H{s.size_h if s.size_h is not None else '?'} mm"
    L.append(f"  種類: {s.type or '未確定'}   寸法: {size}")
    L.append("  [法規]")
    permit = _yn(s.permit_required, "要", "不要")
    if s.permit_required and s.permit_expires:
        permit += f"(期限 {s.permit_expires})"
    L.append(f"    自家用広告物: {_yn(s.is_self_use, '自家用', '自家用でない')}  /  許可: {permit}  /  "
             f"次回点検: {s.next_inspection or '未判定'}")
    L.append("  [安全]")
    L.append(f"    ランク: {_rank(s.rank)}({_diff(s.prev_rank, s.rank)})  最終点検: {s.last_inspected or '未実施'}")
    L.append(f"    所見: {s.remark or '(なし)'}")
    L.append(f"    点検項目(18項目のうち {len(s.items)} 項目に結果あり):")
    if s.items:
        for k in ITEM_KEYS:
            if k in s.items:
                L.append(f"      {ITEM_LABELS[k]}: {s.items[k]}")
    else:
        L.append("      (未記入)")
    L.append(f"    写真 {len(s.photos)} 枚" + ("" if not s.photos else ":"))
    for p in s.photos:
        L.append(f"      {resolve(p) if resolve else p}")
    if s.source_bundle is not None:
        L.append(f"  元の束: #{s.source_bundle}")
    return "\n".join(L)


def _cmd_intake(a, ledger: Ledger, out) -> int:
    from .intake.pdf_bundle import intake_pdf
    r = intake_pdf(ledger, a.pdf, a.data_dir, max_pages=a.max_pages)
    if r.existed:
        print(f"取り込み済みの束です(束 #{r.bundle_id}、{r.pages} ページ)。何もしていません。", file=out)
    else:
        print(f"束 #{r.bundle_id} を取り込みました: {r.pages} ページ", file=out)
    print("  ページ種別: " + ("、".join(f"{k} {v}" for k, v in r.kind_counts.items()) or "なし"), file=out)
    print(f"  看板の下書き: {len(r.signs)} 基(判定は未確定。`ledger list` で見られます)", file=out)
    return 0


def _cmd_ledger(a, ledger: Ledger, out) -> int:
    if a.ledger_cmd == "list":
        signs = ledger.list_signs(site_id=a.site, bundle_id=a.bundle)
        if not signs:
            print("看板はありません。", file=out)
            return 0
        print(f"{'id':<14}{'名前':<22}{'ランク':<10}{'許可':<8}{'写真':>4}  現場", file=out)
        for s in signs:
            site = ledger.get_site(s.site_id) if s.site_id else None
            print(f"{s.id:<14}{(s.name or '-'):<22}{_rank(s.rank):<10}"
                  f"{_yn(s.permit_required, '要', '不要'):<8}{len(s.photos):>4}  {site.name if site else '-'}", file=out)
        print(f"計 {len(signs)} 基", file=out)
        return 0
    if a.ledger_cmd == "show":
        s = ledger.get_sign(a.id)
        if s is None:
            print(f"看板 {a.id} はありません。", file=sys.stderr)
            return 1
        site = ledger.get_site(s.site_id) if s.site_id else None
        print(format_sign(s, site.name if site else None, site.municipality if site else None,
                          resolve=ledger.resolve_path), file=out)
        return 0
    if a.ledger_cmd == "bundles":
        for b in ledger.list_bundles():
            kinds = json.loads(b["kind_counts"])
            print(f"#{b['id']}  {b['status']:<17}{b['pages']:>4} ページ  "
                  + "、".join(f"{k} {v}" for k, v in kinds.items()) + f"  {Path(b['file']).name}", file=out)
        return 0
    if a.ledger_cmd == "import":
        n_sites, n_signs = ledger.import_json(a.json)
        print(f"読み込みました: 現場 {n_sites}、看板 {n_signs}", file=out)
        return 0
    return 2


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kanban-karte", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("KANBAN_KARTE_DB") or str(REPO_ROOT / "data" / "private" / "ledger.sqlite"))
    ap.add_argument("--data-dir", default=os.environ.get("KANBAN_KARTE_DATA") or str(REPO_ROOT / "data" / "private" / "intake"),
                    help="取り込んだページ画像・切り出した写真の置き場")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("intake", help="紙の束(PDF)を取り込む")
    p.add_argument("pdf")
    p.add_argument("--max-pages", type=int, default=None, help="先頭からこのページ数だけ取り込む(試し用)")
    lg = sub.add_parser("ledger", help="台帳を見る・入れる")
    ls = lg.add_subparsers(dest="ledger_cmd", required=True)
    l = ls.add_parser("list")
    l.add_argument("--site")
    l.add_argument("--bundle", type=int)
    sh = ls.add_parser("show")
    sh.add_argument("id")
    ls.add_parser("bundles")
    im = ls.add_parser("import")
    im.add_argument("json")
    return ap


def main(argv=None, out=None) -> int:
    out = out or sys.stdout
    a = build_parser().parse_args(argv)
    with Ledger(a.db, a.data_dir) as ledger:
        if a.cmd == "intake":
            return _cmd_intake(a, ledger, out)
        return _cmd_ledger(a, ledger, out)


if __name__ == "__main__":
    sys.exit(main())
