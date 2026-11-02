"""種別ごとに数ページ、上部 25% を OCR して文字を見る(調査用)。"""
import collections
import csv
import sys
from pathlib import Path

from kanban_karte.eval import crop, dataset
from kanban_karte.ocr.vision import VisionOcr, crop_band

root = Path(dataset.load_config()["scan_root"])
rows = list(csv.DictReader(open(root / "page_index.tsv", encoding="utf-8"), delimiter="\t"))
per = int(sys.argv[1]) if len(sys.argv) > 1 else 3
by = collections.defaultdict(list)
for r in rows:
    by[r["kind"]].append(r)
ocr = VisionOcr()
for kind, rs in by.items():
    step = max(1, len(rs) // per)
    for r in rs[::step][:per]:
        got = crop.JpegPages(root / "pages" / r["id"]).page(int(r["page"]))
        if got is None:
            continue
        im, dpi = got
        if int(r["rot"] or 0):
            im = im.rotate(int(r["rot"]), expand=True)
        lines = ocr.read(f"probe|{r['id']}|{r['page']}", crop_band(im, 0, 0.25))
        print(kind, r["id"], r["page"], im.size, "|", " / ".join(l.text for l in lines)[:200])
