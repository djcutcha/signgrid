"""ガイドの「寸法の実測」工程: 数字の受け方と選択肢。進行(guide.py)と台帳(guide_ledger.py)が使う。

数字はテキスト 1通(全角・単位付きも受ける)。mm と m の取り違えを防ぐため、単位が無い数字は次のとおり扱う:
  1000 以上               mm としてそのまま受ける(確認なし)
  0.1〜50(小数か 1〜2桁)  m とみなし、「X m(Y mm)として受け取ります」と確認を返す
  100〜999                m だと 50 m を超えるので mm とみなし、確認を返す
  単位あり(mm / cm / m)   確認なしで受ける
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# 取付方法の選択肢(2026-11-04 案。有資格者の確認待ち)。「その他」は文字で受ける。LINE のボタンは 13 個まで。
# 出どころ: 東京都 第2号様式・国交省「屋外広告物の安全点検に関する指針(案)」2017 の項目名、日広連ほか「屋外広告物点検基準(案)」2016、
# 「オーナーさんのための看板の安全管理ガイドブック」2024 のポールの形式。バンセン・吊りは客先の手描きスケッチ(1店舗)に実際にあった物
MOUNTS = ("アンカーボルト", "溶接・プレート", "ボルト・ナット・ビス", "ブラケット", "ポール 串刺し式", "ポール 盤上式", "二柱式",
          "基礎・根巻き", "置くだけ", "バンセン", "吊り", "その他(文字で入力)", "分からない")
MOUNT_OTHER = "その他(文字で入力)"
MOUNT_PAGE = 11  # 取付方法は 13 択+「戻る」で上限を超えるので 2 ページ(1 ページ目は先頭 11 個+「次のページ」+「戻る」= 13)
# 照明の選択肢(キー, ボタンの文字, signs.lighting に入れる語。分からないは NULL)
LIGHTINGS = (
    ("inner", "内照式(中にLED・蛍光灯)", "内照式"),
    ("outer", "外照式(スポット・投光器)", "外照式"),
    ("none", "無し", "無し"),
    ("unknown", "分からない", None),
)
LIGHTING_KEYS = tuple(k for k, _, _ in LIGHTINGS)
LIGHTING_LABEL = {k: label for k, label, _ in LIGHTINGS}
LIGHTING_VALUE = {k: v for k, _, v in LIGHTINGS}
MOUNT_TEXT_MAX = 30
UNKNOWN = "分からない"
NONE_POST = "無し"
GL_EDGE = "bottom"          # GL からの高さは「看板の下端まで」で固定して聞く
LEN_MIN_MM, LEN_MAX_MM = 100, 50_000   # 幅・高さの範囲(mm)
DEPTH_MIN_MM, DEPTH_MAX_MM = 10, 5_000  # 厚み(奥行き)の範囲(mm)
GL_MAX_MM = 50_000
POST_MAX = 30

_NUM = re.compile(r"^(\d+(?:\.\d+)?)\s*(mm|ミリメートル|ミリ|cm|センチメートル|センチ|m|メートル)?$", re.I)
_UNIT_MM = {"mm": 1, "ミリ": 1, "ミリメートル": 1, "cm": 10, "センチ": 10, "センチメートル": 10, "m": 1000, "メートル": 1000}
_NOISE = re.compile(r"(約|ぐらい|くらい|程度|です|だと思います|[。、,\s])")


@dataclass
class Length:
    mm: int | None = None
    confirm: str | None = None   # 確認の文(あれば、はいで確定)
    error: str | None = None


def _fmt_m(mm: int) -> str:
    return f"{mm / 1000:g}"


def parse_length(text: str, *, lo: int = LEN_MIN_MM, hi: int = LEN_MAX_MM, allow_zero: bool = False) -> Length:
    t = unicodedata.normalize("NFKC", text).strip()
    t = _NOISE.sub("", t)
    m = _NUM.match(t)
    if not m:
        return Length(error="数字が読み取れませんでした。例: 3600 / 3.6m")
    num, unit = float(m.group(1)), (m.group(2) or "").lower()
    if unit:
        mm = round(num * _UNIT_MM[unit])
        confirm = None
    elif num == 0 and allow_zero:
        return Length(mm=0)
    elif num >= 1000:
        mm, confirm = round(num), None
    elif 0.1 <= num <= 50:
        mm = round(num * 1000)
        confirm = f"「{m.group(1)}」を {m.group(1)} m({mm} mm)として受け取ります。合っていますか"
    elif 100 <= num <= 999 and num == int(num):
        mm = int(num)
        confirm = f"「{m.group(1)}」を {mm} mm({_fmt_m(mm)} m)として受け取ります。合っていますか"
    else:
        return Length(error="単位が分かりませんでした。mm か m を付けてください(例: 3600mm / 3.6m)")
    if mm == 0 and allow_zero:
        return Length(mm=0)
    if not lo <= mm <= hi:
        return Length(error=f"{lo}〜{hi} mm({_fmt_m(lo)}〜{_fmt_m(hi)} m)の範囲で送ってください")
    return Length(mm=mm, confirm=confirm)


def parse_post(text: str) -> str | None:
    """支柱の文(例 □50)。「無し」「なし」は '無し'。長すぎる文は None(受けない)。"""
    t = unicodedata.normalize("NFKC", text).strip()
    if t in ("無し", "なし", "無", "ない"):
        return NONE_POST
    return t if t and len(t) <= POST_MAX else None
