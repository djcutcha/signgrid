"""store_address のオフライン検査(偽クライアント。実 API は呼ばない。住所・店名は架空)。"""
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace as NS

from kanban_karte.ledger.db import Ledger, Site
from kanban_karte.legal import store_address as SA


def _resp(answer, urls=("https://example.test/stores",), searches=2, stop="end_turn"):
    blocks = [NS(type="text", text="検索します"),
              NS(type="web_search_tool_result", content=[NS(url=u, title="t") for u in urls]),
              NS(type="text", text=json.dumps(answer, ensure_ascii=False))]
    return NS(content=blocks, stop_reason=stop,
              usage=NS(input_tokens=3000, output_tokens=300, server_tool_use=NS(web_search_requests=searches)))


class FakeClient:
    def __init__(self, *resps):
        self.resps, self.calls = list(resps), []
        self.messages = NS(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        return self.resps.pop(0)


GOOD = {"address": "東京都架空区見本町1-2-3", "source_url": "https://example.test/stores", "source_kind": "official_store_list",
        "confidence": 0.92, "candidates": [], "note": "公式一覧に一致"}


class SearchWebTest(unittest.TestCase):
    def test_confirmed(self):
        c = FakeClient(_resp(GOOD))
        r = SA.search_web("見本マート 架空店", "見本マート株式会社", "東京都", client=c)
        self.assertEqual(r["status"], SA.CONFIRMED)
        self.assertEqual(r["address"], GOOD["address"])
        self.assertEqual(r["searches"], 2)
        self.assertGreater(r["usd"], 0.02)
        kw = c.calls[0]
        self.assertEqual(kw["tools"][0]["name"], "web_search")
        self.assertIn("運営会社: 見本マート株式会社", kw["messages"][0]["content"])
        self.assertNotIn("temperature", kw)

    def test_low_confidence_needs_check_keeps_candidates(self):
        a = {**GOOD, "confidence": 0.55, "candidates": [{"address": "東京都別区2-2-2", "source_url": None, "note": "同名"}]}
        r = SA.search_web("見本マート 架空店", client=FakeClient(_resp(a)))
        self.assertEqual(r["status"], SA.NEEDS_CHECK)
        self.assertEqual(r["address"], GOOD["address"])
        self.assertEqual(len(r["candidates"]), 1)

    def test_threshold_boundary(self):
        r = SA.search_web("x", client=FakeClient(_resp({**GOOD, "confidence": 0.7})))
        self.assertEqual(r["status"], SA.CONFIRMED)
        r = SA.search_web("x", client=FakeClient(_resp({**GOOD, "confidence": 0.69})))
        self.assertEqual(r["status"], SA.NEEDS_CHECK)

    def test_fabricated_url_is_capped(self):
        r = SA.search_web("x", client=FakeClient(_resp({**GOOD, "source_url": "https://made-up.test/p"})))
        self.assertEqual(r["status"], SA.NEEDS_CHECK)
        self.assertLessEqual(r["confidence"], 0.5)
        self.assertIn("検索結果に無い", r["note"])

    def test_url_match_ignores_scheme_www_slash(self):
        r = SA.search_web("x", client=FakeClient(_resp({**GOOD, "source_url": "http://www.example.test/stores/"})))
        self.assertEqual(r["status"], SA.CONFIRMED)

    def test_not_found(self):
        r = SA.search_web("x", client=FakeClient(_resp({"address": None, "source_url": None, "source_kind": None,
                                                        "confidence": 0, "candidates": [], "note": "見つからない"})))
        self.assertEqual(r["status"], SA.NEEDS_CHECK)
        self.assertIsNone(r["address"])

    def test_bad_json_is_needs_check(self):
        resp = NS(content=[NS(type="text", text="わかりません")], stop_reason="end_turn",
                  usage=NS(input_tokens=10, output_tokens=5, server_tool_use=None))
        r = SA.search_web("x", client=FakeClient(resp))
        self.assertEqual(r["status"], SA.NEEDS_CHECK)

    def test_pause_turn_continues(self):
        first = NS(content=[NS(type="text", text="調べ中")], stop_reason="pause_turn",
                   usage=NS(input_tokens=100, output_tokens=10, server_tool_use=NS(web_search_requests=1)))
        c = FakeClient(first, _resp(GOOD, searches=1))
        r = SA.search_web("x", client=c)
        self.assertEqual(len(c.calls), 2)
        self.assertEqual(r["searches"], 2)
        self.assertEqual(r["status"], SA.CONFIRMED)


class LookupTest(unittest.TestCase):
    def test_ledger_first_no_api(self):
        led = Ledger(":memory:")
        led.upsert_site(Site(id="s1", name="見本マート　架空店", address="東京都台帳区1-1-1"))
        c = FakeClient()
        r = SA.lookup("見本マート 架空店", ledger=led, client=c, cache_path=Path("/nonexistent/c.json"))
        self.assertEqual((r["origin"], r["address"], r["status"]), ("ledger", "東京都台帳区1-1-1", SA.CONFIRMED))
        self.assertEqual(c.calls, [])

    def test_ledger_conflict_needs_check(self):
        led = Ledger(":memory:")
        led.upsert_site(Site(id="s1", name="見本マート 架空店", address="東京都A区1-1-1"))
        led.upsert_site(Site(id="s2", name="見本マート 架空店", address="東京都B区2-2-2"))
        r = SA.lookup("見本マート 架空店", ledger=led)
        self.assertEqual(r["status"], SA.NEEDS_CHECK)
        self.assertEqual(len(r["candidates"]), 2)

    def test_ledger_site_without_address_falls_through(self):
        led = Ledger(":memory:")
        led.upsert_site(Site(id="s1", name="見本マート 架空店", address=None))
        with tempfile.TemporaryDirectory() as d:
            c = FakeClient(_resp(GOOD))
            r = SA.lookup("見本マート 架空店", ledger=led, client=c, cache_path=Path(d) / "c.json")
        self.assertEqual(r["origin"], "web")

    def test_cache_roundtrip_and_refresh(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "c.json"
            c = FakeClient(_resp(GOOD), _resp({**GOOD, "address": "東京都新区9-9-9"}))
            r1 = SA.lookup("見本マート 架空店", client=c, cache_path=p)
            r2 = SA.lookup("見本マート　架空店", client=c, cache_path=p)  # 全角空白でも同じキー
            self.assertEqual((r1["origin"], r2["origin"]), ("web", "cache"))
            self.assertEqual(len(c.calls), 1)
            r3 = SA.lookup("見本マート 架空店", client=c, cache_path=p, refresh=True)
            self.assertEqual(r3["address"], "東京都新区9-9-9")
            self.assertEqual(json.loads(p.read_text(encoding="utf-8")).popitem()[1]["address"], "東京都新区9-9-9")

    def test_no_web(self):
        with tempfile.TemporaryDirectory() as d:
            r = SA.lookup("見本マート 架空店", cache_path=Path(d) / "c.json", web=False)
        self.assertEqual(r["status"], SA.NEEDS_CHECK)
        self.assertIsNone(r["address"])

    def test_operator_changes_cache_key(self):
        self.assertNotEqual(SA.cache_key("A店", "X社"), SA.cache_key("A店", "Y社"))


if __name__ == "__main__":
    unittest.main()
