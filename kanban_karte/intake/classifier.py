"""ページ種別の判定口。今は仮実装(ヒューリスティック)。後で Claude に差し替える。

種別は ledger の pages.kind と同じ: 指示書 / 完成報告書 / 点検表 / 写真 / 地図 / 危険予知 / その他 / 不明。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from PIL import Image

from ..eval import crop
from ..ledger.db import PAGE_KINDS


@dataclass
class PageImage:
    page_no: int      # 1 始まり
    n_pages: int
    image: Image.Image  # 向きを起こした RGB 画像
    dpi: float
    source_name: str  # 取り込んだファイル名


@dataclass
class Classification:
    kind: str
    basis: str  # 根拠(判定役の名前と理由)

    def __post_init__(self):
        if self.kind not in PAGE_KINDS:
            raise ValueError(f"ページ種別は {PAGE_KINDS} のどれか: {self.kind!r}")


class PageClassifier(ABC):
    name = "base"

    @abstractmethod
    def classify(self, page: PageImage) -> Classification:
        ...


class HeuristicClassifier(PageClassifier):
    """仮実装。ファイルの先頭ページは指示書、写真セルが MIN_CELLS 個以上見つかるページは完成報告書、
    それ以外は不明。点検表・地図・危険予知などは区別しない(AI 判定役に差し替えるまで)。"""

    name = "heuristic"
    MIN_CELLS = 2

    def classify(self, page: PageImage) -> Classification:
        cells = crop.cut_cells(page.image, page.dpi)
        if len(cells) >= self.MIN_CELLS:
            return Classification("完成報告書", f"{self.name}: 写真セル {len(cells)} 個")
        if page.page_no == 1:
            return Classification("指示書", f"{self.name}: 先頭ページ")
        return Classification("不明", f"{self.name}: 写真セル {len(cells)} 個")
