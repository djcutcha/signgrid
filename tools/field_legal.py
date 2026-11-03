"""看板ごとの寸法(表の size・スケッチ)から、面積と全高を出して法規(legal.judge)に通し、スケッチ前後を比べる(API なし)。

  python3 tools/field_legal.py                      # 置き場は sources.toml の [eval.dirs].line_paper、住所は data/private/field_site_address.txt
  python3 tools/field_legal.py --out runs/field_legal.json

面積 = W×H(㎡)。全高 = GL からの高さ: 縁が bottom(看板の下端まで)なら gl + h(同じスケッチの H。無ければ採った H)、top(上端まで)なら gl、unknown なら出さない(要実測)。
寸法の出どころ: W・H は表の size を優先し(両方あるとき)、無ければスケッチ。GL は縁が読めるスケッチを優先し、無ければ表の gl_mm(縁は unknown)。
スケッチ前 = 今の台帳の登録(表の W×H、全高なし)。住所は区までで、用途地域・看板の種類は渡さない(区分なし)。
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import field_transcribe as ft  # noqa: E402

from kanban_karte.legal.judge import judge  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
ADDRESS_FILE = REPO / "data/private/field_site_address.txt"
DUTIES = ("maintenance_duty", "inspection_duty", "qualified_inspector", "report_duty")


def full_height_mm(gl_mm, edge: str | None, h_mm) -> int | None:
    """全高(mm)。bottom: GL から下端までの gl に高さ h を足す。top: gl がそのまま全高。unknown・値なしは None。"""
    if not gl_mm:
        return None
    if edge == "top":
        return int(gl_mm)
    if edge == "bottom" and h_mm:
        return int(gl_mm) + int(h_mm)
    return None


def _n(v):
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return None
    return n or None


def dims(row: dict, *, prefer: str = "table") -> dict:
    """items.tsv の 1 行 → {area_m2, height_m, size_w, size_h, size_src, gl_src}。出せない値は None。
    prefer="table" は表の W×H を優先、"sketch" はスケッチの W×H を優先(食い違いの影響を見る用)。"""
    t = json.loads(row.get("size") or "{}")
    tw, th, tgl = _n(t.get("w_mm")), _n(t.get("h_mm")), _n(t.get("gl_mm"))
    sw, sh, sgl = _n(row.get("sketch_w_mm")), _n(row.get("sketch_h_mm")), _n(row.get("sketch_gl_mm"))
    edge = row.get("sketch_gl_edge") or "unknown"
    cands = [("table", tw, th), ("sketch", sw, sh)]
    if prefer == "sketch":
        cands.reverse()
    w = h = None
    src = None
    for name, cw, ch in cands:
        if cw and ch:
            w, h, src = cw, ch, name
            break
    if sgl:
        height_mm, gl_src = full_height_mm(sgl, edge, sh or h), "sketch"  # bottom は同じスケッチの H を足す
    else:
        height_mm, gl_src = None, ("table(縁なし)" if tgl else None)
    return {"size_w": w, "size_h": h, "size_src": src, "gl_src": gl_src,
            "area_m2": round(w * h / 1_000_000, 2) if w and h else None,
            "height_m": round(height_mm / 1000, 2) if height_mm else None}


def before_dims(row: dict) -> dict:
    """スケッチ前の登録: 表の W×H、全高なし。"""
    t = json.loads(row.get("size") or "{}")
    w, h = _n(t.get("w_mm")), _n(t.get("h_mm"))
    return {"area_m2": round(w * h / 1_000_000, 2) if w and h else None, "height_m": None}


def judged(address: str, area, height) -> dict:
    r = judge(address, use="self", area_m2=area, height_m=height)
    return {"permit": r.permit_required, **{d: getattr(r, d) for d in DUTIES}}


def why_unresolved(address: str, area, height) -> str:
    """要実測が残る理由。寸法なし / GL 不明(全高があれば許可の結論が変わる)/ 区分なし(用途地域・看板の種類が足りない)。"""
    if area is None:
        return "寸法なし"
    if height is None:
        lo, hi = judge(address, use="self", area_m2=area, height_m=0.5).permit_required, \
            judge(address, use="self", area_m2=area, height_m=100).permit_required
        if lo != hi:
            return "GL 不明"
    return "区分なし"


def read_items(base: Path) -> list[dict]:
    return list(csv.DictReader(open(base / "items.tsv", encoding="utf-8"), delimiter="\t"))


def tally(address: str, rows: list[dict], dim_of) -> dict:
    out = {"permit": {}, **{d: {} for d in DUTIES}, "unresolved_reason": {}}
    for r in rows:
        d = dim_of(r)
        j = judged(address, d["area_m2"], d["height_m"])
        for k in ("permit", *DUTIES):
            out[k][j[k]] = out[k].get(j[k], 0) + 1
        if j["permit"] == "要実測":
            why = why_unresolved(address, d["area_m2"], d["height_m"])
            out["unresolved_reason"][why] = out["unresolved_reason"].get(why, 0) + 1
    return out


def run(base: Path, address: str) -> dict:
    rows = read_items(base)
    before = tally(address, rows, before_dims)
    after = tally(address, rows, dims)
    after_sk = tally(address, rows, lambda r: dims(r, prefer="sketch"))
    changed_by_priority = sum(
        1 for r in rows if judged(address, dims(r)["area_m2"], dims(r)["height_m"])["permit"]
        != judged(address, dims(r, prefer="sketch")["area_m2"], dims(r, prefer="sketch")["height_m"])["permit"])
    with_sketch = [r for r in rows if r.get("sketch_pages")]
    return {"address": "(住所ファイルの値。区まで)" if address else None, "signs": len(rows), "signs_with_sketch": len(with_sketch),
            "signs_area": sum(1 for r in rows if dims(r)["area_m2"] is not None),
            "signs_area_before": sum(1 for r in rows if before_dims(r)["area_m2"] is not None),
            "signs_height": sum(1 for r in rows if dims(r)["height_m"] is not None),
            "before": before, "after": after, "after_if_sketch_wh_preferred": after_sk["permit"],
            "permit_changed_by_wh_priority": changed_by_priority}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--address-file", default=str(ADDRESS_FILE))
    ap.add_argument("--out", default=str(REPO / "runs/field_legal.json"))
    ap.add_argument("--sources-config")
    a = ap.parse_args()
    raw, _ = ft.paths(a.sources_config)
    address = Path(a.address_file).read_text(encoding="utf-8").strip()
    res = run(raw.parent, address)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
