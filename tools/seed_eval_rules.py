"""規則の効果測定用の台帳を作る(承認済みの規則の種)。評価用の看板からは作らない。

  python3 tools/seed_eval_rules.py --db data/private/loop_rules.sqlite
  python3 -m kanban_karte.eval.run_eval --judge claude --examples 6 --limit 34 --seed 1 --model claude-opus-5-5 \\
      --rules-from-db data/private/loop_rules.sqlite --append-log --log-note "承認済み規則 6 本"

種は「例示側(split=example)の看板の訂正を模した」一般的な点検の知見。台帳には候補として積み、承認の工程(review.approve_rule)を
通して approved にする。下書きに効くのは approved だけ。顧客名・店名・社員名は入れない。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kanban_karte.ledger import Ledger  # noqa: E402
from kanban_karte.pipeline import review  # noqa: E402

BY = "種(本線の作業文)"
# (項目キー or None, 所見, 条件, 区分 / 文, 補足)
SEEDS = [
    ("表示部-4", "退色だけで剥がれが無い", None, "経過観察", "下書きは要改善になりがち"),
    ("照明装置-1", "昼の写真で点灯の有無が見えない", None, "判断できず", "対象外ではなく判断できず"),
    ("基礎部・上部構造-2", "幅が見えるひび割れ", None, "要改善", None),
    ("取付部-1", "赤錆", "全周に回っている", "要改善", None),
    ("取付部-1", "赤錆", "部分だけ", "経過観察", None),
    (None, "パネルの欠け・穴", None, "要改善", None),   # 看板本体の損傷(18項目のどれに当たるかは未決。看板のランクにだけ使う)
]


def seed_text(key: str | None, finding: str, cond: str | None, result: str, note: str | None) -> str:
    label = f"{key} {review.ITEM_TEXT[key]}" if key else "看板本体の損傷"
    text = f"{label}: 「{finding}」{f'({cond})' if cond else ''}は{result}"
    return f"{text}({note})" if note else text


def seed(ledger: Ledger) -> list[int]:
    ids = []
    for key, finding, cond, result, note in SEEDS:
        st = review.structure_of(key or "看板本体", finding, result, None,
                                 {"range": None, "degree": None, "text": cond, "photos": []} if cond else None)
        rid = ledger.add_rule(seed_text(key, finding, cond, result, note), "評価用ではない種", structured=st)
        review.approve_rule(ledger, rid, BY)
        ids.append(rid)
    return ids


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="作る台帳(既にあれば規則を足さずに終わる)")
    a = ap.parse_args(argv)
    with Ledger(a.db) as lg:
        if lg.list_rules():
            print("規則が既にあるので何もしません", file=sys.stderr)
            return 1
        ids = seed(lg)
        for r in lg.list_rules():
            print(f"#{r['id']} [{r['status']}] {r['text']}")
    print(f"承認済み {len(ids)} 本")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
