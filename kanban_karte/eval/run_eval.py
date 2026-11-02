"""評価の実行。

  python3 -m kanban_karte.eval.run_eval --judge oracle --source all
  python3 -m kanban_karte.eval.run_eval --judge oracle --source xlsx_karte --limit 50
  python3 -m kanban_karte.eval.run_eval --judge claude --limit 5 --dry-run   # API を呼ばず送る量だけ見る
  python3 -m kanban_karte.eval.run_eval --judge claude --limit 20 --seed 1

結果 JSON(sign_id 入り)と、人が読む Markdown 要約(件数と率だけ。runs/latest.md)を書く。
runs/ は .gitignore 済み。
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from datetime import datetime
from pathlib import Path

from . import dataset, metrics
from .judge import JUDGE_NAMES, build_judge

REPO_ROOT = dataset.REPO_ROOT
EVAL_MD = REPO_ROOT / "docs" / "EVAL.md"
LOG_HEADING = "## 日ごとの測定記録"
SOURCE_LABEL = {"paper_scan": "紙のスキャン束", "xlsx_center": "客先A センター単位の安全点検報告書(18項目)",
                "xlsx_karte": "客先B 看板カルテ様式(5項目×4段階)"}


def select(pool: list, limit: int | None, seed: int, source: str) -> list:
    """sign_id 順の pool から limit 件。seed ごとに決定論のランダム抽出(limit なしは全件)。"""
    if limit is None or limit >= len(pool):
        return pool
    rng = random.Random(f"{seed}:{source}")
    return sorted(rng.sample(pool, limit), key=lambda i: i.sign_id)


def _pools(items, sources, split, limit, seed):
    """(source, 判定する看板, 正解ランクなしの除外数, 写真なしの除外数)。

    正解のない看板と、入力の写真が1枚もない看板(紙の束で写真セルを切り出せなかった物など)は
    判定しない。API の費用の無駄になるのと、どの判定役も同じ母数で比べるため。除外数は結果に必ず載せる。
    """
    for s in sources:
        inside = [i for i in items if i.source == s and (split == "all" or i.split == split)]
        with_truth = [i for i in inside if i.rank_true is not None]
        pool = sorted((i for i in with_truth if i.photo_paths), key=lambda i: i.sign_id)
        yield (s, select(pool, limit, seed, s), len(inside) - len(with_truth),
               len(with_truth) - len(pool))


def _add_excluded(m: dict, no_truth: int, no_photos: int) -> dict:
    m["n_input"] += no_truth + no_photos
    m["n_excluded_no_truth"] += no_truth
    m["n_excluded_no_photos"] = no_photos
    return m


def _usage_total(preds) -> dict:
    u = [p.usage for p in preds if p.usage]
    costs = [x["cost_usd"] for x in u]
    return {
        "signs": len(preds), "signs_with_usage": len(u),
        "failed_none": sum(1 for p in preds if p.rank_pred is None),
        "attempts": sum(x["attempts"] for x in u),
        "input_tokens": sum(x["input_tokens"] for x in u),
        "output_tokens": sum(x["output_tokens"] for x in u),
        "cost_usd": None if (u and any(c is None for c in costs)) else sum(costs),
    }


def dry_run(judge, source: str, limit: int | None, split: str, seed: int,
            config_path: str | None) -> dict:
    sources = dataset.RANKED_SOURCES if source == "all" else (source,)
    items = dataset.load_all(config_path, sources)
    per = {}
    for s, pool, _, no_photos in _pools(items, sources, split, limit, seed):
        ests = [judge.estimate(it) for it in pool]
        per[s] = {"signs": len(pool), "signs_no_photos": no_photos,
                  "photos": sum(e["photos"] for e in ests),
                  "image_tokens": sum(e["image_tokens"] for e in ests),
                  "text_tokens": sum(e["text_tokens"] for e in ests),
                  "input_tokens": sum(e["input_tokens"] for e in ests)}
    tot = {k: sum(v[k] for v in per.values()) for k in next(iter(per.values()))}
    from .claude_judge import ASSUMED_OUTPUT_TOKENS, cost_usd
    out_tok = ASSUMED_OUTPUT_TOKENS * tot["signs"]
    return {"model": judge.model, "max_photos": judge.max_photos, "split": split, "seed": seed,
            "limit_per_source": limit, "per_source": per, "total": tot,
            "assumed_output_tokens": out_tok,
            "est_cost_usd": cost_usd(judge.model, tot["input_tokens"], out_tok)}


def dry_run_text(d: dict) -> str:
    L = [f"# dry-run(API は呼んでいません)", "",
         f"- モデル: {d['model']}、写真は 1 看板あたり最大 {d['max_photos']} 枚、長辺 1280px に縮小",
         f"- 分割: {d['split']}、limit(ソースごと): {d['limit_per_source'] or 'なし'}、seed: {d['seed']}", "",
         "| ソース | 送る看板数 | 写真なしで除外 | 写真枚数 | 推定入力トークン(画像+文) |", "|---|---|---|---|---|"]
    for s, v in d["per_source"].items():
        L.append(f"| {s} | {v['signs']} | {v['signs_no_photos']} | {v['photos']} | "
                 f"{v['input_tokens']:,}({v['image_tokens']:,}+{v['text_tokens']:,}) |")
    t = d["total"]
    L.append(f"| 合計 | {t['signs']} | {t['signs_no_photos']} | {t['photos']} | "
             f"{t['input_tokens']:,}({t['image_tokens']:,}+{t['text_tokens']:,}) |")
    c = d["est_cost_usd"]
    L += ["", f"- 概算費用: " + ("不明(価格表にないモデル)" if c is None else
          f"約 ${c:.2f}(入力 {t['input_tokens']:,} トークン + 出力は 1 看板 {d['assumed_output_tokens'] // max(1, t['signs'])} トークンと仮定)"),
          "- 画像トークンは「幅x高さ/750(上限 4784)」の概算。文は 1 文字 1 トークンの概算。"
          "正確な値は鍵が入ってから count_tokens で測る。",
          "- JSON 不正の再試行(最大 1 回)が起きると、その看板は入力が約 2 倍になる。"]
    return "\n".join(L)


def run(judge, judge_name: str, source: str, limit: int | None, split: str, seed: int,
        config_path: str | None) -> dict:
    sources = dataset.RANKED_SOURCES if source == "all" else (source,)
    items = dataset.load_all(config_path, sources)
    counts = dataset.summarize(items)

    per_source: dict[str, dict] = {}
    usage_by_source: dict[str, dict] = {}
    records: list[dict] = []
    all_pairs, all_preds = [], []
    ex_truth_total = ex_photo_total = 0
    for s, pool, ex_truth, ex_photo in _pools(items, sources, split, limit, seed):
        preds = judge.judge_many(pool)
        pairs = list(zip(pool, preds))
        per_source[s] = _add_excluded(metrics.compute(pairs), ex_truth, ex_photo)
        ex_truth_total += ex_truth
        ex_photo_total += ex_photo
        usage_by_source[s] = _usage_total(preds)
        all_pairs += pairs
        all_preds += preds
        for it, pr in pairs:
            rec = {"source": s, "sign_id": it.sign_id, "split": it.split,
                   "rank_true": it.rank_true, "rank_pred": pr.rank_pred,
                   "confidence": pr.confidence}
            if pr.usage:
                rec["usage"] = pr.usage
            if pr.rank_pred is None and isinstance(pr.raw, dict):
                rec["raw"] = pr.raw
            elif isinstance(pr.raw, dict) and pr.raw.get("reasons"):
                rec["reasons"] = pr.raw["reasons"]
            records.append(rec)
    return {
        "created": datetime.now().isoformat(timespec="seconds"),
        "judge": judge_name, "model": getattr(judge, "model", None), "source": source,
        "split": split, "limit_per_source": limit, "seed": seed,
        "dataset_counts": counts,
        "overall": _add_excluded(metrics.compute(all_pairs), ex_truth_total, ex_photo_total),
        "usage": _usage_total(all_preds), "usage_by_source": usage_by_source,
        "per_source": per_source,
        "records": records,
    }


def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _metrics_block(m: dict) -> list[str]:
    it = m["items"]
    lines = [
        f"- 評価母数 n = {m['n']}(入力 {m['n_input']}、正解ランクなしで除外 {m['n_excluded_no_truth']}、"
        f"写真なしで除外 {m['n_excluded_no_photos']})",
        f"- 完全一致率 {_pct(m['exact_rate'])}({m['exact']}/{m['n']})",
        f"- ±1以内 {_pct(m['within1_rate'])}({m['within1']}/{m['n']})",
        f"- 要対応(3,4)の見逃し率 {_pct(m['miss_rate'])}({m['action_needed_missed']}/{m['action_needed_true']})",
        f"- 要対応の誤警報率 {_pct(m['false_alarm_rate'])}({m['false_alarm']}/{m['not_action_true']})",
        f"- 要対応の適合率 {_pct(m['action_precision'])}",
        f"- 項目一致率 {_pct(it['item_agreement_rate'])}(判断できずを不一致に数える。項目 {it['cells_correct']}/{it['cells']})、"
        f"答えた項目だけの率 {_pct(it['item_agreement_answered_only_rate'])}",
        "",
        "判断できず(別枠):",
        "",
        f"- 看板単位: ランクを返せず {m['no_answer']} 基 / {m['n']}(これを不一致に数えた完全一致率は上、"
        f"答えた看板だけの完全一致率 {_pct(m['exact_answered_only_rate'])})",
        f"- 項目単位: 判断できず {it['cells_unanswered']} 項目 / {it['cells']}({_pct(it['cells_unanswered_rate'])})。"
        f"1項目以上が判断できずの看板 {it['signs_with_any_unanswered']} 基(項目判定が正解にある看板 {it['signs_with_item_truth']} 基のうち)、"
        f"項目判定を返さなかった看板 {it['signs_without_item_pred']} 基",
        "",
        "混同行列(行=正解、列=予測):",
        "",
        "| 正解＼予測 | 1 | 2 | 3 | 4 |",
        "|---|---|---|---|---|",
    ]
    for i, row in enumerate(m["confusion"], 1):
        lines.append(f"| {i} | " + " | ".join(map(str, row)) + " |")
    return lines


def to_markdown(r: dict, json_name: str) -> str:
    L = [f"# 評価結果 {r['created']}", "",
         f"- 判定役: {r['judge']}", f"- 対象: {r['source']}、分割: {r['split']}、"
         f"limit(ソースごと): {r['limit_per_source'] or 'なし'}、seed: {r['seed']}",
         f"- 結果 JSON: {json_name}", ""]
    if r["judge"] == "oracle":
        L += ["> 検算用です(正解をそのまま返す)。この数字を評価結果として報告しないこと。", ""]
    L += ["## データ件数", "",
          "| ソース | 総数 | ランクあり | ランクなし(除外) | 評価用 | 例示用 | ランクありで写真なし(除外) |",
          "|---|---|---|---|---|---|---|"]
    for s, c in r["dataset_counts"].items():
        L.append(f"| {s} | {c['total']} | {c['with_rank']} | {c['no_rank_excluded']} | "
                 f"{c['eval']} | {c['example']} | {c['no_photos']} |")
    L += ["", "分割は決定論(70% 評価用 / 30% 例示用)。評価用・例示用はランクありの看板だけを数えている。", ""]
    if r["usage"]["signs_with_usage"]:
        u = r["usage"]
        c = u["cost_usd"]
        L += ["## 使用量(API)", "",
              f"- モデル: {r['model']}",
              f"- 判定した看板 {u['signs']} 件(うち判定できず {u['failed_none']} 件)、API 呼び出し {u['attempts']} 回",
              f"- 入力 {u['input_tokens']:,} トークン、出力 {u['output_tokens']:,} トークン",
              "- 推定費用: " + ("不明(価格表にないモデル)" if c is None else f"${c:.2f}"), ""]
    L += ["## 全体", ""] + _metrics_block(r["overall"]) + [""]
    for s, m in r["per_source"].items():
        L += [f"## {s}({SOURCE_LABEL.get(s, s)})", ""] + _metrics_block(m) + [""]
    return "\n".join(L)


def log_row(r: dict, note: str = "", max_photos: int | None = None) -> str:
    """docs/EVAL.md の「日ごとの測定記録」に追記する1行(Markdown の表の行)。"""
    o = r["overall"]
    it = o["items"]
    u = r["usage"]
    cond = f"{r['judge']} / {r['source']} / {r['split']} / " + \
        (f"limit={r['limit_per_source']} seed={r['seed']}" if r["limit_per_source"] else "全件")
    if max_photos and r["judge"] == "claude":
        cond += f" / 写真{max_photos}枚まで"
    cond += " / ゼロショット" if r["judge"] == "claude" else ""
    if note:
        cond += f" / {note}"
    api = bool(u["signs_with_usage"])
    cells = [
        r["created"][:10], cond, r["model"] or "-", r.get("effort") or ("API 既定" if api else "-"),
        f"{o['n']}基", _pct(o["exact_rate"]), _pct(o["within1_rate"]),
        _pct(o["miss_rate"]), _pct(o["false_alarm_rate"]),
        _pct(it["item_agreement_rate"]) if it["cells"] else "-",
        f"看板{o['no_answer']}/項目{it['cells_unanswered']}",
        f"{u['input_tokens']:,}" if api else "-",
        ("不明" if u["cost_usd"] is None else f"${u['cost_usd']:.2f}") if api else "-",
    ]
    return "| " + " | ".join(c.replace("|", "/") for c in cells) + " |"


def append_log(row: str, path: Path = EVAL_MD) -> None:
    text = path.read_text(encoding="utf-8")
    if LOG_HEADING not in text:
        raise RuntimeError(f"{path} に「{LOG_HEADING}」の節がありません")
    if not text.endswith("\n"):
        text += "\n"
    path.write_text(text + row + "\n", encoding="utf-8")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--judge", choices=JUDGE_NAMES, required=True)
    ap.add_argument("--source", choices=["all", *dataset.RANKED_SOURCES], default="all")
    ap.add_argument("--split", choices=["eval", "example", "all"], default="eval")
    ap.add_argument("--limit", type=int, default=None,
                    help="ソースごとの最大件数(seed で決まるランダム抽出。省略時は全件)")
    ap.add_argument("--seed", type=int, default=0, help="--limit の抽出に使う(既定 0)")
    ap.add_argument("--out", default=None, help="結果 JSON のパス(省略時 runs/YYYY-MM-DD_HHMM_<judge>.json)")
    ap.add_argument("--sources-config", default=None, help="sources.toml のパス")
    ap.add_argument("--append-log", action="store_true",
                    help="結果の1行を docs/EVAL.md の「日ごとの測定記録」の表に追記する(oracle は不可)")
    ap.add_argument("--log-note", default="", help="--append-log の「条件」欄に足す一言")
    g = ap.add_argument_group("claude 判定役のみ")
    g.add_argument("--model", default=None, help="モデル ID(既定は ClaudeJudge の DEFAULT_MODEL)")
    g.add_argument("--max-photos", type=int, default=6, help="1 看板に送る写真の最大枚数(既定 6)")
    g.add_argument("--workers", type=int, default=4, help="並列数(1〜4、既定 4)")
    g.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None,
                   help="output_config.effort(省略時は API の既定)")
    g.add_argument("--dry-run", action="store_true", help="API を呼ばず、送る看板数・写真枚数・推定トークンだけ出す")
    a = ap.parse_args(argv)

    opts = {}
    if a.judge == "claude":
        opts = {"max_photos": a.max_photos, "workers": a.workers, "effort": a.effort}
        if a.model:
            opts["model"] = a.model
    elif a.dry_run:
        ap.error("--dry-run は --judge claude のときだけ使えます")
    if a.append_log and a.judge == "oracle":
        ap.error("oracle は検算用なので --append-log できません")
    if a.append_log and a.dry_run:
        ap.error("--dry-run と --append-log は同時に使えません")
    judge = build_judge(a.judge, **opts)

    if a.dry_run:
        print(dry_run_text(dry_run(judge, a.source, a.limit, a.split, a.seed, a.sources_config)))
        return 0

    try:
        r = run(judge, a.judge, a.source, a.limit, a.split, a.seed, a.sources_config)
    except RuntimeError as e:  # 鍵が無いなど
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    r["effort"] = a.effort
    out = Path(a.out) if a.out else REPO_ROOT / "runs" / f"{datetime.now():%Y-%m-%d_%H%M}_{a.judge}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_markdown(r, out.name)
    (out.parent / "latest.md").write_text(md, encoding="utf-8")
    print(md)
    if a.append_log:
        append_log(log_row(r, a.log_note, a.max_photos if a.judge == "claude" else None))
        print(f"\n記録を追記しました: {EVAL_MD}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
