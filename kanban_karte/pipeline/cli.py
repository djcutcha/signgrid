"""本線のコマンド: 束 → 下書き → 有資格者の訂正 → 確定。

  python3 -m kanban_karte.pipeline.cli draft    --bundle <束ID> [--no-rules] [--examples 6]   看板ごとに下書き(API を使う)
  python3 -m kanban_karte.pipeline.cli review   --sign <看板ID> --item <項目名> --value <1-3> --by <有資格者>
                                                [--note 所見]   項目 'rank' なら --value は 1〜4(看板全体)
  python3 -m kanban_karte.pipeline.cli finalize --sign <看板ID> --by <有資格者>
  python3 -m kanban_karte.pipeline.cli changed  [--bundle <束ID>]   確定後にランクが変わった看板数
台帳は --db(既定 data/private/ledger.sqlite、環境変数 KANBAN_KARTE_DB)。
draft は台帳の rules のうち承認済み(approved)の最新20件を例示の後ろに入れる(--no-rules で外す)。AI は下書きまで。確定は有資格者。
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ..ledger.db import Ledger
from ..ledger.items import ITEM_KEYS
from . import draft as draft_mod
from . import review as review_mod

REPO_ROOT = Path(__file__).resolve().parents[2]


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="kanban_karte.pipeline.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="台帳 SQLite(既定 data/private/ledger.sqlite、環境変数 KANBAN_KARTE_DB)")
    ap.add_argument("--data-dir", default=None, help="台帳の画像パス(相対)の基準フォルダ")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("draft", help="束の看板ごとに下書きを書く")
    p.add_argument("--bundle", type=int, required=True)
    p.add_argument("--examples", type=int, default=6, choices=range(0, 10), metavar="N", help="例示の基数(既定 6)")
    p.add_argument("--no-rules", action="store_true", help="台帳の規則を入れない")
    p.add_argument("--sources-config", default=None, help="sources.toml のパス(例示の正解データの参照先)")
    p = sub.add_parser("review", help="有資格者の訂正を1件記録する")
    p.add_argument("--sign", required=True)
    p.add_argument("--item", required=True, help="項目名(点検箇所-番号、様式の項目名、原文の一部)か rank")
    p.add_argument("--value", required=True, help="1〜3(良好/経過観察/要改善)か 区分名。rank は 1〜4")
    p.add_argument("--by", required=True, help="訂正した有資格者")
    p.add_argument("--note", default=None, help="訂正の理由・所見(規則の文に使う)")
    p = sub.add_parser("finalize", help="看板を確定する")
    p.add_argument("--sign", required=True)
    p.add_argument("--by", required=True, help="確定する有資格者")
    p = sub.add_parser("changed", help="確定後にランクが下書きから変わった看板数")
    p.add_argument("--bundle", type=int, default=None)
    return ap


def main(argv=None, out=None, judge_factory=None) -> int:
    """judge_factory(rules, examples, sources_config) -> Judge。テストで偽の判定役に差し替える。"""
    out = out or sys.stdout
    a = build_parser().parse_args(argv)
    db = a.db or os.environ.get("KANBAN_KARTE_DB") or str(REPO_ROOT / "data" / "private" / "ledger.sqlite")
    data_dir = a.data_dir or os.environ.get("KANBAN_KARTE_DATA") or str(REPO_ROOT / "data" / "private" / "intake")
    if not Path(db).exists():
        print(f"台帳が見つかりません: {db}", file=sys.stderr)
        return 1
    with Ledger(db, data_dir=data_dir) as ledger:
        try:
            if a.cmd == "draft":
                return _draft(a, ledger, out, judge_factory)
            if a.cmd == "review":
                c = review_mod.apply_correction(ledger, a.sign, a.item, a.value, a.by, a.note)
                if c is None:
                    print("下書きと同じ値なので、記録していません", file=out)
                    return 0
                print(f"訂正 #{c.id}: {c.sign_id} {c.item}  {c.old_value or '(判断できず)'} → {c.new_value}"
                      f"  ランク {c.rank_before} → {c.rank_after}", file=out)
                if c.rule_id:
                    print(f"  規則候補 #{c.rule_id} を積みました: {ledger.conn.execute('SELECT text FROM rules WHERE id=?', (c.rule_id,)).fetchone()[0]}", file=out)
                return 0
            if a.cmd == "finalize":
                review_mod.finalize(ledger, a.sign, a.by)
                s = ledger.get_sign(a.sign)
                print(f"確定: {s.id} ランク {s.rank}(下書き {s.draft_rank}) {s.confirmed_at} {s.confirmed_by}", file=out)
                return 0
            changed, total = review_mod.rank_changed_count(ledger, a.bundle)
            print(f"確定 {total} 基のうち、ランクが下書きから変わった: {changed} 基", file=out)
            return 0
        except (LookupError, ValueError, RuntimeError) as e:
            print(f"エラー: {e}", file=sys.stderr)
            return 1


def _draft(a, ledger: Ledger, out, judge_factory) -> int:
    rules = [] if a.no_rules else ledger.recent_rules()
    factory = judge_factory or (lambda rules, examples, cfg: draft_mod.default_judge(rules, cfg, examples))
    judge = factory(rules, a.examples, a.sources_config)
    results = draft_mod.draft_bundle(ledger, a.bundle, judge)
    total = 0.0
    for r in results:
        total += r.cost_usd or 0.0
        if r.status == "draft":
            print(f"{r.sign_id}: 下書き ランク {r.rank}、判断できず {r.undecided}/{len(ITEM_KEYS)} 項目", file=out)
        else:
            print(f"{r.sign_id}: {r.status}" + (f"({r.error})" if r.error else ""), file=out)
    print(f"規則 {len(rules)} 件を使用 / 費用 ${total:.3f}", file=out)
    return 1 if any(r.status == "error" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())
