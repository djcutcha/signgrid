"""ページ種別の特徴量を全ページ分取って、種別ごとの分布を見る(調査用。出力は runs/ の未追跡 JSON)。"""
import csv
import json
import sys
from pathlib import Path

import numpy as np

from kanban_karte.eval import crop, dataset
from kanban_karte.intake.features import page_features

cfg = dataset.load_config()
root = Path(cfg["scan_root"])
rows = list(csv.DictReader(open(root / "page_index.tsv", encoding="utf-8"), delimiter="\t"))
out = []
for r in rows:
    got = crop.JpegPages(root / "pages" / r["id"]).page(int(r["page"]))
    if got is None:
        continue
    im, dpi = got
    if int(r["rot"] or 0):
        im = im.rotate(int(r["rot"]), expand=True)
    f = page_features(im, dpi)
    f.update(kind=r["kind"], id=r["id"], page=int(r["page"]))
    out.append(f)
Path(sys.argv[1]).write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
by = {}
for f in out:
    by.setdefault(f["kind"], []).append(f)
keys = [k for k in out[0] if k not in ("kind", "id", "page")]
for kind, fs in by.items():
    print(kind, len(fs))
    for k in keys:
        a = np.array([x[k] for x in fs], float)
        print(f"   {k:14s} p10={np.percentile(a,10):.3f} p50={np.percentile(a,50):.3f} p90={np.percentile(a,90):.3f}")
