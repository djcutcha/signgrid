"""有資格者のアノテーション → 判定役の手本: 分け方(偶奇)、選び方、送る文(番号を伏せる)、物差しへ自分の言葉を見せない、痕跡、規則の候補。"""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from kanban_karte.eval import expert_notes as en
from kanban_karte.eval.claude_judge import ClaudeJudge
from kanban_karte.eval.dataset import EvalItem
from kanban_karte.eval.judge import Prediction


def note(key, kind="壁面", signs=("錆",), reasons="底が錆だらけ", unknowns="漏電は写真ではわからない"):
    return {"sign_key": key, "kind": kind, "signs": list(signs), "reasons": reasons, "unknowns": unknowns}


class SplitPickTest(unittest.TestCase):
    def test_role_is_digit_sum_parity(self):
        self.assertEqual(en.role("0000005930-007"), "example")       # 5+9+3+0+7=24
        self.assertEqual(en.role("0000006157-015"), "measure")       # 25
        self.assertEqual(en.role("xlsx_center:0019"), en.role("xlsx_center-0019"))

    def test_examples_only_example_side_and_cap(self):
        notes = [note("0000005930-007"), note("0000006157-015"), note("0000006157-001"), note("0000006157-005")]
        self.assertEqual([n["sign_key"] for n in en.examples(notes)], ["0000005930-007", "0000006157-001", "0000006157-005"])
        self.assertEqual(len(en.examples(notes, 2)), 2)
        self.assertEqual(en.examples(notes, 0), [])

    def test_pick_kind_and_signs(self):
        pool = [note("a", "屋上", ["錆"]), note("b", "壁面", []), note("c", "壁面", ["錆", "腐食"]), note("d", "袖", ["傾き"])]
        self.assertEqual([n["sign_key"] for n in en.pick(pool, "壁面", ["錆", "腐食"])], ["c", "b", "a"])
        self.assertEqual(en.pick(pool, "", ["基礎のひび"]), [])           # 重なりが無ければ見せない
        self.assertEqual([n["sign_key"] for n in en.pick(pool, "壁面", ["錆"], exclude="c")], ["b", "a"])

    def test_examples_reasons_first_one_good_per_outdoor_kind(self):
        good = lambda k, kind: {**note(k, kind, [], "", ""), "good": True}
        notes = [good("0000005930-007", "壁面"), note("0000006157-001"), good("0000006157-005", "壁面"),
                 good("0000006157-023", "自立"), note("0000006157-041")]
        self.assertEqual([n["sign_key"] for n in en.examples(notes)],
                         ["0000006157-001", "0000006157-041", "0000005930-007", "0000006157-023"])
        self.assertEqual([n["sign_key"] for n in en.examples(notes, 3)], ["0000006157-001", "0000006157-041", "0000005930-007"])

    def test_pick_prefers_reasons_over_good_on_tie(self):
        pool = [{**note("g", "壁面", [], "", ""), "good": True}, note("r", "壁面", [])]
        self.assertEqual([n["sign_key"] for n in en.pick(pool, "壁面", [])], ["r", "g"])
        self.assertIn(en.GOOD_TEXT, en.block_text(pool[:1]))

    def test_block_text_scrubs_numbers(self):
        t = en.block_text([note("0000005930-007", reasons="案件 0000005930 の底が錆")])
        self.assertNotIn("0000005930", t)
        self.assertIn(en.TRACE, t)
        self.assertIn("漏電", t)

    def test_load_skips_skipped_and_empty(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "n.sqlite"
            from kanban_karte.web.expert import Store
            st = Store(db)
            st.save("eval", "0000005930-007", [], {"rank": 3, "reasons": "錆", "signs": ["錆"], "kind": "壁面"})
            st.save("eval", "0000006157-001", [], {"skipped": True, "reasons": "x"})
            st.save("eval", "0000006157-005", [], {"rank": 1})
            st.save("eval", "0000006157-023", [], {"rank": 1, "kind": "自立"})
            st.save("eval", "0000006157-041", [], {"rank": 2, "kind": "壁面"})
            st.save("eval", "0000006157-061", [], {"rank": 1, "kind": "その他"})
            st.save("web", "1", [], {"rank": 1, "reasons": "web は使わない"})
            got = en.load(db)
            self.assertEqual([n["sign_key"] for n in got], ["0000005930-007", "0000006157-023"])   # 文の無い物は屋外のランク 1 だけ
            self.assertTrue(got[1]["good"])
            self.assertEqual(got[0]["signs"], ["錆"])


class JudgeWiringTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        from PIL import Image
        self.photo = str(Path(self.td.name) / "p.jpg")
        Image.new("RGB", (8, 8)).save(self.photo)

    def tearDown(self):
        self.td.cleanup()

    def item(self, sid, name="壁面看板"):
        return EvalItem("paper_scan", sid, [self.photo], 3, None, "eval", {"sign_name": name})

    def texts(self, j, it):
        return "\n".join(b.get("text", "") for b in j.build_messages(it)[0]["content"] if b["type"] == "text")

    def test_measure_item_never_sees_own_note(self):
        notes = [note("0000006157-015", reasons="自分の言葉"), note("0000005930-007", reasons="手本の言葉")]
        j = ClaudeJudge(client=object(), expert_notes=notes, expert_signs={"0000006157-015": ["錆"]})
        t = self.texts(j, self.item("0000006157-015"))
        self.assertIn("手本の言葉", t)
        self.assertNotIn("自分の言葉", t)             # 物差しの側のアノテーションは手本にしない

    def test_no_notes_no_block_and_trace(self):
        j0 = ClaudeJudge(client=object())
        self.assertNotIn("有資格者の見方", self.texts(j0, self.item("0000006157-015")))
        j = ClaudeJudge(client=object(), expert_notes=[note("0000005930-007")])
        j._judge = lambda it: Prediction(3, {}, 0.5, raw={"reasons": ["錆がある"]})
        pr = j.judge(self.item("0000006157-015"))
        self.assertEqual(pr.raw["reasons"][-1], en.TRACE)
        self.assertEqual(pr.raw["expert_refs"], 1)

    def test_add_rule_candidate(self):
        from kanban_karte.ledger.db import Ledger
        db = Path(self.td.name) / "l.sqlite"
        Ledger(db).close()
        import io, contextlib
        for _ in range(2):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                en.main(["add-rule", "--ledger", str(db)])
            out = json.loads(buf.getvalue())
        self.assertEqual(out["status"], "candidate")
        with Ledger(db) as lg:
            self.assertEqual(sum(1 for r in lg.list_rules() if r["text"] == en.UNKNOWNS_RULE), 1)   # 2 回足しても 1 本


if __name__ == "__main__":
    unittest.main()
