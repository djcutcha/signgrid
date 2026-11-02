"""参照物辞書: 写真に写りやすい物の一般的な寸法(公開情報)。寸法役が「仮定」として使い、揺れを範囲に反映する。

各項目: name(呼び名)、dims(軸の名前 → 標準寸法 mm)、tol_mm(軸ごとの ± 幅 mm)、source(JIS / 一般 / 自社)、note。
自社部材(自社で作る看板の既知の寸法)は CUSTOM に枠だけ置く。値は入れない(別途、本人から受け取る)。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class RefObject:
    name: str
    dims: dict[str, int]
    tol_mm: dict[str, int]
    source: str
    note: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


REFERENCE_OBJECTS: tuple[RefObject, ...] = (
    RefObject("ドア(出入口の扉)", {"高さ": 2000}, {"高さ": 50}, "一般", "住宅・店舗の一般的な扉の高さ"),
    RefObject("コンクリートブロック", {"長さ": 390, "高さ": 190}, {"長さ": 5, "高さ": 5}, "JIS",
              "JIS A 5406 の基本形。目地を含めた1段のピッチは約 200 mm"),
    RefObject("脚立", {"1段あたり": 250}, {"1段あたり": 30}, "一般", "段数 × 約 250 mm(踏み面の間隔)"),
    RefObject("自転車", {"全長": 1700}, {"全長": 150}, "一般", "一般的な軽快車"),
    RefObject("成人", {"身長": 1675}, {"身長": 75}, "一般", "身長 1600〜1750 mm"),
    RefObject("電柱", {"径": 300}, {"径": 50}, "一般", "コンクリート柱の地際付近の径"),
    RefObject("駐車枠", {"幅": 2500}, {"幅": 200}, "一般", "普通車の枠の幅(2300〜2700 mm)"),
    RefObject("点字ブロック", {"一辺": 300}, {"一辺": 5}, "JIS", "JIS T 9251。正方形 300 mm"),
    RefObject("メッシュフェンス", {"縦線ピッチ": 50}, {"縦線ピッチ": 5}, "一般", "金網フェンスの縦線の間隔"),
    RefObject("A4 用紙", {"長辺": 297, "短辺": 210}, {"長辺": 1, "短辺": 1}, "JIS", "JIS P 0138"),
    RefObject("消火器", {"高さ": 500}, {"高さ": 100}, "一般", "業務用の一般的な蓄圧式"),
    RefObject("シャッター", {"幅": 3000}, {"幅": 1000}, "一般", "店舗の間口。個体差が大きく、参照物としては弱い"),
    RefObject("カラーコーン", {"高さ": 700}, {"高さ": 50}, "一般", "一般的な道路用コーン"),
    RefObject("縁石", {"高さ": 150}, {"高さ": 30}, "一般", "歩道の縁石の見え高さ"),
)

# 自社部材の枠(値は空)。RefObject(name, {軸: mm}, {軸: ±mm}, "自社") を足す。
CUSTOM: list[RefObject] = []


def all_objects() -> list[RefObject]:
    return list(REFERENCE_OBJECTS) + list(CUSTOM)


def prompt_text() -> str:
    """プロンプトに入れる辞書の文面。"""
    lines = []
    for o in all_objects():
        dims = "、".join(f"{k} {v}±{o.tol_mm.get(k, 0)}" for k, v in o.dims.items())
        lines.append(f"- {o.name}: {dims} mm({o.source}){'。' + o.note if o.note else ''}")
    return "\n".join(lines)
