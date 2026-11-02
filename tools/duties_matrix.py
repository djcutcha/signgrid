"""4自治体 × {自家用・許可不要 / 自家用・許可要 / 一般} × {全高 3m / 5m} の 24 ケースで、4つの義務の判定を表にする(API なし)。

  python3 tools/duties_matrix.py [--out runs/duties_matrix.md] [--md-only]

前提(表の見出しにも出す): 看板の種類は『自立(広告塔・広告板)』、設置場所は許可区域とする(種類を決めないと許可の要否が要実測で止まり、
義務の違いが見えないため)。面積は 自家用・許可不要= 1 ㎡、自家用・許可要= 30 ㎡、一般= 5 ㎡。全高は上端の高さ(地上から)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kanban_karte.legal.judge import judge  # noqa: E402
from kanban_karte.legal.rules_extract import ORDINANCE_FILES  # noqa: E402

CASES = (("自家用・許可不要", "self", 1.0), ("自家用・許可要", "self", 30.0), ("一般", "general", 5.0))
HEIGHTS = (3.0, 5.0)
KIND, PLACE = "自立", "許可区域"
COLS = (("maintenance", "管理"), ("inspection", "点検"), ("qualified", "有資格者"), ("report", "行政への報告"))


def rows() -> list[dict]:
    out = []
    for key in ORDINANCE_FILES:
        for label, use, area in CASES:
            for h in HEIGHTS:
                r = judge(municipality=key, use=use, area_m2=area, height_m=h, kind=KIND, place=PLACE)
                out.append({"key": key, "case": label, "area": area, "height": h, "permit": r.permit_required,
                            "interval": r.inspection_interval_years, "values": {k: r.duties[k]["value"] for k, _ in COLS},
                            "duties": r.duties})
    return out


def to_markdown(rs: list[dict], with_text: bool = True) -> str:
    L = [f"種類= {KIND}(広告塔・広告板)、設置場所= {PLACE}。面積: 自家用・許可不要 1 ㎡ / 自家用・許可要 30 ㎡ / 一般 5 ㎡。全高= 上端の高さ。", "",
         "| 条例 | ケース | 全高 | 許可 | 管理 | 点検 | 有資格者 | 行政への報告 |", "|---|---|---|---|---|---|---|---|"]
    for r in rs:
        v = r["values"]
        ins = v["inspection"] + (f"({r['interval']:g}年)" if r["interval"] and v["inspection"] != "不要" else "")
        L.append(f"| {r['key']} | {r['case']} | {r['height']:g} m | {r['permit']} | {v['maintenance']} | {ins} | {v['qualified']} | {v['report']} |")
    if with_text:
        L += ["", "各セルの文言(画面に出る行):", ""]
        for r in rs:
            L.append(f"- **{r['key']} / {r['case']} / 全高 {r['height']:g} m**(許可 {r['permit']})")
            for k, t in COLS:
                L.append(f"  - {t}: {r['duties'][k]['text']}")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None, help="書き出す先(省略で標準出力)")
    ap.add_argument("--md-only", action="store_true", help="表だけ(各セルの文言を付けない)")
    a = ap.parse_args(argv)
    md = to_markdown(rows(), with_text=not a.md_only)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(md, encoding="utf-8")
        print(a.out)
    else:
        print(md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
