"""住所の文字列 → 自治体(条例を引く単位)の判定インターフェース。"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class Municipality:
    prefecture: str          # 例: 東京都
    city: str | None = None  # 例: 豊島区。都道府県の条例だけで足りる所は None
    code: str | None = None  # 全国地方公共団体コード(分かれば)


class MunicipalityResolver(ABC):
    """住所の文字列から、その場所に適用される自治体を返す。分からなければ None(推測で埋めない)。"""

    @abstractmethod
    def resolve(self, address: str) -> Municipality | None:
        ...


class NullResolver(MunicipalityResolver):
    """常に None を返す(法規の列を空のままにする)。"""

    def resolve(self, address: str) -> Municipality | None:
        return None
