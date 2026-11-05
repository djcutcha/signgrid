"""有資格者の手本の前後・学習曲線を、物差しの側の看板だけで数え直す(API は呼ばない)。

  python3 -m kanban_karte.eval.expert_curve --run "手本0(c2)=runs/a.json" --run "手本0(再実行)=runs/b.json" \\
      --run "手本5+規則=runs/c.json" [--png docs/img/expert_curve.png] [--x 0,0,5]

結果 JSON の records(rank_true・rank_pred)から、物差しの側(expert_notes.role == measure)だけを取り出して
metrics.rank_metrics で 完全一致・±1・見逃し率・誤警報率 を出し、Markdown の表を標準出力へ書く。
--png を付けると、--x(手本の件数)を横軸にした折れ線を描く(同じ x の点は全部描く= 回ごとの揺れが見える)。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .expert_notes import role
from .metrics import rank_metrics


def _int(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        return None


def measure(path: str | Path) -> dict:
    d = json.loads(Path(path).read_text(encoding="utf-8"))
    recs = [r for r in d["records"] if role(r["sign_id"]) == "measure" and _int(r.get("rank_true")) is not None]
    m = rank_metrics([_int(r["rank_true"]) for r in recs], [_int(r.get("rank_pred")) for r in recs])
    cost = sum((r.get("usage") or {}).get("cost_usd", 0) for r in recs)
    refs = sum(1 for r in recs if r.get("expert_refs"))
    return {**m, "cost_usd": round(cost, 4), "with_refs": refs, "model": d.get("model"),
            "expert_notes": len((d.get("config") or {}).get("expert_notes") or []),
            "rules": len((d.get("config") or {}).get("rules") or [])}


def pct(x) -> str:
    return "-" if x is None else f"{x * 100:.1f}%"


def table(rows: list[tuple[str, dict]]) -> str:
    L = ["| 条件 | 母数 | 完全一致 | ±1 | 見逃し率 | 誤警報率 | 手本を見せた看板 | 費用(物差しの分) |",
         "|---|---|---|---|---|---|---|---|"]
    for label, m in rows:
        L.append(f"| {label} | {m['n']}基 | {pct(m['exact_rate'])} | {pct(m['within1_rate'])} | "
                 f"{pct(m['miss_rate'])}({m['action_needed_missed']}/{m['action_needed_true']}) | "
                 f"{pct(m['false_alarm_rate'])}({m['false_alarm']}/{m['not_action_true']}) | {m['with_refs']} | ${m['cost_usd']:.2f} |")
    return "\n".join(L)


def plot(rows: list[tuple[str, dict]], xs: list[int], out: str | Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib import font_manager
    for name in ("Hiragino Sans", "Hiragino Kaku Gothic ProN", "Noto Sans CJK JP"):
        if any(f.name == name for f in font_manager.fontManager.ttflist):
            plt.rcParams["font.family"] = name
            break
    fig, ax = plt.subplots(figsize=(7.2, 4.2), dpi=150)
    series = (("見逃し率(低いほど良い)", "miss_rate", "#c0392b"), ("完全一致", "exact_rate", "#2c6fbb"),
              ("±1", "within1_rate", "#7f8c8d"), ("誤警報率", "false_alarm_rate", "#e67e22"))
    for name, key, color in series:
        pts = sorted((x, m[key] * 100) for x, (_, m) in zip(xs, rows) if m[key] is not None)
        if not pts:
            continue
        by = {}
        for x, y in pts:
            by.setdefault(x, []).append(y)
        ax.plot(list(by), [sum(v) / len(v) for v in by.values()], color=color, marker="o", label=name, linewidth=2)
        ax.scatter([x for x, _ in pts], [y for _, y in pts], color=color, s=14, alpha=.5)
    ax.set_xlabel("手本に使った有資格者の言葉づけ(件)")
    ax.set_ylabel("%")
    ax.set_ylim(0, 100)
    ax.set_xticks(sorted(set(xs)))
    ax.grid(alpha=.3)
    n = rows[0][1]["n"] if rows else 0
    ax.set_title(f"物差し {n} 基(手本に使わない側)での前後")
    ax.legend(loc="center right", fontsize=9, frameon=False)
    fig.tight_layout()
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.eval.expert_curve")
    ap.add_argument("--run", action="append", required=True, metavar="ラベル=JSON")
    ap.add_argument("--x", default=None, help="各 --run の手本の件数(カンマ区切り)。--png のときに要る")
    ap.add_argument("--png", default=None)
    ap.add_argument("--json", action="store_true", help="表の代わりに数字を JSON で")
    a = ap.parse_args(argv)
    rows = []
    for r in a.run:
        label, _, path = r.partition("=")
        rows.append((label, measure(path)))
    if a.json:
        print(json.dumps({l: {k: v for k, v in m.items() if k != "confusion"} for l, m in rows}, ensure_ascii=False, indent=1))
    else:
        print(table(rows))
    if a.png:
        xs = [int(x) for x in (a.x or "").split(",") if x.strip()]
        if len(xs) != len(rows):
            ap.error("--x は --run と同じ数")
        plot(rows, xs, a.png)
        print(f"\n図: {a.png}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
