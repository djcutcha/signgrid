"""寸法役の測定: 正解つきの試作データ(20基 × 写真2枚)で W/H の相対誤差を測る。

  python3 -m kanban_karte.dim.eval --dir ~/Projects/fkogei-estimate-data/eval/pilot/dim --model claude-sonnet-5-5 [--out runs/dim_<時刻>.json]

渡す物は 写真2枚と sign_name だけ(gt.tsv の寸法・hint・note は渡さない)。
結果 JSON は runs/(gitignore 済み)。正解データの実体は repo に複製しない。--dry-run は API を呼ばず、費用の概算だけ出す。
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from ..eval.claude_judge import PRICES, REPO_ROOT, cost_usd, estimate_image_tokens
from .estimator import LONG_EDGE, DimEstimateError, DimEstimator

ASSUMED_TEXT_TOKENS = 1700   # システムプロンプト+辞書+指示の概算(根拠なしの仮置き)
ASSUMED_OUTPUT_TOKENS = 900


def load_cases(d: Path) -> list[dict]:
    with open(d / "gt.tsv", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    cases = []
    for r in rows:
        cid = r["case_id"]
        photos = [d / "photos" / f"{cid}_{i}.jpg" for i in (1, 2)]
        cases.append({"case_id": cid, "sign_name": r["sign_name"], "w_mm": int(r["W_mm"]), "h_mm": int(r["H_mm"]),
                      "photos": [p for p in photos if p.is_file()]})
    return cases


def rel_err(est: float, gt: float) -> float:
    return abs(est - gt) / gt


def summarize(rows: list[dict]) -> dict:
    """rows: measure() の結果(エラーの行は est が None)。失敗した基は W/H とも「外れ」として数える(誤差は集計から除く)。"""
    errs: list[float] = []
    in_range = within15 = within30 = n = 0
    for r in rows:
        for ax in ("w", "h"):
            n += 1
            e = r.get(f"{ax}_rel_err")
            if e is None:
                continue
            errs.append(e)
            within15 += e <= 0.15
            within30 += e <= 0.30
            in_range += bool(r[f"{ax}_in_range"])
    return {"n_values": n, "n_scored": len(errs),
            "median_rel_err": statistics.median(errs) if errs else None,
            "mean_rel_err": statistics.fmean(errs) if errs else None,
            "within_15": within15, "within_30": within30, "in_range": in_range}


def measure(case: dict, est: DimEstimator) -> dict:
    out = {"case_id": case["case_id"], "gt_w_mm": case["w_mm"], "gt_h_mm": case["h_mm"]}
    try:
        e = est.estimate(case["photos"], case["sign_name"])
    except DimEstimateError as ex:
        out.update({"error": str(ex), "usage": getattr(ex, "usage", {})})
        return out
    out.update({"w_mm": e.w_mm, "h_mm": e.h_mm, "w_range": list(e.w_range), "h_range": list(e.h_range),
                "confidence": e.confidence, "read_from_text": e.read_from_text, "references": e.references,
                "notes": e.notes, "usage": e.usage, "combined": e.combined, "photos": e.photos})
    for ax, gt in (("w", case["w_mm"]), ("h", case["h_mm"])):
        v, (lo, hi) = getattr(e, f"{ax}_mm"), getattr(e, f"{ax}_range")
        out[f"{ax}_rel_err"] = rel_err(v, gt)
        out[f"{ax}_signed_err"] = (v - gt) / gt
        out[f"{ax}_in_range"] = lo <= gt <= hi
    return out


def dry_run(cases: list[dict], model: str) -> float:
    total_in = total_out = 0
    for c in cases:
        total_in += sum(estimate_image_tokens(str(p), LONG_EDGE) for p in c["photos"]) + ASSUMED_TEXT_TOKENS
        total_out += ASSUMED_OUTPUT_TOKENS
    cost = cost_usd(model, total_in, total_out) or 0.0
    print(f"{len(cases)} 基、入力 約 {total_in:,} トークン、出力(仮定)約 {total_out:,} トークン → 約 ${cost:.2f}({model})")
    return cost


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.dim.eval", description="寸法役の測定")
    ap.add_argument("--dir", required=True, help="gt.tsv と photos/ があるフォルダ")
    ap.add_argument("--model", default="claude-sonnet-5-5", choices=sorted(PRICES))
    ap.add_argument("--out", default=None, help="結果 JSON(既定 runs/dim_<時刻>.json)")
    ap.add_argument("--limit", type=int, default=None, help="先頭 N 基だけ")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-usd", type=float, default=3.0, help="この費用を超えたら残りを打ち切る")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    cases = load_cases(Path(a.dir).expanduser())
    if a.limit:
        cases = cases[:a.limit]
    cases = [c for c in cases if c["photos"]]
    if a.dry_run:
        dry_run(cases, a.model)
        return 0

    est = DimEstimator(a.model)
    spent = 0.0
    t0 = time.time()

    def run(c):
        nonlocal spent
        if spent >= a.max_usd:
            return {"case_id": c["case_id"], "error": "費用の上限で打ち切り", "usage": {}}
        r = measure(c, est)
        spent += (r.get("usage") or {}).get("cost_usd") or 0.0
        print(f"  {c['case_id']}: " + (r.get("error") or
              f"W {r['w_mm']} (正解 {c['w_mm']}, {r['w_rel_err']:.0%}) H {r['h_mm']} (正解 {c['h_mm']}, {r['h_rel_err']:.0%}) {r['confidence']}"),
              file=sys.stderr)
        return r

    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        rows = list(ex.map(run, cases))
    summ = summarize(rows)
    usage = {"input_tokens": sum((r.get("usage") or {}).get("input_tokens", 0) for r in rows),
             "output_tokens": sum((r.get("usage") or {}).get("output_tokens", 0) for r in rows),
             "cost_usd": sum((r.get("usage") or {}).get("cost_usd") or 0.0 for r in rows)}
    result = {"model": a.model, "run_at": datetime.now().isoformat(timespec="seconds"), "seconds": round(time.time() - t0),
              "summary": summ, "usage": usage, "cases": rows}
    out = Path(a.out) if a.out else REPO_ROOT / "runs" / f"dim_{datetime.now():%Y%m%d_%H%M%S}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    s = summ
    fmt = lambda v: "—" if v is None else f"{v:.1%}"  # noqa: E731
    print(f"\n{a.model}: W/H {s['n_values']}値中 {s['n_scored']}値を採点  相対誤差 中央値 {fmt(s['median_rel_err'])} 平均 {fmt(s['mean_rel_err'])}  "
          f"±15%以内 {s['within_15']}/{s['n_values']}  ±30%以内 {s['within_30']}/{s['n_values']}  範囲に正解 {s['in_range']}/{s['n_values']}")
    print(f"入力 {usage['input_tokens']:,} 出力 {usage['output_tokens']:,} トークン、費用 ${usage['cost_usd']:.3f}、{result['seconds']} 秒 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
