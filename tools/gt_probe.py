"""正解データの形を見る(調査用)。  python3 -m tools.gt_probe [bundle_id ...]"""
import collections
import csv
import sys
from pathlib import Path

from kanban_karte.eval import dataset

root = Path(dataset.load_config()["scan_root"])
g = list(csv.DictReader(open(root / "ground_truth_items.tsv", encoding="utf-8"), delimiter="\t"))
if sys.argv[1:]:
    for r in g:
        if r["id"] in sys.argv[1:]:
            print(r["item_group"], r["pages"], r["store_id"], r["item_no"], r["item_name"], r["fmt"], r["confidence"], r["photo_count"], sep=" | ")
else:
    print(len(g), len({r["id"] for r in g}), collections.Counter(r["fmt"] for r in g), collections.Counter(r["confidence"] for r in g))
    print(collections.Counter(bool(r["item_no"]) for r in g), collections.Counter(bool(r["item_name"]) for r in g))
