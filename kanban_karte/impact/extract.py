"""指示書の件名・仕様文から 看板の種類 / 寸法 / 用途(自家用か一般広告か)を正規表現で抜く。

推測で埋めない: 取れなければ None / "unknown"。1つの指示書に複数の看板があっても、
種類は最初に当たったもの、面積は取れた寸法の最大(= 1基の最大面積)を採る。

屋外広告物かどうか(is_outdoor_sign)は『屋外の看板の種類を示す語がある』ことだけで決める。
シート出力・室内サイン・床シート等は対象外(屋内語だけのときは種類 "屋内")。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

SELF, GENERAL, UNKNOWN = "self", "general", "unknown"

# (種類, 正規表現)。上から順に当たったものを kinds に積み、先頭を主たる種類にする。
_KIND_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("のぼり", re.compile(r"のぼり|ノボリ|幟|登り旗")),
    ("屋上", re.compile(r"屋上")),
    ("突出", re.compile(r"袖看板|袖サイン|突出|突き出し|張出|張り出し")),
    ("自立", re.compile(r"自立|独立|ポール看板|ポールサイン|ポール|スタンド看板|野立|パイロン|サイン塔|看板塔|タワーサイン|広告塔|広告板")),
    ("壁面", re.compile(r"壁面|壁付|ファサード|チャンネル文字|箱文字|ＬＥＤ文字|LED文字|切文字|切り文字|外壁")),
    ("看板", re.compile(r"看板|サイン|SIGN|ｻｲﾝ")),
]
_INDOOR = re.compile(r"室内|店内|屋内|フロア|床|天吊り|天井|券売機|インターホン|デスク|什器|卓上|エレベーター|EV|ＥＶ|トイレ|館内")
# 屋外の看板の種類として数える種類(これ以外 = 屋内)
OUTDOOR_KINDS = ("のぼり", "屋上", "突出", "自立", "壁面", "看板")

# 判定側(legal.judge.KIND_KEYWORDS)に渡す呼び名
JUDGE_KIND = {"のぼり": "旗", "屋上": "屋上", "突出": "突出", "自立": "自立", "壁面": "壁面", "看板": None}

# 一般の広告(第三者の広告・場外の広告)を示す語
_GENERAL = re.compile(r"広告塔|貸看板|貸し看板|第三者|広告主|スポンサー|広告枠|媒体|アドボード|野立|分譲|現地看板|誘導看板|テナント募集|入居者募集|入居募集")
# 自己の店名・社名の表示を示す語(強い根拠)
_SELF_STRONG = re.compile(r"店名|社名|屋号|会社名|店舗名|法人名|医院名|院名|校名|教室名|館名|施設名|ロゴ|社章|銘板|表札|営業中|営業時間|屋号看板")
# 件名が『○○店』『○○医院』等で終わる語を含む(弱い根拠)
_SELF_WEAK = re.compile(r"[^\s|]{1,}(?:店|支店|営業所|医院|クリニック|薬局|病院|歯科|教室|学院|事務所|工場|センター)(?:[\s　]|$|様|ー|－|-)")

_NUM = r"(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_SEP = r"\s*[×x*＊✕]\s*"
_UNIT = r"\s*(mm|㎜|cm|m)?"
_WH = re.compile(rf"W\s*[=:]?\s*{_NUM}{_UNIT}{_SEP}H\s*[=:]?\s*{_NUM}{_UNIT}", re.I)
_HW = re.compile(rf"H\s*[=:]?\s*{_NUM}{_UNIT}{_SEP}W\s*[=:]?\s*{_NUM}{_UNIT}", re.I)
# 英字なしの 1200x600 / 1200*600*200(3つ目は厚み。先頭2つを採る)。両方が3桁以上の整数だけ
_PLAIN = re.compile(rf"(?<![\d.A-Za-z]){_NUM}{_UNIT}{_SEP}{_NUM}{_UNIT}(?:{_SEP}\d+)?(?![\d.])")

_MIN_SIDE_MM = 10.0
_MAX_SIDE_MM = 50000.0


@dataclass
class SignSpec:
    kinds: list[str] = field(default_factory=list)
    kind: str | None = None                 # 主たる種類(屋外の看板が無ければ "屋内" か None)
    is_outdoor_sign: bool = False
    sizes_mm: list[tuple[float, float]] = field(default_factory=list)   # (W, H)
    area_m2: float | None = None            # 取れた寸法の最大面積
    use: str = UNKNOWN                      # self / general / unknown
    use_evidence: str | None = None

    @property
    def has_size(self) -> bool:
        return self.area_m2 is not None


def normalize(text: str | None) -> str:
    s = unicodedata.normalize("NFKC", text or "")
    return s.replace("\r", "\n")


def _to_mm(val: str, unit: str | None, other_unit: str | None) -> float | None:
    v = float(val.replace(",", ""))
    u = (unit or other_unit or "").lower()
    if u in ("mm", "㎜"):
        return v
    if u == "cm":
        return v * 10
    if u == "m":
        return v * 1000
    return v  # 単位なしは mm(看板の指示書の慣習)。小数で 50 未満の組は _plausible で落とす


def _plausible(w: float, h: float, bare_unit: bool) -> bool:
    if not (_MIN_SIDE_MM <= w <= _MAX_SIDE_MM and _MIN_SIDE_MM <= h <= _MAX_SIDE_MM):
        return False
    return True


def extract_sizes(text: str) -> list[tuple[float, float]]:
    """(W, H) mm のリスト。W/H 付き表記を優先し、無ければ『数×数』の素の表記(両辺 100 以上)を使う。"""
    s = normalize(text)
    out: list[tuple[float, float]] = []
    for pat, order in ((_WH, "wh"), (_HW, "hw")):
        for m in pat.finditer(s):
            a, ua, b, ub = m.group(1), m.group(2), m.group(3), m.group(4)
            va, vb = _to_mm(a, ua, ub), _to_mm(b, ub, ua)
            if va is None or vb is None:
                continue
            # 単位なしの小数だけ(例: 1.2×0.6)は m と mm のどちらか決められないので捨てる
            if not (ua or ub) and ("." in a or "." in b) and max(va, vb) < 50:
                continue
            w, h = (va, vb) if order == "wh" else (vb, va)
            if _plausible(w, h, not (ua or ub)):
                out.append((w, h))
    if out:
        return out
    for m in _PLAIN.finditer(s):
        a, ua, b, ub = m.group(1), m.group(2), m.group(3), m.group(4)
        va, vb = _to_mm(a, ua, ub), _to_mm(b, ub, ua)
        if va is None or vb is None or min(va, vb) < 100:
            continue
        if _plausible(va, vb, not (ua or ub)):
            out.append((va, vb))
    return out


def extract_kinds(text: str) -> list[str]:
    s = normalize(text)
    kinds = [k for k, pat in _KIND_PATTERNS if pat.search(s)]
    return kinds


def classify_use(title: str, spec: str) -> tuple[str, str | None]:
    t, p = normalize(title), normalize(spec)
    both = t + "\n" + p
    m = _GENERAL.search(both)
    if m:
        return GENERAL, f"一般:{m.group(0)}"
    m = _SELF_STRONG.search(both)
    if m:
        return SELF, f"自家用:{m.group(0)}"
    m = _SELF_WEAK.search(t)
    if m:
        return SELF, "自家用(弱):件名が店・施設名"
    return UNKNOWN, None


def extract(title: str | None, spec: str | None) -> SignSpec:
    t, p = normalize(title), normalize(spec)
    both = t + "\n" + p
    kinds = extract_kinds(both)
    out = SignSpec(kinds=kinds)
    outdoor = [k for k in kinds if k in OUTDOOR_KINDS]
    if outdoor:
        # 『看板』だけの汎用語より具体的な種類を主にする
        specific = [k for k in outdoor if k != "看板"]
        out.kind = specific[0] if specific else "看板"
        out.is_outdoor_sign = True
        # 室内語しか文脈が無い『サイン』は屋内に倒す(『サイン』のみ・屋内語あり・具体的な種類なし)
        if out.kind == "看板" and _INDOOR.search(both) and not re.search(r"看板", both):
            out.kind, out.is_outdoor_sign = "屋内", False
    elif _INDOOR.search(both):
        out.kind = "屋内"
    out.sizes_mm = extract_sizes(both)
    if out.sizes_mm:
        out.area_m2 = round(max(w * h for w, h in out.sizes_mm) / 1e6, 4)
    out.use, out.use_evidence = classify_use(t, p)
    return out
