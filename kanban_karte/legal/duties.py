"""看板1枚への 4つの義務(管理 / 点検 / 有資格者に点検させる / 行政へ報告)の判定。

条例ごとに掛かり方が違うので、judge の 1 本の『点検』に畳まず、義務ごとに rules(maintenance_duty / inspection_duty /
qualified_inspector / report_duty)の conditions を看板の寸法・許可の要否・種類に当てはめる。
  値: 要 / 努力(有資格者だけ。努力義務)/ 不要 / 要実測(判定に要る値が足りない)
  - 許可不要の自家用でも、conditions.permit_required_only が false の義務は条例どおりに掛かる(許可の要否に連動するのは true の義務だけ)。
  - 条件は AND(上端 4m 超 かつ 許可要 など)。1つでも外れていれば 不要、外れが無く未確定が残れば 要実測。
  - 種類で絞る条件(kinds)は、種類が分からなければ 要実測。除外の種類(excluded_kinds)は、種類が分からなければ除外しない。
  - 高さは『上端の高さ(全高、地上から)』。『超える』は height_min_inclusive=false(境界値は対象外)。
"""
from __future__ import annotations

import re

REQUIRED, EFFORT, NOT_REQUIRED, NEEDS_MEASURE = "要", "努力", "不要", "要実測"

TITLES = {"maintenance": "管理", "inspection": "点検", "qualified": "有資格者", "report": "行政への報告"}
RULE_KEYS = {"maintenance": "maintenance_duty", "inspection": "inspection_duty",
             "qualified": "qualified_inspector", "report": "report_duty"}


def _g(x) -> str:
    return f"{x:g}"


def _kind_hit(kinds: list[str], words: tuple[str, ...]) -> bool:
    return any(w in k or k in w for k in kinds for w in words)


def _num_check(c: dict, area, height, gc) -> tuple[list[str], list[str]]:
    """寸法の下限(面積・上端の高さ・地上高)を看板に当てる。(外れた理由の一覧, 未確定の理由の一覧)。"""
    miss: list[str] = []
    unknown: list[str] = []
    incl = c.get("height_min_inclusive")
    incl = True if incl is None else incl
    ainc = c.get("area_min_inclusive")
    ainc = True if ainc is None else ainc
    for val, lim, label, strict in ((area, c.get("area_min_m2"), "表示面積", not ainc),
                                    (height, c.get("height_min_m"), "上端の高さ", not incl),
                                    (gc, c.get("ground_clearance_min_m"), "地上高", False)):
        if lim is None:
            continue
        if val is None:
            unknown.append("全高が分からない" if label == "上端の高さ" else f"{label}が分からない")
        elif (val > lim) if strict else (val >= lim):
            continue
        else:
            miss.append(f"{label} {_g(val)}{'㎡' if label == '表示面積' else 'm'} が {_g(lim)}{'㎡' if label == '表示面積' else 'm'}{'を超えない' if strict else '未満'}ため")
    return miss, unknown


def eval_conditions(c: dict | None, *, area, height, gc, permit: str, words, permit_only: bool | None = None) -> tuple[str, str]:
    """conditions を看板に当てる。("hit" | "miss" | "unknown", 理由)。miss が1つでもあれば miss(理由はその1つ)。"""
    c = c or {}
    miss: list[str] = []
    unknown: list[str] = []
    if permit_only is None:
        permit_only = bool(c.get("permit_required_only"))
    if permit_only:
        if permit == NOT_REQUIRED:
            miss.append("許可不要のため")
        elif permit == NEEDS_MEASURE:
            unknown.append("許可の要否が決まっていない")
    kinds = c.get("kinds") or []
    if kinds:
        if words is None:
            unknown.append("看板の種類が分からない")
        elif not _kind_hit(kinds, words):
            miss.append("対象の種類でないため")
    ex = c.get("excluded_kinds") or []
    if ex and words is not None and _kind_hit(ex, words):
        miss.append("対象外の種類のため")
    # size_kinds: 寸法の条件(any_of を含む)がこの種類にだけ掛かる。それ以外の種類(例: アーチ)は寸法によらない
    size_kinds = c.get("size_kinds") or []
    size_applies = not (size_kinds and (words is None or not _kind_hit(size_kinds, words)))   # 種類不明なら寸法で外さない(上で要実測)
    any_of = (c.get("any_of") or []) if size_applies else []
    if any_of:
        # OR 条件(例: 高さ4m超 または 表示面積10㎡超)。どれか1つ満たせば満たす。満たすものが無く未確定が残れば unknown
        alts = [_num_check(a, area, height, gc) for a in any_of]
        if not any(not m and not u for m, u in alts):
            if any(not m for m, _ in alts):
                unknown.append("または".join(dict.fromkeys(w for m, u in alts if not m for w in u)))
            else:
                ms = [m[0] for m, _ in alts]
                miss.append("、".join(x.removesuffix("ため") if i < len(ms) - 1 else x for i, x in enumerate(ms)))
    if size_applies:
        m, u = _num_check(c, area, height, gc)
        miss += m
        unknown += u
    if miss:
        return "miss", miss[0]
    if unknown:
        return "unknown", "、".join(unknown)
    return "hit", ""


def cond_text(c: dict | None, permit_only: bool | None = None) -> str:
    """conditions を画面用の短い文に(例: 上端 4m 超かつ許可要)。"""
    c = c or {}
    parts = []
    if c.get("area_min_m2") is not None:
        parts.append(f"面積 {_g(c['area_min_m2'])}㎡ {'以上' if c.get('area_min_inclusive') is not False else '超'}")
    if c.get("height_min_m") is not None:
        inc = c.get("height_min_inclusive")
        parts.append(f"上端 {_g(c['height_min_m'])}m {'以上' if (True if inc is None else inc) else '超'}")
    if c.get("ground_clearance_min_m") is not None:
        parts.append(f"地上高 {_g(c['ground_clearance_min_m'])}m 以上")
    if c.get("any_of"):
        alts = [cond_text({**a, "kinds": [], "permit_required_only": False}) for a in c["any_of"]]
        parts.append("または".join(x for x in alts if x))
    if c.get("kinds"):
        parts.append("・".join(c["kinds"]) + "に限る")
    if permit_only if permit_only is not None else c.get("permit_required_only"):
        parts.append("許可要")
    return "かつ".join(parts)


_BASE = re.compile(r"^(第[0-9〇一二三四五六七八九十百]+条(?:の[0-9〇一二三四五六七八九十]+)*)(.*)$")
_PARA = re.compile(r"第[0-9〇一二三四五六七八九十百]+項")


def ref_text(key: str | None, cites: list[dict], per_source: int = 2) -> list[str]:
    """引用 -> 『埼玉県条例 第14条の2 第1項・第3項』『規則 第10条の4』。条ごとにまとめ(号は省く)、条例・規則それぞれ先頭 per_source 条まで。"""
    groups: dict[str, dict[str, list[str]]] = {"条例": {}, "施行規則": {}}
    for c in cites:
        m = _BASE.match((c.get("article") or "").strip())
        if not m or c.get("source") not in groups:
            continue
        paras = groups[c["source"]].setdefault(m.group(1), [])
        pm = _PARA.match(m.group(2))
        if pm and pm.group(0) not in paras:
            paras.append(pm.group(0))
    out: list[str] = []
    for src, pre in (("条例", f"{key}条例" if key else "条例"), ("施行規則", "規則")):
        items = [f"{base} {'・'.join(paras)}" if paras else base for base, paras in list(groups[src].items())[:per_source]]
        if items:
            out.append(f"{pre} " + "、".join(items))
    return out


def _refs_in(refs: list[str]) -> str:
    return "、".join(refs)


def _paren(*parts: str) -> str:
    p = [x for x in parts if x]
    return f"({'、'.join(p)})" if p else ""


def _paren_why(why: str, refs: str) -> str:
    """理由と条文: (許可不要のため。埼玉県条例 第14条の2 第4項)"""
    return f"({'。'.join(x for x in (why, refs) if x)})" if (why or refs) else ""


def _entry(kind: str, value: str, text: str, refs: list[str], reason: str = "", **extra) -> dict:
    d = {"title": TITLES[kind], "value": value, "text": text, "refs": refs, "reason": reason}
    d.update(extra)
    return d


def judge_duties(R: dict, key: str | None, *, area, height, gc, permit: str, words) -> dict:
    """rules R(4つの義務の形)から {"maintenance": entry, "inspection": entry, "qualified": entry, "report": entry, "_cites": [(field, node)], "_confs": [..]}。
    entry = {title, value, text(画面の1行。条文つき), refs, reason, ...}"""
    kw = dict(area=area, height=height, gc=gc, permit=permit, words=words)
    out: dict = {}
    cites: list[tuple[str, dict]] = []
    confs: list[float] = []

    def use(field: str, node: dict, active: bool = True):
        if node.get("citations"):
            cites.append((field, node))
            if active and node.get("required") not in ("no", "unknown"):
                confs.append(node["confidence"])

    # ----- 管理義務 -----
    md = R["maintenance_duty"]
    refs = ref_text(key, md.get("citations", []))
    use("maintenance_duty", md)
    if md["required"] == "unknown":
        out["maintenance"] = _entry("maintenance", NEEDS_MEASURE, "原文から読み取れていません", refs, "原文から読み取れていない")
    elif md["required"] == "no":
        out["maintenance"] = _entry("maintenance", NOT_REQUIRED, "管理義務の規定は見当たりません", refs, "規定なし")
    else:
        st, why = eval_conditions(md.get("conditions"), **kw)
        v = {"hit": REQUIRED, "miss": NOT_REQUIRED, "unknown": NEEDS_MEASURE}[st]
        t = {REQUIRED: f"良好な状態に保つ義務 あり{_paren(_refs_in(refs))}",
             NOT_REQUIRED: f"義務なし{_paren(why)}", NEEDS_MEASURE: f"要実測{_paren(why)}"}[v]
        out["maintenance"] = _entry("maintenance", v, t, refs, why, subject=md.get("subject"))

    # ----- 点検する義務 -----
    idt = R["inspection_duty"]
    refs = ref_text(key, idt.get("citations", []))
    use("inspection_duty", idt)
    excl = list(idt.get("exclusions") or [])
    interval = idt.get("interval_years")
    if idt["required"] == "unknown":
        v, why = NEEDS_MEASURE, "原文から読み取れていない"
        t = "原文から読み取れていません"
    elif idt["required"] == "no":
        v, why = NOT_REQUIRED, "規定なし" if idt.get("absent_in_text") else "義務を置いていない"
        t = "条例・規則に点検の義務の規定は見当たりません" if idt.get("absent_in_text") else f"点検の義務なし{_paren(_refs_in(refs))}"
    else:
        st, why = eval_conditions(idt.get("conditions"), **kw)
        v = {"hit": REQUIRED, "miss": NOT_REQUIRED, "unknown": NEEDS_MEASURE}[st]
        if v == REQUIRED:
            t = (f"{_g(interval)}年ごとに点検 義務あり" if interval is not None else "点検する義務 あり") + _paren(_refs_in(refs))
        elif v == NOT_REQUIRED:
            t = f"点検の義務の対象外{_paren_why(why, _refs_in(refs))}"
        else:
            t = f"要実測{_paren(why)}"
    out["inspection"] = _entry("inspection", v, t, refs, why, interval_years=interval if v != NOT_REQUIRED else None,
                               subject=idt.get("subject"), exclusions=excl)

    # ----- 有資格者に点検させる義務 -----
    qi = R["qualified_inspector"]
    refs = ref_text(key, qi.get("citations", []))
    use("qualified_inspector", qi)
    qual = qi.get("qualification")
    eff = qi.get("effort")
    ctext = cond_text(qi.get("conditions"))
    manager = qi.get("duty_kind") == "manager"   # 東京都 条例第25条: 点検をさせる義務ではなく『有資格の管理者を置く』義務
    if manager and qi["required"] == "duty":
        st, why = eval_conditions(qi.get("conditions"), **kw)
        lead = "有資格の屋外広告物管理者を置く義務"
        if st == "hit":
            v, why, t = REQUIRED, "", f"{lead}: 要{_paren_why(qi.get('scope') or ctext, _refs_in(refs))}"
        elif st == "miss":
            v = NOT_REQUIRED
            why = "許可物件のみ" if why == "許可不要のため" else why
            t = f"{lead}: 不要{_paren(why)}"
        else:
            v, t = NEEDS_MEASURE, f"{lead}: 要実測{_paren(why)}"
        out["qualified"] = _entry("qualified", v, t, refs, why, qualification=qual, duty_kind="manager")
    elif qi["required"] == "unknown":
        v, why, t = NEEDS_MEASURE, "原文から読み取れていない", "原文から読み取れていません"
    elif qi["required"] == "no":
        v, why = NOT_REQUIRED, "規定なし" if idt.get("absent_in_text") or not qi.get("citations") else "義務を置いていない"
        t = "有資格者に点検させる義務の規定は見当たりません" if not qi.get("citations") else f"義務なし{_paren(_refs_in(refs))}"
    else:
        d_st, d_why = eval_conditions(qi.get("conditions"), **kw)
        if qi["required"] == "effort":
            e_st, e_why, e_node = d_st, d_why, qi
            d_st = "miss"
        elif eff:
            e_st, e_why = eval_conditions(eff.get("conditions"), **kw)
            e_node = eff
        else:
            e_st, e_why, e_node = "miss", d_why, None
        if d_st == "hit" and qi["required"] == "duty":
            v, why = REQUIRED, ""
            t = f"有資格者に点検させる義務 あり{_paren(ctext, _refs_in(refs))}"
        elif e_st == "hit" and d_st != "unknown":
            erefs = ref_text(key, (e_node or {}).get("citations", [])) or refs
            if e_node is not None and e_node is not qi:
                use("qualified_inspector", e_node, active=False)
            v, why = EFFORT, ""
            t = f"努力義務{_paren(cond_text((e_node or {}).get('conditions')), _refs_in(erefs))}"
            refs = erefs
        elif d_st == "unknown" or (e_st == "unknown" and d_st != "hit"):
            v, why = NEEDS_MEASURE, "、".join(dict.fromkeys(w for w in (d_why if d_st == "unknown" else "", e_why if e_st == "unknown" else "") if w))
            t = f"要実測{_paren(why)}"
        else:
            v, why = NOT_REQUIRED, e_why if e_node is not None else d_why
            t = f"義務なし{_paren(why)}"
    if not manager or qi["required"] != "duty":
        ins_v = out["inspection"]["value"]
        if ins_v == NOT_REQUIRED and v != NOT_REQUIRED:
            v, why, t = NOT_REQUIRED, "点検の義務の対象外のため", "義務なし(点検の義務の対象外のため)"
        out["qualified"] = _entry("qualified", v, t, refs, why, qualification=qual)

    # ----- 行政への報告義務 -----
    rd = R["report_duty"]
    refs = ref_text(key, rd.get("citations", []))
    use("report_duty", rd)
    trig, form = rd.get("trigger"), rd.get("form")
    rc = dict(rd.get("conditions") or {})
    permit_only = bool(rd.get("permit_required_only") or rc.get("permit_required_only"))
    if rd["required"] == "unknown":
        v, why, t = NEEDS_MEASURE, "原文から読み取れていない", "原文から読み取れていません"
    elif rd["required"] == "no":
        v, why = NOT_REQUIRED, "規定なし" if not rd.get("citations") else "義務を置いていない"
        t = "報告の義務の規定は見当たりません" if not rd.get("citations") else f"不要{_paren(_refs_in(refs))}"
    else:
        st, why = eval_conditions(rc, permit_only=permit_only, **kw)
        v = {"hit": REQUIRED, "miss": NOT_REQUIRED, "unknown": NEEDS_MEASURE}[st]
        if v == REQUIRED:
            short = trig if trig and len(trig) <= 30 else "許可・更新の申請時"  # 長い原文の言い回しは details の trigger に残す
            t = f"{short}に点検結果を報告{_paren(form, _refs_in(refs))}"
        elif v == NOT_REQUIRED:
            t = f"不要{_paren_why(why, _refs_in(refs))}"
        else:
            t = f"要実測{_paren(why)}"
    out["report"] = _entry("report", v, t, refs, why, trigger=trig, form=form)

    out["_cites"], out["_confs"] = cites, confs
    return out
