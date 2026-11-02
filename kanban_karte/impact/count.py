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
def _judge(key: str, pref: str, city: str | None, use: str, area: float | None, kind: str | None) -> str:
    res = J.judge(municipality=Municipality(pref, city), use=use, area_m2=area, kind=kind)
    return res.permit_required


# 母数の内訳(区分ごとの Counter のキー)
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


@dataclass
class Report:
    db_name: str
    total_instructions: int = 0
    extract: Counter = field(default_factory=Counter)
    groups: dict[str, GroupStat] = field(default_factory=lambda: defaultdict(GroupStat))
    agency_signs: int = 0          # 代理店経由で屋外の看板が取れた指示書(住所未確定)
    agency_instructions: int = 0
    kinds: Counter = field(default_factory=Counter)
    generated: str = ""


SQL = """
SELECT s.cSHIJISYO, s.sSHIJISYO, s.sseisakusiyou, b.sTOKUISAKI,
       COALESCE(b.sTOKUISAKIJUSYO1,'') || COALESCE(b.sTOKUISAKIJUSYO2,''),
       (SELECT group_concat(DISTINCT m.sTOKUISAKI) FROM r_shijisyo_mitsu x
          JOIN r_mitumori m ON m.cMITUMORI = x.cMITUMORI WHERE x.cSHIJISYO = s.cSHIJISYO)
FROM r_shijisyo s LEFT JOIN r_bukken b ON b.cBUKKEN = s.cSHIJISYO
"""


def run(db_path: str | Path, agency_words: tuple[str, ...] | None = None, rows=None) -> Report:
    """rows を渡すとそれを使う(テスト用。SQL と同じ並びのタプル)。"""
    matcher = AgencyMatcher(load_agency_words() if agency_words is None else agency_words)
    resolver = AddressResolver()
    rep = Report(db_name=Path(str(db_path)).name, generated=datetime.now().strftime("%Y-%m-%d %H:%M"))
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
        if e.use == UNKNOWN:
            r = _judge(key, muni.prefecture, city, "self", e.area_m2, kind)
            g.unknown_as_self[r] += 1
            g.outcome[USE_UNKNOWN] += 1
        elif e.use == SELF:
            r = _judge(key, muni.prefecture, city, "self", e.area_m2, kind)
            g.outcome[{"不要": SELF_EXEMPT, "要": PERMIT}.get(r, MEASURE)] += 1
            g.detail[f"自家用:{r}"] += 1
        else:
            r = _judge(key, muni.prefecture, city, "general", e.area_m2, kind)
            g.outcome[PERMIT if r == "要" else MEASURE] += 1
            g.detail[f"一般:{r}"] += 1
    if con:
        con.close()
    return rep


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
    return t


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
    L.append("## 自治体別")
    L.append("")
    L.append("| 自治体 | 直取引の指示書 | 母数(屋外の看板) | 寸法あり | 自家用・許可不要 | 許可要 | 要実測 | 用途不明 | (不明を自家用と仮定した不要) |")
    L.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for name in (*GROUPS, "合計"):
        g = t if name == "合計" else rep.groups.get(name, GroupStat())
        o = g.outcome
        L.append(f"| {name} | {g.instructions} | {g.base} | {g.with_size} ({_pct(g.with_size, g.base)}) | "
                 f"{o[SELF_EXEMPT]} ({_pct(o[SELF_EXEMPT], g.base)}) | {o[PERMIT]} | {o[MEASURE]} | "
                 f"{o[USE_UNKNOWN]} | {g.unknown_as_self['不要']} |")
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
