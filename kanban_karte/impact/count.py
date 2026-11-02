"""物件(得意先住所)× 指示書(寸法・用途)を結び、自家用・許可不要(= 点検義務の無い看板)を数える。

単位は『看板の指示書1件』(1件に複数の看板があっても1基と数える。面積はその中の最大)。
対象は直取引(代理店リスト外)の指示書で、得意先住所が条例を持つ4自治体内にあるもの。
代理店経由の指示書は住所が看板の場所ではないので、件数だけ『住所未確定』として別に出す。

代理店かどうかは、(1) 下の一般語(看板屋・広告・建設 等)を名前に含む、(2) data/private/agencies.txt
(未追跡。1行1語の部分一致)に載っている、のどちらか。物件の得意先名と、紐づく見積の得意先名の
どちらかが当たれば代理店扱い(住所が現地でない可能性が少しでもあれば、数に入れない側に倒す)。
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from ..legal import judge as J
from ..legal.municipality import AddressResolver, Municipality, ordinance_key
from .extract import GENERAL, SELF, UNKNOWN, JUDGE_KIND, extract

PRIVATE_AGENCIES = Path(__file__).resolve().parents[2] / "data" / "private" / "agencies.txt"

# 代理店・施工元(看板屋・広告・建設・内装・住宅・ビル管理 等)と見なす一般語。社名ではない
GENERIC_AGENCY_WORDS = (
    "広告", "看板", "サイン", "宣伝", "宣広", "広美", "広芸", "工芸", "工藝", "美巧", "企画", "デザイン",
    "エージェンシー", "建装", "建設", "建築", "工務店", "内装", "設計", "ハウジング", "ホーム", "住宅",
    "ハウス", "ビルド", "施工", "装飾", "照明", "印刷", "プリント", "ディスプレイ", "ビル管理", "ビルシステム",
    "アドサイン", "アド・",
)
GROUPS = ("東京都", "埼玉県(越谷市・さいたま市以外)", "越谷市", "さいたま市")
OTHER = "対象外(他県・住所不明)"


def _norm_name(name: str | None) -> str:
    s = unicodedata.normalize("NFKC", name or "")
    s = re.sub(r"[\s　]+", "", s)
    return re.sub(r"株式会社|有限会社|\(株\)|\(有\)|㈱|㈲", "", s)


def load_agency_words(path: Path | None = None) -> tuple[str, ...]:
    p = path or PRIVATE_AGENCIES
    words: list[str] = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                words.append(_norm_name(line))
    return tuple(w for w in words if w)


class AgencyMatcher:
    def __init__(self, private_words: tuple[str, ...] = (), generic: tuple[str, ...] = GENERIC_AGENCY_WORDS):
        self.words = tuple(private_words) + tuple(_norm_name(g) for g in generic)

    def is_agency(self, *names: str | None) -> bool:
        """名前が1つも無い(空)ときも、住所の主体が分からないので True(= 住所未確定側)。"""
        norm = [_norm_name(n) for n in names]
        if not any(norm):
            return True
        return any(w in n for n in norm if n for w in self.words)


def group_of(address: str, resolver: AddressResolver | None = None):
    """住所 -> (集計の区分, Municipality)。条例を持つ4自治体の外は OTHER。"""
    m = (resolver or AddressResolver()).resolve(address)
    key = ordinance_key(m)
    if key is None:
        return OTHER, m
    if key == "東京都":
        return "東京都", m
    if key == "埼玉県":
        return GROUPS[1], m
    return key, m  # 越谷市 / さいたま市


@lru_cache(maxsize=4096)
def _judge(key: str, pref: str, city: str | None, use: str, area: float | None, kind: str | None,
           place: str | None = None) -> str:
    res = J.judge(municipality=Municipality(pref, city), use=use, area_m2=area, kind=kind, place=place)
    return res.permit_required


# 母数の内訳(区分ごとの Counter のキー)
# 要実測の主因(先に当たった物を1つ): 寸法なし → 区分なし(用途地域の外・座標化できず・GIS なし) → 規則側(寸法も区分もあるのに基準が割れる)
MEASURE_REASONS = ("寸法なし", "区分なし", "規則側")

SELF_EXEMPT, PERMIT, MEASURE, USE_UNKNOWN = "自家用・許可不要(把握外)", "許可要", "要実測", "用途不明"


@dataclass
class GroupStat:
    instructions: int = 0          # 区分内の直取引の指示書(看板でないものも含む)
    base: int = 0                  # 母数: そのうち屋外の看板の種類が取れたもの
    bukken: set = field(default_factory=set)
    with_size: int = 0
    outcome: Counter = field(default_factory=Counter)      # SELF_EXEMPT / PERMIT / MEASURE / USE_UNKNOWN
    unknown_as_self: Counter = field(default_factory=Counter)  # 用途不明を自家用と仮定した判定
    use: Counter = field(default_factory=Counter)          # self / general / unknown
    detail: Counter = field(default_factory=Counter)       # 自家用/一般 × 判定
    # --with-zoning のときだけ使う
    zoning: Counter = field(default_factory=Counter)       # 設置場所の区分の取れ方(place.Placed.status)
    with_place: int = 0                                    # 区分(用途地域)を judge に渡せた件数
    base_outcome: Counter = field(default_factory=Counter)  # 区分を渡さなかった場合の内訳(区分なしの集計 = 前回の表)
    base_unknown_as_self: Counter = field(default_factory=Counter)
    measure_reason: Counter = field(default_factory=Counter)  # 自家用で『要実測』になった主因(MEASURE_REASONS)
    measure_reason_unknown: Counter = field(default_factory=Counter)  # 用途不明を自家用と仮定した『要実測』の主因


@dataclass
class Report:
    db_name: str
    total_instructions: int = 0
    extract: Counter = field(default_factory=Counter)
    groups: dict[str, GroupStat] = field(default_factory=lambda: defaultdict(GroupStat))
    agency_signs: int = 0          # 代理店経由で屋外の看板が取れた指示書(住所未確定)
    agency_instructions: int = 0
    kinds: Counter = field(default_factory=Counter)
    zoning_enabled: bool = False
    precision: Counter = field(default_factory=Counter)   # 座標化できた住所検索の粒度
    generated: str = ""


SQL = """
SELECT s.cSHIJISYO, s.sSHIJISYO, s.sseisakusiyou, b.sTOKUISAKI,
       COALESCE(b.sTOKUISAKIJUSYO1,'') || COALESCE(b.sTOKUISAKIJUSYO2,''),
       (SELECT group_concat(DISTINCT m.sTOKUISAKI) FROM r_shijisyo_mitsu x
          JOIN r_mitumori m ON m.cMITUMORI = x.cMITUMORI WHERE x.cSHIJISYO = s.cSHIJISYO)
FROM r_shijisyo s LEFT JOIN r_bukken b ON b.cBUKKEN = s.cSHIJISYO
"""


def run(db_path: str | Path, agency_words: tuple[str, ...] | None = None, rows=None, placer=None) -> Report:
    """rows を渡すとそれを使う(テスト用。SQL と同じ並びのタプル)。

    placer(place.ZonePlacer)を渡すと、母数の各指示書の住所を座標化 → 用途地域に直し、judge の place に渡す
    (用途地域ベースの暫定。禁止地域のうち用途地域で決まらないものは未判定)。区分を渡さない場合の内訳も base_outcome に持つ。"""
    matcher = AgencyMatcher(load_agency_words() if agency_words is None else agency_words)
    resolver = AddressResolver()
    rep = Report(db_name=Path(str(db_path)).name, generated=datetime.now().strftime("%Y-%m-%d %H:%M"),
                 zoning_enabled=placer is not None)
    con = None
    if rows is None:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        rows = con.execute(SQL)
    for cid, title, spec, tname, addr, mnames in rows:
        rep.total_instructions += 1
        e = extract(title, spec)
        rep.extract["指示書"] += 1
        rep.extract["屋外の看板の種類あり"] += e.is_outdoor_sign
        rep.extract["寸法あり(全指示書)"] += e.has_size
        if e.is_outdoor_sign:
            rep.extract["看板で寸法あり"] += e.has_size
            rep.extract[f"看板で用途:{e.use}"] += 1
            rep.kinds[e.kind] += 1
        agency = matcher.is_agency(tname, *(mnames or "").split(","))
        if agency:
            rep.agency_instructions += 1
            rep.agency_signs += e.is_outdoor_sign
            continue
        grp, muni = group_of(addr, resolver)
        g = rep.groups[grp]
        g.instructions += 1
        if not e.is_outdoor_sign or grp == OTHER:
            continue
        g.base += 1
        g.bukken.add(cid)
        g.with_size += e.has_size
        g.use[e.use] += 1
        kind = JUDGE_KIND.get(e.kind)
        key = ordinance_key(muni)
        city = muni.city if muni and muni.prefecture == "埼玉県" and key == muni.city else None
        placed = placer.place(addr, muni) if placer else None
        if placed:
            g.zoning[placed.status] += 1
            g.with_place += placed.vocab is not None
            if placed.precision and placed.status not in ("not_found", "error", "mismatch"):
                rep.precision[placed.precision] += 1
        place = placed.vocab if placed else None
        use = "general" if e.use == GENERAL else "self"      # 用途不明は『仮に自家用なら』で判定する
        r = _judge(key, muni.prefecture, city, use, e.area_m2, kind, place)
        r0 = _judge(key, muni.prefecture, city, use, e.area_m2, kind) if placed else r   # 区分なし(前回の集計)
        if e.use == UNKNOWN:
            g.unknown_as_self[r] += 1
            g.outcome[USE_UNKNOWN] += 1
            g.base_unknown_as_self[r0] += 1
            g.base_outcome[USE_UNKNOWN] += 1
            if r == "要実測":
                g.measure_reason_unknown[_measure_reason(e.area_m2, place, placer is not None)] += 1
        else:
            g.outcome[_bucket(e.use, r)] += 1
            g.base_outcome[_bucket(e.use, r0)] += 1
            g.detail[("自家用:" if e.use == SELF else "一般:") + r] += 1
            if e.use == SELF and r == "要実測":
                g.measure_reason[_measure_reason(e.area_m2, place, placer is not None)] += 1
    if con:
        con.close()
    return rep


def _measure_reason(area_m2, place, zoning_enabled: bool) -> str:
    """自家用の『要実測』の主因を1つに決める。寸法が無ければ『寸法なし』、区分(用途地域)を渡せていなければ『区分なし』
    (--with-zoning なしの集計では全部ここ)、両方あるのに決まらなければ『規則側』(基準が種類・区域で割れる、数値化されていない)。"""
    if area_m2 is None:
        return MEASURE_REASONS[0]
    if not zoning_enabled or place is None:
        return MEASURE_REASONS[1]
    return MEASURE_REASONS[2]


def _bucket(use: str, r: str) -> str:
    """用途が分かっているものの判定 -> 母数の内訳。"""
    if use == SELF:
        return {"不要": SELF_EXEMPT, "要": PERMIT}.get(r, MEASURE)
    return PERMIT if r == "要" else MEASURE


def _pct(a: int, b: int) -> str:
    return f"{a / b * 100:.1f}%" if b else "-"


def totals(rep: Report) -> GroupStat:
    t = GroupStat()
    for name in GROUPS:
        g = rep.groups.get(name)
        if not g:
            continue
        t.instructions += g.instructions
        t.base += g.base
        t.bukken |= g.bukken
        t.with_size += g.with_size
        t.outcome.update(g.outcome)
        t.unknown_as_self.update(g.unknown_as_self)
        t.use.update(g.use)
        t.detail.update(g.detail)
        t.zoning.update(g.zoning)
        t.with_place += g.with_place
        t.base_outcome.update(g.base_outcome)
        t.base_unknown_as_self.update(g.base_unknown_as_self)
        t.measure_reason.update(g.measure_reason)
        t.measure_reason_unknown.update(g.measure_reason_unknown)
    return t


ZONING_STATUS = (("zone", "用途地域あり"), ("outside", "用途地域の外(調整区域等)"), ("no_data", "GISにその自治体が無い"),
                 ("not_found", "座標化できず: 住所検索に該当なし"), ("mismatch", "座標化できず: 座標が別の自治体"),
                 ("error", "座標化できず: 通信エラー"))


PRECISION_LABEL = {"lot": "番地・号まで", "chome": "丁目まで", "area": "町名まで"}


def _render_zoning(rep: Report, t: GroupStat) -> list[str]:
    L: list[str] = [""]
    L.append("### 区分なし(前回)との差")
    L.append("")
    L.append("用途地域ベースの暫定。禁止地域のうち用途地域で決まらないもの(風致・景観・文教・文化財周辺 等)は未判定のため、"
             "区分ありでも禁止区域を見落としている点がある。区分を渡せなかった指示書(下表)は区分なしと同じ判定。")
    L.append("")
    L.append("| 自治体 | 母数 | 自家用・許可不要 区分なし→あり | 許可要 区分なし→あり | 要実測 区分なし→あり | (不明を自家用と仮定した不要) 区分なし→あり |")
    L.append("|---|---:|---:|---:|---:|---:|")
    for name in (*GROUPS, "合計"):
        g = t if name == "合計" else rep.groups.get(name, GroupStat())
        o, b = g.outcome, g.base_outcome
        L.append(f"| {name} | {g.base} | {b[SELF_EXEMPT]} → {o[SELF_EXEMPT]} | {b[PERMIT]} → {o[PERMIT]} | "
                 f"{b[MEASURE]} → {o[MEASURE]} | {g.base_unknown_as_self['不要']} → {g.unknown_as_self['不要']} |")
    L.append("")
    L.append("### 要実測の内訳(主因を1つ。自家用 / 用途不明を自家用と仮定)")
    L.append("")
    L.append("寸法なし= 指示書の仕様文から W×H が取れない。区分なし= 用途地域の外(市街化調整区域等)・座標化できず・GIS にその自治体が無い。"
             "規則側= 寸法も用途地域もあるのに基準が割れる(埼玉県系の条例は許可区域の壁面・突出しの基準が比率や文言で数値化されていない等)。")
    L.append("")
    L.append("| 自治体 | 自家用・要実測 | " + " | ".join(MEASURE_REASONS) + " | 用途不明・要実測 | " + " | ".join(MEASURE_REASONS) + " |")
    L.append("|---|---:|" + "---:|" * (2 * len(MEASURE_REASONS) + 1))
    for name in (*GROUPS, "合計"):
        g = t if name == "合計" else rep.groups.get(name, GroupStat())
        L.append(f"| {name} | {sum(g.measure_reason.values())} | " + " | ".join(str(g.measure_reason[k]) for k in MEASURE_REASONS)
                 + f" | {sum(g.measure_reason_unknown.values())} | " + " | ".join(str(g.measure_reason_unknown[k]) for k in MEASURE_REASONS) + " |")
    L.append("")
    L.append("### 座標化と用途地域の取れ方(母数の指示書ごと)")
    L.append("")
    L.append("| 自治体 | 母数 | " + " | ".join(label for _, label in ZONING_STATUS) + " |")
    L.append("|---|---:|" + "---:|" * len(ZONING_STATUS))
    for name in (*GROUPS, "合計"):
        g = t if name == "合計" else rep.groups.get(name, GroupStat())
        L.append(f"| {name} | {g.base} | " + " | ".join(str(g.zoning[k]) for k, _ in ZONING_STATUS) + " |")
    failed = sum(t.zoning[k] for k in ("not_found", "mismatch", "error"))
    geocoded = t.base - failed
    L.append("")
    L.append(f"- 座標化できた: {geocoded} / {t.base} ({_pct(geocoded, t.base)})。できなかった: {failed} 件")
    L.append(f"- 区分(用途地域)を judge に渡せた: {t.with_place} / {t.base} ({_pct(t.with_place, t.base)})")
    if rep.precision:
        L.append("- 住所検索の粒度(座標化できたもの): " + "、".join(
            f"{PRECISION_LABEL[k]} {v}" for k, v in rep.precision.most_common()))
    return L


def render(rep: Report) -> str:
    ex = rep.extract
    L: list[str] = []
    L.append(f"# 把握の数字(自社版) {rep.generated}")
    L.append("")
    L.append(f"データ: {rep.db_name}(読むだけ)。件数と率のみ。単位は『看板の指示書1件』。")
    L.append("")
    t = totals(rep)
    lo = t.outcome[SELF_EXEMPT]
    hi = lo + t.unknown_as_self["不要"]
    L.append("## 要約")
    L.append("")
    L.append(f"- 母数(直取引・4自治体内・屋外の看板の指示書): **{t.base}** 件")
    L.append(f"- 自家用で許可不要(誰にも点検義務が無い看板、確定分): **{lo} 基({_pct(lo, t.base)})**")
    L.append(f"- 用途が不明のものを自家用と仮定すると最大: {hi} 基({_pct(hi, t.base)})")
    L.append(f"- 代理店経由で看板と取れた指示書は住所未確定: {rep.agency_signs} 件(この母数に入っていない)")
    L.append("")
    z = rep.zoning_enabled
    L.append("## 自治体別" + ("(用途地域あり。用途地域ベースの暫定)" if z else ""))
    L.append("")
    L.append("| 自治体 | 直取引の指示書 | 母数(屋外の看板) | 寸法あり | 自家用・許可不要 | 許可要 | 要実測 | 用途不明 | (不明を自家用と仮定した不要) |"
             + (" 区分あり |" if z else ""))
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|" + ("---:|" if z else ""))
    for name in (*GROUPS, "合計"):
        g = t if name == "合計" else rep.groups.get(name, GroupStat())
        o = g.outcome
        L.append(f"| {name} | {g.instructions} | {g.base} | {g.with_size} ({_pct(g.with_size, g.base)}) | "
                 f"{o[SELF_EXEMPT]} ({_pct(o[SELF_EXEMPT], g.base)}) | {o[PERMIT]} | {o[MEASURE]} | "
                 f"{o[USE_UNKNOWN]} | {g.unknown_as_self['不要']} |" + (f" {g.with_place} ({_pct(g.with_place, g.base)}) |" if z else ""))
    if z:
        L.extend(_render_zoning(rep, t))
    og = rep.groups.get(OTHER)
    if og:
        L.append("")
        L.append(f"対象外(他県・住所不明)の直取引の指示書: {og.instructions} 件(判定していない)")
    L.append("")
    L.append("## 住所未確定(代理店経由)")
    L.append("")
    L.append(f"- 代理店経由の指示書: {rep.agency_instructions} 件、うち屋外の看板が取れたもの: {rep.agency_signs} 件。"
             "得意先住所は看板の場所ではないため判定していない(店名→住所の後で)")
    L.append("")
    L.append("## 抽出の取れた割合")
    L.append("")
    n, k = ex["指示書"], ex["屋外の看板の種類あり"]
    L.append(f"- 全指示書 {n} 件のうち 屋外の看板の種類が取れた: {k} ({_pct(k, n)})")
    L.append(f"- 寸法(W×H)が取れた: 全指示書で {ex['寸法あり(全指示書)']} ({_pct(ex['寸法あり(全指示書)'], n)})、"
             f"看板の指示書では {ex['看板で寸法あり']} ({_pct(ex['看板で寸法あり'], k)})")
    L.append(f"- 看板の指示書の用途: 自家用 {ex['看板で用途:self']} ({_pct(ex['看板で用途:self'], k)}) / "
             f"一般 {ex['看板で用途:general']} ({_pct(ex['看板で用途:general'], k)}) / "
             f"不明 {ex['看板で用途:unknown']} ({_pct(ex['看板で用途:unknown'], k)})")
    L.append("- 種類の内訳: " + "、".join(f"{kk} {v}" for kk, v in rep.kinds.most_common()))
    L.append("")
    L.append("## 母数の内訳(判定の詳細)")
    L.append("")
    for k2, v in sorted(t.detail.items()):
        L.append(f"- {k2}: {v}")
    for k2, v in sorted(t.unknown_as_self.items()):
        L.append(f"- 用途不明(自家用と仮定):{k2}: {v}")
    L.append("")
    return "\n".join(L)


def write_run(rep: Report, runs_dir: Path | str = "runs") -> Path:
    d = Path(runs_dir)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"impact_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    p.write_text(render(rep), encoding="utf-8")
    return p
