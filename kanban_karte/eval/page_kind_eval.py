"""ページ種別(PageClassifier)の精度測定。

正解は page_index.tsv(紙の束のページごとの種別)。ページ画像は eval/pages/<id>/p-NNN.jpg(90dpi)を
page_index の rot で起こして PageClassifier に渡す。混同行列と正解率(全体・種別ごと)を
runs/page_kind_<ts>.md に書く(runs/ は未追跡)。

  python3 -m kanban_karte.eval.page_kind_eval [--classifier heuristic] [--limit-bundles N]
"""
from __future__ import annotations

import argparse
import csv
import time
from collections import Counter, defaultdict
from pathlib import Path

from ..intake.classifier import HeuristicClassifier, PageClassifier, PageImage
from ..ledger.db import PAGE_KINDS
from . import crop, dataset

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET_KINDS = ("完成報告書", "指示書")
TARGET_RECALL = 0.95


def load_truth(scan_root: Path) -> list[dict]:
    with open(scan_root / "page_index.tsv", newline="", encoding="utf-8") as f:
        return [r for r in csv.DictReader(f, delimiter="\t")]


def confusion(pairs: list[tuple[str, str]]) -> dict[str, Counter]:
    """[(正解, 予測)] → {正解: Counter({予測: 件数})}"""
    m: dict[str, Counter] = defaultdict(Counter)
    for t, p in pairs:
        m[t][p] += 1
    return m


def render_markdown(title: str, pairs: list[tuple[str, str]], note: str = "") -> str:
    m = confusion(pairs)
    kinds = [k for k in PAGE_KINDS if k in m or any(k in c for c in m.values())]
    n = len(pairs)
    ok = sum(1 for t, p in pairs if t == p)
    L = [f"# {title}", "", note, "", f"ページ数 {n}、全体の正解率 {ok / n:.1%}({ok}/{n})", "",
         "## 種別ごとの再現率・適合率", "", "| 種別 | 正解件数 | 再現率 | 適合率 |", "|---|---:|---:|---:|"]
    for k in kinds:
        tot = sum(m[k].values())
        tp = m[k][k]
        pred = sum(c[k] for c in m.values())
        rec = f"{tp / tot:.1%}" if tot else "-"
        prec = f"{tp / pred:.1%}" if pred else "-"
        mark = ""
        if k in TARGET_KINDS and tot:
            mark = " ✓" if tp / tot >= TARGET_RECALL else " ✗(目標 95%)"
        L.append(f"| {k} | {tot} | {rec}{mark} | {prec} |")
    L += ["", "## 混同行列(行=正解、列=予測)", "", "| 正解＼予測 | " + " | ".join(kinds) + " |",
          "|---|" + "---:|" * len(kinds)]
    for t in kinds:
        L.append(f"| {t} | " + " | ".join(str(m[t][p]) if m[t][p] else "" for p in kinds) + " |")
    return "\n".join(L) + "\n"


def run(classifier: PageClassifier, scan_root: Path, limit_bundles: int | None = None) -> list[tuple[str, str]]:
    truth = load_truth(scan_root)
    ids = sorted({r["id"] for r in truth})
    if limit_bundles:
        ids = ids[:limit_bundles]
    by_id: dict[str, list[dict]] = defaultdict(list)
    for r in truth:
        by_id[r["id"]].append(r)
    pairs: list[tuple[str, str]] = []
    for bid in ids:
        rows = sorted(by_id[bid], key=lambda r: int(r["page"]))
        n = len(rows)
        src = crop.JpegPages(scan_root / "pages" / bid)
        for r in rows:
            got = src.page(int(r["page"]))
            if got is None:
                continue
            im, dpi = got
            rot = int(r["rot"] or 0)
            if rot:
                im = im.rotate(rot, expand=True)
            res = classifier.classify(PageImage(int(r["page"]), n, im, dpi, bid))
            pairs.append((r["kind"], res.kind))
    return pairs


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--classifier", default="heuristic", choices=["heuristic", "layout"])
    ap.add_argument("--limit-bundles", type=int, default=None)
    ap.add_argument("--sources", default=None)
    a = ap.parse_args(argv)
    cfg = dataset.load_config(a.sources)
    scan_root = Path(cfg["scan_root"])
    if a.classifier == "heuristic":
        clf: PageClassifier = HeuristicClassifier()
    else:
        from ..intake.classifier import LayoutClassifier
        clf = LayoutClassifier()
    t0 = time.time()
    pairs = run(clf, scan_root, a.limit_bundles)
    ts = time.strftime("%Y-%m-%d_%H%M")
    out = REPO_ROOT / "runs" / f"page_kind_{ts}_{a.classifier}.md"
    out.parent.mkdir(exist_ok=True)
    md = render_markdown(f"ページ種別の精度: {clf.name}", pairs, f"所要 {time.time() - t0:.0f} 秒")
    out.write_text(md, encoding="utf-8")
    print(md)
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
