"""壁面・袖の rules 追記案(公式の規則全文で照合したもの)を data/ordinances/rules_<自治体>.json に取り込む。

使い方: python3 tools/merge_kinds_addendum.py <追記案.json> [--write]
  --write が無ければ、取り込む値の内訳だけ出す(ファイルは変えない)。

取り込みの決め(本線 2026-11-04): 値を入れるのは verified=official のキーだけ。copy(repo の写しのみ)と unverified の
キーは値を入れず null にして unverified_keys に並べる(判定では「未確認= 要実測」)。追記案の引用は kind_citations に
(url つき。越谷市の引用は文字欠けを補った要旨なので verbatim=false)。道路占用 road_occupancy は rules の中に置く
(法規役は rules だけを読むため)。by_manager の copy・unverified の数値は判定に使われない(kinds.road_occupancy)。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import re  # noqa: E402

from kanban_karte.legal import rules_extract as RX  # noqa: E402
from kanban_karte.legal import schema as S  # noqa: E402

DATA = ROOT / "data" / "ordinances"
MAX_Q = S.MAX_QUOTE_CHARS
# 追記案の注(official)に書かれているが set に無い値。注の文をそのまま根拠にする
EXTRA = {
    # 越谷市 禁止地域で許可を受ける自家広告の壁面:『1/5 以下かつ 10㎡ 以下』(上限)。追記案の note より
    ("越谷市", "permit_standards", "壁面広告", "禁止地域で許可を受ける自家広告(条例8条5項1号)"): {"wall_ratio_cap_m2": 10},
}


def _matches(elem: dict, match: dict) -> bool:
    return all(elem.get(k) == v for k, v in match.items() if k != "note")


def _short_cites(cites: list[dict], muni: str) -> list[dict]:
    """schema の citations(条例/施行規則、100字以内、原文の写しに逐語で在る物)用。追記案の引用を「/」「…」で切った抜粋のうち、
    repo の写しに逐語で在る物だけを使う(機械照合 quote_verified=true)。1つも無ければ空(根拠は kind_citations に在る)。"""
    texts = {k: RX._norm(v) for k, v in RX.load_texts(muni).items()}
    out = []
    for c in cites:
        if c.get("source") not in S.SOURCES:
            continue
        for seg in re.split(r"[/…]", c["quote"]):
            q = seg.strip().strip("()()。 ")[:MAX_Q]
            if len(q) >= 6 and RX._norm(q) in texts.get(c["source"], "") and all(o["quote"] != q for o in out):
                out.append({"source": c["source"], "article": c["article"], "quote": q, "quote_verified": True})
    return out[:3]


def _apply(elem: dict, entry: dict, muni: str, tally: Counter, extra: dict | None = None) -> None:
    verified = entry.get("verified")
    uv = set(entry.get("unverified_keys") or [])
    for k, v in {**entry["set"], **(extra or {})}.items():
        if verified == "official" and k not in uv:
            elem[k] = v
            tally["official" if v is not None else "official_null"] += 1
        else:
            elem[k] = None
            uv.add(k)
            tally["unverified" if verified == "official" else ("copy" if verified in ("copy", "copy_only") else verified)] += 1
    elem["verified"] = "copy" if verified in ("copy", "copy_only") else verified
    if uv:
        elem["unverified_keys"] = sorted(uv)
    if entry.get("verified_url"):
        elem["verified_url"] = entry["verified_url"]
    if entry.get("verified_at"):
        elem["verified_at"] = entry["verified_at"]
    verbatim = "逐語ではない" not in (entry.get("note") or "")
    elem["kind_citations"] = [{**c, "verbatim": verbatim} for c in entry.get("citations") or []]
    if entry.get("note"):
        elem["kind_note"] = entry["note"]


def merge(add: dict, muni: str, rules: dict, tally: Counter) -> list[str]:
    log = []
    a = add[muni]
    for entry in a.get("self_use.standards", []):
        hits = [s for s in rules["self_use"]["standards"] if _matches(s, entry["match"])]
        if not hits:
            log.append(f"{muni} self_use: 当たらない {entry['match']}")
        for s in hits:
            _apply(s, entry, muni, tally)
        # 越谷市: 商業系の別エントリ(zone=other・近隣商業/商業)にも同じ照合で 3/10 を入れる(追記案の note の指示)
        byz = entry["set"].get("wall_ratio_by_zone") or {}
        if entry["match"].get("kind") == "壁面広告" and byz and entry.get("verified") == "official":
            for s in rules["self_use"]["standards"]:
                if s.get("kind") == "壁面広告" and s.get("zone") == "other" and set(s.get("zone_labels") or []) & set(byz):
                    r = {byz[z] for z in s["zone_labels"] if z in byz}
                    if len(r) == 1:
                        _apply(s, {**entry, "set": {"wall_ratio_max": r.pop()}}, muni, tally)
                        log.append(f"{muni} self_use: 商業系の別エントリにも {s['wall_ratio_max']}")
    rules.setdefault("permit_standards", [])
    for entry in a.get("permit_standards", []):
        hits = [s for s in rules["permit_standards"] if _matches(s, entry["match"])]
        extra = EXTRA.get((muni, "permit_standards", entry["match"].get("kind"), entry["match"].get("when")))
        if not hits:
            when = entry["match"].get("when") or ""
            new = {"kind": entry["match"]["kind"], "when": when, "self_use_only": bool(entry["match"].get("self_use_only", False)),
                   "zone": "prohibited_zone" if "禁止地域" in when else ("permit_zone" if when.startswith("許可基準") else None),
                   "height_max_m": None, "ground_clearance_min_m": None, "protrusion_max_m": None, "area_max_m2": None,
                   "conditions": entry.get("note") or None, "citations": _short_cites(entry.get("citations") or [], muni),
                   "confidence": 0.9 if entry.get("verified") == "official" else 0.5}
            rules["permit_standards"].append(new)
            hits = [new]
            log.append(f"{muni} permit_standards: 新規 {new['kind']}・{when}")
        for s in hits:
            _apply(s, entry, muni, tally, extra)
    if not rules["permit_standards"]:
        del rules["permit_standards"]
    ro = a.get("road_occupancy")
    if ro:
        out = {k: ro[k] for k in ("law_citation", "use_permit_citation") if ro.get(k)}
        out["by_manager"] = []
        for b in ro.get("by_manager", []):
            v = b.get("verified")
            b = {**b, "verified": "copy" if v in ("copy", "copy_only") else v}
            b["kind_citations"] = b.pop("citations", None) or []   # 条例の外の根拠。条例の写しとの機械照合(citations)には入れない
            tally[f"road_{b['verified']}"] += 1
            out["by_manager"].append(b)
        rules["road_occupancy"] = out
    return log


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 2
    add = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    write = "--write" in argv
    total = Counter()
    for muni in [k for k in add if not k.startswith("_")]:
        p = DATA / f"rules_{muni}.json"
        doc = json.loads(p.read_text(encoding="utf-8"))
        tally = Counter()
        log = merge(add, muni, doc["rules"], tally)
        errs = S.validate_file(doc)
        print(muni, dict(tally), *log, sep="\n  ")
        if errs:
            print("  検査の誤り:", *errs[:10], sep="\n    ")
            return 1
        total += tally
        if write:
            p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("合計", dict(total), "(書き込み済み)" if write else "(書き込みなし)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
