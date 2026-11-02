import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from PIL import Image

from kanban_karte.eval import closeup as cu
from kanban_karte.eval.dataset import EvalItem

FORM = ["広告面の退色", "看板本体の損傷"]


class FakeClient:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=self.texts.pop(0))],
                               usage=SimpleNamespace(input_tokens=15000, output_tokens=500),
                               stop_reason="end_turn")


class TileTest(unittest.TestCase):
    def test_split_2x2_count_and_size(self):
        im = Image.new("RGB", (4000, 3001))
        parts = cu.split_2x2(im)
        self.assertEqual([n for n, _ in parts], ["左上", "右上", "左下", "右下", "全景"])
        for _, t in parts:
            self.assertLessEqual(max(t.size), 1280)
        self.assertEqual(parts[0][1].size, (1280, 960))     # 2000x1500 → 長辺 1280
        self.assertEqual(parts[2][1].size[1], round(1501 * 1280 / 2000))  # 下段は高さ 1501(奇数の分を下に)
        self.assertEqual(parts[4][1].size, (1280, 960))

    def test_small_image_not_upscaled(self):
        parts = cu.split_2x2(Image.new("RGB", (800, 600)))
        self.assertEqual([t.size for _, t in parts], [(400, 300)] * 4 + [(800, 600)])

    def test_tiles_cover_image(self):
        im = Image.new("RGB", (101, 51))
        w = sum(t.size[0] for _, t in cu.split_2x2(im, 10_000)[:2])
        h = sum(t.size[1] for _, t in (cu.split_2x2(im, 10_000)[0], cu.split_2x2(im, 10_000)[2]))
        self.assertEqual((w, h), (101, 51))

    def test_photo_blocks_max_five_images(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "a.jpg")
            Image.new("RGB", (2000, 1500)).save(p)
            b = cu.photo_blocks(3, p)
        self.assertEqual(sum(x["type"] == "image" for x in b), 5)
        self.assertEqual(b[0]["text"], "写真3 全景")
        self.assertIn("写真3 拡大タイル 左上", b[2]["text"])


class ChoosePhotosTest(unittest.TestCase):
    def test_cited_first(self):
        r = ["写真4の柱に錆", "写真2・3は良好", "写真4の再掲"]
        self.assertEqual(cu.cited_photos(r, 6), [4, 2, 3])
        self.assertEqual(cu.choose_photos(r, 6), [4, 2])

    def test_fallback_to_first_two(self):
        self.assertEqual(cu.choose_photos(["錆は見られない"], 5), [1, 2])
        self.assertEqual(cu.choose_photos(None, 5), [1, 2])

    def test_fill_and_range(self):
        self.assertEqual(cu.choose_photos(["写真3のみ"], 5), [3, 1])
        self.assertEqual(cu.choose_photos(["写真9"], 4), [1, 2])   # 範囲外の番号は無視
        self.assertEqual(cu.choose_photos(["写真7"], 9), [1, 2])   # 第1パスは先頭 6 枚まで
        self.assertEqual(cu.choose_photos(["写真1"], 1), [1])


class ParseComposeTest(unittest.TestCase):
    def test_parse(self):
        t = ('{"rank": 3, "items": {"広告面の退色": {"rank": 3, "evidence": "右上に退色", "photo": 2, "tile": "右上"},'
             ' "看板本体の損傷": null, "他": 4}, "summary": "広告面が退色"}')
        p = cu.parse_closeup(t, FORM)
        self.assertEqual(p["rank"], 3)
        self.assertEqual(p["items"]["広告面の退色"], {"rank": 3, "evidence": "右上に退色", "photo": 2, "tile": "右上"})
        self.assertIsNone(p["items"].get("看板本体の損傷", {"rank": None})["rank"])
        self.assertNotIn("他", p["items"])   # 様式の外の項目は捨てる

    def test_parse_invalid(self):
        for t in ["", "{", '{"items": {}}', '{"rank": 5}', '{"rank": 2, "items": []}',
                  '{"rank": 2, "items": {"広告面の退色": {"rank": 2, "photo": "2"}}}']:
            with self.assertRaises(ValueError, msg=t):
                cu.parse_closeup(t, FORM)

    def test_compose_worst_item(self):
        p = {"rank": 1, "items": {"広告面の退色": {"rank": 3}, "看板本体の損傷": {"rank": None}}}
        rank, merged = cu.compose({"広告面の退色": 1, "看板本体の損傷": 2}, p, True)
        self.assertEqual((rank, merged), (3, {"広告面の退色": 3, "看板本体の損傷": 2}))

    def test_compose_no_form_uses_model_rank(self):
        self.assertEqual(cu.compose(None, {"rank": 3, "items": {}}, False), (3, None))
        self.assertEqual(cu.compose({}, {"rank": 2, "items": {}}, True), (2, {}))


def _item(d, sid="xlsx_karte:0001", rank_true=3, form=FORM, n_photos=3, ij=None):
    paths = []
    for i in range(n_photos):
        p = os.path.join(d, f"{sid.replace(':', '_')}_{i}.jpg")
        Image.new("RGB", (1600, 1200)).save(p)
        paths.append(p)
    return EvalItem("xlsx_karte", sid, paths, rank_true, ij if ij is not None else {FORM[0]: 3, FORM[1]: 1},
                    "eval", {"form_items": form, "sign_name": "テスト看板"})


REC = {"sign_id": "xlsx_karte:0001", "rank_pred": 2, "reasons": ["写真2の広告面は退色が見られない"],
       "item_judgments_pred": {"広告面の退色": 2, "看板本体の損傷": 1}}
GOOD = ('{"rank": 3, "items": {"広告面の退色": {"rank": 3, "evidence": "左上に退色", "photo": 2, "tile": "左上"},'
        ' "看板本体の損傷": {"rank": 1, "evidence": "拡大でも見られない", "photo": null, "tile": null}},'
        ' "summary": "写真2左上に退色"}')


class RunOneTest(unittest.TestCase):
    def test_json_shape_and_images(self):
        with tempfile.TemporaryDirectory() as d:
            it = _item(d)
            cl = FakeClient([GOOD])
            out = cu.CloseupJudge(client=cl).run_one(it, REC, "本丸")
        self.assertEqual(out["rank_closeup"], 3)
        self.assertEqual(out["photos_used"], [2, 1])
        self.assertEqual(out["items_closeup"], {"広告面の退色": 3, "看板本体の損傷": 1})
        self.assertEqual(out["rank_first"], 2)
        self.assertEqual(out["group"], "本丸")
        self.assertEqual(out["usage"]["input_tokens"], 15000)
        self.assertAlmostEqual(out["usage"]["cost_usd"], (15000 * 4.0 + 500 * 20.0) / 1e6)
        json.dumps(out, ensure_ascii=False)
        content = cl.calls[0]["messages"][0]["content"]
        self.assertEqual(sum(b["type"] == "image" for b in content), 10)   # 2 枚 × (全景+4)
        text = content[-1]["text"]
        self.assertIn("第1パスの写真番号 2・1", text)
        self.assertIn("看板全体のランク: 2", text)
        self.assertNotIn("正解", text)
        self.assertEqual(cl.calls[0]["model"], "claude-opus-5-5")

    def test_retry_on_bad_json_then_fail(self):
        with tempfile.TemporaryDirectory() as d:
            it = _item(d)
            ok = cu.CloseupJudge(client=FakeClient(["だめ", GOOD])).run_one(it, REC)
            self.assertEqual(ok["rank_closeup"], 3)
            self.assertEqual(ok["usage"]["attempts"], 2)
            bad = cu.CloseupJudge(client=FakeClient(["だめ", "まだだめ"])).run_one(it, REC)
        self.assertIsNone(bad["rank_closeup"])
        self.assertIn("parse", bad["error"])

    def test_no_item_form_uses_model_rank(self):
        with tempfile.TemporaryDirectory() as d:
            it = _item(d, form=[], ij=None)
            rec = {"sign_id": it.sign_id, "rank_pred": 2, "reasons": []}
            out = cu.CloseupJudge(client=FakeClient(['{"rank": 3, "items": {}, "summary": "s"}'])).run_one(it, rec)
        self.assertEqual(out["rank_closeup"], 3)
        self.assertIsNone(out["items_closeup"])


class SelectSummaryTest(unittest.TestCase):
    def _data(self):
        items, recs = {}, []

        def add(i, src, t, p, ij):
            sid = f"{src}:{i:03d}"
            items[sid] = EvalItem(src, sid, ["x.jpg"], t, ij, "eval")
            recs.append({"sign_id": sid, "source": src, "rank_true": t, "rank_pred": p})
        add(1, "xlsx_karte", 3, 2, {"外装": 3})        # 本丸(c, 見逃し)
        add(2, "xlsx_karte", 3, 1, {"外装": 2})        # b → 本丸でない
        add(3, "xlsx_karte", 3, 4, {"外装": 3})        # 見逃してない
        add(4, "xlsx_karte", 4, 2, {"球切れ": 4})      # d
        add(5, "xlsx_center", 3, 1, {"外装": 3})       # 本丸
        add(6, "paper_scan", 3, 1, None)               # a
        for i in range(10, 20):
            add(i, "xlsx_karte", 1 + i % 2, 1 + i % 2, {"外装": 1})   # 対照の候補(正しく 1・2)
        for i in range(20, 25):
            add(i, "xlsx_center", 2, 2, {"外装": 2})
        add(30, "xlsx_karte", 1, 3, {"外装": 1})       # 誤警報済み → 対照にしない
        return recs, items

    def test_select(self):
        recs, items = self._data()
        sel = cu.select_targets(recs, items, seed=1)
        tg = [s for s, g in sel if g == "本丸"]
        ct = [s for s, g in sel if g == "対照"]
        self.assertEqual(tg, ["xlsx_center:005", "xlsx_karte:001"])
        self.assertEqual(len(ct), 2)
        self.assertEqual(sum(s.startswith("xlsx_karte") for s in ct), 1)   # 本丸と同じ様式の比率
        self.assertEqual(sum(s.startswith("xlsx_center") for s in ct), 1)
        self.assertNotIn("xlsx_karte:030", ct)
        self.assertEqual(sel, cu.select_targets(recs, items, seed=1))      # 決定論
        self.assertNotEqual(cu.select_targets(recs, items, {"xlsx_karte": 5}, seed=1)[2:],
                            cu.select_targets(recs, items, {"xlsx_karte": 5}, seed=2)[2:])

    def test_summarize(self):
        u = {"input_tokens": 100, "output_tokens": 10, "attempts": 1, "cost_usd": 0.5}
        recs = [{"sign_id": "s", "rank_first": 2, "group": "本丸", "rank_true": 3, "rank_closeup": 3, "usage": u},
                {"sign_id": "s", "rank_first": 2, "group": "本丸", "rank_true": 3, "rank_closeup": 2, "usage": u},
                {"sign_id": "s", "rank_first": 2, "group": "本丸", "rank_true": 3, "rank_closeup": None, "error": "parse"},
                {"sign_id": "s", "rank_first": 1, "group": "対照", "rank_true": 1, "rank_closeup": 3, "usage": u},
                {"sign_id": "s", "rank_first": 1, "group": "対照", "rank_true": 1, "rank_closeup": 1, "usage": u}]
        s = cu.summarize(recs)
        self.assertEqual((s["target"], s["target_rescued"], s["target_unmoved"]), (3, 1, 2))
        self.assertEqual((s["control"], s["control_false_alarm"], s["failed"]), (2, 1, 1))
        self.assertEqual((s["input_tokens"], s["cost_usd"]), (400, 2.0))
        r = {"created": "2026-11-02T20:00:00", "model": "m", "first_pass": "f.json", "records": recs, "summary": s}
        self.assertIn("本丸で救えた", cu.to_markdown(r))
        self.assertEqual(cu.log_row(r).count("|"), 15)


if __name__ == "__main__":
    unittest.main()
