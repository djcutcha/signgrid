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


class GuardFormatTest(unittest.TestCase):
    def test_v2_items_with_evidence_and_needs_onsite(self):
        t = ('{"rank": 3, "items": {'
             '"②腐食": {"rank": 3, "evidence": "写真2の柱脚に赤錆", "needs_onsite": false},'
             '"②破損": {"rank": 1, "evidence": "写真1に破損なし", "needs_onsite": false},'
             '"④基礎": {"rank": null, "evidence": "基礎が写っていない", "needs_onsite": true}}}')
        p = parse_response(t, FORM)
        self.assertEqual(p["items"], {"②腐食": 3, "②破損": 1})  # needs_onsite は判断できず= 値に入れない
        self.assertEqual(p["needs_onsite"], 1)
        self.assertEqual(p["item_notes"]["②腐食"]["evidence"], "写真2の柱脚に赤錆")
        self.assertTrue(p["item_notes"]["④基礎"]["needs_onsite"])

    def test_needs_onsite_drops_even_if_rank_given(self):
        t = '{"rank": 2, "items": {"②腐食": {"rank": 2, "evidence": "e", "needs_onsite": true}}}'
        self.assertEqual(parse_response(t, FORM)["items"], {})

    def test_v2_invalid(self):
        for t in ['{"rank": 2, "items": {"②腐食": {"rank": 7}}}',
                  '{"rank": 2, "items": {"②腐食": {"rank": 2, "needs_onsite": "yes"}}}',
                  '{"rank": 2, "items": {"②腐食": {"rank": 2, "evidence": 5}}}']:
            with self.assertRaises(ValueError, msg=t):
                parse_response(t, FORM)

    def test_v1_values_still_work_and_notes_empty(self):
        p = parse_response('{"rank": 2, "items": {"②腐食": 2}}', FORM)
        self.assertEqual((p["items"], p["item_notes"], p["needs_onsite"]), ({"②腐食": 2}, {}, 0))

    def test_guard_prompt(self):
        self.assertEqual(cj.system_prompt(0, False), cj.SYSTEM_PROMPT)
        g = cj.system_prompt(0, True)
        for w in ("最も重い誤り", "ひび", "evidence", "needs_onsite", "念のため"):
            self.assertIn(w, g)
        self.assertNotIn("判定例", g)
        self.assertIn("判定例", cj.system_prompt(6, True))
        self.assertNotIn("evidence", cj.system_prompt(6, False))

    def test_rubric_prompt(self):
        r = cj.system_prompt(0, False, True)
        for w in ("a(1)", "d(4)", "アンカーボルト", "ビス", "配線", "退色"):
            self.assertIn(w, r)
        self.assertEqual(cj.system_prompt(0, False, False), cj.SYSTEM_PROMPT)
        self.assertNotIn("評価基準", cj.system_prompt(6, True))
        self.assertLess(cj.system_prompt(6, True, True).index("評価基準"),
                        cj.system_prompt(6, True, True).index("見逃し対策"))

    def test_judge_guard_records_notes(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        p = os.path.join(tmp.name, "a.jpg")
        Image.new("RGB", (50, 50)).save(p)
        item = EvalItem("xlsx_center", "x:0", [p], 3, {"②腐食": 3}, "eval", {"form_items": FORM})
        c = FakeClient(['{"rank": 3, "items": {"②腐食": {"rank": 3, "evidence": "e", "needs_onsite": false},'
                        '"④基礎": {"rank": null, "evidence": "f", "needs_onsite": true}}}'])
        pr = ClaudeJudge(client=c, guard=True).judge(item)
        self.assertEqual(pr.item_judgments_pred, {"②腐食": 3})
        self.assertEqual(pr.raw["needs_onsite"], 1)
        self.assertIn("evidence", c.calls[0]["system"])


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


class ExamplesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def _item(self, source, rank, split, group, n_photos=5):
        self.n += 1
        paths = []
        for k in range(n_photos):
            p = os.path.join(self.tmp.name, f"{self.n}_{k}.jpg")
            Image.new("RGB", (64, 48), (self.n * 7 % 256, k * 30, 0)).save(p)
            paths.append(p)
        return EvalItem(source, f"{source}:{self.n:03d}", paths, rank, {"②腐食": rank} if rank else None,
                        split, {"group_key": f"{source}|{group}", "form_items": FORM, "sign_name": f"秘密の看板{self.n}"})

    def _pool(self):
        pool = []
        for r in (1, 2, 3, 4):
            for g in range(3):
                pool.append(self._item("xlsx_center", r, "example", f"g{r}{g}"))
        pool.append(self._item("xlsx_center", 1, "eval", "ev"))        # 評価用は絶対に入れない
        pool.append(self._item("xlsx_karte", 3, "example", "k"))       # 別の様式は入れない
        pool.append(self._item("xlsx_center", None, "example", "nr"))  # 正解ランクなしは入れない
        return pool

    def test_select_spreads_ranks_and_pairs_1_and_2(self):
        pool = self._pool()
        ex = cj.select_examples(pool, "xlsx_center", 6)
        self.assertEqual(sorted(e.rank_true for e in ex), [1, 1, 2, 2, 3, 4])
        self.assertTrue(all(e.split == "example" and e.source == "xlsx_center" for e in ex))
        self.assertEqual(len({e.meta["group_key"] for e in ex}), 6)  # 同じ現場から重ねない
        self.assertEqual(ex, cj.select_examples(list(reversed(pool)), "xlsx_center", 6))  # 決定論(入力順に依らない)

    def test_select_missing_rank_and_small_n(self):
        pool = [i for i in self._pool() if i.rank_true != 4]
        self.assertEqual(sorted(e.rank_true for e in cj.select_examples(pool, "xlsx_center", 4)), [1, 1, 2, 3])
        self.assertEqual(sorted(e.rank_true for e in cj.select_examples(pool, "xlsx_center", 2)), [1, 2])
        self.assertEqual(cj.select_examples(pool, "xlsx_center", 0), [])
        self.assertEqual(cj.select_examples(pool, "paper_scan", 6), [])  # 例示用が無い様式は 0 基

    def test_mix_parse(self):
        self.assertEqual(cj.parse_mix("1,1,2, 2,3,3,4"), [1, 1, 2, 2, 3, 3, 4])
        for bad in ("", "0,1", "1,5", "a", ",".join(["1"] * 10)):
            with self.assertRaises(ValueError):
                cj.parse_mix(bad)

    def test_select_by_mix_and_equals_default(self):
        pool = self._pool()
        a = cj.select_examples(pool, "xlsx_center", 0, 0, [1, 1, 2, 2, 3, 4])
        self.assertEqual(a, cj.select_examples(pool, "xlsx_center", 6))  # 構成指定と --examples 6 は同じ選び方
        b = cj.select_examples(pool, "xlsx_center", 0, 0, [1, 2, 3, 3, 4])
        self.assertEqual(sorted(e.rank_true for e in b), [1, 2, 3, 3, 4])

    def test_select_mix_drops_missing_rank_and_prefers_photos(self):
        pool = [i for i in self._pool() if i.rank_true != 4]
        x = cj.select_examples(pool, "xlsx_center", 0, 0, [1, 3, 4, 4])
        self.assertEqual(sorted(e.rank_true for e in x), [1, 3])  # 4 は無いので省く
        pool = [self._item("xlsx_center", 1, "example", f"p{k}", n_photos=k) for k in (1, 2, 3, 4, 5)]
        for seed in range(5):
            (e,) = cj.select_examples(pool, "xlsx_center", 0, seed, [1])
            self.assertGreaterEqual(len(e.photo_paths), cj.EXAMPLE_MIN_PHOTOS)
        only_small = [self._item("xlsx_center", 1, "example", "s", n_photos=1)]
        self.assertEqual(len(cj.select_examples(only_small, "xlsx_center", 0, 0, [1])), 1)  # 無ければ少なくても使う

    def test_why_line(self):
        it = self._item("xlsx_center", 2, "example", "w")
        it.item_judgments_true = {"腐食": 2, "破損": 1, "固定": 1}
        self.assertEqual(cj.example_why_text(it), "なぜこのランクか: 項目別= 腐食2・破損1・固定1 → 最悪値2")
        it.item_judgments_true = {"腐食": 1}
        self.assertIn("看板全体の評価でランク 2", cj.example_why_text(it))
        it.item_judgments_true = None
        self.assertIn("項目別の判定が無く", cj.example_why_text(it))
        self.assertNotIn("なぜ", cj.example_answer_text(it))
        self.assertIn("なぜ", cj.example_answer_text(it, why=True))

    def test_judge_with_mix_and_why(self):
        j = ClaudeJudge(client=FakeClient(['{"rank": 2}']), example_mix=[1, 2, 3], example_why=True)
        self.assertEqual(j.n_examples, 3)
        j.set_example_pool(self._pool())
        text = " ".join(b.get("text", "") for b in j.example_blocks("xlsx_center"))
        self.assertEqual(text.count("なぜこのランクか"), 3)
        self.assertNotIn("秘密の看板", text)

    def test_blocks_cached_hidden_names_and_photo_cap(self):
        j = ClaudeJudge(client=FakeClient(['{"rank": 2}']), examples=6)
        j.set_example_pool(self._pool())
        target = self._item("xlsx_center", 2, "eval", "t", n_photos=2)
        j.judge(target)
        kw = j._client.calls[0]
        content = kw["messages"][0]["content"]
        marked = [i for i, b in enumerate(content) if "cache_control" in b]
        self.assertEqual(len(marked), 1)
        # 例示ブロックが先頭に固定され、キャッシュ印は例示の最後。判定対象の写真はその後ろ
        first_target_photo = next(i for i, b in enumerate(content) if b.get("text") == "写真1")
        self.assertLess(marked[0], first_target_photo)
        ex_imgs = [b for b in content[: marked[0]] if b["type"] == "image"]
        self.assertEqual(len(ex_imgs), 6 * cj.EXAMPLE_PHOTOS)  # 各基 3 枚に絞る(元は 5 枚)
        text = " ".join(b.get("text", "") for b in content[: marked[0] + 1])
        self.assertNotIn("秘密の看板", text)  # 例示の看板名は伏せる
        self.assertIn("ランク", text)
        self.assertIn("判定例", kw["system"])

    def test_zero_shot_unchanged(self):
        j = ClaudeJudge(client=FakeClient(['{"rank": 2}']))
        self.assertEqual(j.system, cj.SYSTEM_PROMPT)
        self.assertEqual(j.example_blocks("xlsx_center"), [])
        self.assertNotIn("判定例", cj.SYSTEM_PROMPT)

    def test_cache_usage_and_cost(self):
        self.assertAlmostEqual(cj.cost_usd("claude-sonnet-5-5", 0, 0, cache_write=1_000_000), 2.0 * 1.25)
        self.assertAlmostEqual(cj.cost_usd("claude-sonnet-5-5", 0, 0, cache_read=1_000_000), 2.0 * 0.1)

        class C(FakeClient):
            def _create(self, **kw):
                r = super()._create(**kw)
                r.usage = SimpleNamespace(input_tokens=100, output_tokens=10,
                                          cache_creation_input_tokens=5000, cache_read_input_tokens=0)
                return r
        j = ClaudeJudge(client=C(['{"rank": 2}']))
        item = EvalItem("xlsx_center", "x:1", [self._item("xlsx_center", 1, "eval", "q").photo_paths[0]],
                        2, None, "eval", {})
        u = j.judge(item).usage
        self.assertEqual((u["input_tokens"], u["cache_write_tokens"], u["cache_read_tokens"]), (5100, 5000, 0))
        self.assertAlmostEqual(u["cost_usd"], (100 * 2.0 + 5000 * 2.0 * 1.25 + 10 * 10.0) / 1e6)

    def test_judge_many_with_examples_keeps_order(self):
        c = FakeClient(['{"rank": %d}' % (i % 4 + 1) for i in range(4)])
        j = ClaudeJudge(client=c, examples=2)
        j.set_example_pool(self._pool())
        items = [self._item("xlsx_center", 2, "eval", f"t{i}", 1) for i in range(4)]
        j.workers = 2
        preds = j.judge_many(items)
        self.assertEqual(len(preds), 4)
        self.assertEqual(preds[0].rank_pred, 1)  # 1 基目は単独で先に流す
        self.assertEqual(len(c.calls), 4)


if __name__ == "__main__":
    unittest.main()
