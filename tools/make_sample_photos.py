"""data/sample/photos/ の無地の代用画像を作る(自社の看板写真は後で差し替える)。
使い方: python3 tools/make_sample_photos.py
"""
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parents[1] / "data" / "sample" / "photos"
COLORS = {"plain_a": (206, 214, 222), "plain_b": (222, 214, 206), "plain_c": (210, 222, 210),
          "plain_d": (222, 210, 218), "plain_e": (214, 214, 214), "plain_f": (230, 226, 200)}
OUT.mkdir(parents=True, exist_ok=True)
for name, rgb in COLORS.items():
    Image.new("RGB", (640, 480), rgb).save(OUT / f"{name}.png", optimize=True)
print(f"{len(COLORS)} 枚を {OUT} に作りました")
