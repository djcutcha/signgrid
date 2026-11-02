"""束ねの調査用: 束の各ページの種別・セル数・OCR 冒頭と、正解の看板のページ指定を並べる。
  python3 -m tools.group_probe <bundle_id> [max_page]
"""
import csv
import json
import sys
from pathlib import Path

from kanban_karte.eval import dataset

bid = sys.argv[1]
mx = int(sys.argv[2]) if sys.argv[2:] else 14
root = Path(dataset.load_config()["scan_root"])
for r in json.load(open("runs/page_dump.json", encoding="utf-8")):
    if r["id"] == bid and r["page"] <= mx:
        print(r["page"], r["kind"], int(r["n_cells"]), round(r["aspect"], 2), r["text"][:110].replace("\n", " "))
print()
for r in csv.DictReader(open(root / "ground_truth_items.tsv", encoding="utf-8"), delimiter="\t"):
    if r["id"] == bid:
        print(r["item_group"], r["pages"], r["item_no"], r["fmt"])
