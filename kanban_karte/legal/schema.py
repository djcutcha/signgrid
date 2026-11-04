"""条例から抽出した規則(rules_<自治体>.json)の形と、その検査。

JSON の形(ファイル全体):
  {
    "schema_version": 1,
    "ordinance_key": "越谷市",            # 東京都 / 埼玉県 / 越谷市 / さいたま市
    "status": "AI抽出の第1版(検算前)",
    "model": "...", "extracted_at": "...",
    "sources": [{"file": ..., "bytes": ..., "mtime": ...}],
    "rules": { ...下の RULES_SHAPE... },
    "extraction_notes": ["モデルが書いた、読み取りに迷った点"],
    "citation_check": {"total": n, "verified": n}   # 引用が原文にそのまま在るか(機械照合)
  }
引用(citation)= {"source": "条例"|"施行規則", "article": "第6条第1項第3号", "quote": "原文の抜粋(100字以内)",
                 "quote_verified": bool(抽出後に機械で付ける)}
数値は m / ㎡ / 年。原文に無い値は null(推測で埋めない)。
種類ごとの任意キー(壁面率 wall_ratio_max・出幅・地上高・道路占用 road_occupancy 等)の形は kinds.py の先頭。
"""
from __future__ import annotations

from . import kinds as K

SCHEMA_VERSION = 1
STATUS_FIRST_DRAFT = "AI抽出の第1版(検算前)"
STATUS_SECOND_PREFIX = "第2版(手元の正解と照合済み"

ZONES = ("all", "permit_zone", "prohibited_zone", "other")
AREA_BASES = ("per_face", "total", "per_sign", "unspecified")
INSPECTION_REQUIRED = ("yes", "no", "conditional", "unknown")   # 旧 inspection(互換のために残す)
# 4つの義務(管理する / 点検する / 有資格者に点検させる / 行政へ報告する)。掛かり方は条例ごとに違う
MAINTENANCE_REQUIRED = ("yes", "no", "unknown")
INSPECTION_DUTY_REQUIRED = ("yes", "conditional", "no", "unknown")
QUALIFIED_REQUIRED = ("duty", "effort", "no", "unknown")        # duty=義務 / effort=努力義務
REPORT_REQUIRED = ("yes", "conditional", "no", "unknown")
DUTY_KINDS = ("inspection", "manager")   # 有資格者の義務の中身。inspection= 点検をさせる / manager= 有資格の管理者を置く(東京都 条例第25条)
DUTY_KEYS = ("maintenance_duty", "inspection_duty", "qualified_inspector", "report_duty")
SOURCES = ("条例", "施行規則")
MAX_QUOTE_CHARS = 100

# zone_labels / prohibited_zones.labels に使う語(用途地域・地区の名前。住所から区域を引く側もこの語で持つ)
ZONE_VOCAB = (
    "第一種低層住居専用地域", "第二種低層住居専用地域", "第一種中高層住居専用地域", "第二種中高層住居専用地域",
    "田園住居地域", "第一種住居地域", "第二種住居地域", "準住居地域", "近隣商業地域", "商業地域",
    "準工業地域", "工業地域", "工業専用地域", "風致地区", "特別緑地保全地区", "生産緑地地区",
    "自然公園特別地域", "第一種文教地区", "第二種文教地区", "景観地区", "文化財周辺", "市街化調整区域",
    "都市計画区域外", "道路・鉄道沿い", "その他",
)

# 4つの義務に共通の conditions(その義務が掛かる物の絞り込み)。条件が無ければ空の値のまま
CONDITIONS_SHAPE = ('{"area_min_m2": 数値|null, "height_min_m": 数値|null(上端の高さ等), "height_min_inclusive": true|false(以上なら true、超えるなら false), '
                    '"ground_clearance_min_m": 数値|null, "permit_required_only": true|false(許可・更新を要する物だけが対象なら true), '
                    '"kinds": ["対象の種類(限るときだけ)"], "excluded_kinds": ["対象外の種類"], "note": "数値で表せない条件(文)|null"}')

# 4つの義務の JSON の形(rules に直接入る。義務だけの再抽出にも使う)
_DUTIES_BODY = """"maintenance_duty": {   // 管理義務: 補修その他必要な管理を行い、良好な状態に保持する義務(点検とは別)
    "required": "yes | no | unknown",
    "subject": "義務を負う者(原文の主語のまま。例: 広告物の表示者等、所有者等)|null",
    "conditions": {CONDITIONS_SHAPE},
    "citations": [引用, ...], "confidence": 0〜1
  },
  "inspection_duty": {   // 点検する義務: 劣化・損傷の状況を点検する(させる)義務。誰が点検するかは問わない
    "required": "yes | conditional | no | unknown",   // conditional= 規模・高さ・種類の条件に該当すると義務。no= 義務を置いていない
    "absent_in_text": true|false,   // no のうち、原文にそれらしい条文が見当たらないだけのとき true(原文が「義務なし」と書いているときは false)
    "interval_years": 数値|null,    // 周期(年)。原文に周期が無ければ null
    "subject": "義務を負う者(原文の主語のまま)|null",
    "exclusions": ["点検の義務が掛からない物(原文のまま。種類・設置後の期間・他法令による点検 等を1件ずつ)"],
    "conditions": {CONDITIONS_SHAPE},
    "citations": [引用, ...], "confidence": 0〜1
  },
  "qualified_inspector": {   // 有資格者に点検させる義務。点検する義務とは別に、点検を資格者にさせなければならないか
    "required": "duty | effort | no | unknown",   // duty=義務 / effort=努力義務(させるよう努める)。義務の層と努力義務の層が両方あるときは duty
    "conditions": {CONDITIONS_SHAPE},   // duty の層の対象(例: 上端 4m 超かつ許可を要する物 -> height_min_m=4, height_min_inclusive=false, permit_required_only=true)
    "effort": null | {"conditions": {CONDITIONS_SHAPE}, "citations": [引用, ...]},   // duty の層に当たらない物のうち努力義務が掛かる層(例: 上端 4m 超なら許可不要でも)。無ければ null。required が effort のときは null にして conditions に書く
    "qualification": "点検できる者の資格(原文のまま)|null",
    "citations": [引用, ...], "confidence": 0〜1
  },
  "report_duty": {   // 行政へ点検結果を報告・提出する義務
    "required": "yes | conditional | no | unknown",
    "trigger": "報告・提出するとき(例: 許可・更新の申請時)|null",
    "form": "様式(様式第何号、名称)|null",
    "permit_required_only": true|false,   // 許可・更新を受ける物だけが対象か(許可不要の物には掛からないか)
    "conditions": {CONDITIONS_SHAPE},   // permit_required_only は上と同じ値
    "citations": [引用, ...], "confidence": 0〜1
  },"""

# プロンプトに貼る、期待する出力の形(値は説明)
_RULES_SHAPE_TMPL = """{
  "self_use": {
    "definition": "自家用広告物の定義(条例の言い回しを短く)。無ければ null",
    "exempt_from_permit": true|false|null,   // 許可区域で、基準内の自家用広告物は許可が要らないか
    "standards": [   // 許可を要さずに掲出できる自家用広告物の基準。種類・区域ごとに1件
      {
        "kind": "壁面広告 | 突出し広告 | 屋上広告 | 広告塔・広告板 | 窓面 | 全種類 など原文の区分。区分が無ければ \\"全種類\\"",
        "zone": "all | permit_zone | prohibited_zone | other",   // all=区域によらない / permit_zone=許可区域 / prohibited_zone=禁止区域 / other=特定の用途地域など
        "zone_note": "zone が other のとき、どの区域か(文)。それ以外は null",
        "zone_labels": ["この基準が適用される区域を、次の語から選んで列挙: {ZONE_VOCAB_JOINED}。zone が all/permit_zone/prohibited_zone で区域を絞らないときは空配列"],
        "area_max_m2": 数値|null,          // この面積以下なら基準内(㎡)
        "area_basis": "per_face | total | per_sign | unspecified",   // 1面あたり / 合計 / 1個あたり / 原文に無い
        "height_max_m": 数値|null,          // 高さの上限(m)。地盤面からの高さ・広告物自体の高さ・建物の高さ等のどれかは conditions に書く
        "ground_clearance_min_m": 数値|null, // 地上高(歩道上などで下端が地上から何m以上)の下限
        "conditions": "上記の数値で表せない条件(文)。無ければ null",
        "citations": [引用, ...],
        "confidence": 0〜1
      }
    ],
    "prohibited_zone_permit": {   // 禁止区域で自家用広告物を出すには許可が要るか(許可で出せるのか、出せないのか)
      "required": "permit_possible | not_allowed | exempt | unknown",
      "summary": "文", "citations": [引用, ...], "confidence": 0〜1
    }
  },
  "permit_required_types": [   // 許可が要る広告物の種類と閾値(一般の広告物)
    {
      "kind": "種類",
      "area_threshold_m2": 数値|null,   // この面積を超えると許可が要る(面積によらず要るなら null)
      "height_threshold_m": 数値|null, "ground_clearance_threshold_m": 数値|null,
      "description": "文", "citations": [引用, ...], "confidence": 0〜1
    }
  ],
  "permit_term": {
    "default_years": 数値|null,   // 許可期間の標準(年)。上限が書いてあるだけなら上限
    "items": [ {"kind": "種類", "years": 数値, "condition": "文|null", "is_maximum": true|false, "citations": [引用, ...], "confidence": 0〜1} ]
  },
  {DUTIES_BODY}
  "prohibited_zones": {"summary": "禁止区域・禁止物件の概要(文のみ。200字以内)", "labels": ["禁止区域になる用途地域・地区を上の語から選んで列挙(知事・市長の指定による除外があっても列挙し、summary に書く)"], "citations": [引用, ...], "confidence": 0〜1}
}"""

DUTIES_SHAPE = "{\n  " + _DUTIES_BODY.replace("{CONDITIONS_SHAPE}", CONDITIONS_SHAPE) + "\n}"
RULES_SHAPE = (_RULES_SHAPE_TMPL.replace("{DUTIES_BODY}", _DUTIES_BODY)
               .replace("{ZONE_VOCAB_JOINED}", " / ".join(ZONE_VOCAB))
               .replace("{CONDITIONS_SHAPE}", CONDITIONS_SHAPE))

CITATION_SHAPE = '{"source": "条例|施行規則", "article": "第6条第1項第3号", "quote": "原文の抜粋(連続した100字以内・原文のまま)"}'


def _is_num(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _num_or_none(errs, path, d, key):
    if key not in d:
        errs.append(f"{path}.{key} がない")
    elif d[key] is not None and not _is_num(d[key]):
        errs.append(f"{path}.{key} は数値か null")


def _conf(errs, path, d):
    c = d.get("confidence")
    if not _is_num(c) or not (0 <= c <= 1):
        errs.append(f"{path}.confidence は 0〜1 の数")


def _citations(errs, path, d, required=True):
    cs = d.get("citations")
    if not isinstance(cs, list):
        errs.append(f"{path}.citations は配列")
        return
    if required and not cs:
        errs.append(f"{path}.citations が空(条文の根拠が必要)")
    for i, c in enumerate(cs):
        p = f"{path}.citations[{i}]"
        if not isinstance(c, dict):
            errs.append(f"{p} はオブジェクト")
            continue
        if c.get("source") not in SOURCES:
            errs.append(f"{p}.source は {SOURCES}")
        if not isinstance(c.get("article"), str) or not c["article"]:
            errs.append(f"{p}.article がない")
        q = c.get("quote")
        if not isinstance(q, str) or not q:
            errs.append(f"{p}.quote がない")
        elif len(q) > MAX_QUOTE_CHARS:
            errs.append(f"{p}.quote が {MAX_QUOTE_CHARS} 字を超える({len(q)}字)")


def has_duties(rules: dict) -> bool:
    """4つの義務の形で持っているか(False なら旧 inspection だけ)。"""
    return isinstance(rules, dict) and any(k in rules for k in DUTY_KEYS)


def validate_conditions(errs, path, c):
    """義務の conditions(掛かる物の絞り込み)の検査。path は conditions 自身の位置。"""
    if not isinstance(c, dict):
        errs.append(f"{path} はオブジェクト")
        return
    for k in ("area_min_m2", "height_min_m", "ground_clearance_min_m"):
        _num_or_none(errs, path, c, k)
    # 任意: any_of= 寸法の OR 条件(例: 高さ4m超 または 面積10㎡超)。各要素は area/height/ground_clearance の下限だけ
    any_of = c.get("any_of", [])
    if not isinstance(any_of, list) or any(not isinstance(a, dict) for a in any_of):
        errs.append(f"{path}.any_of はオブジェクトの配列")
    else:
        for i, a in enumerate(any_of):
            for k in ("area_min_m2", "height_min_m", "ground_clearance_min_m"):
                if a.get(k) is not None and (isinstance(a[k], bool) or not isinstance(a[k], (int, float))):
                    errs.append(f"{path}.any_of[{i}].{k} は数値か null")
            for k in ("height_min_inclusive", "area_min_inclusive"):
                if k in a and not isinstance(a[k], bool):
                    errs.append(f"{path}.any_of[{i}].{k} は true/false")
    if "area_min_inclusive" in c and not isinstance(c["area_min_inclusive"], bool):
        errs.append(f"{path}.area_min_inclusive は true/false")
    if not isinstance(c.get("permit_required_only"), bool):
        errs.append(f"{path}.permit_required_only は true/false")
    if "height_min_inclusive" in c and not isinstance(c["height_min_inclusive"], bool):
        errs.append(f"{path}.height_min_inclusive は true/false")
    for k in ("kinds", "excluded_kinds", "size_kinds"):
        v = c.get(k, [])
        if not isinstance(v, list) or any(not isinstance(x, str) for x in v):
            errs.append(f"{path}.{k} は文字列の配列")


def validate_duties(rules: dict) -> list[str]:
    """4つの義務(maintenance_duty / inspection_duty / qualified_inspector / report_duty)の検査。"""
    errs: list[str] = []
    for k in DUTY_KEYS:
        if not isinstance(rules.get(k), dict):
            errs.append(f"{k} がない")
    if errs:
        return errs
    md, idt, qi, rd = (rules[k] for k in DUTY_KEYS)

    def need(path, d, allowed):
        if d.get("required") not in allowed:
            errs.append(f"{path}.required は {allowed}")

    need("maintenance_duty", md, MAINTENANCE_REQUIRED)
    validate_conditions(errs, "maintenance_duty.conditions", md.get("conditions"))
    _citations(errs, "maintenance_duty", md, required=md.get("required") == "yes")
    _conf(errs, "maintenance_duty", md)

    need("inspection_duty", idt, INSPECTION_DUTY_REQUIRED)
    _num_or_none(errs, "inspection_duty", idt, "interval_years")
    if not isinstance(idt.get("absent_in_text", False), bool):
        errs.append("inspection_duty.absent_in_text は true/false")
    if not isinstance(idt.get("exclusions", []), list):
        errs.append("inspection_duty.exclusions は配列")
    validate_conditions(errs, "inspection_duty.conditions", idt.get("conditions"))
    _citations(errs, "inspection_duty", idt, required=idt.get("required") in ("yes", "conditional"))
    _conf(errs, "inspection_duty", idt)

    need("qualified_inspector", qi, QUALIFIED_REQUIRED)
    if qi.get("duty_kind", "inspection") not in DUTY_KINDS:   # 任意。既定 inspection
        errs.append(f"qualified_inspector.duty_kind は {DUTY_KINDS}")
    validate_conditions(errs, "qualified_inspector.conditions", qi.get("conditions"))
    eff = qi.get("effort")
    if eff is not None:
        if not isinstance(eff, dict):
            errs.append("qualified_inspector.effort はオブジェクトか null")
        else:
            validate_conditions(errs, "qualified_inspector.effort.conditions", eff.get("conditions"))
            _citations(errs, "qualified_inspector.effort", eff)
    _citations(errs, "qualified_inspector", qi, required=qi.get("required") in ("duty", "effort"))
    _conf(errs, "qualified_inspector", qi)

    need("report_duty", rd, REPORT_REQUIRED)
    if not isinstance(rd.get("permit_required_only"), bool):
        errs.append("report_duty.permit_required_only は true/false")
    validate_conditions(errs, "report_duty.conditions", rd.get("conditions"))
    _citations(errs, "report_duty", rd, required=rd.get("required") in ("yes", "conditional"))
    _conf(errs, "report_duty", rd)
    return errs


def validate_permit_standards(items) -> list[str]:
    """rules["permit_standards"](東京都 別表第三。許可の基準の高さ・地上高・出幅)の検査。"""
    errs: list[str] = []
    if not isinstance(items, list) or not items:
        return ["permit_standards は空でない配列"]
    for i, s in enumerate(items):
        p = f"permit_standards[{i}]"
        if not isinstance(s.get("kind"), str) or not s["kind"]:
            errs.append(f"{p}.kind がない")
        if not isinstance(s.get("self_use_only"), bool):
            errs.append(f"{p}.self_use_only は true/false")
        for k in ("height_max_m", "ground_clearance_min_m", "protrusion_max_m", "area_max_m2"):
            _num_or_none(errs, p, s, k)
        K.validate_std_keys(errs, p, s)
        _citations(errs, p, s)
        _conf(errs, p, s)
    return errs


def validate_rules(rules: dict) -> list[str]:
    """rules(ファイルの "rules" 部分)を検査。問題の文の一覧を返す(空なら適合)。"""
    errs: list[str] = []
    if not isinstance(rules, dict):
        return ["rules はオブジェクト"]
    su = rules.get("self_use")
    if not isinstance(su, dict):
        errs.append("self_use がない")
    else:
        if su.get("exempt_from_permit") not in (True, False, None):
            errs.append("self_use.exempt_from_permit は true/false/null")
        sts = su.get("standards")
        if not isinstance(sts, list):
            errs.append("self_use.standards は配列")
        else:
            for i, s in enumerate(sts):
                p = f"self_use.standards[{i}]"
                if s.get("zone") not in ZONES:
                    errs.append(f"{p}.zone は {ZONES}")
                if s.get("area_basis") not in AREA_BASES:
                    errs.append(f"{p}.area_basis は {AREA_BASES}")
                for k in ("area_max_m2", "height_max_m", "ground_clearance_min_m"):
                    _num_or_none(errs, p, s, k)
                zl = s.get("zone_labels")
                if not isinstance(zl, list) or any(x not in ZONE_VOCAB for x in zl):
                    errs.append(f"{p}.zone_labels は ZONE_VOCAB の語の配列")
                if not isinstance(s.get("kind"), str):
                    errs.append(f"{p}.kind がない")
                K.validate_std_keys(errs, p, s)   # 任意: 壁面率・出幅・地上高 等(kinds.py)
                _citations(errs, p, s)
                _conf(errs, p, s)
        pz = su.get("prohibited_zone_permit")
        if not isinstance(pz, dict) or pz.get("required") not in ("permit_possible", "not_allowed", "exempt", "unknown"):
            errs.append("self_use.prohibited_zone_permit.required は permit_possible/not_allowed/exempt/unknown")
        else:
            _citations(errs, "self_use.prohibited_zone_permit", pz, required=pz["required"] != "unknown")
            _conf(errs, "self_use.prohibited_zone_permit", pz)
    pts = rules.get("permit_required_types")
    if not isinstance(pts, list):
        errs.append("permit_required_types は配列")
    else:
        for i, t in enumerate(pts):
            p = f"permit_required_types[{i}]"
            for k in ("area_threshold_m2", "height_threshold_m", "ground_clearance_threshold_m"):
                _num_or_none(errs, p, t, k)
            _citations(errs, p, t)
            _conf(errs, p, t)
    pt = rules.get("permit_term")
    if not isinstance(pt, dict):
        errs.append("permit_term がない")
    else:
        _num_or_none(errs, "permit_term", pt, "default_years")
        if not isinstance(pt.get("items"), list):
            errs.append("permit_term.items は配列")
        else:
            for i, it in enumerate(pt["items"]):
                p = f"permit_term.items[{i}]"
                if not _is_num(it.get("years")):
                    errs.append(f"{p}.years は数値")
                _citations(errs, p, it)
                _conf(errs, p, it)
    if has_duties(rules):
        errs += validate_duties(rules)   # 4つの義務。旧 inspection は無くてもよい(あれば下で検査)
    ins = rules.get("inspection")
    if ins is None and has_duties(rules):
        pass
    elif not isinstance(ins, dict):
        errs.append("inspection がない")
    else:
        if ins.get("required") not in INSPECTION_REQUIRED:
            errs.append(f"inspection.required は {INSPECTION_REQUIRED}")
        _num_or_none(errs, "inspection", ins, "interval_years")
        if not isinstance(ins.get("conditions"), list):
            errs.append("inspection.conditions は配列")
        else:
            for i, c in enumerate(ins["conditions"]):
                for k in ("area_min_m2", "height_min_m", "ground_clearance_min_m"):
                    _num_or_none(errs, f"inspection.conditions[{i}]", c, k)
        _citations(errs, "inspection", ins, required=ins.get("required") not in ("no", "unknown"))
        _conf(errs, "inspection", ins)
    if "permit_standards" in rules:  # 任意(東京都 別表第三の再抽出で足す)
        errs += validate_permit_standards(rules["permit_standards"])
    if "road_occupancy" in rules:  # 任意(道路占用。条例の外の基準。kinds.py)
        errs += K.validate_road_occupancy(rules["road_occupancy"])
    pz = rules.get("prohibited_zones")
    if not isinstance(pz, dict) or not isinstance(pz.get("summary"), str):
        errs.append("prohibited_zones.summary がない")
    else:
        lb = pz.get("labels")
        if not isinstance(lb, list) or any(x not in ZONE_VOCAB for x in lb):
            errs.append("prohibited_zones.labels は ZONE_VOCAB の語の配列")
        _citations(errs, "prohibited_zones", pz)
        _conf(errs, "prohibited_zones", pz)
    return errs


def validate_file(doc: dict) -> list[str]:
    """rules_<自治体>.json 全体の検査。"""
    errs = []
    if doc.get("schema_version") != SCHEMA_VERSION:
        errs.append(f"schema_version は {SCHEMA_VERSION}")
    for k in ("ordinance_key", "status", "model", "extracted_at", "sources", "rules"):
        if k not in doc:
            errs.append(f"{k} がない")
    st = str(doc.get("status", ""))
    if "status" in doc and "検算前" not in st and not (st.startswith(STATUS_SECOND_PREFIX) and "一致" in st):
        errs.append("status は『検算前』か『第2版(手元の正解と照合済み、一致 x/y)』")
    errs += validate_rules(doc.get("rules"))
    return errs
