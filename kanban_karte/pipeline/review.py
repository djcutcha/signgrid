"""有資格者の確認と確定。AI の下書き(status=draft)を直し、確定する。確定は有資格者だけがする。

- apply_correction: 項目の区分(または看板全体のランク)を直す。差分を corrections に記録し、項目の訂正なら
  rules に「項目 × 写真の所見 → 区分」の規則候補を1行積む(次回の下書きで先に効く)。
- finalize: status=confirmed、confirmed_at。確定した看板は draft_bundle が上書きしない。訂正も受けない。
- rank_changed_count: 確定した看板のうち、確定後のランクが下書きと違う数。
規則の文は、項目名と所見(訂正の note、無ければ下書きの evidence から写真番号を除いた文)と訂正後の区分から機械的に作る。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime

from ..ledger.db import Ledger
from ..ledger.items import _ITEMS, ITEM_KEYS, ITEM_LABELS, RESULT_TO_RANK, RESULTS

ITEM_TEXT = {f"{g}-{n}": t for g, n, t in _ITEMS}
NUM_TO_RESULT = {1: "良好", 2: "経過観察", 3: "要改善"}
RANK_ITEM = "rank"


@dataclass
class Correction:
    id: int
    sign_id: str
    item: str
    old_value: str | None
    new_value: str
    rank_before: int | None
    rank_after: int | None
    rule_id: int | None


def resolve_item(text: str) -> str:
    """項目の指定 → 台帳のキー。キーそのもの、「点検箇所 番号 原文」(ITEM_LABELS)、「④」付きの様式の項目名、
    項目の原文の一部(1件に決まるときだけ)を受ける。"""
    t = text.strip().lstrip("④")
    if t == RANK_ITEM:
        return RANK_ITEM
    if t in ITEM_KEYS:
        return t
    for k in ITEM_KEYS:
        if t in (ITEM_LABELS[k], f"{k} {ITEM_TEXT[k]}"):
            return k
    hits = [k for k in ITEM_KEYS if t and t in ITEM_TEXT[k]]
    if len(hits) == 1:
        return hits[0]
    raise ValueError(f"項目を決められません: {text!r}" + (f"(候補 {', '.join(hits)})" if hits else ""))


def parse_value(v) -> str:
    """区分の指定 → 良好/経過観察/要改善/対象外。1〜3(数字・文字)も受ける。"""
    s = str(v).strip()
    if s.isdigit() and int(s) in NUM_TO_RESULT:
        return NUM_TO_RESULT[int(s)]
    if s in RESULTS:
        return s
    raise ValueError(f"区分は 1〜3 か {RESULTS} のどれか: {v!r}")


def _rank_of(items: dict[str, str]) -> int | None:
    ranks = [RESULT_TO_RANK[v] for v in items.values() if RESULT_TO_RANK[v] is not None]
    return max(ranks) if ranks else None


def finding_text(note: str | None, evidence: dict | None) -> str | None:
    """規則の「所見」: 有資格者の note があればそれ、無ければ下書きの evidence の文から「写真N(の)」を除いたもの。"""
    if note and note.strip():
        return note.strip()
    t = ((evidence or {}).get("text") or "").strip()
    t = re.sub(r"写真\s*\d+\s*(の|で|に|では|には)?", "", t).strip().rstrip("。")
    return t or None


def rule_text(key: str, finding: str | None, new: str, old: str | None, condition: str | None = None) -> tuple[str, str]:
    """(規則の文, 矛盾の判定に使う頭の部分)。顧客名・店名は入らない(項目名と所見と条件と区分だけ)。
    condition = 聞き返しで得た条件(範囲・程度)。写真番号は看板ごとの番号なので文に入れない。"""
    label = f"{key} {ITEM_TEXT[key]}"
    if finding:
        head = f"{label}: 「{finding}」{f'({condition})' if condition else ''}は"
        return f"{head}{new}(下書きは{old or '判断できず'})", head
    head = f"{label}: 有資格者は"
    return f"{head}{new}と判定(下書きは{old or '判断できず'})", ""


_RULE_RE = re.compile(r"^(.*は)(?:良好|経過観察|要改善|対象外)\(下書きは")


def rule_head(text: str) -> str:
    """規則の文の頭(項目・所見・条件まで)。所見の無い規則は ''(矛盾の対象にしない)。"""
    m = _RULE_RE.match(text)
    return m[1] if m and "「" in m[1] else ""


def structure_of(key: str, finding: str | None, new: str, old: str | None, condition: dict | None = None,
                 question: str | None = None, answer: str | None = None) -> dict:
    """規則の構造 {item, finding, condition, result, draft, question, answer}。condition = {range, degree, text, photos}。"""
    return {"item": key, "finding": finding, "condition": condition, "result": new, "draft": old,
            "question": question, "answer": answer}


def _add_rule(ledger: Ledger, text: str, head: str, source: str, structured: dict | None = None) -> int:
    rules = ledger.list_rules()
    for r in rules:
        if r["text"] == text:
            return r["id"]  # 同じ規則は積み直さない
    conflicts = [r["id"] for r in rules if head and r["status"] != "rejected" and r["text"].startswith(head)]
    return ledger.add_rule(text, source, conflicts, structured=structured)


def reconflict(ledger: Ledger, rule_id: int, text: str) -> list[int]:
    """文を直した規則の矛盾の相手を引き直す。自分の conflicts_with を返し、自分を指す他の規則の古い印を外す。"""
    head, mine = rule_head(text), []
    for r in ledger.list_rules():
        if r["id"] == rule_id:
            continue
        same = bool(head) and r["status"] != "rejected" and rule_head(r["text"]) == head and r["text"] != text
        if same and r["id"] < rule_id:
            mine.append(r["id"])
        theirs = json.loads(r["conflicts_with"] or "[]")
        if rule_id in theirs and not same:
            ledger.conn.execute("UPDATE rules SET conflicts_with=? WHERE id=?",
                                (json.dumps([i for i in theirs if i != rule_id]), r["id"]))
        elif same and r["id"] > rule_id and rule_id not in theirs:
            ledger.conn.execute("UPDATE rules SET conflicts_with=? WHERE id=?", (json.dumps([*theirs, rule_id]), r["id"]))
    ledger.conn.commit()
    return mine


# ---- 規則の承認(候補 → 承認/却下)。下書きに効くのは承認だけ ----
def conflict_partners(ledger: Ledger, rule_id: int) -> list[int]:
    """この規則と矛盾する(却下されていない)規則の id。conflicts_with は新しい側にだけ積むので、両方向を見る。"""
    rules = {r["id"]: r for r in ledger.list_rules()}
    out = set(json.loads(rules[rule_id]["conflicts_with"] or "[]")) if rule_id in rules else set()
    out |= {i for i, r in rules.items() if rule_id in json.loads(r["conflicts_with"] or "[]")}
    return sorted(i for i in out if i in rules and i != rule_id and rules[i]["status"] != "rejected")


def _need_by(by: str | None) -> str:
    if not by or not by.strip():
        raise ValueError("承認・却下する人(by)が要ります")
    return by.strip()


def _rule(ledger: Ledger, rule_id: int):
    r = ledger.get_rule(rule_id)
    if r is None:
        raise LookupError(f"規則 {rule_id} が台帳にありません")
    return r


def approve_rule(ledger: Ledger, rule_id: int, by: str, resolution: str | None = None) -> None:
    """規則を承認する(以後の下書きに効く)。承認済みの規則と矛盾する間は承認できない(resolve_conflict で片方を選ぶ)。"""
    by = _need_by(by)
    _rule(ledger, rule_id)
    clash = [i for i in conflict_partners(ledger, rule_id) if ledger.get_rule(i)["status"] == "approved"]
    if clash:
        raise ValueError(f"規則 {rule_id} は承認済みの規則 {', '.join(f'#{i}' for i in clash)} と矛盾します。"
                         "どちらを残すかを選んでください")
    ledger.set_rule_status(rule_id, "approved", by, resolution)


def reject_rule(ledger: Ledger, rule_id: int, by: str, resolution: str | None = None) -> None:
    by = _need_by(by)
    _rule(ledger, rule_id)
    ledger.set_rule_status(rule_id, "rejected", by, resolution)


def resolve_conflict(ledger: Ledger, keep_id: int, drop_id: int, by: str, reason: str | None = None) -> None:
    """矛盾する2規則のどちらを残すか。残す側を承認、他方を却下し、理由(resolution)を両方に書く。1回の書き込み。"""
    by = _need_by(by)
    if keep_id == drop_id:
        raise ValueError("残す規則と却下する規則が同じです")
    _rule(ledger, keep_id), _rule(ledger, drop_id)
    if drop_id not in conflict_partners(ledger, keep_id) and keep_id not in conflict_partners(ledger, drop_id):
        raise ValueError(f"規則 {keep_id} と {drop_id} は矛盾の組ではありません")
    note = (reason or "").strip() or None
    ledger.set_rule_status(keep_id, "approved", by, f"残した: {note}" if note else "残した", commit=False)
    ledger.set_rule_status(drop_id, "rejected", by, f"却下: {note}" if note else f"却下(#{keep_id} を残した)", commit=False)
    ledger.conn.commit()


def apply_correction(ledger: Ledger, sign_id: str, item: str, new_value, by: str,
                     note: str | None = None) -> Correction | None:
    """有資格者の訂正を受ける。item は項目(resolve_item の形)か 'rank'(看板全体、new_value は 1〜4)。
    下書きと同じ値なら何も記録せず None。確定済みの看板は ValueError。"""
    if not by or not by.strip():
        raise ValueError("訂正した人(by)が要ります")
    s = ledger.get_sign(sign_id)
    if s is None:
        raise LookupError(f"看板 {sign_id} が台帳にありません")
    if s.status != "draft":
        raise ValueError(f"看板 {sign_id} は status={s.status}。訂正できるのは下書き(draft)だけ")
    key = resolve_item(item)
    before = s.rank
    if key == RANK_ITEM:
        if str(new_value).strip() not in ("1", "2", "3", "4"):
            raise ValueError(f"ランクは 1〜4: {new_value!r}")
        new, old = str(int(str(new_value))), None if s.rank is None else str(s.rank)
        if new == old:
            return None
        s.rank = int(new)
        rule_id = None
    else:
        new, old = parse_value(new_value), s.items.get(key)
        if new == old:
            return None
        s.items[key] = new
        if s.rank != 4:  # 4(即時対応)は項目の区分では表せない。変えるのはランクの訂正で
            s.rank = _rank_of(s.items) or s.rank
        finding = finding_text(note, s.evidence.get(key))
        text, head = rule_text(key, finding, new, old)
        rule_id = _add_rule(ledger, text, head, f"{sign_id} / {by.strip()}", structure_of(key, finding, new, old))
    ledger.upsert_sign(s)
    cid = ledger.add_correction(sign_id, key, old, new, by.strip(), rank_before=before, rank_after=s.rank,
                                note=note, rule_id=rule_id)
    return Correction(cid, sign_id, key, old, new, before, s.rank, rule_id)


def finalize(ledger: Ledger, sign_id: str, by: str) -> None:
    """有資格者が確定する。下書き(draft)で、ランクが付いているものだけ。"""
    if not by or not by.strip():
        raise ValueError("確定する人(by)が要ります")
    s = ledger.get_sign(sign_id)
    if s is None:
        raise LookupError(f"看板 {sign_id} が台帳にありません")
    if s.status != "draft":
        raise ValueError(f"看板 {sign_id} は status={s.status}。確定できるのは下書き(draft)だけ")
    if s.rank is None:
        raise ValueError(f"看板 {sign_id} にランクがありません")
    s.status, s.confirmed_by = "confirmed", by.strip()
    s.confirmed_at = datetime.now().isoformat(timespec="seconds")
    ledger.upsert_sign(s)


def rank_changed_count(ledger: Ledger, bundle_id: int | None = None) -> tuple[int, int]:
    """(確定後のランクが下書きと違う看板数, 確定した看板数)。下書きのランクを持たない看板は数えない。"""
    done = [s for s in ledger.list_signs(bundle_id=bundle_id)
            if s.status == "confirmed" and s.draft_rank is not None]
    return sum(1 for s in done if s.rank != s.draft_rank), len(done)
