"""保存済みの結果 JSON に後処理(項目からのランク合成・較正)を当てて再集計する。API は呼ばない。

  python3 -m kanban_karte.eval.postproc --run runs/X.json --rank-from items
  python3 -m kanban_karte.eval.postproc --run runs/X.json --calib mode --calib-from runs/example.json
  python3 -m kanban_karte.eval.postproc --run runs/X.json --rank-from items --calib shift \\
      --calib-from runs/example.json --append-log --log-note "条件⑤"

--rank-from items|blend は結果 JSON の records に item_judgments_pred が要る(run_eval が残す)。
--calib の学習データ(--calib-from)は split=example の結果 JSON だけ。--run の看板と交わるとエラーにする。
学習側にも --rank-from を同じに当ててから写像を作る。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import aggregate, calib, metrics
from .run_eval import EVAL_MD, SOURCE_LABEL, _pct, append_log


def transform(records: list[dict], rank_from: str) -> list[dict]:
    """rank_pred を合成したランクに置き換えた写し。rank_from が items/blend で項目が1件も残っていなければエラー。"""
    if rank_from != "model" and not any("item_judgments_pred" in r for r in records):
        raise ValueError("records に item_judgments_pred がありません(項目判定を保存していない結果 JSON)")
    out = []
    for r in records:
        r2 = dict(r)
        r2["rank_pred_model"] = r["rank_pred"]
        r2["rank_pred"] = aggregate.synth_rank(r["rank_pred"], r.get("item_judgments_pred"), rank_from)
        out.append(r2)
    return out


def check_disjoint(train: list[dict], evald: list[dict]) -> None:
    both = calib.train_ids(train) & calib.train_ids(evald)
    if both:
        raise ValueError(f"学習データと評価データの看板が重なっています: {sorted(both)[:3]}")


def process(run: dict, rank_from: str = "model", calib_kind: str = "none",
            train_run: dict | None = None) -> tuple[list[dict], dict | None]:
    recs = transform(run["records"], rank_from)
    model = None
    if calib_kind != "none":
        if train_run is None:
            raise ValueError("--calib には --calib-from が要る")
        train = transform(train_run["records"], rank_from)
        check_disjoint(train, recs)
        model = calib.fit(train, calib_kind)
        for r in recs:
            r["rank_pred"] = calib.apply(model, r["source"], r["rank_pred"])
    return recs, model


def summarize(recs: list[dict]) -> dict:
    rs = [r for r in recs if r["rank_true"] is not None]
    res = {"overall": metrics.rank_metrics([r["rank_true"] for r in rs], [r["rank_pred"] for r in rs]),
           "per_source": {}}
    for s in sorted({r["source"] for r in rs}):
        sub = [r for r in rs if r["source"] == s]
        res["per_source"][s] = metrics.rank_metrics([r["rank_true"] for r in sub], [r["rank_pred"] for r in sub])
    return res


def transitions(m: dict) -> dict:
    c = m["confusion"]
    return {"1→2": c[0][1], "正解1": sum(c[0]), "2→3": c[1][2], "正解2": sum(c[1])}


def condition(run: dict, rank_from: str, calib_kind: str, train_name: str, note: str) -> str:
    cfg = run.get("config") or {}
    cond = (f"claude / {run['source']} / {run['split']} / limit={run['limit_per_source']} seed={run['seed']}"
            f" / 例示{cfg.get('examples', 0)} / 後処理(API なし): rank-from={rank_from}")
    if calib_kind != "none":
        cond += f" calib={calib_kind}(学習 {train_name})"
    return cond + (f" / {note}" if note else "")


def log_row(run: dict, summ: dict, cond: str) -> str:
    o, it = summ["overall"], run["overall"]["items"]
    cells = [run["created"][:10], cond, run["model"] or "-", run.get("effort") or "API 既定", f"{o['n']}基",
             _pct(o["exact_rate"]), _pct(o["within1_rate"]), _pct(o["miss_rate"]), _pct(o["false_alarm_rate"]),
             _pct(it["item_agreement_rate"]) if it["cells"] else "-",  # 項目判定は後処理で変わらない
             f"看板{o['no_answer']}/項目{it['cells_unanswered']}", "-", "-"]
    return "| " + " | ".join(c.replace("|", "/") for c in cells) + " |"


def to_markdown(summ: dict, model: dict | None, cond: str) -> str:
    def block(m):
        t = transitions(m)
        L = [f"- n={m['n']} 完全一致 {_pct(m['exact_rate'])}({m['exact']}) ±1 {_pct(m['within1_rate'])} "
             f"見逃し {_pct(m['miss_rate'])}({m['action_needed_missed']}/{m['action_needed_true']}) "
             f"誤警報 {_pct(m['false_alarm_rate'])}({m['false_alarm']}/{m['not_action_true']}) "
             f"判断できず(看板) {m['no_answer']}",
             f"- 正解1→予測2 {t['1→2']}/{t['正解1']}、正解2→予測3 {t['2→3']}/{t['正解2']}",
             "", "| 正解＼予測 | 1 | 2 | 3 | 4 |", "|---|---|---|---|---|"]
        L += [f"| {i} | " + " | ".join(map(str, row)) + " |" for i, row in enumerate(m["confusion"], 1)]
        return L + [""]
    L = [f"# 後処理の結果: {cond}", "", "## 全体", ""] + block(summ["overall"])
    for s, m in summ["per_source"].items():
        L += [f"## {s}({SOURCE_LABEL.get(s, s)})", ""] + block(m)
    if model:
        L += ["## 較正の写像(学習データ側)", "", f"種類: {model['kind']}、学習の看板数: {model['n']}", ""]
        for s, conf in model["confusion"].items():
            L.append(f"- {s}: 予測→正解の度数 {json.dumps(conf, ensure_ascii=False)} → 写像 {json.dumps(model['map'][s])}")
    return "\n".join(L)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", required=True, help="評価側の結果 JSON")
    ap.add_argument("--rank-from", choices=aggregate.RANK_FROM, default="model")
    ap.add_argument("--calib", choices=calib.CALIB, default="none")
    ap.add_argument("--calib-from", default=None, help="学習側(split=example)の結果 JSON")
    ap.add_argument("--append-log", action="store_true")
    ap.add_argument("--log-note", default="")
    a = ap.parse_args(argv)
    run = json.loads(Path(a.run).read_text(encoding="utf-8"))
    train = json.loads(Path(a.calib_from).read_text(encoding="utf-8")) if a.calib_from else None
    try:
        recs, model = process(run, a.rank_from, a.calib, train)
    except ValueError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    summ = summarize(recs)
    cond = condition(run, a.rank_from, a.calib, Path(a.calib_from).name if a.calib_from else "", a.log_note)
    print(to_markdown(summ, model, cond))
    if a.append_log:
        append_log(log_row(run, summ, cond))
        print(f"\n記録を追記しました: {EVAL_MD}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
