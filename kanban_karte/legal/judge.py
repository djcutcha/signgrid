"""看板1枚の 自家用か / 許可が要るか / 許可期間 / 点検義務 を、抽出済みの規則(rules_<自治体>.json)から判定する。

入力: 自治体(住所 or 条例キー)、用途(self=自家用 / general=一般)、表示面積㎡・高さm・地上高m、
      分かれば 設置場所の区分(place)と看板の種類(kind)。
出力: JudgeResult(to_dict() で JSON 化)
  is_self_use_exempt : 自家用の許可不要基準に収まるか(True/False/None=決められない。用途が一般なら False)
  permit_required    : "要" / "不要" / "要実測"   <- 3段階。判定に要る寸法・区分が足りないときは 要実測
  permit_term_years  : 許可期間(年。上限が書いてある場合は上限。許可が不要なら None)
  inspection_required: "要" / "不要" / "要実測"
  citations          : 根拠条文(条・項・号と原文の抜粋)
  confidence         : 根拠に使った規則の confidence の最小値(AI抽出の自己申告で、検算前)
  branches           : 要実測のとき『区域がこうなら 要/不要』の場合分け

判定の規則(保守側に倒す):
  - 基準は「その面積以下なら許可不要」。境界値(面積 = 閾値)は基準内。
  - 適用できる基準が複数あって(種類・区域が絞れていない)結論が割れるときは 要実測。
    全部の基準に収まれば 不要、全部の基準を超えれば 要。
  - 表示面積が None なら無条件に 要実測。高さ・地上高が None で、それを要する基準があれば 要実測(超過が確定していれば 要)。
  - 数値で書かれていない基準(壁面面積の何分の一 等)しか無い種類は 要実測。
  - 検算前の第1版を使う判定は、すべて status に『検算前』を付けて返す。
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from pathlib import Path

from . import rules_extract as RX
from . import schema as S
from .municipality import AddressResolver, Municipality, coverage_note, ordinance_key

REQUIRED, NOT_REQUIRED, NEEDS_MEASURE = "要", "不要", "要実測"

# 台帳の種類(自立・壁面 等)や日本語の呼び名 -> 基準の kind に含まれる語
KIND_KEYWORDS: dict[str, tuple[str, ...]] = {
    "自立": ("広告塔", "広告板", "独立"),
    "広告塔": ("広告塔", "広告板", "独立"),
    "広告板": ("広告塔", "広告板", "独立"),
    "野立": ("広告塔", "広告板", "独立"),
    "壁面": ("壁面",),
    "突出": ("突出",),
    "袖": ("突出",),
    "屋上": ("屋上",),
    "幕": ("広告幕",),
    "旗": ("広告旗",),
    "掛": ("掛看板",),
    "窓": ("窓",),
    "電柱": ("電柱",),
}
ALL_KIND_WORDS = ("全種類", "全般", "すべて", "全て")

PLACE_PERMIT = ("permit_zone", "許可区域", "許可地域")
PLACE_PROHIBITED = ("prohibited_zone", "禁止区域", "禁止地域")


@dataclass
class JudgeResult:
    ordinance_key: str | None
    municipality: str | None
    use: str
    is_self_use_exempt: bool | None
    permit_required: str
    permit_term_years: float | None
    inspection_required: str
    citations: list[dict] = field(default_factory=list)
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)
    branches: list[dict] = field(default_factory=list)
    status: str = S.STATUS_FIRST_DRAFT

    def to_dict(self) -> dict:
        return asdict(self)

    def to_ledger_fields(self) -> dict:
        """ledger の signs 表の列に合わせた値(1/0/None)。要実測は None(未確定)。"""
        pr = {REQUIRED: 1, NOT_REQUIRED: 0}.get(self.permit_required)
        return {"is_self_use": 1 if self.use == "self" else 0, "permit_required": pr}


# ---------- 部品 ----------

def _kind_words(kind: str | None) -> tuple[str, ...] | None:
    if not kind:
        return None
    words: list[str] = [kind]
    for k, ws in KIND_KEYWORDS.items():
        if k in kind:
            words.extend(ws)
    return tuple(dict.fromkeys(words))


def _kind_match(std_kind: str, words: tuple[str, ...] | None) -> bool:
    if words is None:
        return True
    if any(w in std_kind for w in ALL_KIND_WORDS):
        return True
    return any(w in std_kind for w in words)


def _norm_place(place: str | None, prohibited_labels: list[str]) -> tuple[str | None, str | None]:
    """(区分, 用途地域名)。区分は permit_zone / prohibited_zone / None。"""
    if not place:
        return None, None
    p = place.strip()
    if p in PLACE_PERMIT:
        return "permit_zone", None
    if p in PLACE_PROHIBITED:
        return "prohibited_zone", None
    if p in S.ZONE_VOCAB:
        return ("prohibited_zone" if p in prohibited_labels else "permit_zone"), p
    raise ValueError(f"設置場所の区分が分からない: {place!r}(permit_zone / prohibited_zone / 許可区域 / 禁止区域 / 用途地域名)")


def _std_applies(std: dict, pclass: str | None, plabel: str | None, prohibited_labels: list[str]) -> bool:
    z = std["zone"]
    if z == "all":
        return True
    if z == "other":
        labels = std.get("zone_labels") or []
        if not labels:
            return True  # 基準側に区域の語が無い -> 候補に残す
        if plabel is not None:
            return plabel in labels
        # 用途地域は不明でも、許可区域/禁止区域が分かっていれば、その側に有り得る基準だけ残す
        if pclass == "prohibited_zone":
            return any(l in prohibited_labels for l in labels)
        if pclass == "permit_zone":
            return any(l not in prohibited_labels for l in labels)
        return True
    if pclass is None:
        return True
    return z == pclass


def _numeric_count(std: dict) -> int:
    return sum(std.get(k) is not None for k in ("area_max_m2", "height_max_m", "ground_clearance_min_m"))


def _is_generic(std: dict) -> bool:
    return any(w in std["kind"] for w in ALL_KIND_WORDS)


def _eval_std(std: dict, area, height, gc) -> tuple[str, list[str]]:
    """基準1件への当てはめ。("within" | "exceeds" | "unknown", 理由)"""
    fail, unknown, checked = [], [], 0
    for key, val, label, mode in (("area_max_m2", area, "表示面積", "max"),
                                  ("height_max_m", height, "高さ", "max"),
                                  ("ground_clearance_min_m", gc, "地上高", "min")):
        lim = std.get(key)
        if lim is None:
            continue
        checked += 1
        if val is None:
            unknown.append(f"{label}が不明")
        elif (mode == "max" and val > lim) or (mode == "min" and val < lim):
            fail.append(f"{label} {val} が基準 {lim}{'以下' if mode == 'max' else '以上'}を外れる")
    if fail:
        return "exceeds", fail
    if checked == 0:
        return "unknown", ["数値で書かれた基準が無い(壁面面積の比率など。条件欄を確認)"]
    if unknown:
        return "unknown", unknown
    return "within", []


def _cite(field_name: str, c: dict, idx: dict) -> dict:
    return {"field": field_name, "source": c.get("source"), "article": c.get("article"),
            "quote": c.get("quote"), "quote_verified": c.get("quote_verified")}


def _add_cites(out: list[dict], field_name: str, node: dict):
    for c in node.get("citations", []):
        row = _cite(field_name, c, {})
        key = (row["source"], row["article"], row["quote"])
        if not any((o["source"], o["article"], o["quote"]) == key for o in out):
            out.append(row)


def _std_label(std: dict) -> str:
    z = {"all": "区域を問わず", "permit_zone": "許可区域", "prohibited_zone": "禁止区域"}.get(std["zone"])
    if z is None:
        z = "・".join(std.get("zone_labels") or []) or (std.get("zone_note") or "特定の区域")
    return f"{std['kind']}({z})"


# ---------- 判定 ----------

def judge(address: str | None = None, *, municipality: str | Municipality | None = None, use: str = "self",
          area_m2: float | None = None, height_m: float | None = None, ground_clearance_m: float | None = None,
          place: str | None = None, kind: str | None = None,
          rules: dict | None = None, data_dir: Path | None = None) -> JudgeResult:
    """判定する。municipality は 条例キー("越谷市" 等)/Municipality。address だけ渡せば住所から引く。"""
    if use not in ("self", "general"):
        raise ValueError("use は self か general")
    m: Municipality | None
    if isinstance(municipality, Municipality):
        m = municipality
    elif isinstance(municipality, str):
        if municipality not in RX.ORDINANCE_FILES:
            raise ValueError(f"条例キーは {list(RX.ORDINANCE_FILES)} のどれか: {municipality!r}")
        m = (Municipality("東京都") if municipality == "東京都" else
             Municipality("埼玉県", None if municipality == "埼玉県" else municipality))
    else:
        m = AddressResolver().resolve(address) if address else None
    key = ordinance_key(m)
    res = JudgeResult(ordinance_key=key, municipality=(m.city or m.prefecture) if m else None, use=use,
                      is_self_use_exempt=None, permit_required=NEEDS_MEASURE, permit_term_years=None,
                      inspection_required=NEEDS_MEASURE)
    note = coverage_note(m)
    if note:
        res.notes.append(note)
    if key is None:
        res.notes.append("条例の写しが無いため判定できない")
        return res
    doc_status = None
    if rules is None:
        try:
            doc = RX.load_rules(key, data_dir)
        except FileNotFoundError as e:
            res.notes.append(str(e))
            return res
        rules = doc["rules"]
        doc_status = doc.get("status")
    R = rules
    if doc_status:
        res.status = doc_status

    pclass, plabel = _norm_place(place, R["prohibited_zones"].get("labels", []))
    words = _kind_words(kind)
    confs: list[float] = []

    # ----- 許可の要否 -----
    if use == "general":
        res.is_self_use_exempt = False
        general = [t for t in R["permit_required_types"]
                   if t.get("area_threshold_m2") is None and t.get("height_threshold_m") is None
                   and t.get("ground_clearance_threshold_m") is None]
        if general:
            res.permit_required = REQUIRED
            for t in general:
                _add_cites(res.citations, "permit_required", t)
                confs.append(t["confidence"])
        else:
            res.notes.append("面積によらず許可が要るとする規則が見つからない(閾値型のみ)。要実測とする")
        if pclass == "prohibited_zone":
            res.notes.append("禁止区域に当たる場合、一般の広告物は原則表示できない(禁止区域の条文を確認)")
            _add_cites(res.citations, "prohibited_zones", R["prohibited_zones"])
    else:
        su = R["self_use"]
        if su.get("exempt_from_permit") is False:
            res.is_self_use_exempt = False
            res.permit_required = REQUIRED
            res.notes.append("自家用広告物でも許可不要の扱いが無い")
        elif su.get("exempt_from_permit") is None:
            res.notes.append("自家用広告物の許可不要の扱いが原文から読み取れていない")
        elif area_m2 is None:
            res.notes.append("表示面積が不明なため判定できない(実測が要る)")
            res.branches = []
        else:
            cands = [s for s in su["standards"]
                     if _std_applies(s, pclass, plabel, R["prohibited_zones"].get("labels", [])) and _kind_match(s["kind"], words)]
            if not cands:
                res.notes.append("該当する基準が規則に見つからない(種類・区域の指定を確認)")
            else:
                evals = [(s, *_eval_std(s, area_m2, height_m, ground_clearance_m)) for s in cands]
                # 種類を問わない(全種類)基準で数値が無いもの(色彩・光源の禁止事項など)は、数値の基準が他にあれば
                # 判定から外し、注に残す。種類を限った基準で数値が無いもの(壁面面積の比率など)は 要実測 の材料に残す。
                generic_cond_only = [e for e in evals if _is_generic(e[0]) and _numeric_count(e[0]) == 0]
                if generic_cond_only and len(generic_cond_only) < len(evals):
                    evals = [e for e in evals if e not in generic_cond_only]
                    res.notes.append("数値以外の基準(色彩・光源など)は判定に含めていない。条件欄を確認")
                verdicts = {v for _, v, _ in evals}
                for s, v, why in evals:
                    res.branches.append({"when": _std_label(s), "verdict":
                                         {"within": NOT_REQUIRED, "exceeds": REQUIRED, "unknown": NEEDS_MEASURE}[v],
                                         "reason": "; ".join(why) or "基準内"})
                if verdicts == {"within"}:
                    res.is_self_use_exempt, res.permit_required = True, NOT_REQUIRED
                elif verdicts == {"exceeds"}:
                    res.is_self_use_exempt, res.permit_required = False, REQUIRED
                else:
                    res.notes.append("設置場所の区分(用途地域・許可/禁止区域)や看板の種類を絞ると確定できる" if
                                     (pclass is None or words is None) else "基準の数値だけでは確定できない(条件欄を確認)")
                if res.permit_required != NEEDS_MEASURE:
                    res.branches = []  # 全基準が同じ結論なら場合分けは出さない
                for s in cands:
                    _add_cites(res.citations, "self_use_standard", s)
                    confs.append(s["confidence"])
                    if s.get("conditions") and res.permit_required == NOT_REQUIRED:
                        res.notes.append(f"{_std_label(s)}: 数値以外の条件もある: {s['conditions'][:120]}")
        if res.permit_required == REQUIRED and pclass == "prohibited_zone":
            pz = su["prohibited_zone_permit"]
            _add_cites(res.citations, "prohibited_zone_permit", pz)
            confs.append(pz["confidence"])
            if pz["required"] == "not_allowed":
                res.notes.append("禁止区域では基準を超える自家用広告物は表示できない可能性がある")
            elif pz["required"] == "permit_possible":
                res.notes.append("禁止区域でも、許可を受ければ表示できる")

    # ----- 許可期間 -----
    if res.permit_required != NOT_REQUIRED:
        pt = R["permit_term"]
        item = None
        if words is not None:
            item = next((i for i in pt["items"] if any(w in i["kind"] for w in words)), None)
        years = item["years"] if item else pt.get("default_years")
        if years is not None:
            res.permit_term_years = years
            src = item if item else (pt["items"][0] if pt["items"] else None)
            if src:
                _add_cites(res.citations, "permit_term", src)
                confs.append(src["confidence"])
            others = sorted({(i["kind"][:14], i["years"]) for i in pt["items"] if i["years"] != years})
            if others and item is None:
                res.notes.append("許可期間は種類で違う: " + "、".join(f"{k}={y}年" for k, y in others))
            if pt["items"] and all(i.get("is_maximum") for i in pt["items"]):
                res.notes.append("許可期間は上限(◯年以内)。実際の期間は許可書で確認")

    # ----- 許可の基準(東京都 別表第三。高さ・地上高・出幅)-----
    # 許可不要かどうかの判定には使わない。寸法が基準を外れるときだけ注に出す(種類が分かっているときのみ)。
    if words is not None and (height_m is not None or ground_clearance_m is not None):
        for ps in R.get("permit_standards") or []:
            if (ps.get("self_use_only") and use != "self") or not _kind_match(ps["kind"], words):
                continue
            when = f"・{ps['when']}" if ps.get("when") else ""
            for val, lim, label, over in ((height_m, ps.get("height_max_m"), "高さ", True),
                                          (ground_clearance_m, ps.get("ground_clearance_min_m"), "地上高", False)):
                if val is None or lim is None or not ((val > lim) if over else (val < lim)):
                    continue
                res.notes.append(f"{label} {val}m は許可の基準({ps['kind']}{when})の{lim}m{'以下' if over else '以上'}を外れる。"
                                 "該当する条件か確認(基準は条件ごとに別項目)")
                _add_cites(res.citations, "permit_standard", ps)
                confs.append(ps["confidence"])

    # ----- 点検 -----
    ins = R["inspection"]
    r = ins["required"]
    if r == "no":
        res.inspection_required = NOT_REQUIRED
    elif r == "unknown":
        res.inspection_required = NEEDS_MEASURE
        res.notes.append("点検義務の有無が原文から読み取れていない")
    else:
        thr = [c for c in ins["conditions"] if c.get("area_min_m2") is not None
               or c.get("height_min_m") is not None or c.get("ground_clearance_min_m") is not None]
        if thr:
            hit = unk = False
            for c in thr:
                for val, lim in ((area_m2, c.get("area_min_m2")), (height_m, c.get("height_min_m")),
                                 (ground_clearance_m, c.get("ground_clearance_min_m"))):
                    if lim is None:
                        continue
                    if val is None:
                        unk = True
                    elif val >= lim:
                        hit = True
            res.inspection_required = REQUIRED if hit else (NEEDS_MEASURE if unk else NOT_REQUIRED)
            res.notes.append("点検の対象規模は『" + thr[0]["description"][:100] + "』(閾値は以上で判定)")
        elif res.permit_required == REQUIRED:
            res.inspection_required = REQUIRED
        elif res.permit_required == NOT_REQUIRED:
            if ins.get("applies_to_self_use") is True:
                res.inspection_required = REQUIRED
            elif ins.get("applies_to_self_use") is False:
                res.inspection_required = NOT_REQUIRED
            else:
                res.inspection_required = NEEDS_MEASURE
                res.notes.append("点検義務は許可に結びつく規定。許可不要の自家用広告物に及ぶかは原文から断定できない")
        else:
            res.inspection_required = NEEDS_MEASURE
        if ins.get("qualification"):
            res.notes.append(f"点検できる者: {ins['qualification'][:80]}")
        if ins.get("form"):
            res.notes.append(f"報告の様式: {ins['form']}")
        if ins.get("interval_years") is not None:
            res.notes.append(f"点検の周期: {ins['interval_years']}年(抽出値。要検算)")
    _add_cites(res.citations, "inspection", ins)
    if r not in ("no", "unknown"):
        confs.append(ins["confidence"])

    # 検算されていない引用が根拠にあるときは信頼度を下げる
    res.confidence = round(min(confs), 2) if confs else 0.0
    if any(c.get("quote_verified") is False for c in res.citations):
        res.confidence = round(max(0.0, res.confidence - 0.2), 2)
        res.notes.append("原文に一致しない引用がある(引用の機械照合で不一致)")
    return res
