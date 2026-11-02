"""ページ画像の画素特徴量(OCR なし)。ページ種別の判定材料。"""
from __future__ import annotations

import numpy as np
from PIL import Image

from ..eval import crop

FEAT_DPI = 45  # 特徴量は粗い画像で十分


def page_features(img: Image.Image, dpi: float) -> dict[str, float]:
    cells = crop.cut_cells(img, dpi)
    s = FEAT_DPI / dpi
    small = img.convert("RGB").resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.BILINEAR) \
        if s < 1 else img.convert("RGB")
    a = np.asarray(small).astype(np.int16)
    H, W = a.shape[:2]
    mx, mn = a.max(axis=2), a.min(axis=2)
    ink = mx < crop.NONWHITE
    sat = (mx - mn) > 50  # 色のある画素(写真・マーカー・地図)
    dark = mx < 110
    top = slice(0, max(1, int(H * 0.2)))
    cell_area = sum((c.x1 - c.x0) * (c.y1 - c.y0) for c in cells) / float(img.width * img.height)
    return {
        "n_cells": float(len(cells)),
        "cell_area": float(cell_area),
        "aspect": W / H,
        "ink": float(ink.mean()),
        "sat": float(sat.mean()),
        "dark": float(dark.mean()),
        "top_ink": float(ink[top].mean()),
        "top_sat": float(sat[top].mean()),
        "mid_sat": float(sat[int(H * 0.2):int(H * 0.8)].mean()),
        "row_ink_var": float(ink.mean(axis=1).std()),
    }
