"""有資格者の言葉づけ(/expert/ の expert_notes)を判定役の「手本」にする。

- 手本と物差しの分け方(機械的な規則): sign_key の数字の合計が偶数なら手本、奇数なら物差し。
  物差しの看板には手本を一切見せない(言葉づけがあっても手本に使わない)。
- 手本の選び方: 判定する看板と同じ種類(kind)で、所見(signs)が重なる件を最大 3 件。
  種類は台帳の看板名の語から、所見は判定役の過去の下書きの理由から拾う(どちらも正解は使わない)。
  手本の文は reasons をそのまま使い、案件番号のような長い数字は伏せる。店名・人名・案件番号・sign_key は送らない。
- 規則の候補: unknowns(写真では分からない物)を「判断できず(要実測)」にする規則を 1 本。
  台帳には status=candidate で入れる(承認は本線/有資格者)。

  python3 -m kanban_karte.eval.expert_notes split  [--db PATH]          手本と物差しの件数
  python3 -m kanban_karte.eval.expert_notes add-rule --ledger PATH     規則の候補を台帳に 1 本足す(同じ文は足さない)
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

PER_ITEM = 3
TRACE = "有資格者の見方を参照"
UNKNOWNS_RULE = ("写真では分からない物(ボルト・ナットの緩み、漏電などの電気関係、ピースやボルトでの固定の状態)は、"
                 "写真の見た目から良否を決めない。該当する項目は「判断できず(要実測)」とし、現地で確かめる点として reasons に書く。")
HEAD = ("【有資格者の見方 {n} 件】同じ種類・似た所見の看板について、屋外広告士が写真を見て書いた見方です。"
        "看板の判定そのものではありません。見るべき所と、写真で分からない所の考え方として参考にしてください。")


def key_of(sign_id: str) -> str:
    return str(sign_id).replace(":", "-")


def role(sign_key: str) -> str:
    """'example'(手本)か 'measure'(物差し)。数字の合計の偶奇で決める。"""
    return "example" if sum(int(c) for c in key_of(sign_key) if c.isdigit()) % 2 == 0 else "measure"


def guess_kind(name: str) -> str:
    """web/expert.py の guess_kind と同じ語(eval から web を import しないため写す)。"""
    for word, kind in (("屋上", "屋上"), ("自立", "自立"), ("袖", "袖"), ("壁面", "壁面"), ("壁", "壁面"), ("塔", "自立")):
        if word in (name or ""):
            return kind
    return ""


def load(db: str | Path) -> list[dict]:
    """評価セットの言葉づけ(飛ばしでない物)を入力順に。"""
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        rows = c.execute("SELECT * FROM expert_notes WHERE source='eval' AND skipped=0 ORDER BY id").fetchall()
    finally:
        c.close()
    out = []
    for r in rows:
        d = dict(r)
        d["signs"] = json.loads(d.get("signs") or "[]")
        if (d.get("reasons") or "").strip() or (d.get("unknowns") or "").strip():
            out.append(d)
    return out


def examples(notes: list[dict], cap: int | None = None) -> list[dict]:
    """手本の側だけ、入力順に最大 cap 件(学習曲線の 0 / 10 / 25 / 50)。"""
    ex = [n for n in notes if role(n["sign_key"]) == "example"]
    return ex if cap is None else ex[: max(0, cap)]


def _scrub(t: str) -> str:
    return re.sub(r"\d{5,}", "…", (t or "").strip())


def pick(pool: list[dict], kind: str, signs: list[str], k: int = PER_ITEM, exclude: str = "") -> list[dict]:
    """同じ種類(+2)と重なる所見(1 つ +1)の点が 1 以上の件を、点の高い順(同点は入力順)に最大 k 件。"""
    scored = []
    for i, n in enumerate(pool):
        if exclude and n["sign_key"] == exclude:
            continue
        s = (2 if kind and n.get("kind") == kind else 0) + len(set(signs) & set(n.get("signs") or []))
        if s > 0:
            scored.append((-s, i, n))
    return [n for _, _, n in sorted(scored, key=lambda x: x[:2])[:k]]


def block_text(sel: list[dict]) -> str:
    lines = [HEAD.format(n=len(sel))]
    for j, n in enumerate(sel, 1):
        lines.append(f"--- 見方{j}(種類: {n.get('kind') or '不明'} / 所見: {'・'.join(n.get('signs') or []) or 'なし'}) ---")
        if (n.get("reasons") or "").strip():
            lines.append("見方: " + _scrub(n["reasons"]))
        if (n.get("unknowns") or "").strip():
            lines.append("写真では分からない点: " + _scrub(n["unknowns"]))
    lines.append(f"この見方を判定に使ったときは、reasons の最後の 1 文を「{TRACE}」にしてください。")
    return "\n".join(lines)


def target_signs(drafts_json: str | Path | None) -> dict[str, list[str]]:
    """判定役の過去の下書き(結果 JSON)から、看板ごとの所見の候補。web/expert.py の parse_draft を使う。"""
    if not drafts_json:
        return {}
    from ..web.expert import load_drafts
    return {k: v["suggest"] for k, v in load_drafts(drafts_json).items()}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.eval.expert_notes")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("split")
    s.add_argument("--db", required=True)
    r = sub.add_parser("add-rule")
    r.add_argument("--ledger", required=True)
    a = ap.parse_args(argv)
    if a.cmd == "split":
        notes = load(a.db)
        print(json.dumps({"notes": len(notes), "example": [n["sign_key"] for n in notes if role(n["sign_key"]) == "example"],
                          "measure": [n["sign_key"] for n in notes if role(n["sign_key"]) == "measure"]}, ensure_ascii=False))
    else:
        from ..ledger.db import Ledger
        from ..pipeline.review import _add_rule
        with Ledger(a.ledger) as lg:
            rid = _add_rule(lg, UNKNOWNS_RULE, "", "expert_notes:unknowns")
            print(json.dumps({"rule_id": rid, "status": lg.get_rule(rid)["status"]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
