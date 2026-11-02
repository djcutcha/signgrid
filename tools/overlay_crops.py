"""切り出しの目視確認用: ページ画像に検出した写真セルの枠を重ねて、並べた1枚の画像にする。
使い方: python3 tools/overlay_crops.py OUT.jpg <束id> [<束id> ...]
各束の「真ん中あたりのアイテム」の先頭ページを描く。正解データの場所は data/private/sources.toml。
"""
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw  # noqa: E402

from kanban_karte.eval import crop  # noqa: E402
from kanban_karte.eval.dataset import load_config  # noqa: E402

root = Path(load_config()["scan_root"])
rows = list(csv.DictReader(open(root / "ground_truth_items.tsv", newline="", encoding="utf-8"), delimiter="\t"))
rots = crop.load_rotations(root / "page_index.tsv")
out, ids = sys.argv[1], sys.argv[2:]
tiles = []
for i in ids:
    xs = [y for y in rows if y["id"] == i and y["fmt"] == "header-strip"]
    x = xs[len(xs) // 2]
    pg, half = crop.parse_page_spec(x["pages"])[0]
    im = crop.open_page(root / "pages" / i / f"p-{pg:03d}.jpg", rots.get((i, pg), 0))
    cs = crop.find_cells(im, half)
    d = ImageDraw.Draw(im)
    for c in cs:
        d.rectangle((c.x0, c.y0, c.x1, c.y1), outline=(255, 0, 0), width=4)
    print(i, x["pages"], len(cs))
    if half and im.width > 1200:
        w = im.width // 2
        im = im.crop((0, 0, w, im.height) if half == "L" else (w, 0, im.width, im.height))
    im.thumbnail((400, 560))
    tiles.append(im)
W = 6
sh = Image.new("RGB", (W * 410, ((len(tiles) + W - 1) // W) * 570), "gray")
for k, im in enumerate(tiles):
    sh.paste(im, ((k % W) * 410, (k // W) * 570))
sh.save(out, quality=70)
