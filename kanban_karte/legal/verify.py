"""rules_*.json の数値を、手元の正解(非公開の検算済みデータ)と照合する。

正解の置き場は data/private/sources.toml(未追跡)の [legal.verify] rules_dir / files。
読むだけで、値はこの repo にも出力にも書かない(出力は件数と不一致の項目名だけ)。

照合の方法(粗い「存在照合」):
  正解ファイルは散文の Markdown / TSV なので、構造では比べない。自治体ごとの正解ファイルから
  「数字+単位」(㎡・m・年・月。漢数字・全角数字も直す)を集め、rules の数値項目(面積・高さ・地上高・
  許可期間)の値が、その集合に在るかを見る。在れば一致、無ければ不一致(項目名を出す)。
  どの区域・どの種類の値かまでは見ない(同じ値が別の所に在れば一致になる)ので、一致は
  「少なくとも正解に在る値」という意味で、不一致のほうが確かな信号。
"""
from __future__ import annotations

import json
import re
import tomllib
import unicodedata
from pathlib import Path

from . import schema as S
from .rules_extract import ORDINANCE_FILES, REPO_ROOT, load_rules, load_texts, rules_path

DEFAULT_SOURCES = REPO_ROOT / "data" / "private" / "sources.toml"

# 条例キー -> 正解ファイル名に含まれる語(sources.toml の files から選ぶ)
KEY_TOKENS = {
    "東京都": ("tokyo_", "completeness_tokyo"),
    "埼玉県": ("saitama_numeric", "completeness_saitama_pref"),
    "越谷市": ("koshigaya_", "completeness_koshigaya"),
    "さいたま市": ("saitama_shi_", "completeness_saitama_city"),
}

_KANJI = {"〇": 0, "一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
_NUM = r"[0-9〇一二三四五六七八九十百]+(?:[.・][0-9〇一二三四五六七八九]+)?"
_AREA = re.compile(rf"({_NUM})\s*(?:平方メートル|㎡|m2|m²)")
_LEN = re.compile(rf"({_NUM})\s*(?:(?<!平方)メートル|m(?![²2a-zA-Z]))")
_YEAR = re.compile(rf"({_NUM})\s*年(?!度|間\s*以上)")
_MONTH = re.compile(rf"({_NUM})\s*(?:か月|ヶ月|箇月|月)(?!曜)")


def _to_num(tok: str) -> float | None:
    """'10' '四・五' '二十' '百二十' '4.5' -> 数。読めなければ None"""
    t = tok.replace("・", ".")
    if re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", t):
        return float(t)
    if "." in t:
        a, b = t.split(".", 1)
        x, y = _to_num(a), _to_num(b)
        if x is None or y is None or not re.fullmatch(r"[0-9〇一二三四五六七八九]+", b):
            return None
        return x + float("0." + "".join(str(_KANJI.get(c, c)) for c in b))
    total, cur = 0, 0
    for c in t:
        if c in _KANJI:
            cur = cur * 10 + _KANJI[c] if cur and c in "〇" else _KANJI[c]
        elif c == "十":
            total += (cur or 1) * 10
            cur = 0
        elif c == "百":
            total += (cur or 1) * 100
            cur = 0
        elif c.isdigit():
            cur = cur * 10 + int(c)
        else:
            return None
    return float(total + cur)


def truth_numbers(text: str) -> dict[str, set[float]]:
    """正解の文から {単位: {値}}。単位 = area(㎡) / length(m) / years(年。月は 1/12 年)"""
    t = unicodedata.normalize("NFKC", text)
    out: dict[str, set[float]] = {"area": set(), "length": set(), "years": set()}
    for unit, rx, f in (("area", _AREA, 1), ("length", _LEN, 1), ("years", _YEAR, 1), ("years", _MONTH, 1 / 12)):
        for m in rx.finditer(t):
            v = _to_num(m.group(1))
            if v is not None:
                out[unit].add(round(v * f, 4))
    return out


def rule_numbers(rules: dict) -> list[tuple[str, str, float]]:
    """rules の数値項目 -> [(項目名, 単位, 値)]。項目名に値は入れない。"""
    items: list[tuple[str, str, float]] = []

    def add(name, unit, v):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            items.append((name, unit, float(v)))

    for i, s in enumerate(rules["self_use"]["standards"]):
        p = f"self_use.standards[{i}]({s.get('kind')}/{s.get('zone')})"
        add(p + ".area_max_m2", "area", s.get("area_max_m2"))
        add(p + ".height_max_m", "length", s.get("height_max_m"))
        add(p + ".ground_clearance_min_m", "length", s.get("ground_clearance_min_m"))
    for i, t in enumerate(rules["permit_required_types"]):
        p = f"permit_required_types[{i}]({t.get('kind')})"
        add(p + ".area_threshold_m2", "area", t.get("area_threshold_m2"))
        add(p + ".height_threshold_m", "length", t.get("height_threshold_m"))
        add(p + ".ground_clearance_threshold_m", "length", t.get("ground_clearance_threshold_m"))
    for i, ps in enumerate(rules.get("permit_standards") or []):
        p = f"permit_standards[{i}]({ps.get('kind')}/{ps.get('when')})"
        add(p + ".height_max_m", "length", ps.get("height_max_m"))
        add(p + ".ground_clearance_min_m", "length", ps.get("ground_clearance_min_m"))
        add(p + ".protrusion_max_m", "length", ps.get("protrusion_max_m"))
        add(p + ".area_max_m2", "area", ps.get("area_max_m2"))
    pt = rules["permit_term"]
    add("permit_term.default_years", "years", pt.get("default_years"))
    for i, it in enumerate(pt["items"]):
        add(f"permit_term.items[{i}]({it.get('kind')}).years", "years", it.get("years"))
    ins = rules.get("inspection")  # 旧形式(4つの義務に分ける前)
    if ins:
        add("inspection.interval_years", "years", ins.get("interval_years"))
        for i, c in enumerate(ins["conditions"]):
            add(f"inspection.conditions[{i}].area_min_m2", "area", c.get("area_min_m2"))
            add(f"inspection.conditions[{i}].height_min_m", "length", c.get("height_min_m"))
            add(f"inspection.conditions[{i}].ground_clearance_min_m", "length", c.get("ground_clearance_min_m"))
    add("inspection_duty.interval_years", "years", (rules.get("inspection_duty") or {}).get("interval_years"))
    for dk in S.DUTY_KEYS:
        node = rules.get(dk) or {}
        conds = [("conditions", node.get("conditions"))]
        if isinstance(node.get("effort"), dict):
            conds.append(("effort.conditions", node["effort"].get("conditions")))
        for cn, c in conds:
            for k, unit in (("area_min_m2", "area"), ("height_min_m", "length"), ("ground_clearance_min_m", "length")):
                add(f"{dk}.{cn}.{k}", unit, (c or {}).get(k))
            for j, a in enumerate((c or {}).get("any_of") or []):
                for k, unit in (("area_min_m2", "area"), ("height_min_m", "length"), ("ground_clearance_min_m", "length")):
                    add(f"{dk}.{cn}.any_of[{j}].{k}", unit, a.get(k))
    return items


def load_sources(sources: Path | None = None) -> tuple[Path, list[str]]:
    cfg = tomllib.loads((sources or DEFAULT_SOURCES).read_text(encoding="utf-8"))
    v = cfg["legal"]["verify"]
    return Path(v["rules_dir"]).expanduser(), list(v["files"])


def truth_files(key: str, rules_dir: Path, files: list[str]) -> list[Path]:
    toks = KEY_TOKENS[key]
    return [rules_dir / f for f in files if any(f.startswith(t) for t in toks) and (rules_dir / f).exists()]


def _has(truth: dict[str, set[float]], unit: str, v: float) -> bool:
    return any(abs(v - x) < 0.002 for x in truth[unit])


def verify_key(key: str, rules: dict, rules_dir: Path, files: list[str], data_dir: Path | None = None) -> dict:
    """不一致(正解に無い値)は、さらに原文(条例・施行規則)に在るかで2つに分ける。
    only_source = 正解には無いが原文には在る(正解が当該の表・項を持たない可能性。原文と合うので修正は不要)
    nowhere     = 正解にも原文にも無い(抽出の誤りの疑い。原文を読み直す)
    """
    paths = truth_files(key, rules_dir, files)
    truth: dict[str, set[float]] = {"area": set(), "length": set(), "years": set()}
    for p in paths:
        for u, vs in truth_numbers(p.read_text(encoding="utf-8")).items():
            truth[u] |= vs
    source: dict[str, set[float]] = {"area": set(), "length": set(), "years": set()}
    for text in load_texts(key, data_dir).values():
        for u, vs in truth_numbers(text).items():
            source[u] |= vs
    items = rule_numbers(rules)
    mism = [(n, u, v) for n, u, v in items if not _has(truth, u, v)]
    only_source = [n for n, u, v in mism if _has(source, u, v)]
    nowhere = [n for n, u, v in mism if not _has(source, u, v)]
    return {"key": key, "total": len(items), "matched": len(items) - len(mism), "mismatch": [n for n, _, _ in mism],
            "only_source": only_source, "nowhere": nowhere, "truth_files": len(paths)}


def verify_all(sources: Path | None = None, data_dir: Path | None = None) -> list[dict]:
    rules_dir, files = load_sources(sources)
    out = []
    for key in ORDINANCE_FILES:
        out.append(verify_key(key, load_rules(key, data_dir)["rules"], rules_dir, files, data_dir))
    return out


def second_edition_status(matched: int, total: int) -> str:
    return f"第2版(手元の正解と照合済み、一致 {matched}/{total})"


def stamp(res: dict, data_dir: Path | None = None) -> Path:
    """検算結果を rules_<自治体>.json の status に書く。"""
    p = rules_path(res["key"], data_dir)
    doc = json.loads(p.read_text(encoding="utf-8"))
    doc["status"] = second_edition_status(res["matched"], res["total"])
    doc["verification"] = {"matched": res["matched"], "total": res["total"], "mismatch_items": res["mismatch"], "mismatch_only_in_source": res["only_source"],
                           "method": "数値の存在照合(正解ファイルに同じ値が在るか。区域・種類までは見ない)"}
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def main(argv=None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="kanban_karte.legal.verify")
    ap.add_argument("--sources-config", default=None)
    ap.add_argument("--stamp", action="store_true", help="status を第2版に書き換える(不一致が残っていても件数つきで)")
    a = ap.parse_args(argv)
    res = verify_all(Path(a.sources_config) if a.sources_config else None)
    for r in res:
        print(f"{r['key']}: 一致 {r['matched']}/{r['total']}(正解ファイル {r['truth_files']} 件)")
        for m in r["only_source"]:
            print(f"  正解に無い・原文に在る: {m}")
        for m in r["nowhere"]:
            print(f"  不一致(正解にも原文にも無い): {m}")
        if a.stamp:
            stamp(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
