"""画面 v2 の API: 看板の法規(根拠条文)、行政の一括登録、オーナーの1枚撮り判定。見本 DB だけ使い、外部 API は呼ばない。"""
import tempfile
import unittest
from pathlib import Path

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

    def test_place_in_sample(self):
        L = self.req("/api/signs/demo-001")[1]["legal"]
        self.assertEqual(L["place"]["status"], "sample")
        self.assertEqual(L["place"]["zone"], "商業地域")
        self.assertIn("国土交通省", L["place"]["source"])

    def test_place_from_placer_and_fallback(self):
        from kanban_karte.impact.place import Placed

        class P:
            def __init__(self, placed):
                self.placed = placed

            def place(self, address, muni):
                return self.placed

        L = api.legal_judgement("東京都新宿区西新宿2-8-1", None, "self", 7.0, placer=P(Placed("zone", "商業地域")))
        self.assertEqual((L["place"]["status"], L["place"]["zone"], L["permit_required"]), ("zone", "商業地域", "不要"))
        L = api.legal_judgement("東京都新宿区西新宿2-8-1", None, "self", 7.0, placer=P(Placed("not_found")))
        self.assertEqual((L["place"]["status"], L["place"]["zone"], L["permit_required"]), ("not_found", None, "要実測"))
        L = api.legal_judgement("東京都新宿区西新宿2-8-1", None, "self", 7.0)
        self.assertEqual(L["place"]["status"], "none")

    def test_height_put_and_pass_to_judge(self):
        put = lambda body, sid="demo-001": self.req(f"/api/signs/{sid}/height", body, method="PUT")
        self.assertEqual(self.req("/api/signs/demo-001")[1]["height_m"], None)
        self.assertEqual(put({"height_m": 4.5})[0], 400)                     # by が要る
        st, d, _ = put({"height_m": 4.5, "by": "テスト"})
        self.assertEqual((st, d["height_m"]), (200, 4.5))
        self.assertEqual(self.req("/api/signs/demo-001")[1]["height_m"], 4.5)
        for bad in ("x", 0, -1, 100, True):
            self.assertEqual(put({"height_m": bad, "by": "t"})[0], 400, bad)
        self.assertEqual(put({"height_m": 1, "by": "t"}, "nope")[0], 404)
        self.assertEqual(self.req("/api/signs/demo-001/height", {"height_m": 1, "by": "t"})[0], 405)   # POST は不可
        self.assertEqual(self.req("/api/owner/judge", {"address": "東京都豊島区", "area_m2": 1}, method="PUT")[0], 405)
        self.assertIsNone(put({"height_m": None, "by": "テスト"})[1]["height_m"])          # 消せる

    def test_sign_legal_passes_height(self):
        from kanban_karte.ledger.db import Sign
        seen = {}
        orig = api.judge_at
        api.judge_at = lambda *a, **k: seen.update(k) or orig(*a, **k)
        try:
            api.sign_legal(Sign(id="x", address="東京都豊島区", is_self_use=True, size_w=1000, size_h=1000, height_m=3.2), None)
        finally:
            api.judge_at = orig
        self.assertEqual(seen["height_m"], 3.2)

    def test_legal_error_is_caught(self):
        orig = api.judge_at
        api.judge_at = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("壊れた"))
        try:
            L = api.legal_judgement("東京都豊島区", None, "self", 1.0)
        finally:
            api.judge_at = orig
        self.assertIn("RuntimeError", L["error"])


class BundlesTest(WebTest):
    def test_bundle_source_kind(self):
        with self.app.ledger() as lg:
            a = lg.add_bundle("/tmp/scan/a.pdf", "sha-a", 3)
            b = lg.add_bundle("line://Uxxx/2026-11-02/s1", "sha-b", 2)
        rows = {r["id"]: r for r in self.req("/api/bundles")[1]}
        self.assertEqual(rows[a]["source"], "PC")
        self.assertEqual(rows[b]["source"], "LINE")
        self.assertEqual(rows[b]["file"], "s1")
        self.assertEqual(rows[b]["status"], "new")


class GovTest(WebTest):
    def test_no_gov_tables(self):
        st, d, _ = self.req("/api/gov/batches")
        self.assertEqual(st, 200)
        self.assertFalse(d["available"])
        self.assertEqual(d["batches"], [])
        with self.app.ledger() as lg:  # 開いただけでは表を作らない
            self.assertFalse(api._has_gov(lg))
        self.assertEqual(self.req("/api/gov/candidates?batch=1")[0], 404)
        self.assertEqual(self.req("/api/gov/map.html?batch=1", raw=True)[0], 404)
        self.assertEqual(self.req("/api/gov/candidates")[0], 400)


class GovSampleTest(WebTest):
    TODAY = "2026-11-02"

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from kanban_karte.gov import make_sample
        from kanban_karte.gov.batch_intake import intake
        cls.gov_dir = tempfile.TemporaryDirectory()
        make_sample.build(Path(cls.gov_dir.name))
        with cls.app.ledger() as lg:
            lg.import_json(Path(cls.gov_dir.name) / "ledger_gov.json")
            intake(lg.conn, Path(cls.gov_dir.name) / "photos")

    @classmethod
    def tearDownClass(cls):
        cls.gov_dir.cleanup()
        super().tearDownClass()

    def test_batches(self):
        st, d, _ = self.req(f"/api/gov/batches?today={self.TODAY}")
        self.assertTrue(d["available"])
        self.assertEqual(len(d["batches"]), 1)
        b = d["batches"][0]
        self.assertEqual(b["photo_count"], 10)
        self.assertEqual(b["candidate_count"], 6)          # 位置あり5基 + 位置なし写真1枚
        self.assertEqual(b["by_color"], {"red": 2, "orange": 2, "green": 1, "gray": 1})
        self.assertEqual(set(d["colors"]), {"red", "orange", "green", "gray"})

    def test_candidates_sorted_red_first(self):
        st, d, _ = self.req(f"/api/gov/candidates?batch=1&today={self.TODAY}")
        cs = d["candidates"]
        self.assertEqual([c["color"] for c in cs], ["red", "red", "orange", "orange", "green", "gray"])
        unreg = [c for c in cs if c["status"] == "unregistered"][0]
        self.assertIsNone(unreg["sign"])
        self.assertEqual(unreg["photo_count"], 2)
        ok = [c for c in cs if c["status"] == "permitted"][0]
        self.assertEqual(ok["sign"]["id"], "gov-001")
        self.assertEqual(ok["permit_expires"], "2027-09-30")
        self.assertEqual(ok["photo_count"], 3)              # 同じ看板を3枚束ねた
        gray = cs[-1]
        self.assertFalse(gray["has_position"])

    def test_map_html(self):
        st, body, h = self.req(f"/api/gov/map.html?batch=1&today={self.TODAY}", raw=True)
        self.assertEqual(st, 200)
        self.assertIn("text/html", h["Content-Type"])
        html = body.decode()
        self.assertIn("cyberjapandata.gsi.go.jp", html)
        self.assertIn("地理院タイル", html)
        self.assertNotIn("openstreetmap", html.lower())
        self.assertEqual(self.req("/api/gov/map.html?batch=99", raw=True)[0], 404)

    def test_bad_today(self):
        self.assertEqual(self.req("/api/gov/batches?today=xx")[0], 400)


class OwnerTest(WebTest):
    def post(self, body):
        return self.req("/api/owner/judge", body)

    def test_exempt(self):
        st, d, _ = self.post({"address": "東京都豊島区見本町1-2-3", "area_m2": 3.5, "height_m": 4})
        self.assertEqual(st, 200)
        self.assertEqual(d["permit_required"], "不要")
        self.assertTrue(d["is_self_use_exempt"])
        self.assertEqual(d["use"], "self")
        self.assertTrue(d["citations"])
        for banned in ("rank", "items", "draft_rank"):     # 安全の判定は出さない
            self.assertNotIn(banned, d)

    def test_owner_has_place(self):
        d = self.post({"address": "東京都新宿区西新宿2-8-1", "area_m2": 7})[1]
        self.assertEqual(d["place"]["zone"], "商業地域")
        self.assertEqual(d["permit_required"], "不要")     # 見本は商業地域(5㎡超10㎡以下でも区分があれば決まる)

    def test_over_10m2_needs_permit(self):
        d = self.post({"address": "東京都豊島区", "area_m2": 12})[1]
        self.assertEqual(d["permit_required"], "要")

    def test_unknown_address(self):
        d = self.post({"address": "大阪府大阪市", "area_m2": 3})[1]
        self.assertEqual(d["permit_required"], "要実測")
        self.assertTrue(d["notes"])

    def test_bad_input(self):
        self.assertEqual(self.post({"area_m2": 3})[0], 400)
        self.assertEqual(self.post({"address": "東京都豊島区"})[0], 400)
        self.assertEqual(self.post({"address": "東京都豊島区", "area_m2": "abc"})[0], 400)
        self.assertEqual(self.post({"address": "東京都豊島区", "area_m2": -1})[0], 400)

    def test_does_not_write_db(self):
        with self.app.ledger() as lg:
            before = [tuple(r) for r in lg.conn.execute("SELECT id, status, rank FROM signs ORDER BY id")]
        self.post({"address": "東京都豊島区", "area_m2": 3})
        with self.app.ledger() as lg:
            self.assertEqual(before, [tuple(r) for r in lg.conn.execute("SELECT id, status, rank FROM signs ORDER BY id")])


class OwnerEstimateTest(WebTest):
    """寸法の推定(偽の推定役。API は呼ばない)。写真は保存しない・台帳に書かない。"""
    @classmethod
    def photo_b64(cls):
        import base64
        import io
        from PIL import Image
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (200, 200, 200)).save(buf, "JPEG")
        return base64.b64encode(buf.getvalue()).decode()

    def setUp(self):
        from kanban_karte.dim.estimator import DimEstimate
        self.seen = []

        class Fake:
            def estimate(s, photos, name):
                self.seen.append((len(photos), name))
                return DimEstimate(3200, 900, (2800, 3600), (800, 1000), [{"name": "ドア", "assumed_mm": 2000, "how": "扉"}],
                                   False, "mid", "板面のみ", "fake", {"input_tokens": 1, "output_tokens": 1})
        self.app.estimator_factory = Fake

    def tearDown(self):
        self.app.estimator_factory = None

    def post(self, body):
        return self.req("/api/owner/estimate", body)

    def test_estimate_and_judge(self):
        st, d, _ = self.post({"address": "東京都豊島区見本町1-2-3", "photos": [self.photo_b64(), self.photo_b64()], "target_name": "欄間看板"})
        self.assertEqual(st, 200)
        self.assertEqual(self.seen, [(2, "欄間看板")])
        self.assertEqual(d["estimate"]["w_range"], [2800, 3600])
        j = d["judgement"]
        self.assertEqual((j["area_low"], j["area_high"], j["permit"]), (2.24, 3.6, "不要"))  # 自家用 10㎡以内
        self.assertTrue(j["citations"])
        for banned in ("rank", "items", "draft_rank"):
            self.assertNotIn(banned, d)

    def test_straddling_10m2_is_needs_measure(self):
        from kanban_karte.dim.estimator import DimEstimate
        self.app.estimator_factory = lambda: type("F", (), {"estimate": lambda s, p, n: DimEstimate(
            3000, 3000, (2500, 3600), (2500, 3600), [], False, "low", "", "fake", {})})()
        d = self.post({"address": "東京都豊島区", "photos": [self.photo_b64()]})[1]  # 6.25〜12.96 ㎡
        self.assertEqual(d["judgement"]["permit"], "要実測")

    def test_bad_input(self):
        ok = self.photo_b64()
        self.assertEqual(self.post({"photos": [ok]})[0], 400)                                   # 住所なし
        self.assertEqual(self.post({"address": "東京都豊島区"})[0], 400)                         # 写真なし
        self.assertEqual(self.post({"address": "東京都豊島区", "photos": []})[0], 400)
        self.assertEqual(self.post({"address": "東京都豊島区", "photos": [ok] * 3})[0], 400)    # 3枚
        self.assertEqual(self.post({"address": "東京都豊島区", "photos": ["***"]})[0], 400)      # base64 でない
        self.assertEqual(self.seen, [])

    def test_estimator_error_is_502(self):
        from kanban_karte.dim.estimator import DimEstimateError

        def boom():
            raise DimEstimateError("壊れた")
        self.app.estimator_factory = lambda: type("F", (), {"estimate": lambda s, p, n: boom()})()
        self.assertEqual(self.post({"address": "東京都豊島区", "photos": [self.photo_b64()]})[0], 502)

    def test_no_key_is_503(self):
        from unittest import mock
        self.app.estimator_factory = None
        with mock.patch.object(api, "can_draft", return_value=False):
            st, d, _ = self.post({"address": "東京都豊島区", "photos": [self.photo_b64()]})
        self.assertEqual(st, 503)
        self.assertIn("ANTHROPIC_API_KEY", d["error"])

    def test_meta_has_can_estimate_and_no_db_write(self):
        self.assertIn("can_estimate", self.req("/api/meta")[1])
        with self.app.ledger() as lg:
            before = [tuple(r) for r in lg.conn.execute("SELECT id, status, rank FROM signs ORDER BY id")]
        self.post({"address": "東京都豊島区", "photos": [self.photo_b64()]})
        with self.app.ledger() as lg:
            self.assertEqual(before, [tuple(r) for r in lg.conn.execute("SELECT id, status, rank FROM signs ORDER BY id")])


if __name__ == "__main__":
    unittest.main()
