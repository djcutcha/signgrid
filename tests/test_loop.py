"""規則が育つ一周: 承認の工程・聞き返し・構造化・矛盾の解決(API を呼ばない)。"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from kanban_karte.ledger import Ledger, Sign
from kanban_karte.pipeline import clarify, review
from kanban_karte.web import api

KEY = "取付部-1"


def ledger_with_signs(n=3):
    lg = Ledger()
    for i in range(1, n + 1):
        lg.upsert_sign(Sign(id=f"s{i}", status="draft", rank=2, items={KEY: "経過観察"},
                            evidence={KEY: {"photos": [1], "text": "写真1で支柱の根元に赤錆", "needs_onsite": False}}))
    return lg


class AlterCompatTest(unittest.TestCase):
    def test_old_rules_table_gets_columns_and_rows_become_candidates(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "old.sqlite"
            c = sqlite3.connect(db)
            c.execute("CREATE TABLE rules (id INTEGER PRIMARY KEY AUTOINCREMENT, text TEXT NOT NULL, source TEXT, "
                      "created TEXT NOT NULL DEFAULT (datetime('now')), conflicts_with TEXT NOT NULL DEFAULT '[]')")
            c.execute("INSERT INTO rules(text) VALUES ('古い規則')")
            c.commit()
            c.close()
            with Ledger(db) as lg:
                cols = {r[1] for r in lg.conn.execute("PRAGMA table_info(rules)")}
                self.assertTrue({"status", "approved_by", "approved_at", "resolution", "structured"} <= cols)
                self.assertEqual(lg.list_rules()[0]["status"], "candidate")
                self.assertEqual(lg.recent_rules(), [])  # 古い規則は承認されるまで下書きに効かない
            Ledger(db).close()  # 2回開いても同じ


class ApprovalTest(unittest.TestCase):
    def test_only_approved_reaches_drafts(self):
        lg = ledger_with_signs()
        a = review.apply_correction(lg, "s1", KEY, "要改善", "A", "赤錆").rule_id
        b = review.apply_correction(lg, "s2", KEY, "良好", "A", "錆なし").rule_id
        self.assertEqual(lg.recent_rules(), [])
        review.approve_rule(lg, a, "有資格者A")
        self.assertEqual(lg.recent_rules(), [lg.get_rule(a)["text"]])
        review.reject_rule(lg, b, "有資格者A")
        self.assertEqual(len(lg.recent_rules()), 1)
        r = lg.get_rule(a)
        self.assertEqual((r["status"], r["approved_by"]), ("approved", "有資格者A"))
        self.assertTrue(r["approved_at"])

    def test_by_is_required(self):
        lg = ledger_with_signs()
        rid = review.apply_correction(lg, "s1", KEY, "要改善", "A", "赤錆").rule_id
        with self.assertRaises(ValueError):
            review.approve_rule(lg, rid, " ")
        with self.assertRaises(ValueError):
            review.reject_rule(lg, rid, "")

    def test_web_draft_uses_approved_only(self):
        lg = ledger_with_signs()
        a = review.apply_correction(lg, "s1", KEY, "要改善", "A", "赤錆").rule_id
        review.apply_correction(lg, "s2", KEY, "良好", "A", "錆なし")
        review.approve_rule(lg, a, "A")
        got = []
        lg.add_bundle("x.pdf", "sha", 1)
        api.draft_bundle(lg, 1, lambda rules: got.append(list(rules)) or _NoJudge())
        self.assertEqual(got, [[lg.get_rule(a)["text"]]])


class _NoJudge:
    def judge_many(self, items):
        return []


class ConflictTest(unittest.TestCase):
    def setUp(self):
        self.lg = ledger_with_signs()
        self.a = review.apply_correction(self.lg, "s1", KEY, "要改善", "A", "赤錆").rule_id
        self.b = review.apply_correction(self.lg, "s2", KEY, "良好", "B", "赤錆").rule_id

    def test_pair_is_detected_both_ways(self):
        self.assertEqual(review.conflict_partners(self.lg, self.a), [self.b])
        self.assertEqual(review.conflict_partners(self.lg, self.b), [self.a])
        rows = {r["id"]: r for r in api.rules(self.lg)}
        self.assertEqual(rows[self.a]["conflicts"], [self.b])

    def test_resolve_keeps_one_rejects_other_with_reason(self):
        review.resolve_conflict(self.lg, self.a, self.b, "有資格者A", "全周なら要改善")
        ra, rb = self.lg.get_rule(self.a), self.lg.get_rule(self.b)
        self.assertEqual((ra["status"], rb["status"]), ("approved", "rejected"))
        self.assertIn("全周なら要改善", ra["resolution"])
        self.assertIn("全周なら要改善", rb["resolution"])
        self.assertEqual(review.conflict_partners(self.lg, self.a), [])  # 却下された側は矛盾に数えない
        self.assertEqual(self.lg.recent_rules(), [ra["text"]])

    def test_cannot_approve_against_approved_conflict(self):
        review.approve_rule(self.lg, self.a, "A")
        with self.assertRaises(ValueError):
            review.approve_rule(self.lg, self.b, "A")
        with self.assertRaises(api.ApiError) as cm:
            api.rule_decide(self.lg, self.b, "approve", {"by": "A"})
        self.assertEqual(cm.exception.status, 409)

    def test_resolve_rejects_non_pair_and_same(self):
        with self.assertRaises(ValueError):
            review.resolve_conflict(self.lg, self.a, self.a, "A")
        self.lg.upsert_sign(Sign(id="s4", status="draft", rank=2, items={"表示部-4": "要改善"}))
        other = review.apply_correction(self.lg, "s4", "表示部-4", "良好", "A", "退色").rule_id
        with self.assertRaises(ValueError):
            review.resolve_conflict(self.lg, self.a, other, "A")

    def test_api_resolve(self):
        out = api.rule_resolve(self.lg, {"keep": self.b, "drop": self.a, "by": "A", "reason": "理由"})
        self.assertEqual([r["status"] for r in out], ["approved", "rejected"])
        with self.assertRaises(api.ApiError):
            api.rule_resolve(self.lg, {"keep": "x", "drop": 1, "by": "A"})


class ClarifyTest(unittest.TestCase):
    def test_needs_question(self):
        self.assertTrue(clarify.needs_question(None))
        self.assertTrue(clarify.needs_question("赤錆"))
        self.assertFalse(clarify.needs_question("支柱の根元だけに赤錆"))
        self.assertFalse(clarify.needs_question("基礎の天端に幅2mmのひび割れが走っている"))

    def test_fixed_question_has_no_api_and_asks_one_thing(self):
        ctx = clarify.Context(KEY, f"{KEY} x", "赤錆", "要改善", "経過観察")
        q = clarify.FixedClarifier().ask(ctx)
        self.assertIn("「赤錆」", q)
        self.assertIn("写真何番", q)
        self.assertEqual(q, clarify.FixedClarifier().ask(ctx))  # 固定文

    def test_structured_shape_on_correction_and_after_answer(self):
        lg = ledger_with_signs()
        c = review.apply_correction(lg, "s1", KEY, "要改善", "A", "赤錆")
        st = json.loads(lg.get_rule(c.rule_id)["structured"])
        self.assertEqual((st["item"], st["finding"], st["result"], st["draft"], st["condition"]), (KEY, "赤錆", "要改善", "経過観察", None))
        out = clarify.apply_answer(lg, c.rule_id, "q", "全周に回っている。写真3で見える", "A", clarify.FixedClarifier())
        self.assertEqual(out["condition"]["text"], "全周に回っている")
        self.assertEqual(out["condition"]["photos"], [3])
        self.assertEqual((out["question"], out["answer"]), ("q", "全周に回っている。写真3で見える"))
        r = lg.get_rule(c.rule_id)
        self.assertIn("「赤錆」(全周に回っている)は要改善", r["text"])
        self.assertNotIn("写真", r["text"])  # 写真番号は看板ごと。規則の文には入れない
        self.assertEqual(json.loads(r["structured"])["condition"]["photos"], [3])

    def test_unknown_answer_keeps_rule_as_is(self):
        lg = ledger_with_signs()
        c = review.apply_correction(lg, "s1", KEY, "要改善", "A", "赤錆")
        before = lg.get_rule(c.rule_id)["text"]
        for ans in ("", "分からない", "不明。"):
            clarify.apply_answer(lg, c.rule_id, "q", ans, "A", clarify.FixedClarifier())
        self.assertEqual(lg.get_rule(c.rule_id)["text"], before)
        self.assertEqual(lg.get_rule(c.rule_id)["status"], "candidate")

    def test_answer_only_for_candidates(self):
        lg = ledger_with_signs()
        c = review.apply_correction(lg, "s1", KEY, "要改善", "A", "赤錆")
        review.approve_rule(lg, c.rule_id, "A")
        with self.assertRaises(ValueError):
            clarify.apply_answer(lg, c.rule_id, "q", "全周", "A", clarify.FixedClarifier())

    def test_answer_makes_same_condition_conflict(self):
        lg = ledger_with_signs()
        a = review.apply_correction(lg, "s1", KEY, "要改善", "A", "赤錆")
        b = review.apply_correction(lg, "s2", KEY, "良好", "B", "赤錆")
        self.assertEqual(review.conflict_partners(lg, a.rule_id), [b.rule_id])  # 所見だけ同じ
        clarify.apply_answer(lg, a.rule_id, "q", "全周", "A", clarify.FixedClarifier())
        self.assertEqual(review.conflict_partners(lg, a.rule_id), [])  # 条件が付いて別の規則になった
        clarify.apply_answer(lg, b.rule_id, "q", "全周", "B", clarify.FixedClarifier())
        self.assertEqual(review.conflict_partners(lg, a.rule_id), [b.rule_id])  # 同じ条件で区分が違う → 矛盾

    def test_web_correct_returns_one_question_only_when_short(self):
        lg = ledger_with_signs()
        fx = clarify.FixedClarifier()
        d = api.correct(lg, "s1", {"item": KEY, "value": "要改善", "by": "A", "note": "赤錆"}, clarifier=fx)
        self.assertEqual(d["clarify"]["rule_id"], d["rule_id"])
        self.assertTrue(d["clarify"]["question"])
        d = api.correct(lg, "s2", {"item": KEY, "value": "良好", "by": "A", "note": "支柱の根元だけに赤錆が出ている程度"}, clarifier=fx)
        self.assertIsNone(d["clarify"])

    def test_claude_clarifier_failure_falls_back_to_fixed(self):
        class Boom:
            name = "claude"

            def ask(self, ctx):
                raise RuntimeError("api down")

        ctx = clarify.Context(KEY, f"{KEY} x", "赤錆", "要改善", None)
        q, who = clarify.ask_question(Boom(), ctx)
        self.assertEqual(who, "fixed")
        self.assertIn("赤錆", q)


if __name__ == "__main__":
    unittest.main()
