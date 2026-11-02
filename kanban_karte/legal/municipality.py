"""住所の文字列 → 自治体(条例を引く単位)の判定。

対象は東京都・埼玉県の2都県。条例の写しがあるのは 東京都 / 埼玉県 / 越谷市 / さいたま市 の4つ。
推測で埋めない方針: 住所から断定できないときは None、条例の写しが無い自治体は
ordinance_key() が None を返す(「条例が未取得」と「条例が無い」を区別する)。

判定の手順:
  1. NFKC 正規化(全角数字・記号、郵便番号〒、空白を整える)
  2. 先頭が都道府県名なら、その都道府県。
       東京都 -> 区市町村名を表(TOKYO_MUNICIPALITIES)の最長一致で取る
       埼玉県 -> 市町村名を表(SAITAMA_MUNICIPALITIES)の最長一致で取る
       それ以外 -> 都道府県のみ(市区町村は取らない。"四日市市" のような取り違えを避ける)
  3. 都道府県名が無いときは、全国で一意と分かっている名前だけ
     (東京の特別区のうち一意な20区・越谷市・さいたま市)で補う。それ以外は None。
"""
from __future__ import annotations

import re
import unicodedata
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


# ---------- 自治体の表 ----------

PREFECTURES = (
    "北海道 青森県 岩手県 宮城県 秋田県 山形県 福島県 茨城県 栃木県 群馬県 埼玉県 千葉県 東京都 神奈川県 "
    "新潟県 富山県 石川県 福井県 山梨県 長野県 岐阜県 静岡県 愛知県 三重県 滋賀県 京都府 大阪府 兵庫県 "
    "奈良県 和歌山県 鳥取県 島根県 岡山県 広島県 山口県 徳島県 香川県 愛媛県 高知県 福岡県 佐賀県 長崎県 "
    "熊本県 大分県 宮崎県 鹿児島県 沖縄県"
).split()

TOKYO_WARDS = (
    "千代田区 中央区 港区 新宿区 文京区 台東区 墨田区 江東区 品川区 目黒区 大田区 世田谷区 渋谷区 中野区 "
    "杉並区 豊島区 北区 荒川区 板橋区 練馬区 足立区 葛飾区 江戸川区"
).split()
TOKYO_CITIES = (
    "八王子市 立川市 武蔵野市 三鷹市 青梅市 府中市 昭島市 調布市 町田市 小金井市 小平市 日野市 東村山市 "
    "国分寺市 国立市 福生市 狛江市 東大和市 清瀬市 東久留米市 武蔵村山市 多摩市 稲城市 羽村市 あきる野市 西東京市"
).split()
TOKYO_TOWNS = (
    "瑞穂町 日の出町 檜原村 奥多摩町 大島町 利島村 新島村 神津島村 三宅村 御蔵島村 八丈町 青ヶ島村 小笠原村"
).split()
TOKYO_MUNICIPALITIES = tuple(TOKYO_WARDS + TOKYO_CITIES + TOKYO_TOWNS)  # 23+26+13 = 62

SAITAMA_CITIES = (
    "さいたま市 川越市 熊谷市 川口市 行田市 秩父市 所沢市 飯能市 加須市 本庄市 東松山市 春日部市 狭山市 "
    "羽生市 鴻巣市 深谷市 上尾市 草加市 越谷市 蕨市 戸田市 入間市 朝霞市 志木市 和光市 新座市 桶川市 久喜市 "
    "北本市 八潮市 富士見市 三郷市 蓮田市 坂戸市 幸手市 鶴ヶ島市 日高市 吉川市 ふじみ野市 白岡市"
).split()
SAITAMA_TOWNS = (
    "伊奈町 三芳町 毛呂山町 越生町 滑川町 嵐山町 小川町 川島町 吉見町 鳩山町 ときがわ町 横瀬町 皆野町 "
    "長瀞町 小鹿野町 東秩父村 美里町 神川町 上里町 寄居町 宮代町 杉戸町 松伏町"
).split()
SAITAMA_MUNICIPALITIES = tuple(SAITAMA_CITIES + SAITAMA_TOWNS)  # 40+23 = 63

# 都道府県名なしでも全国で一意と分かる名前(中央区・港区・北区は他県にもあるので入れない)
_UNIQUE_WITHOUT_PREFECTURE = {
    **{w: "東京都" for w in TOKYO_WARDS if w not in ("中央区", "港区", "北区")},
    "越谷市": "埼玉県",
    "さいたま市": "埼玉県",
}

# 市独自の屋外広告物条例の写しを持つ市(data/ordinances/)
CITY_ORDINANCES = ("越谷市", "さいたま市")

COVERAGE_NOTE_SAITAMA_OTHER = "埼玉県(市独自条例の有無は未確認)"

_CODE_NOISE = re.compile(r"^(?:〒\s*)?\d{3}\s*[-‐ー−]?\s*\d{4}\s*")


def normalize_address(address: str) -> str:
    s = unicodedata.normalize("NFKC", address or "")
    s = re.sub(r"[\s　]+", "", s)
    s = _CODE_NOISE.sub("", s) if s.startswith("〒") or re.match(r"^\d{3}[-‐ー−]?\d{4}", s) else s
    s = s.lstrip("〒")
    return s


def _longest_prefix(s: str, names: tuple[str, ...]) -> str | None:
    best = None
    for n in names:
        if s.startswith(n) and (best is None or len(n) > len(best)):
            best = n
    return best


class AddressResolver(MunicipalityResolver):
    """住所文字列から Municipality を返す(東京都・埼玉県の区市町村まで。他県は都道府県のみ)。"""

    def resolve(self, address: str) -> Municipality | None:
        s = normalize_address(address)
        if not s:
            return None
        pref = next((p for p in PREFECTURES if s.startswith(p)), None)
        if pref is not None:
            rest = s[len(pref):]
        else:
            # 都道府県名が無い: 一意と分かる名前だけで補う
            name = _longest_prefix(s, tuple(_UNIQUE_WITHOUT_PREFECTURE))
            if name is None:
                return None
            return Municipality(_UNIQUE_WITHOUT_PREFECTURE[name], name)
        if pref == "東京都":
            return Municipality(pref, _longest_prefix(_strip_gun(rest), TOKYO_MUNICIPALITIES))
        if pref == "埼玉県":
            return Municipality(pref, _longest_prefix(_strip_gun(rest), SAITAMA_MUNICIPALITIES))
        return Municipality(pref, None)


def _strip_gun(rest: str) -> str:
    """"西多摩郡日の出町..." / "入間郡三芳町..." の 郡 を落とす。"""
    m = re.match(r"^[^\d市区]{1,6}?郡(.+)$", rest)
    return m.group(1) if m else rest


def ordinance_key(m: Municipality | None) -> str | None:
    """条例の写し(data/ordinances/)のキー。東京都 / 埼玉県 / 越谷市 / さいたま市。無ければ None。

    東京都 -> "東京都"(区市町村は都条例の下。区市町村独自の条例の有無は調べていない)
    埼玉県の 越谷市・さいたま市 -> 市独自条例。他の市町村 -> "埼玉県"(市独自条例の有無は未確認)
    """
    if m is None:
        return None
    if m.prefecture == "東京都":
        return "東京都"
    if m.prefecture == "埼玉県":
        return m.city if m.city in CITY_ORDINANCES else "埼玉県"
    return None


def coverage_note(m: Municipality | None) -> str | None:
    """判定の限界を1行で(画面に出す用)。"""
    if m is None:
        return "住所から自治体を特定できない"
    key = ordinance_key(m)
    if key is None:
        return f"{m.prefecture}の条例は未取得(対象は東京都・埼玉県)"
    if m.prefecture == "埼玉県" and key == "埼玉県":
        return COVERAGE_NOTE_SAITAMA_OTHER if m.city else "埼玉県(市町村が不明。市独自条例の有無は未確認)"
    if m.prefecture == "東京都" and m.city is None:
        return "東京都(区市町村が不明)"
    return None
