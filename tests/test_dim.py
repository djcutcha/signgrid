import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from PIL import Image

from kanban_karte.dim import reference_objects as ref
from kanban_karte.dim.estimator import DimEstimateError, DimEstimator, judge_from_range, parse_estimate
from kanban_karte.legal.judge import JudgeResult

GOOD = {"w_mm": 3200, "h_mm": 900, "w_range": [2800, 3600], "h_range": [800, 1000],
        "references": [{"name": "ドア", "assumed_mm": 2000, "how": "入口の扉の高さ"}],
        "read_from_text": False, "confidence": "mid", "notes": "板面のみ"}


def resp(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=1000, output_tokens=200))


class FakeClient:
    def __init__(self, *texts):
        self.texts, self.calls = list(texts), []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return resp(self.texts.pop(0))


def photo(td: str, name="a.jpg", size=(2400, 1800)) -> Path:
    p = Path(td) / name
    Image.new("RGB", size, (200, 200, 200)).save(p)
    return p


def fake_judge(threshold: float):
    """面積が threshold 以下なら 不要、超えたら 要。"""
    def judge(address, *, use, area_m2, **kw):
        req = area_m2 > threshold
        return JudgeResult("東京都", "東京都", use, not req, "要" if req else "不要", None, "要" if req else "不要",
                           citations=[{"article": "x"}])
    return judge


class ParseTest(unittest.TestCase):
    def test_good(self):
        e = parse_estimate("```json\n" + json.dumps(GOOD) + "\n```")
        self.assertEqual((e.w_mm, e.h_mm, e.w_range, e.confidence), (3200, 900, (2800, 3600), "mid"))
        d = e.to_dict()
        self.assertEqual(d["area_m2"], 2.88)
        self.assertEqual(d["w_pct"], 13)

    def test_value_outside_range_widens_range(self):
        e = parse_estimate(json.dumps({**GOOD, "w_range": [3300, 3600]}))
        self.assertEqual(e.w_range, (3200, 3600))

    def test_bad(self):
        for bad in ({**GOOD, "confidence": "great"}, {**GOOD, "w_mm": -5}, {**GOOD, "h_range": [1]},
                    {k: v for k, v in GOOD.items() if k != "w_mm"}, {**GOOD, "read_from_text": "yes"}):
            with self.assertRaises(ValueError):
                parse_estimate(json.dumps(bad))
        with self.assertRaises(ValueError):
            parse_estimate("JSON ではない")


class EstimatorTest(unittest.TestCase):
    def test_estimate_sends_two_photos_downscaled_and_name(self):
        with tempfile.TemporaryDirectory() as td:
            c = FakeClient(json.dumps(GOOD))
            e = DimEstimator("claude-sonnet-5-5", c).estimate([photo(td, "a.jpg"), photo(td, "b.jpg"), photo(td, "c.jpg")], "欄間看板")
        content = c.calls[0]["messages"][0]["content"]
        self.assertEqual([b["type"] for b in content], ["image", "image", "text"])  # 3枚目は捨てる
        self.assertIn("欄間看板", content[-1]["text"])
        import base64
        im = Image.open(io.BytesIO(base64.b64decode(content[0]["source"]["data"])))
        self.assertEqual(max(im.size), 1600)
        self.assertIn("ドア", c.calls[0]["system"])  # 辞書がプロンプトに入る
        self.assertEqual(e.usage["attempts"], 1)
        self.assertGreater(e.usage["cost_usd"], 0)

    def test_retry_on_bad_json(self):
        with tempfile.TemporaryDirectory() as td:
            c = FakeClient("壊れた", json.dumps(GOOD))
            e = DimEstimator("claude-sonnet-5-5", c).estimate([photo(td)], None)
        self.assertEqual(e.usage["attempts"], 2)
        self.assertEqual(len(c.calls), 2)
        self.assertEqual(c.calls[1]["messages"][-1]["role"], "user")

    def test_fails_after_two_bad(self):
        with tempfile.TemporaryDirectory() as td:
            c = FakeClient("壊れた", "まだ壊れている")
            with self.assertRaises(DimEstimateError) as cm:
                DimEstimator("claude-sonnet-5-5", c).estimate([photo(td)], None)
        self.assertEqual(cm.exception.usage["attempts"], 2)

    def test_no_photos(self):
        c = FakeClient()
        with self.assertRaises(DimEstimateError):
            DimEstimator("claude-sonnet-5-5", c).estimate([], "x")
        self.assertEqual(c.calls, [])

    def test_bytes_photo_and_broken_image(self):
        buf = io.BytesIO()
        Image.new("RGB", (100, 80)).save(buf, "JPEG")
        c = FakeClient(json.dumps(GOOD))
        DimEstimator("m", c).estimate([buf.getvalue()], None)
        with self.assertRaises(DimEstimateError):
            DimEstimator("m", FakeClient()).estimate([b"not an image"], None)


class JudgeFromRangeTest(unittest.TestCase):
    def est(self, wr, hr, w=3200, h=900):
        return parse_estimate(json.dumps({**GOOD, "w_mm": w, "h_mm": h, "w_range": wr, "h_range": hr}))

    def test_both_ends_within(self):  # 2.24 ㎡〜3.6 ㎡、基準 10 ㎡
        r = judge_from_range(self.est([2800, 3600], [800, 1000]), "東京都", fake_judge(10))
        self.assertEqual((r["permit"], r["area_low"], r["area_high"]), ("不要", 2.24, 3.6))
        self.assertEqual(r["inspection_required"], "不要")
        self.assertEqual(len(r["citations"]), 1)  # 重複は1件にまとめる

    def test_range_returns_four_duties(self):
        # 実際の規則で、範囲の両端が同じ結論の義務はそのまま、分かれる義務は 要実測
        r = judge_from_range(self.est([2800, 3600], [800, 1000]), "埼玉県さいたま市大宮区桜木町1-1-1")
        for k in ("maintenance_duty", "inspection_duty", "qualified_inspector", "report_duty", "duties"):
            self.assertIn(k, r)
        self.assertEqual(set(r["duties"]), {"maintenance", "inspection", "qualified", "report"})
        self.assertEqual(r["maintenance_duty"], "要")
        self.assertEqual(r["duties"]["maintenance"]["value"], r["maintenance_duty"])

    def test_both_ends_exceed(self):
        r = judge_from_range(self.est([6000, 7000], [2000, 2500], w=6500, h=2200), "東京都", fake_judge(10))
        self.assertEqual(r["permit"], "要")

    def test_boundary_straddles_is_needs_measure(self):  # 2.24 ㎡〜3.6 ㎡、基準 3 ㎡ をまたぐ
        r = judge_from_range(self.est([2800, 3600], [800, 1000]), "東京都", fake_judge(3))
        self.assertEqual(r["permit"], "要実測")
        self.assertEqual((r["low"]["permit"], r["high"]["permit"]), ("不要", "要"))
        self.assertTrue(any("両端で結論が分かれる" in n for n in r["notes"]))

    def test_boundary_exactly_at_end_is_within(self):  # 上端がちょうど基準 → 超えない側
        r = judge_from_range(self.est([2800, 3600], [800, 1000]), "東京都", fake_judge(3.6))
        self.assertEqual(r["permit"], "不要")

    def test_calls_judge_with_both_ends(self):
        seen = []

        def spy(address, *, use, area_m2, **kw):
            seen.append((address, use, area_m2))
            return fake_judge(10)(address, use=use, area_m2=area_m2)
        judge_from_range(self.est([2800, 3600], [800, 1000]), "東京都豊島区", spy)
        self.assertEqual(seen, [("東京都豊島区", "self", 2.24), ("東京都豊島区", "self", 3.6)])

    def test_height_is_passed_to_both_ends(self):
        seen = []

        def spy(address, *, use, area_m2, height_m=None, **kw):
            seen.append((area_m2, height_m))
            return fake_judge(10)(address, use=use, area_m2=area_m2)
        r = judge_from_range(self.est([2800, 3600], [800, 1000]), "東京都豊島区", spy, height_m=4.5)
        self.assertEqual(seen, [(2.24, 4.5), (3.6, 4.5)])
        self.assertEqual(r["height_m"], 4.5)
        self.assertIsNone(judge_from_range(self.est([2800, 3600], [800, 1000]), "東京都", fake_judge(10))["height_m"])

    def test_height_resolves_needs_measure_in_saitama(self):
        # 実際の規則: 埼玉県(市の条例が無い市)の有資格者は上端の高さの基準。全高なし= 要実測、全高 3m= 不要
        addr = "埼玉県川口市本町1-1-1"
        self.assertEqual(judge_from_range(self.est([2800, 3600], [800, 1000]), addr)["qualified_inspector"], "要実測")
        r = judge_from_range(self.est([2800, 3600], [800, 1000]), addr, height_m=3.0)
        self.assertEqual(r["qualified_inspector"], "不要")
        self.assertEqual(r["duties"]["qualified"]["value"], "不要")


class ReferenceObjectsTest(unittest.TestCase):
    def test_dictionary_and_empty_custom(self):
        names = [o.name for o in ref.all_objects()]
        self.assertIn("コンクリートブロック", names)
        self.assertEqual(ref.CUSTOM, [])  # 自社部材は枠だけ
        for o in ref.REFERENCE_OBJECTS:
            self.assertIn(o.source, ("JIS", "一般"))
            self.assertEqual(set(o.dims), set(o.tol_mm))
        self.assertIn("390", ref.prompt_text())


if __name__ == "__main__":
    unittest.main()
