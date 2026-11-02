import os
import tempfile
import unittest
from types import SimpleNamespace

from PIL import Image

from kanban_karte.eval import verifier as vf
from kanban_karte.eval.dataset import EvalItem
from kanban_karte.eval.judge import Prediction
from kanban_karte.eval.verifier import Verifier, apply_verdict, parse_verdict

FORM = ["②腐食", "②破損", "④基礎"]


class FakeClient:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.texts.pop(0))],
                               usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
                               stop_reason="end_turn")


class ParseVerdictTest(unittest.TestCase):
    def test_missed(self):
        t = '{"missed": [{"item": "②腐食", "photo": 2, "reason": "柱脚に赤錆"}]}'
        self.assertEqual(parse_verdict(t), [{"item": "②腐食", "photo": 2, "reason": "柱脚に赤錆"}])

    def test_none(self):
        self.assertEqual(parse_verdict('{"missed": []}'), [])
        self.assertEqual(parse_verdict("なし"), [])
        self.assertEqual(parse_verdict("「なし」。"), [])

    def test_fence(self):
        self.assertEqual(parse_verdict('```json\n{"missed": []}\n```'), [])

    def test_invalid(self):
        for t in ["", "{", '{"missed": "なし"}', '{"x": 1}', '{"missed": [{"photo": 1}]}',
                  '{"missed": [{"item": "a", "photo": "2"}]}', '{"missed": [{"item": "a", "reason": 3}]}']:
            with self.assertRaises(ValueError, msg=t):
                parse_verdict(t)


class ApplyVerdictTest(unittest.TestCase):
    def test_raises_rank_and_item(self):
        p = Prediction(2, {"②腐食": 2, "②破損": 1})
        o = apply_verdict(p, [{"item": "②腐食", "photo": 1, "reason": "r"}], FORM)
        self.assertEqual(o.rank_pred, 3)
        self.assertEqual(o.item_judgments_pred, {"②腐食": 3, "②破損": 1})
        self.assertTrue(o.verified)
        self.assertEqual(o.rank_before, 2)
        self.assertEqual(p.item_judgments_pred, {"②腐食": 2, "②破損": 1})  # 元は変えない

    def test_item_not_answered_becomes_3(self):
        o = apply_verdict(Prediction(1, {"②破損": 1}), [{"item": "④基礎", "photo": None, "reason": ""}], FORM)
        self.assertEqual(o.item_judgments_pred, {"②破損": 1, "④基礎": 3})

    def test_unknown_item_still_raises_rank_only(self):
        o = apply_verdict(Prediction(2, {"②腐食": 2}), [{"item": "全体", "photo": 1, "reason": ""}], FORM)
        self.assertEqual((o.rank_pred, o.item_judgments_pred), (3, {"②腐食": 2}))

    def test_no_items_pred(self):
        o = apply_verdict(Prediction(2, None), [{"item": "全体", "photo": 1, "reason": ""}], [])
        self.assertEqual((o.rank_pred, o.item_judgments_pred), (3, None))

    def test_nothing_missed(self):
        o = apply_verdict(Prediction(2, {"②腐食": 2}), [], FORM)
        self.assertEqual((o.rank_pred, o.verified, o.verifier_notes, o.rank_before), (2, True, [], 2))


class VerifierTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        paths = []
        for i in range(3):
            p = os.path.join(self.tmp.name, f"{i}.jpg")
            Image.new("RGB", (64, 48), (200, 100, 50)).save(p)
            paths.append(p)
        self.item = EvalItem("xlsx_karte", "x:0", paths, 3, {"②腐食": 3}, "eval",
                             {"form_items": FORM, "sign_name": "看板A"})
        self.pred = Prediction(2, {"②腐食": 2}, raw={"reasons": ["軽い錆"]},
                               usage={"input_tokens": 5000, "output_tokens": 500, "attempts": 1, "cost_usd": 0.05})

    def test_raises_and_merges_usage(self):
        c = FakeClient(['{"missed": [{"item": "②腐食", "photo": 1, "reason": "赤錆"}]}'])
        o = Verifier(client=c).verify(self.item, self.pred)
        self.assertEqual((o.rank_pred, o.rank_before, o.verified), (3, 2, True))
        self.assertEqual(o.usage["input_tokens"], 6000)
        self.assertEqual(o.usage["attempts"], 2)
        self.assertAlmostEqual(o.usage["cost_usd"], 0.05 + (1000 * 2 + 100 * 10) / 1e6)
        self.assertEqual(o.usage["verifier"]["input_tokens"], 1000)

    def test_prompt_has_rubric_no_examples_no_truth(self):
        c = FakeClient(['{"missed": []}'])
        Verifier(client=c, effort="high").verify(self.item, self.pred)
        kw = c.calls[0]
        self.assertIn("評価基準", kw["system"])
        self.assertNotIn("判定例", kw["system"].replace("判定例があれば", ""))
        self.assertEqual(kw["model"], vf.DEFAULT_VERIFIER_MODEL)
        self.assertEqual(kw["output_config"], {"effort": "high"})
        content = kw["messages"][0]["content"]
        self.assertEqual(sum(1 for b in content if b["type"] == "image"), 3)
        text = content[-1]["text"]
        self.assertIn("②腐食: 2", text)
        self.assertIn("軽い錆", text)
        self.assertIn("看板A", text)

    def test_skips_rank3_none_and_no_photos(self):
        c = FakeClient([])
        v = Verifier(client=c)
        for p in (Prediction(3, {}), Prediction(4, {}), Prediction(None)):
            self.assertIs(v.verify(self.item, p), p)
        nophoto = EvalItem("xlsx_karte", "x:1", [], 3, None, "eval", {})
        self.assertIs(v.verify(nophoto, self.pred), self.pred)
        self.assertEqual(c.calls, [])

    def test_retry_then_fail_keeps_draft(self):
        c = FakeClient(["だめ", "まだだめ"])
        o = Verifier(client=c).verify(self.item, self.pred)
        self.assertEqual(len(c.calls), 2)
        self.assertEqual(o.rank_pred, 2)
        self.assertFalse(o.verified)
        self.assertIn("verifier_error", o.raw)
        self.assertEqual(o.raw["reasons"], ["軽い錆"])
        self.assertEqual(o.usage["attempts"], 3)

    def test_verify_many_order(self):
        c = FakeClient(['{"missed": []}'] * 2)
        v = Verifier(client=c, workers=1)
        out = v.verify_many([self.item, self.item], [self.pred, Prediction(3, {})])
        self.assertEqual([o.rank_pred for o in out], [2, 3])
        self.assertEqual(len(c.calls), 1)


if __name__ == "__main__":
    unittest.main()
