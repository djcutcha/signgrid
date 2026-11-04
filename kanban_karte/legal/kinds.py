"""種類ごとの判定(壁面 / 突出し=袖)。rules_*.json の基準に足した数値キーで、壁面率・上端の高さ・出幅・下端の地上高を当てはめる。

対象は 壁面と突出(袖)だけ(Cutcha 2026-11-04 16:05「壁面と袖だけ実装」)。自立・屋上は今の面積・高さの判定のまま。

基準の数値キー(self_use.standards[] と permit_standards[] の各要素に任意で足す。無い物は null か省略):
  wall_ratio_max                      壁面率の上限(0.2 = 1/5、0.3 = 3/10)。表示面積 ÷ 取り付く壁面 1 面の面積
  wall_ratio_floor_m2                 比率で出した面積がこれ未満なら、この面積まで可(「1/5 が 10㎡ 未満なら 10㎡ 以下」)
  top_limit                           上端の相対高さ "eave"(軒高以下)| "wall"(壁面の高さ以下)| null
  protrusion_from_wall_max_m          壁面からの出幅の上限(m)
  protrusion_from_road_boundary_max_m 道路境界からの出幅の上限(m)
  road_protrusion_allowed             道路上に突き出してよいか(false =「道路上に突き出していないこと」)
  ground_clearance_sidewalk_min_m     下端の地上高の下限(歩道上)
  ground_clearance_roadway_min_m      下端の地上高の下限(車道上・歩道の無い道路)
  verified / verified_url / verified_at  公式例規集との照合 "official" | "copy"(repo の写しのみ)| "unverified"

道路占用(条例の外。rules["road_occupancy"]、任意): 道路に出る袖看板は、屋外広告物の許可とは別に道路管理者の道路占用許可
(道路法 第32条)が要る。道路使用許可(道路交通法 第77条)は警察署の判断(要る場合がある)なので「警察署に確認」と出す。

境界付近は確定させない: 基準の ±15%(基準が 0 m の時は ±0.15 m)に入る値は「要実測」。
この幅は SignGrid の設計値で、条文の数値ではない(写真からの概算は数 cm〜数十 cm ずれるため)。
"""
from __future__ import annotations

WALL, PROJECTING = "wall", "projecting"
OK, OVER, MEASURE, MISSING = "適合", "超過", "要実測", "未入力"
BAND = 0.15            # 基準の ±15% は要実測(設計値)
ZERO_BAND_M = 0.15     # 基準が 0 m(道路に出ない)の時の幅(設計値)
VERIFIED = ("official", "copy", "unverified")
TOP_LIMITS = ("eave", "wall", "wall_with_overshoot_le_protrusion")   # 最後= 壁面の高さを超えてよいが、超える高さ ≦ 壁面からの出幅(さいたま市)
OVER_KINDS = ("sidewalk", "roadway")   # roadway= 車道上・歩道の無い道路
ROAD_MANAGERS = ("国道", "都道", "県道", "市道", "区道")
GC_SW_NEAR = "ground_clearance_sidewalk_min_m_if_road_boundary_projection_le_0_5m"   # 東京都: 道路境界からの出幅 0.5m 以下なら歩道上 2.5m
NEAR_PROJECTION_M = 0.5
STD_NUM_KEYS = ("wall_ratio_max", "wall_ratio_floor_m2", "wall_ratio_cap_m2", "protrusion_from_wall_max_m",
                "protrusion_from_road_boundary_max_m", "ground_clearance_sidewalk_min_m", "ground_clearance_roadway_min_m", GC_SW_NEAR)
ROAD_SOURCES = ("条例", "施行規則", "法律", "道路管理者の基準")
# 未確認のキー(unverified_keys)-> 種類ごとに効く物だけ「未確認= 要実測」の行にする
UNVERIFIED_ITEMS = {
    WALL: {"wall_ratio_max": "wall_ratio", "wall_ratio_by_zone": "wall_ratio", "wall_ratio_floor_m2": "wall_ratio",
           "wall_ratio_cap_m2": "wall_ratio", "top_limit": "top"},
    PROJECTING: {"protrusion_from_wall_max_m": "protrusion_wall", "protrusion_from_road_boundary_max_m": "protrusion_road",
                 "road_protrusion_allowed": "road_protrusion", "ground_clearance_sidewalk_min_m": "clearance",
                 "ground_clearance_roadway_min_m": "clearance", GC_SW_NEAR: "clearance", "top_limit": "top"},
}

# 道路側の許可の根拠(e-Gov 法令検索、2026-11-04 取得。法規調査_種類別の判定項目_2026-11-04.md §3-1 ✅)。
# rules["road_occupancy"] に law_citation / use_permit_citation があればそちらを使う
LAW_CITATION = {"source": "法律", "article": "道路法 第32条第1項",
                "quote": "道路に次の各号のいずれかに掲げる工作物、物件又は施設を設け、継続して道路を使用しようとする場合においては、道路管理者の許可を受けなければならない。",
                "url": "https://laws.e-gov.go.jp/api/1/lawdata/327AC1000000180", "quote_verified": None}
USE_PERMIT_CITATION = {"source": "法律", "article": "道路交通法 第77条第1項第2号",
                       "quote": "道路に石碑、銅像、広告板、アーチその他これらに類する工作物を設けようとする者",
                       "url": "https://laws.e-gov.go.jp/api/1/lawdata/335AC0000000105", "quote_verified": None}
ROAD_TEXT = ("道路に出ていれば、屋外広告物の許可とは別に、道路管理者の道路占用許可(道路法 第32条)が要ります。"
             "道路使用許可(道路交通法 第77条)は、要るかどうかを所轄の警察署に確認してください。")

LABELS = {"wall_ratio": "壁面率(看板の面積 ÷ 壁面の面積)", "top_eave": "上端の高さ(軒高以下)", "top_wall": "上端の高さ(壁面の高さ以下)",
          "top_overshoot": "上端の高さ(壁面の高さを超える分は出幅以下)", "top": "上端の高さ",
          "protrusion_wall": "壁面からの出幅", "protrusion_road": "道路境界からの出幅", "road_protrusion": "道路上に突き出していないか",
          "clearance": "下端の地上高"}


def category(kind: str | None) -> str | None:
    """台帳・画面の種類の語 -> wall / projecting / None(それ以外は種類ごとの判定をしない)。"""
    if not kind:
        return None
    if "壁面" in kind or kind == WALL:
        return WALL
    if "突出" in kind or "袖" in kind or "突き出" in kind or kind == PROJECTING:
        return PROJECTING
    return None


def has_kind_keys(std: dict, cat: str | None = None) -> bool:
    """種類ごとの判定に使うキー(値か、未確認の印)を持つか。cat を渡すとその種類に効くキーだけ見る。"""
    keys = STD_NUM_KEYS + ("top_limit", "road_protrusion_allowed", "wall_ratio_by_zone")
    if cat == WALL:
        keys = ("wall_ratio_max", "wall_ratio_by_zone", "wall_ratio_floor_m2", "wall_ratio_cap_m2", "top_limit")
    elif cat == PROJECTING:
        keys = tuple(k for k in keys if not k.startswith("wall_ratio"))
    if any(std.get(k) is not None for k in keys):
        return True
    uv = std.get("unverified_keys") or []
    return any(k in uv for k in (UNVERIFIED_ITEMS.get(cat) or {**UNVERIFIED_ITEMS[WALL], **UNVERIFIED_ITEMS[PROJECTING]}))


def check(value, limit, mode: str) -> str:
    """値1つを基準に当てる。mode= max(以下なら適合)/ min(以上なら適合)。境界の ±15% は要実測。"""
    if value is None:
        return MISSING
    band = abs(limit) * BAND if limit else ZERO_BAND_M
    if mode == "max":
        if value > limit + band:
            return OVER
        return OK if value < limit - band else MEASURE
    if value < limit - band:
        return OVER
    return OK if value > limit + band else MEASURE


def _cites(std: dict) -> list[dict]:
    """行に付ける根拠。種類ごとの照合で付けた引用(kind_citations。url・逐語かどうかつき)があればそれ、無ければ基準の citations。"""
    src = std.get("kind_citations") or std.get("citations") or []
    return [{"source": c.get("source"), "article": c.get("article"), "quote": c.get("quote"), "url": c.get("url"),
             "verbatim": c.get("verbatim", True), "quote_verified": c.get("quote_verified")} for c in src]


def _row(item, value, limit, unit, verdict, std, label=None, note=None):
    return {"item": item, "label": label or LABELS[item], "value": value, "limit": limit, "unit": unit, "verdict": verdict,
            "note": note, "confidence": std.get("confidence"), "verified": std.get("verified") or "copy",
            "citations": _cites(std)}


def _wall_ratio_row(std: dict, m: dict) -> dict:
    base, byz = std.get("wall_ratio_max"), std.get("wall_ratio_by_zone") or {}
    floor, cap = std.get("wall_ratio_floor_m2"), std.get("wall_ratio_cap_m2")
    wa, area, zl = m.get("wall_area"), m.get("area"), m.get("zone_label")
    if zl is not None:
        opts = [(zl, byz.get(zl, base))]
    else:   # 用途地域が分からない: 区域ごとの比率を全部当てる(結論が割れたら要実測)
        opts = [("", base)] + [(z, r) for z, r in byz.items() if r != base]
    opts = [(z, r) for z, r in opts if r is not None]
    if not opts:
        return None
    ratio = opts[0][1]
    if wa is None or area is None:
        return _row("wall_ratio", None, ratio, "比", MISSING, std, note="壁面の面積が未入力" if wa is None else "看板の面積が未入力")
    verdicts, notes = [], []
    for z, r in opts:
        lim = r * wa
        if floor:
            lim = max(lim, floor)
        if cap:
            lim = min(lim, cap)
        verdicts.append(check(area, lim, "max"))
        notes.append(f"{z + 'は' if z else ''}上限 {lim:.1f} ㎡(壁面 {wa:.1f} ㎡ × {r:g}"
                     + (f"、{floor:g} ㎡ 未満なら {floor:g} ㎡ まで" if floor else "") + (f"、かつ {cap:g} ㎡ 以下" if cap else "") + ")")
    v = verdicts[0] if len(set(verdicts)) == 1 else MEASURE
    note = "、".join(notes) + ("。用途地域で結論が分かれる" if len(set(verdicts)) > 1 else "")
    return _row("wall_ratio", round(area / wa, 3), ratio, "比", v, std, note=note)


def _top_row(std: dict, m: dict) -> dict | None:
    tl = std.get("top_limit")
    if tl not in TOP_LIMITS:
        return None
    top = m.get("top")
    if tl == "eave":
        item, ref, what = "top_eave", m.get("eave"), "軒高"
    elif tl == "wall":
        item, ref, what = "top_wall", m.get("wall_height"), "壁面の高さ"
    else:
        wh, pw = m.get("wall_height"), m.get("protrusion_wall")
        item, ref, what = "top_overshoot", (wh + pw if wh is not None and pw is not None else None), "壁面の高さ・壁面からの出幅"
    if ref is None or top is None:
        return _row(item, top, ref, "m", MISSING, std, note=f"{what}が未入力" if ref is None else "上端の高さが未入力")
    return _row(item, top, round(ref, 3), "m", check(top, ref, "max"), std,
                note="壁面の高さ + 壁面からの出幅まで" if item == "top_overshoot" else None)


def _clearance_row(std: dict, m: dict) -> dict | None:
    sw, rw, near = std.get("ground_clearance_sidewalk_min_m"), std.get("ground_clearance_roadway_min_m"), std.get(GC_SW_NEAR)
    if sw is None and rw is None and near is None:
        return None
    gc, over, pr = m.get("clearance"), m.get("over"), m.get("protrusion_road")
    note = None
    if near is not None and (over in (None, "sidewalk")):
        if pr is not None and check(pr, NEAR_PROJECTION_M, "max") == OK:
            sw, note = near, f"道路境界からの出幅 {NEAR_PROJECTION_M:g} m 以下の時の基準"
        elif sw is not None:
            note = f"道路境界からの出幅が {NEAR_PROJECTION_M:g} m 以下と確かめられれば {near:g} m 以上"
    if over in OVER_KINDS:
        lim = sw if over == "sidewalk" else rw
        if lim is None:
            return None   # この基準は他方(歩道上 / 車道上)の物
        return _row("clearance", gc, lim, "m", check(gc, lim, "min"), std,
                    label=f"下端の地上高({'歩道上' if over == 'sidewalk' else '車道上・歩道の無い道路'})", note=note)
    # 歩道か車道か分からない: 厳しい方に収まれば適合、緩い方にも足りなければ超過、間は要実測
    lims = [x for x in (sw, rw) if x is not None]
    hi_v, lo_v = check(gc, max(lims), "min"), check(gc, min(lims), "min")
    v = MISSING if gc is None else OK if hi_v == OK else OVER if lo_v == OVER else MEASURE
    base = "基準 " + " / ".join(f"{n} {x:g} m 以上" for n, x in (("歩道上", sw), ("車道上", rw)) if x is not None)
    return _row("clearance", gc, max(lims), "m", v, std, label="下端の地上高(歩道か車道か未入力)",
                note=base + (f"。{note}" if note else ""))


def std_checks(std: dict, cat: str, m: dict) -> list[dict]:
    """基準1件に、種類ごとの入力 m を当てた行の一覧。m のキー: area, wall_area, top, eave, wall_height,
    protrusion_wall, protrusion_road, clearance, over(sidewalk/roadway/None), zone_label(用途地域。分かれば)。
    基準の unverified_keys(公式の条文で照合できていないキー)は、値を使わず「未確認= 要実測」の行にする。"""
    rows: list[dict] = []
    if cat == WALL:
        rows.append(_wall_ratio_row(std, m))
    rows.append(_top_row(std, m))
    if cat == PROJECTING:
        pw, pr = m.get("protrusion_wall"), m.get("protrusion_road")
        if std.get("protrusion_from_wall_max_m") is not None:
            lim = std["protrusion_from_wall_max_m"]
            rows.append(_row("protrusion_wall", pw, lim, "m", check(pw, lim, "max"), std))
        if std.get("protrusion_from_road_boundary_max_m") is not None:
            lim = std["protrusion_from_road_boundary_max_m"]
            rows.append(_row("protrusion_road", pr, lim, "m", check(pr, lim, "max"), std))
        if std.get("road_protrusion_allowed") is False:
            rows.append(_row("road_protrusion", pr, 0, "m", check(pr, 0, "max"), std, note="道路境界より道路側に出ていれば超過"))
        rows.append(_clearance_row(std, m))
    rows = [r for r in rows if r is not None]
    have = {r["item"] for r in rows}
    for k in std.get("unverified_keys") or []:
        item = (UNVERIFIED_ITEMS.get(cat) or {}).get(k)
        if item is None or item in have or (item == "top" and have & {"top_eave", "top_wall", "top_overshoot"}):
            continue
        have.add(item)
        rows.append({**_row(item, None, None, "", MEASURE, std, note="未確認(公式の条文で照合できていない)。実測と窓口での確認が要る"),
                     "verified": "unverified"})
    return rows

def eval_std(std: dict, cat: str | None, m: dict) -> tuple[list[str], list[str], int]:
    """judge._eval_std に足す分: (超過の理由, 不明の理由, 当てた数)。種類ごとのキーが無い基準は (空, 空, 0)。"""
    if cat is None or not has_kind_keys(std, cat):
        return [], [], 0
    fail, unknown, n = [], [], 0
    for row in std_checks(std, cat, m):
        n += 1
        if row["verdict"] == OVER:
            fail.append(f"{row['label']} が基準を外れる")
        elif row["verdict"] == MEASURE:
            unknown.append(f"{row['label']} が基準の境目付近(要実測)")
        elif row["verdict"] == MISSING:
            unknown.append(f"{row['label']} の値が無い")
    return fail, unknown, n


def merge_rows(groups: list[tuple[str, list[dict]]]) -> list[dict]:
    """基準ごとの行を項目ごとにまとめる。基準(区域)で結論が違う項目は 要実測 にして、基準ごとの結論を branches に残す。"""
    by: dict[str, dict] = {}
    for when, rows in groups:
        for row in rows:
            cur = by.get(row["item"])
            if cur is None:
                by[row["item"]] = {**row, "branches": [{"when": when, "limit": row["limit"], "verdict": row["verdict"], "note": row["note"]}]}
                continue
            cur["branches"].append({"when": when, "limit": row["limit"], "verdict": row["verdict"], "note": row["note"]})
            for c in row["citations"]:
                if c not in cur["citations"]:
                    cur["citations"].append(c)
            if cur["verified"] != row["verified"]:
                order = {"unverified": 0, "copy": 1, "official": 2}
                cur["verified"] = min(cur["verified"], row["verified"], key=lambda x: order.get(x, 0))
            if row["verdict"] != cur["verdict"]:
                cur["verdict"] = MEASURE
    out = list(by.values())
    for o in out:
        if len({b["verdict"] for b in o["branches"]}) == 1 and len({b["limit"] for b in o["branches"]}) == 1:
            o["branches"] = []
    return out


def road_occupancy(R: dict, cat: str | None, m: dict) -> dict | None:
    """袖(突出)のときの道路側の注意。道路に出ていれば 要、出ているか分からない・境目付近は 要確認、出ていなければ 不要。"""
    if cat != PROJECTING:
        return None
    ro = R.get("road_occupancy") or {}
    pr = m.get("protrusion_road")
    v = check(pr, 0, "max")
    status = {OVER: "要", OK: "不要", MEASURE: "要確認", MISSING: "要確認"}[v]
    cites = [ro.get("law_citation") or LAW_CITATION, ro.get("use_permit_citation") or USE_PERMIT_CITATION]
    mgr = m.get("road_manager")
    stds = []
    for b in ro.get("by_manager") or []:
        if mgr and b.get("manager") != mgr:
            continue
        rows = []
        if b.get("verified") not in (None, "official"):   # 照合できていない数値は判定に使わず、注意文だけ
            stds.append({"manager": b.get("manager"), "self_use_only": None, "general_not_allowed": None, "note": b.get("note"),
                         "rows": [], "verified": b.get("verified"), "citations": b.get("kind_citations", []), "confidence": b.get("confidence")})
            continue
        lim = b.get("protrusion_from_road_boundary_max_m")
        if lim is not None:
            rows.append(_row("protrusion_road", pr, lim, "m", check(pr, lim, "max"), b))
        over = m.get("over")
        gl = b.get("ground_clearance_sidewalk_min_m") if over == "sidewalk" else b.get("ground_clearance_no_sidewalk_min_m") if over == "roadway" else None
        if gl is not None:
            rows.append(_row("clearance", m.get("clearance"), gl, "m", check(m.get("clearance"), gl, "min"), b,
                             label=f"下端の地上高({'歩道上' if over == 'sidewalk' else '歩道の無い道路'}、道路占用の基準)"))
        stds.append({"manager": b.get("manager"), "self_use_only": b.get("self_use_only"),
                     "general_not_allowed": b.get("general_not_allowed"), "note": b.get("note"), "rows": rows,
                     "verified": b.get("verified") or "copy",
                     "citations": b.get("kind_citations", []), "confidence": b.get("confidence")})
    text = {"要": "道路境界より道路側に出ています。" + ROAD_TEXT,
            "不要": "道路境界の内側に収まっています(道路占用許可は不要の見込み。境界の位置は実測か道路台帳で確認)。",
            "要確認": "道路に出ているか確定できません(境界の位置か出幅が未入力、または境目付近)。" + ROAD_TEXT}[status]
    if not mgr:
        text += " 道路の管理者(国道・都道・県道・市区道)で窓口と基準が変わります。"
    return {"status": status, "text": text, "citations": cites, "manager": mgr, "by_manager": stds}


def validate_std_keys(errs: list[str], path: str, s: dict) -> None:
    """基準1件の種類ごとのキーの検査(任意キー。在る物だけ)。"""
    for k in STD_NUM_KEYS:
        v = s.get(k)
        if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
            errs.append(f"{path}.{k} は 0 以上の数か null")
    wr = s.get("wall_ratio_max")
    if isinstance(wr, (int, float)) and not isinstance(wr, bool) and not 0 < wr <= 1:
        errs.append(f"{path}.wall_ratio_max は 0 より大きく 1 以下")
    if s.get("top_limit") not in (None,) + TOP_LIMITS:
        errs.append(f"{path}.top_limit は {TOP_LIMITS} か null")
    if s.get("road_protrusion_allowed") not in (None, True, False):
        errs.append(f"{path}.road_protrusion_allowed は true/false/null")
    if s.get("verified") not in (None,) + VERIFIED:
        errs.append(f"{path}.verified は {VERIFIED}")
    byz = s.get("wall_ratio_by_zone")
    if byz is not None and (not isinstance(byz, dict) or any(
            isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 < v <= 1 for v in byz.values())):
        errs.append(f"{path}.wall_ratio_by_zone は 用途地域 -> 比率(0〜1)のオブジェクト")
    uv = s.get("unverified_keys", [])
    if not isinstance(uv, list) or any(not isinstance(x, str) for x in uv):
        errs.append(f"{path}.unverified_keys は文字列の配列")
    kc = s.get("kind_citations", [])
    if not isinstance(kc, list) or any(not isinstance(c, dict) or c.get("source") not in ROAD_SOURCES
                                       or not c.get("article") or not c.get("quote") for c in kc):
        errs.append(f"{path}.kind_citations は source・article・quote を持つ引用の配列")
    if s.get("zone") is not None and s.get("zone") not in ("all", "permit_zone", "prohibited_zone", "other"):
        errs.append(f"{path}.zone は all/permit_zone/prohibited_zone/other")


def validate_road_occupancy(ro) -> list[str]:
    errs: list[str] = []
    if not isinstance(ro, dict):
        return ["road_occupancy はオブジェクト"]
    for k in ("law_citation", "use_permit_citation"):
        c = ro.get(k)
        if c is not None and (not isinstance(c, dict) or c.get("source") not in ROAD_SOURCES or not c.get("article") or not c.get("quote")):
            errs.append(f"road_occupancy.{k} は source({ROAD_SOURCES})・article・quote を持つ引用")
    bm = ro.get("by_manager", [])
    if not isinstance(bm, list):
        return errs + ["road_occupancy.by_manager は配列"]
    for i, b in enumerate(bm):
        p = f"road_occupancy.by_manager[{i}]"
        if not isinstance(b, dict):
            errs.append(f"{p} はオブジェクト")
            continue
        if b.get("manager") not in ROAD_MANAGERS:
            errs.append(f"{p}.manager は {ROAD_MANAGERS}")
        for k in ("protrusion_from_road_boundary_max_m", "ground_clearance_sidewalk_min_m", "ground_clearance_no_sidewalk_min_m"):
            v = b.get(k)
            if v is not None and (isinstance(v, bool) or not isinstance(v, (int, float)) or v < 0):
                errs.append(f"{p}.{k} は 0 以上の数か null")
        for k in ("self_use_only", "general_not_allowed"):
            if b.get(k) not in (None, True, False):
                errs.append(f"{p}.{k} は true/false/null")
        if b.get("verified") not in (None,) + VERIFIED:
            errs.append(f"{p}.verified は {VERIFIED}")
        cs = b.get("kind_citations")
        if not isinstance(cs, list) or (not cs and b.get("verified") in (None, "official")):
            errs.append(f"{p}.kind_citations が空(照合済みの値には根拠が要る)")
        else:
            for j, c in enumerate(cs):
                if not isinstance(c, dict) or c.get("source") not in ROAD_SOURCES or not c.get("article") or not c.get("quote"):
                    errs.append(f"{p}.kind_citations[{j}] は source({ROAD_SOURCES})・article・quote を持つ引用")
        c = b.get("confidence")
        if isinstance(c, bool) or not isinstance(c, (int, float)) or not 0 <= c <= 1:
            errs.append(f"{p}.confidence は 0〜1 の数")
    return errs
