"""ページ種別 v2: 上部 25% の OCR(決まり文句)と画素の規則(features.py)を組み合わせた判定。

OCR は macOS Vision(ローカル)。結果は data/private/ocr_cache/ に画像内容のハッシュで保存する。
判定そのもの(decide)は文字列と特徴量だけを受ける純関数で、OCR なしでも試せる。
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

from PIL import Image

from ..ocr.vision import VisionOcr, crop_band
from .classifier import Classification, PageClassifier, PageImage
from .features import page_features

TOP_BAND = 0.25


def norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text)).upper()


# 決まり文句。OCR が崩す(「作業指示書」→「指示書分额」など)ので、短い語の部分一致を使う。
KYT = ("危険予知", "予知活動")
SHIJI = ("作業指示", "指示書")
TENKEN = ("保守点検表", "看板保守", "広告物カルテ", "点検表")
KANSEI = ("店舗NO", "アイテムNO", "評価ランク", "写真NO", "申請NO", "中請NO", "申錆NO")
CHOSA = ("調査対象", "調査日", "総評", "店舗名", "作業形態")
MAP = ("地図", "GOOGLE", "マップ", "MAP", "周辺")


def hits(t: str, words: tuple[str, ...]) -> int:
    return sum(1 for w in words if w in t)


def decide(text: str, f: dict[str, float], page_no: int) -> tuple[str, str]:
    """(種別, 根拠)。text は OCR 全文(上部 25%)、f は page_features。"""
    t = norm(text)
    if hits(t, KYT):
        return "危険予知", "OCR: 危険予知"
    if hits(t, TENKEN):
        return "点検表", "OCR: 点検表/カルテ"
    if hits(t, SHIJI):
        return "指示書", "OCR: 作業指示書"
    k, c = hits(t, KANSEI), hits(t, CHOSA)
    if k >= 2 or (k >= 1 and f["n_cells"] >= 2) or (c >= 2 and f["n_cells"] >= 2):
        return "完成報告書", f"OCR: 帳票の項目名 {k + c} 語 + 写真セル {int(f['n_cells'])}"
    if hits(t, MAP):
        return "地図", "OCR: 地図"
    if page_no == 1:
        return "指示書", "先頭ページ"
    return "不明", "手掛かりなし"


class OcrHeuristicClassifier(PageClassifier):
    name = "ocr-heuristic"

    def __init__(self, ocr: VisionOcr | None = None):
        self.ocr = ocr or VisionOcr()

    @staticmethod
    def band(img: Image.Image) -> tuple[str, Image.Image]:
        b = crop_band(img, 0.0, TOP_BAND)
        key = hashlib.sha1(b.tobytes()).hexdigest()[:20] + f"-top{TOP_BAND}"
        return key, b

    def prefetch(self, pages: list[PageImage]) -> None:
        """まとめて OCR する(キャッシュに無いものだけ)。classify は個別でも動く。"""
        self.ocr.read_many([self.band(p.image) for p in pages])

    def text(self, page: PageImage) -> str:
        key, b = self.band(page.image)
        return " ".join(l.text for l in self.ocr.read(key, b))

    def classify(self, page: PageImage) -> Classification:
        kind, why = decide(self.text(page), page_features(page.image, page.dpi), page.page_no)
        return Classification(kind, f"{self.name}: {why}")
