"""全ページの OCR(上部 25%)と画素特徴量を runs/page_dump.json に書く(規則の調整用。OCR キャッシュも温まる)。

  python3 -m tools.page_dump [out.json]
"""
import csv
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

from kanban_karte.eval import crop, dataset
from kanban_karte.intake.classifier import PageImage
from kanban_karte.intake.features import page_features
from kanban_karte.intake.ocr_classifier import OcrHeuristicClassifier

root = Path(dataset.load_config()["scan_root"])
rows = list(csv.DictReader(open(root / "page_index.tsv", encoding="utf-8"), delimiter="\t"))
by = defaultdict(list)
for r in rows:
    by[r["id"]].append(r)
clf = OcrHeuristicClassifier()
out = []
t0 = time.time()
t_ocr = 0.0
for n, (bid, rs) in enumerate(sorted(by.items()), 1):
    src = crop.JpegPages(root / "pages" / bid)
    pages = []
    for r in sorted(rs, key=lambda r: int(r["page"])):
        got = src.page(int(r["page"]))
        if got is None:
            continue
        im, dpi = got
        if int(r["rot"] or 0):
            im = im.rotate(int(r["rot"]), expand=True)
        pages.append((r, PageImage(int(r["page"]), len(rs), im, dpi, bid)))
    t1 = time.time()
    clf.prefetch([p for _, p in pages])
    t_ocr += time.time() - t1
    for r, p in pages:
        f = page_features(p.image, p.dpi)
        f.update(kind=r["kind"], id=bid, page=p.page_no, n_pages=p.n_pages, text=clf.text(p))
        out.append(f)
    print(f"{n}/{len(by)} {bid} {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
dst = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/page_dump.json")
dst.parent.mkdir(exist_ok=True)
dst.write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
print(f"pages={len(out)} total={time.time() - t0:.0f}s ocr_only={t_ocr:.0f}s")
