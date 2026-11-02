"""項目対応表(下書き): 客先様式の項目 → 東京都 第2号様式の18項目(ledger/items.py の ITEM_KEYS)。

対応させる相手は台帳の items のキー(「点検箇所-番号」)。確信の無い物は無理に対応させず target=None(対応なし)。
confidence は対応づけの確からしさ(対応なしの行は常に low)。needs_review=True の行は、有資格者が現場で
確かめる(docs/EVAL.md の「有資格者に確認する項目の一覧」)。この表は下書きで、確定は有資格者。

出典の様式(dataset.py の source キー): xlsx_center(客先A、東京都様式の18項目+腐食/破損/固定の A〜D)、
xlsx_karte(客先B、5項目×4段階)、paper_scan(紙の束、総合ランクのみ)。
"""
from __future__ import annotations

from dataclasses import dataclass

from .items import ITEM_KEYS, ITEM_LABELS

CONFIDENCE = ("high", "medium", "low")


@dataclass(frozen=True)
class Mapping:
    source: str            # xlsx_center / xlsx_karte / paper_scan
    source_item: str       # 客先様式での項目名
    target: str | None     # ITEM_KEYS のキー。None=対応なし
    confidence: str        # high / medium / low
    needs_review: bool     # 有資格者の確認が要る
    note: str = ""

    def __post_init__(self):
        if self.confidence not in CONFIDENCE:
            raise ValueError(f"confidence は {CONFIDENCE}: {self.confidence!r}")
        if self.target is not None and self.target not in ITEM_LABELS:
            raise ValueError(f"未知の点検項目: {self.target!r}")
        if self.target is None and self.confidence != "low":
            raise ValueError("対応なしの行の confidence は low")


# 客先A: ④の18項目は東京都様式そのもの(項目名の原文が同じ)。恒等で対応させる
_CENTER_IDENTITY = [Mapping("xlsx_center", f"④{k}", k, "high", False, "東京都様式と同じ項目(原文一致)")
                    for k in ITEM_KEYS]

_CENTER_OTHER = [
    Mapping("xlsx_center", "②腐食", None, "low", True,
            "看板全体の A〜D 評価で、箇所ごとではない。18項目のどれにも1対1にならない。看板のランクの材料にはなる"),
    Mapping("xlsx_center", "②破損", None, "low", True, "同上(看板全体の A〜D)"),
    Mapping("xlsx_center", "②固定", None, "low", True,
            "同上。支持部-2・取付部-1 に近いが、看板全体の評価なので対応させない"),
    Mapping("xlsx_center", "センター総合評価", None, "low", False,
            "センター(現場)単位で、看板単位ではない。看板には対応させない"),
]

_KARTE = [
    Mapping("xlsx_karte", "球切れ", "照明装置-1", "high", False,
            "不点灯・不発光と同じ事象。ただし即時修理(4段階目)は様式の区分に無い(下の値の対応参照)"),
    Mapping("xlsx_karte", "広告面の退色", "表示部-4", "medium", True,
            "退色≒変色。表示面の汚染、変色、はく離の一部しか言っていない"),
    Mapping("xlsx_karte", "広告面の損傷", "表示部-1", "medium", True,
            "広告面がシート・フィルムの場合は表示面板と同じと見てよいか"),
    Mapping("xlsx_karte", "看板本体の損傷", None, "low", True,
            "本体は 表示部-2/-3、基礎部・上部構造-3、支持部 など複数に広がる。どれか決められない"),
    Mapping("xlsx_karte", "その他", "その他-3", "medium", True,
            "自由記入欄。「その他(既設ポール)」「その他(配線部)」など括弧書きの付いた派生がある。"
            "括弧の中身で 付属部材(その他-1)や周辺機器(照明装置-3)に当たる物があるか"),
]

_PAPER = [
    Mapping("paper_scan", "総合ランク(評価欄)", None, "low", False,
            "看板全体のランクだけで項目別の判定が無い。18項目には対応させず、看板のランク(rank)の材料にだけ使う"),
]

MAPPINGS: list[Mapping] = _CENTER_IDENTITY + _CENTER_OTHER + _KARTE + _PAPER

# 値(判定の区分)の対応。東京都様式は 良好 / 経過観察 / 要改善 / 対象外 の4区分
VALUE_NOTES: list[tuple[str, str, str]] = [
    ("xlsx_center ④・xlsx_karte", "良好 / 経過観察 / 要改善", "そのまま同じ区分(要確認ではない)"),
    ("xlsx_center ④", "対象外", "そのまま。空欄は「未記入」で、対象外ではない"),
    ("xlsx_karte", "即時修理", "様式に対応する区分が無い。要改善に寄せると緊急度が落ちる → 要確認(今は対応させない)"),
    ("両方", "「良好/対象外」のように複数の印がある", "どちらか決められないので落とす(評価用データと同じ扱い)"),
    ("xlsx_center ②", "A〜D(A が最良)", "区分でなく4段階評価。項目には対応させない"),
]


def mappings_for(source: str) -> list[Mapping]:
    return [m for m in MAPPINGS if m.source == source]


def review_items() -> list[Mapping]:
    """有資格者に確認する行(要確認の印が付いた物)。"""
    return [m for m in MAPPINGS if m.needs_review]


def render_markdown() -> str:
    """docs/EVAL.md の対応表の節に貼る Markdown(コードと文書がずれないよう、ここから作る)。"""
    L = ["| 客先様式 | 客先の項目 | 東京都の項目 | confidence | 要確認 | 備考 |", "|---|---|---|---|---|---|"]
    center_ok = [m for m in _CENTER_IDENTITY]
    L.append(f"| xlsx_center | ④の18項目(原文が東京都様式と同じ) | 同名の18項目(恒等) | high | - | {len(center_ok)} 行 |")
    for m in _CENTER_OTHER + _KARTE + _PAPER:
        tgt = ITEM_LABELS[m.target] if m.target else "**対応なし**"
        L.append(f"| {m.source} | {m.source_item} | {tgt} | {m.confidence} | {'要確認' if m.needs_review else '-'} | {m.note} |")
    return "\n".join(L)
