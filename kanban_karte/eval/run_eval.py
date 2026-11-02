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


def select_spread(pool: list, limit: int, seed: int, source: str) -> list:
    """現場(group_key)をばらして limit 件: 現場ごとにシャッフルし、現場を順に回って1基ずつ取る。"""
    rng = random.Random(f"{seed}:{source}:spread")
    by_site: dict[str, list] = {}
    for i in pool:
        by_site.setdefault(i.meta.get("group_key", ""), []).append(i)
    sites = sorted(by_site)
    rng.shuffle(sites)
    for k in sites:
        rng.shuffle(by_site[k])
    out = []
    while len(out) < limit and any(by_site.values()):
        for k in sites:
            if by_site[k] and len(out) < limit:
                out.append(by_site[k].pop())
    return sorted(out, key=lambda i: i.sign_id)


def _pools(items, sources, split, limit, seed, spread=False, exclude_ids=None):
    """(source, 判定する看板, 正解ランクなしの除外数, 写真なしの除外数)。

    正解のない看板と、入力の写真が1枚もない看板(紙の束で写真セルを切り出せなかった物など)は
    判定しない。API の費用の無駄になるのと、どの判定役も同じ母数で比べるため。除外数は結果に必ず載せる。
    """
    for s in sources:
        inside = [i for i in items if i.source == s and (split == "all" or i.split == split)]
        with_truth = [i for i in inside if i.rank_true is not None]
        pool = sorted((i for i in with_truth if i.photo_paths
                       and i.sign_id not in (exclude_ids or ())), key=lambda i: i.sign_id)
        pick = select_spread if spread and limit is not None and limit < len(pool) else select
        yield (s, pick(pool, limit, seed, s), len(inside) - len(with_truth),
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
        "cache_write_tokens": sum(x.get("cache_write_tokens", 0) for x in u),
        "cache_read_tokens": sum(x.get("cache_read_tokens", 0) for x in u),
        "cost_usd": None if (u and any(c is None for c in costs)) else sum(costs),
    }


def dry_run(judge, source: str, limit: int | None, split: str, seed: int,
            config_path: str | None) -> dict:
    sources = dataset.RANKED_SOURCES if source == "all" else (source,)
    items = dataset.load_all(config_path, sources)
    if hasattr(judge, "set_example_pool"):
        judge.set_example_pool(items)
    per = {}
    for s, pool, _, no_photos in _pools(items, sources, split, limit, seed):
        ests = [judge.estimate(it) for it in pool]
        per[s] = {"signs": len(pool), "signs_no_photos": no_photos,
                  "photos": sum(e["photos"] for e in ests),
                  "image_tokens": sum(e["image_tokens"] for e in ests),
                  "text_tokens": sum(e["text_tokens"] for e in ests),
                  "example_tokens": sum(e["example_image_tokens"] + e["example_text_tokens"] for e in ests),
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
                 f"{v['input_tokens']:,}(判定対象の画像 {v['image_tokens']:,} + 文 {v['text_tokens']:,} + 例示 {v['example_tokens']:,}) |")
    t = d["total"]
    L.append(f"| 合計 | {t['signs']} | {t['signs_no_photos']} | {t['photos']} | "
             f"{t['input_tokens']:,}(判定対象の画像 {t['image_tokens']:,} + 文 {t['text_tokens']:,} + 例示 {t['example_tokens']:,}) |")
    c = d["est_cost_usd"]
    L += ["", f"- 概算費用: " + ("不明(価格表にないモデル)" if c is None else
          f"約 ${c:.2f}(入力 {t['input_tokens']:,} トークン + 出力は 1 看板 {d['assumed_output_tokens'] // max(1, t['signs'])} トークンと仮定)"),
          "- 画像トークンは「幅x高さ/750(上限 4784)」の概算。文は 1 文字 1 トークンの概算。"
          "正確な値は鍵が入ってから count_tokens で測る。",
          "- 例示ありのとき、例示ブロックは prompt caching の対象(1 基目で書き込み 1.25 倍、以降の読み出しは 0.1 倍)。"
          "この概算は全て通常単価で数えた上限寄りの値。",
          "- JSON 不正の再試行(最大 1 回)が起きると、その看板は入力が約 2 倍になる。"]
    return "\n".join(L)


def verify_summary(pairs, verifier) -> dict:
    """反証役の結果: 通した看板数、上げた基数と、その当たり外れ(正解が 3 以上なら当たり)。
    pairs は反証役を通した後の (看板, 予測)。"""
    called = [(it, p) for it, p in pairs if p.verified]
    raised = [(it, p) for it, p in called if p.rank_pred != p.rank_before]
    hit = [x for x in raised if x[0].rank_true >= 3]
    u = [p.usage["verifier"] for _, p in pairs if p.usage and p.usage.get("verifier")]
    costs = [x.get("cost_usd") for x in u]
    return {"model": verifier.model, "effort": verifier.effort,
            "verified": len(called), "raised": len(raised),
            "raised_correct": len(hit), "raised_wrong": len(raised) - len(hit),
            "raised_from": {str(r): sum(1 for _, p in raised if p.rank_before == r) for r in (1, 2)},
            "raised_to": {str(r): sum(1 for _, p in raised if p.rank_pred == r) for r in (3, 4)},
            "errors": sum(1 for _, p in pairs if isinstance(p.raw, dict) and p.raw.get("verifier_error")),
            "cost_usd": None if any(c is None for c in costs) else sum(costs),
            "input_tokens": sum(x["input_tokens"] for x in u),
            "output_tokens": sum(x["output_tokens"] for x in u)}


def run(judge, judge_name: str, source: str, limit: int | None, split: str, seed: int,
        config_path: str | None, spread: bool = False, exclude_shown: bool = False, verifier=None) -> dict:
    sources = dataset.RANKED_SOURCES if source == "all" else (source,)
    items = dataset.load_all(config_path, sources)
    counts = dataset.summarize(items)
    if hasattr(judge, "set_example_pool"):
        judge.set_example_pool(items)

    per_source: dict[str, dict] = {}
    usage_by_source: dict[str, dict] = {}
    records: list[dict] = []
    all_pairs, all_preds = [], []
    ex_truth_total = ex_photo_total = 0
    shown = {e.sign_id for s in sources for e in judge.examples_for(s)} if exclude_shown else None
    for s, pool, ex_truth, ex_photo in _pools(items, sources, split, limit, seed, spread, shown):
        preds = judge.judge_many(pool)
        if verifier:
            preds = verifier.verify_many(pool, preds)
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
            if pr.item_judgments_pred is not None:  # 後処理(aggregate.py)で項目からランクを合成するため残す
                rec["item_judgments_pred"] = pr.item_judgments_pred
            if pr.usage:
                rec["usage"] = pr.usage
            if pr.verified:
                rec["verified"] = True
                rec["rank_before"] = pr.rank_before
                rec["verifier_notes"] = pr.verifier_notes
            if pr.rank_pred is None and isinstance(pr.raw, dict):
                rec["raw"] = pr.raw
            elif isinstance(pr.raw, dict) and pr.raw.get("reasons"):
                rec["reasons"] = pr.raw["reasons"]
            if isinstance(pr.raw, dict) and pr.raw.get("item_notes"):
                rec["item_notes"] = pr.raw["item_notes"]
                rec["needs_onsite"] = pr.raw.get("needs_onsite", 0)
            records.append(rec)
    out = {
        "created": datetime.now().isoformat(timespec="seconds"),
        "judge": judge_name, "model": getattr(judge, "model", None), "source": source,
        "split": split, "limit_per_source": limit, "seed": seed,
        "dataset_counts": counts,
        "overall": _add_excluded(metrics.compute(all_pairs), ex_truth_total, ex_photo_total),
        "usage": _usage_total(all_preds), "usage_by_source": usage_by_source,
        "config": judge_config(judge, sources),
        "needs_onsite_items": sum(rec.get("needs_onsite", 0) for rec in records),
        "per_source": per_source,
        "records": records,
    }
    if verifier:
        out["verify"] = verify_summary(all_pairs, verifier)
    if hasattr(judge, "source_run"):
        out["replay_of"] = judge.source_run
    return out


def judge_config(judge, sources) -> dict:
    """結果 JSON に残す判定役の条件。例示に使った看板の sign_id も残す(どの看板を見せたかの記録)。"""
    n = getattr(judge, "n_examples", 0)
    return {"examples": n, "example_mix": getattr(judge, "example_mix", None),
            "example_mix_by_source": getattr(judge, "example_mix_by_source", {}),
            "example_why": bool(getattr(judge, "example_why", False)),
            "example_scale": bool(getattr(judge, "example_scale", False)),
            "example_select": getattr(judge, "example_select", "rank"),
            "example_notes": getattr(judge, "example_notes", {}) if n else {}, "guard": bool(getattr(judge, "guard", False)),
            "rubric": bool(getattr(judge, "rubric", False)), "rules": list(getattr(judge, "rules", [])),
            "examples_used": {s: [e.sign_id for e in judge.examples_for(s)] for s in sources} if n else {},
            # 実際に使えた構成(要求した構成から、該当する例示用の看板が無いランクを省いたもの)
            "example_mix_used": {s: sorted(e.rank_true for e in judge.examples_for(s)) for s in sources} if n else {}}


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
              f"- 入力 {u['input_tokens']:,} トークン(うちキャッシュ書き込み {u['cache_write_tokens']:,}、"
              f"キャッシュ読み出し {u['cache_read_tokens']:,})、出力 {u['output_tokens']:,} トークン",
              "- 推定費用: " + ("不明(価格表にないモデル)" if c is None else f"${c:.2f}"), ""]
        if (r.get("config") or {}).get("guard"):
            L += [f"- 写真では判定できない(needs_onsite)と答えた項目: {r['needs_onsite_items']} 項目"
                  "(「判断できず」の項目に含まれる)", ""]
    if r.get("verify"):
        v = r["verify"]
        c = v["cost_usd"]
        L += ["## 反証役", "",
              f"- モデル: {v['model']}(effort {v['effort'] or 'API 既定'})。下書きが 1・2 の看板 {v['verified']} 基を通した"
              + (f"(判定役の下書きは {r['replay_of']} を再利用)" if r.get("replay_of") else ""),
              f"- 要改善以上に上げた看板: {v['raised']} 基(うち正解が 3 以上 {v['raised_correct']}、未満 {v['raised_wrong']})。"
              f"元のランク 1→{v['raised_from']['1']} 基、2→{v['raised_from']['2']} 基。上げた先 3: {v['raised_to']['3']}、4: {v['raised_to']['4']}",
              f"- 反証役の失敗(下書きのまま): {v['errors']} 基",
              "- 反証役の費用: " + ("不明" if c is None else f"${c:.2f}") + "(使用量の費用は判定役+反証役の合計)", ""]
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
    if r["judge"] == "claude":
        n_ex = (r.get("config") or {}).get("examples", 0)
        cond += f" / 例示{n_ex}" if n_ex else " / ゼロショット"
        if (r.get("config") or {}).get("example_why"):
            cond += "(理由行つき)"
        if (r.get("config") or {}).get("example_select") == "boundary":
            cond += "(境界例示)"
        if (r.get("config") or {}).get("example_scale"):
            cond += "(物差し行つき)"
        if (r.get("config") or {}).get("guard"):
            cond += " / 見逃し対策"
        if (r.get("config") or {}).get("rubric"):
            cond += " / ルーブリック"
    if r.get("verify"):
        v = r["verify"]
        cond += (f" / 反証役 {v['model']}: 上げ{v['raised']}基(正解3以上{v['raised_correct']}、未満{v['raised_wrong']})"
                 + (f"、判定は{r['replay_of']}を再利用" if r.get("replay_of") else ""))
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
    ap.add_argument("--spread-sites", action="store_true",
                    help="--limit のとき現場(group_key)をばらして抽出する(較正の学習データ用)")
    ap.add_argument("--exclude-shown", action="store_true",
                    help="判定役に例示として見せた看板を判定の対象から外す(--split example の較正用。答えを見た看板を学習に使わない)")
    ap.add_argument("--out", default=None, help="結果 JSON のパス(省略時 runs/YYYY-MM-DD_HHMM_<judge>.json)")
    ap.add_argument("--sources-config", default=None, help="sources.toml のパス")
    ap.add_argument("--append-log", action="store_true",
                    help="結果の1行を docs/EVAL.md の「日ごとの測定記録」の表に追記する(oracle は不可)")
    ap.add_argument("--log-note", default="", help="--append-log の「条件」欄に足す一言")
    ap.add_argument("--replay", default=None, metavar="JSON",
                    help="--judge replay 用。以前の結果 JSON(runs/…)の予測を判定役の下書きとして使う(API なし)")
    v = ap.add_argument_group("反証役(claude / replay の判定の後に通す)")
    v.add_argument("--verify", action="store_true",
                   help="判定の後に反証役(別の呼び出し)を通し、要改善以上の見落としがあれば rank を 3 以上に上げる。"
                        "下書きが 3 以上の看板は呼ばない")
    v.add_argument("--verifier-model", default=None, help="反証役のモデル ID(既定 claude-sonnet-5-5)")
    v.add_argument("--verifier-effort", choices=["low", "medium", "high", "xhigh", "max"], default=None,
                   help="反証役の output_config.effort(省略時は API の既定)")
    g = ap.add_argument_group("claude 判定役のみ")
    g.add_argument("--model", default=None, help="モデル ID(既定は ClaudeJudge の DEFAULT_MODEL)")
    g.add_argument("--max-photos", type=int, default=6, help="1 看板に送る写真の最大枚数(既定 6)")
    g.add_argument("--workers", type=int, default=4, help="並列数(1〜4、既定 4)")
    g.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None,
                   help="output_config.effort(省略時は API の既定)")
    g.add_argument("--examples", type=int, default=0, choices=range(0, 13), metavar="N",
                   help="同じ様式の例示用(split=example)から N 基(0〜12)を判定例として先頭に置く。0 でゼロショット")
    g.add_argument("--example-mix", default=None, metavar="RANKS",
                   help='例示のランク構成(例 "1,1,2,2,3,3,4")。指定時は --examples を無視。'
                        "該当ランクの看板が例示用に無ければその分は省き、使った構成を結果 JSON に記録")
    g.add_argument("--example-mix-by-source", default=None, metavar="SRC=RANKS;...",
                   help='様式ごとの例示構成(例 "paper_scan=1,1,2,3,4;xlsx_center=1,2,3")。--example-mix より優先。'
                        "指定の無い様式は --example-mix / --examples に従う")
    g.add_argument("--example-why", action="store_true",
                   help="各例示に「なぜそのランクか」の 1 行(項目別の正解から機械的に作る)を付ける")
    g.add_argument("--example-select", choices=["rank", "boundary"], default="rank",
                   help="例示の選び方。rank= ランク構成だけ(既定)。boundary= 例示側の予測(--example-pred)で"
                        "AI の予測と正解がずれた看板を優先(6 基固定。--examples / --example-mix は無視)")
    g.add_argument("--example-pred", default=None, metavar="JSON",
                   help="--example-select boundary 用。例示側(split=example)を判定した結果 JSON(runs/…)")
    g.add_argument("--example-scale", action="store_true",
                   help="各例示に「有資格者の判定= y(この様式では、写真に写る程度の<項目名>は y と判定する)」の 1 行を付ける")
    g.add_argument("--guard", action="store_true",
                   help="見逃し対策の指示を足し、項目ごとに evidence と needs_onsite(写真では判定できない= 判断できず)を出させる")
    g.add_argument("--rubric", action="store_true",
                   help="評価基準(a〜d と部位ごとの判定ポイント)の要点をシステムプロンプトに入れる")
    g.add_argument("--rules-from-db", default=None, metavar="LEDGER",
                   help="台帳(SQLite)の rules 表の最新20件を「有資格者の規則」として例示の後ろに入れる(既定オフ)")
    g.add_argument("--dry-run", action="store_true", help="API を呼ばず、送る看板数・写真枚数・推定トークンだけ出す")
    a = ap.parse_args(argv)

    opts = {}
    if a.judge == "claude":
        opts = {"max_photos": a.max_photos, "workers": a.workers, "effort": a.effort, "examples": a.examples, "guard": a.guard, "rubric": a.rubric,
                "example_why": a.example_why, "example_scale": a.example_scale}
        if a.example_select == "boundary":
            if not a.example_pred or not Path(a.example_pred).exists():
                ap.error("--example-select boundary には存在する --example-pred が要ります")
            recs = json.loads(Path(a.example_pred).read_text(encoding="utf-8"))["records"]
            opts["example_pred"] = {r["sign_id"]: r["rank_pred"] for r in recs
                                    if r.get("split") == "example" and r.get("rank_pred") is not None}
            opts["example_select"] = "boundary"
        elif a.example_pred:
            ap.error("--example-pred は --example-select boundary のときだけ使えます")
        if a.example_mix:
            from .claude_judge import parse_mix
            try:
                opts["example_mix"] = parse_mix(a.example_mix)
            except ValueError as e:
                ap.error(str(e))
        if a.example_mix_by_source:
            from .claude_judge import parse_mix_by_source
            try:
                opts["example_mix_by_source"] = parse_mix_by_source(a.example_mix_by_source)
            except ValueError as e:
                ap.error(str(e))
        if a.model:
            opts["model"] = a.model
        if a.rules_from_db:
            from ..ledger.db import Ledger
            from .claude_judge import RULES_MAX
            if not Path(a.rules_from_db).exists():
                ap.error(f"台帳がありません: {a.rules_from_db}")
            with Ledger(a.rules_from_db) as lg:
                opts["rules"] = lg.recent_rules(RULES_MAX)
    elif a.judge == "replay":
        if not a.replay or not Path(a.replay).exists():
            ap.error("--judge replay には存在する --replay が要ります")
        if a.dry_run:
            ap.error("--dry-run は --judge claude のときだけ使えます")
        opts = {"path": a.replay}
    elif a.examples or a.guard or a.rubric or a.example_mix or a.example_mix_by_source or a.example_why or a.rules_from_db or a.example_select != "rank" or a.example_pred or a.example_scale:
        ap.error("--examples / --example-mix / --example-why / --guard / --rubric / --rules-from-db は --judge claude のときだけ使えます")
    elif a.dry_run:
        ap.error("--dry-run は --judge claude のときだけ使えます")
    if a.replay and a.judge != "replay":
        ap.error("--replay は --judge replay のときだけ使えます")
    if (a.verify or a.verifier_model or a.verifier_effort) and not (a.verify and a.judge in ("claude", "replay")):
        ap.error("--verify は --judge claude / replay のときだけ使えます(--verifier-* は --verify と一緒に)")
    if a.append_log and a.judge == "oracle":
        ap.error("oracle は検算用なので --append-log できません")
    if a.append_log and a.dry_run:
        ap.error("--dry-run と --append-log は同時に使えません")
    judge = build_judge(a.judge, **opts)

    verifier = None
    if a.verify:
        from .verifier import DEFAULT_VERIFIER_MODEL, Verifier
        verifier = Verifier(a.verifier_model or DEFAULT_VERIFIER_MODEL, a.max_photos, a.workers, a.verifier_effort)

    if a.dry_run:
        print(dry_run_text(dry_run(judge, a.source, a.limit, a.split, a.seed, a.sources_config)))
        return 0

    try:
        r = run(judge, a.judge, a.source, a.limit, a.split, a.seed, a.sources_config,
                a.spread_sites, a.exclude_shown, verifier)
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
