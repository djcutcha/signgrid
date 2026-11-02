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
"""
from __future__ import annotations

SCHEMA_VERSION = 1
STATUS_FIRST_DRAFT = "AI抽出の第1版(検算前)"

ZONES = ("all", "permit_zone", "prohibited_zone", "other")
AREA_BASES = ("per_face", "total", "per_sign", "unspecified")
INSPECTION_REQUIRED = ("yes", "no", "conditional", "unknown")
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
  "inspection": {   // 安全点検(有資格者による点検)と報告の義務
    "required": "yes | no | conditional | unknown",   // conditional= 規模・高さ等の条件に該当すると義務
    "conditions": [ {"description": "どのような広告物に義務があるか(文)", "area_min_m2": 数値|null, "height_min_m": 数値|null, "ground_clearance_min_m": 数値|null} ],
    "applies_to_self_use": true|false|null,
    "qualification": "点検できる資格(例: 屋外広告士・点検技士 等。原文のまま)|null",
    "form": "報告の様式(様式第何号、名称)|null",
    "interval_years": 数値|null,   // 点検・報告の周期(年)
    "citations": [引用, ...], "confidence": 0〜1
  },
  "prohibited_zones": {"summary": "禁止区域・禁止物件の概要(文のみ。200字以内)", "labels": ["禁止区域になる用途地域・地区を上の語から選んで列挙(知事・市長の指定による除外があっても列挙し、summary に書く)"], "citations": [引用, ...], "confidence": 0〜1}
}"""

RULES_SHAPE = _RULES_SHAPE_TMPL.replace("{ZONE_VOCAB_JOINED}", " / ".join(ZONE_VOCAB))

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
    ins = rules.get("inspection")
    if not isinstance(ins, dict):
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
    if "status" in doc and "検算前" not in str(doc["status"]):
        errs.append("status に『検算前』の明記が必要")
    errs += validate_rules(doc.get("rules"))
    return errs
