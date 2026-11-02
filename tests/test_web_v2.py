"""画面 v2 の API: 看板の法規(根拠条文)、行政の一括登録、オーナーの1枚撮り判定。見本 DB だけ使い、外部 API は呼ばない。"""
import unittest

from kanban_karte.web import api
from tests.test_web import WebTest


class LegalTest(WebTest):
    def test_detail_has_legal(self):
        st, d, _ = self.req("/api/signs/demo-001")
        L = d["legal"]
        self.assertNotIn("error", L)
        self.assertEqual(L["permit_required"], "不要")      # 1.95 x 0.6 m = 1.17 ㎡、自家用
        self.assertEqual(L["area_m2"], 1.17)
        self.assertEqual(L["municipality"], "豊島区")
        self.assertTrue(L["citations"])
        for c in L["citations"]:
            self.assertIn("quote_verified", c)
            self.assertTrue(c["article"] and c["quote"])
        for k in api.LEGAL_KEYS:
            self.assertIn(k, L)

    def test_summary_keeps_permit_required(self):
        _, signs, _ = self.req("/api/signs")
        self.assertTrue(all("permit_required" in s for s in signs))

    def test_no_size_needs_measure(self):
        from kanban_karte.ledger.db import Sign
        s = Sign(id="x", address="東京都豊島区", is_self_use=True)
        self.assertIsNone(api.sign_area_m2(s))
        L = api.sign_legal(s, None)
        self.assertEqual(L["permit_required"], "要実測")

    def test_legal_error_is_caught(self):
        orig = api.legal_judge
        api.legal_judge = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("壊れた"))
        try:
            L = api.legal_judgement("東京都豊島区", None, "self", 1.0)
        finally:
            api.legal_judge = orig
        self.assertIn("RuntimeError", L["error"])


if __name__ == "__main__":
    unittest.main()
