import os
import tempfile
import unittest
from types import SimpleNamespace

from PIL import Image

from kanban_karte.eval import claude_judge as cj
from kanban_karte.eval.claude_judge import ClaudeJudge, parse_response
from kanban_karte.eval.dataset import EvalItem

FORM = ["②腐食", "②破損", "④基礎"]


class ParseResponseTest(unittest.TestCase):
    def test_normal(self):
        t = '{"rank": 3, "items": {"②腐食": 3, "②破損": 1, "④基礎": null}, "reasons": ["錆"], "confidence": 0.7}'
        p = parse_response(t, FORM)
        self.assertEqual(p["rank"], 3)
        self.assertEqual(p["items"], {"②腐食": 3, "②破損": 1})  # null は落とす
        self.assertEqual(p["reasons"], ["錆"])
        self.assertEqual(p["confidence"], 0.7)

    def test_code_fence_and_surrounding_text(self):
        t = '判定です。\n```json\n{"rank": 2, "items": {}, "reasons": [], "confidence": 1}\n```\n以上'
        self.assertEqual(parse_response(t, FORM)["rank"], 2)
        self.assertEqual(parse_response('前置き {"rank": 4} 後書き', FORM)["rank"], 4)

    def test_optional_fields(self):
        p = parse_response('{"rank": 1}', FORM)
        self.assertEqual((p["items"], p["reasons"], p["confidence"]), ({}, [], None))

    def test_unknown_item_keys_dropped_and_no_form(self):
        t = '{"rank": 2, "items": {"②腐食": 2, "存在しない項目": 3}}'
        self.assertEqual(parse_response(t, FORM)["items"], {"②腐食": 2})
        self.assertEqual(parse_response(t, [])["items"], {})
        self.assertEqual(parse_response(t, None)["items"], {})

    def test_reasons_capped(self):
        t = '{"rank": 2, "reasons": ["a","b","c","d","e","f","g"]}'
        self.assertEqual(len(parse_response(t)["reasons"]), 5)

    def test_broken_json(self):
        for t in ['{"rank": 2,', 'rank: 2', '', '{"rank": 2 "items": {}}', '[1,2,3]', 'null']:
            with self.assertRaises(ValueError, msg=t):
                parse_response(t, FORM)

    def test_rank_missing_or_out_of_range(self):
        for t in ['{"items": {}}', '{"rank": 0}', '{"rank": 5}', '{"rank": -1}', '{"rank": "2"}',
                  '{"rank": 2.5}', '{"rank": true}', '{"rank": null}', '{"rank": [2]}']:
            with self.assertRaises(ValueError, msg=t):
                parse_response(t, FORM)

    def test_item_value_out_of_range(self):
        for t in ['{"rank": 2, "items": {"②腐食": 5}}', '{"rank": 2, "items": {"②腐食": "良好"}}',
                  '{"rank": 2, "items": [1]}', '{"rank": 2, "items": {"②腐食": 0}}']:
            with self.assertRaises(ValueError, msg=t):
                parse_response(t, FORM)

    def test_confidence_out_of_range_and_reasons_type(self):
        for t in ['{"rank": 2, "confidence": 1.5}', '{"rank": 2, "confidence": -0.1}',
                  '{"rank": 2, "confidence": "high"}', '{"rank": 2, "reasons": "錆"}',
                  '{"rank": 2, "reasons": [1]}']:
            with self.assertRaises(ValueError, msg=t):
                parse_response(t, FORM)


class HelperTest(unittest.TestCase):
    def test_cost(self):
        self.assertAlmostEqual(cj.cost_usd("claude-sonnet-5-5", 1_000_000, 100_000), 2.0 + 1.0)
        self.assertIsNone(cj.cost_usd("no-such-model", 1, 1))

    def test_resized_size(self):
        self.assertEqual(cj.resized_size(2560, 1280), (1280, 640))
        self.assertEqual(cj.resized_size(800, 600), (800, 600))  # 拡大しない
        self.assertEqual(cj.resized_size(1000, 4000), (320, 1280))

    def test_prepare_image_resizes_and_handles_alpha(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.png")
            Image.new("RGBA", (3000, 1500), (255, 0, 0, 128)).save(p)
            import base64, io
            im = Image.open(io.BytesIO(base64.b64decode(cj.prepare_image(p))))
            self.assertEqual(im.format, "JPEG")
            self.assertEqual(im.size, (1280, 640))
            self.assertEqual(im.mode, "RGB")

    def test_dotenv_does_not_override(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            __import__("pathlib").Path(p).write_text('# c\nKK_TEST_A="from_file"\nKK_TEST_B=b\n')
            os.environ["KK_TEST_A"] = "already"
            os.environ.pop("KK_TEST_B", None)
            try:
                cj.load_dotenv(__import__("pathlib").Path(p))
                self.assertEqual(os.environ["KK_TEST_A"], "already")
                self.assertEqual(os.environ["KK_TEST_B"], "b")
            finally:
                os.environ.pop("KK_TEST_A", None)
                os.environ.pop("KK_TEST_B", None)


class FakeClient:
    """messages.create(**kw) が用意した応答を順に返す。受け取った引数も残す。"""

    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        text = self.texts.pop(0)
        return SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=1000, output_tokens=100),
            stop_reason="end_turn")


class JudgeFlowTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.photos = []
        for i in range(8):
            p = os.path.join(self.tmp.name, f"{i}.jpg")
            Image.new("RGB", (100, 80), (i * 20, 0, 0)).save(p)
            self.photos.append(p)
        self.item = EvalItem("xlsx_center", "x:0", self.photos, 3, {"②腐食": 3}, "eval",
                             {"sign_name": "テスト看板", "form_items": FORM})

    def tearDown(self):
        self.tmp.cleanup()

    def test_success_first_try(self):
        c = FakeClient(['{"rank": 3, "items": {"②腐食": 3}, "reasons": ["錆"], "confidence": 0.5}'])
        pr = ClaudeJudge(client=c).judge(self.item)
        self.assertEqual(pr.rank_pred, 3)
        self.assertEqual(pr.item_judgments_pred, {"②腐食": 3})
        self.assertEqual(pr.usage["attempts"], 1)
        self.assertEqual(pr.usage["input_tokens"], 1000)
        kw = c.calls[0]
        self.assertNotIn("temperature", kw)
        self.assertEqual(kw["model"], cj.DEFAULT_MODEL)
        imgs = [b for b in kw["messages"][0]["content"] if b["type"] == "image"]
        self.assertEqual(len(imgs), 6)  # 8 枚あっても既定は 6 枚まで

    def test_retry_once_then_ok(self):
        c = FakeClient(["すみません、分かりません", '{"rank": 2}'])
        pr = ClaudeJudge(client=c).judge(self.item)
        self.assertEqual(pr.rank_pred, 2)
        self.assertEqual(pr.usage["attempts"], 2)
        self.assertEqual(pr.usage["input_tokens"], 2000)
        self.assertEqual(pr.raw["retries"], 1)
        # 2 回目の送信には、不正だった応答と指摘が付く
        self.assertEqual(c.calls[1]["messages"][-1]["role"], "user")
        self.assertEqual(c.calls[1]["messages"][-2]["role"], "assistant")

    def test_both_bad_returns_none_with_raw(self):
        c = FakeClient(["bad1", '{"rank": 9}'])
        pr = ClaudeJudge(client=c).judge(self.item)
        self.assertIsNone(pr.rank_pred)
        self.assertEqual(len(c.calls), 2)  # 再試行は1回だけ
        self.assertEqual(len(pr.raw["failed"]), 2)
        self.assertEqual(pr.raw["failed"][0]["text"], "bad1")
        self.assertEqual(pr.usage["attempts"], 2)

    def test_no_photos_makes_no_call(self):
        c = FakeClient([])
        item = EvalItem("xlsx_karte", "y:0", [], 2, None, "eval", {})
        pr = ClaudeJudge(client=c).judge(item)
        self.assertIsNone(pr.rank_pred)
        self.assertEqual(c.calls, [])

    def test_no_form_items_gives_empty_items(self):
        item = EvalItem("paper_scan", "z:0", self.photos[:1], 2, None, "eval", {})
        c = FakeClient(['{"rank": 2, "items": {"x": 1}}'])
        pr = ClaudeJudge(client=c).judge(item)
        self.assertEqual(pr.item_judgments_pred, {})

    def test_estimate_and_effort_passthrough(self):
        j = ClaudeJudge(client=FakeClient(['{"rank": 1}']), effort="low", max_photos=2)
        e = j.estimate(self.item)
        self.assertEqual(e["photos"], 2)
        self.assertGreater(e["input_tokens"], e["image_tokens"])
        j.judge(self.item)
        self.assertEqual(j._client.calls[0]["output_config"], {"effort": "low"})

    def test_workers_capped_and_order_kept(self):
        j = ClaudeJudge(client=FakeClient(['{"rank": %d}' % (i % 4 + 1) for i in range(5)]), workers=99)
        self.assertEqual(j.workers, 4)
        j.workers = 1
        preds = j.judge_many([self.item] * 5)
        self.assertEqual([p.rank_pred for p in preds], [1, 2, 3, 4, 1])


if __name__ == "__main__":
    unittest.main()
