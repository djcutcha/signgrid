"""看板への束ねの精度測定。

正解は ground_truth_items.tsv の pages 列(看板ごとのページ・面の指定)。完成報告書ページの面
(ページ番号, L/R/無印)を SignGrouper で束ね、正解の看板と面の集合が完全に一致した割合を測る。
面の粒度は正解に合わせる(正解が「7」のように面を書かないページは、予測の L/R を畳んで比べる)。
ページ種別は --kinds truth(page_index の正解)か predicted(OCR 判定 v2、通し)。
結果は runs/grouping_<ts>.md(runs/ は未追跡)。

  python3 -m kanban_karte.eval.grouping_eval [--kinds truth|predicted] [--limit-bundles N]
"""
from __future__ import annotations

import argparse
import csv
import time
from collections import defaultdict
from pathlib import Path

from ..intake.classifier import PageImage
from ..intake.pdf_bundle import PerPageGrouper, SignGrouper
from . import crop, dataset
from .page_kind_eval import load_truth

REPO_ROOT = Path(__file__).resolve().parents[2]

Face = tuple[int, str | None]


def truth_items(scan_root: Path) -> dict[str, list[frozenset[Face]]]:
    out: dict[str, list[frozenset[Face]]] = defaultdict(list)
    with open(scan_root / "ground_truth_items.tsv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            out[r["id"]].append(frozenset(crop.parse_page_spec(r["pages"])))
    return out


def face_set(group: list[tuple[int, crop.Cell]], coarse_pages: set[int]) -> frozenset[Face]:
    out = set()
    for no, c in group:
        half = None if (c.half == "-" or no in coarse_pages) else c.half
        out.add((no, half))
    return frozenset(out)


def match_rate(truth: list[frozenset[Face]], pred_groups) -> tuple[int, int]:
    """(一致した正解の看板数, 予測の看板数)。正解が面を書かないページは予測の面を畳む。"""
    coarse = {no for t in truth for no, h in t if h is None}
    preds = [face_set(g, coarse) for g in pred_groups]
    return sum(1 for t in truth if t in preds), len(preds)


def run(grouper: SignGrouper, scan_root: Path, kinds: str = "truth", limit_bundles: int | None = None):
    truth_pages = load_truth(scan_root)
    items = truth_items(scan_root)
    by_id: dict[str, list[dict]] = defaultdict(list)
    for r in truth_pages:
        by_id[r["id"]].append(r)
    ids = sorted(i for i in items if i in by_id)
    if limit_bundles:
        ids = ids[:limit_bundles]
    clf = None
    if kinds == "predicted":
        from ..intake.ocr_classifier import OcrHeuristicClassifier
        clf = OcrHeuristicClassifier()
    rows = []
    for bid in ids:
        src = crop.JpegPages(scan_root / "pages" / bid)
        rs = sorted(by_id[bid], key=lambda r: int(r["page"]))
        faces, imgs = [], {}
        pages = []
        for r in rs:
            got = src.page(int(r["page"]))
            if got is None:
                continue
            im, dpi = got
            if int(r["rot"] or 0):
                im = im.rotate(int(r["rot"]), expand=True)
            pages.append((r, PageImage(int(r["page"]), len(rs), im, dpi, bid)))
        if clf is not None:
            clf.prefetch([p for _, p in pages])
        for r, p in pages:
            kind = clf.classify(p).kind if clf is not None else r["kind"]
            if kind != "完成報告書":
                continue
            imgs[p.page_no] = p.image
            cells = crop.cut_cells(p.image, p.dpi)
            for half in sorted({c.half for c in cells}):
                faces.append((p.page_no, half, [c for c in cells if c.half == half]))
        grouper.prepare(imgs)
        groups = grouper.group(faces)
        ok, n_pred = match_rate(items[bid], groups)
        rows.append({"id": bid, "truth": items[bid], "groups": groups, "ok": ok, "n_truth": len(items[bid]),
                     "n_pred": n_pred})
    return rows


def fmt_spec(fs) -> str:
    return ",".join(f"{n}{h or ''}" for n, h in sorted(fs, key=lambda x: (x[0], x[1] or "")))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--kinds", default="truth", choices=["truth", "predicted"])
    ap.add_argument("--limit-bundles", type=int, default=None)
    ap.add_argument("--sources", default=None)
    a = ap.parse_args(argv)
    scan_root = Path(dataset.load_config(a.sources)["scan_root"])
    from ..intake.ocr_grouper import OcrSignGrouper
    results = {}
    for name, g in (("v1 1面=1基", PerPageGrouper()), ("v2 OCR見出し", OcrSignGrouper())):
        t0 = time.time()
        results[name] = (run(g, scan_root, a.kinds, a.limit_bundles), time.time() - t0)
    L = [f"# 看板への束ねの精度(ページ種別={a.kinds})", "",
         "注意: 正解は機械+人手混在の ground_truth_items。看板の面の集合が完全一致した割合。", "",
         "| 束ね方 | 正解の看板数 | 完全一致 | 一致率 | 予測の看板数 | 所要(秒) |", "|---|---:|---:|---:|---:|---:|"]
    for name, (rows, sec) in results.items():
        nt = sum(r["n_truth"] for r in rows)
        ok = sum(r["ok"] for r in rows)
        npred = sum(r["n_pred"] for r in rows)
        L.append(f"| {name} | {nt} | {ok} | {ok / nt:.1%} | {npred} | {sec:.0f} |")
    v2 = results["v2 OCR見出し"][0]
    L += ["", "## 束ごと(v2): 正解と予測の看板(ページ指定)", ""]
    for r in v2:
        pred = [fmt_spec(face_set(g, {no for t in r["truth"] for no, h in t if h is None})) for g in r["groups"]]
        L.append(f"- {r['id']}: 一致 {r['ok']}/{r['n_truth']}(予測 {r['n_pred']})")
        if r["ok"] < r["n_truth"]:
            L.append(f"    - 正解: {' | '.join(fmt_spec(t) for t in r['truth'])}")
            L.append(f"    - 予測: {' | '.join(pred)}")
    out = REPO_ROOT / "runs" / f"grouping_{time.strftime('%Y-%m-%d_%H%M')}_{a.kinds}.md"
    out.parent.mkdir(exist_ok=True)
    md = "\n".join(L) + "\n"
    out.write_text(md, encoding="utf-8")
    print("\n".join(L[:10]))
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
