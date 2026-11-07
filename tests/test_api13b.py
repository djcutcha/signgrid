"""UI v3 の API 不足 4 点: /api/meta の company、/api/sites の next_inspection・permit_expires、進み具合の転記の件数、看板の from_paper。"""
import os
import unittest
from unittest import mock

from kanban_karte.ledger.db import Sign, Site
from kanban_karte.web import api
from tests.test_ui_v3 import Server


class Api13bTest(Server, unittest.TestCase):
    def setUp(self):
        self.setUp_server(sample=False)

    def tearDown(self):
        self.tearDown_server()

    def test_meta_company_default_and_env(self):
        env = {k: v for k, v in os.environ.items() if k != "KANBAN_KARTE_COMPANY"}
        with mock.patch.dict(os.environ, env, clear=True), mock.patch("kanban_karte.eval.claude_judge.load_dotenv", lambda *a, **k: None):
            self.assertEqual(self.req("/api/meta")[1]["company"], "株式会社エフ広芸")
        with mock.patch.dict(os.environ, {"KANBAN_KARTE_COMPANY": " 見本工業株式会社 "}):
            self.assertEqual(self.req("/api/meta")[1]["company"], "見本工業株式会社")
        with mock.patch.dict(os.environ, {"KANBAN_KARTE_COMPANY": "  "}):
            self.assertEqual(self.req("/api/meta")[1]["company"], "株式会社エフ広芸")

    def test_sites_have_min_deadlines(self):
        with self.app.ledger() as lg:
            lg.upsert_site(Site("a", "A"))
            lg.upsert_site(Site("b", "B"))
            lg.upsert_sign(Sign(id="a1", site_id="a", next_inspection="2026-12-10", permit_expires="2027-01-31"))
            lg.upsert_sign(Sign(id="a2", site_id="a", next_inspection="2026-11-20", permit_expires=None))
            lg.upsert_sign(Sign(id="a3", site_id="a"))
            lg.upsert_sign(Sign(id="b1", site_id="b"))
        got = {s["id"]: s for s in self.req("/api/sites")[1]}
        self.assertEqual((got["a"]["next_inspection"], got["a"]["permit_expires"]), ("2026-11-20", "2027-01-31"))   # 配下の看板の最小
        self.assertEqual((got["b"]["next_inspection"], got["b"]["permit_expires"]), (None, None))

    def test_progress_transcribe_counts_paper_pages(self):
        with self.app.ledger() as lg:
            bid = lg.add_bundle("upload://x", "sha-paper", 3)
            lg.upsert_page(bid, 1, kind="点検表")
            lg.upsert_page(bid, 2, kind="完成報告書")
            lg.upsert_page(bid, 3, kind="写真")
        t = lambda: next(s for s in self.req(f"/api/intake/{bid}/progress")[1]["stages"] if s["key"] == "transcribe")   # noqa: E731
        self.assertEqual((t()["done"], t()["total"]), (0, 2))        # 紙の頁 2 枚。看板の枠はまだ
        with self.app.ledger() as lg:
            lg.upsert_sign(Sign(id="b-s1", source_bundle=bid))
        self.assertEqual((t()["done"], t()["total"]), (2, 2))        # 枠を切り出した後は全頁を済みに数える
        with self.app.ledger() as lg:
            pb = lg.add_bundle("upload://p", "sha-photos", 1)
            lg.upsert_page(pb, 1, kind="写真")
        s = next(s for s in self.req(f"/api/intake/{pb}/progress")[1]["stages"] if s["key"] == "transcribe")
        self.assertEqual((s["done"], s["total"]), (None, None))      # 写真だけの束は転記する物が無い

    def test_sign_from_paper(self):
        with self.app.ledger() as lg:
            pb = lg.add_bundle("upload://paper", "sha-paper2", 1)
            lg.upsert_page(pb, 1, kind="完成報告書")
            hb = lg.add_bundle("upload://photos", "sha-photos2", 1)
            lg.upsert_page(hb, 1, kind="写真")
            lg.upsert_sign(Sign(id="cut", source_bundle=hb, photos=["cells/000009/b9-s001/c01.jpg"]))   # 紙の頁から切り出した写真
            lg.upsert_sign(Sign(id="rep", source_bundle=pb))                                             # 元の束に紙の頁がある
            lg.upsert_sign(Sign(id="photo", source_bundle=hb, photos=["pages/000002/p-001.jpg"]))
            lg.upsert_sign(Sign(id="none"))
        got = {i: self.req(f"/api/signs/{i}")[1]["from_paper"] for i in ("cut", "rep", "photo", "none")}
        self.assertEqual(got, {"cut": True, "rep": True, "photo": False, "none": False})


if __name__ == "__main__":
    unittest.main()
