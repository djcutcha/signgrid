"""現場ウィザードの送信(/api/field/photo, /api/field/submit): 写真を 1 枚ずつ置き、看板 1 基を下書きとして台帳に入れる。
Claude API は呼ばない。"""
import unittest

from kanban_karte.web import api
from tests.test_ui_v3 import FakeJudge, Server, b64, png_bytes


def photo(sid, slot, n=1, color=0):
    return {"submission_id": sid, "slot": slot, "n": n, "name": "x.jpg", "data": b64(png_bytes(color))}


SID = "0123abcd-4567-89ef-0123-456789abcdef"
PARTS = ["基礎部・上部構造", "支持部", "取付部"]


def submit_body(**kw):
    b = {"submission_id": SID, "kind": "wall", "address": "埼玉県越谷市越ヶ谷一丁目", "municipality": "越谷市", "lat": 35.889, "lng": 139.79,
         "photos": [{"slot": "全景", "n": 1}, {"slot": "正面", "n": 1}, {"slot": "取付部", "n": 1}, {"slot": "取付部", "n": 2}],
         "size": {"w": 3600, "h": 900, "depth": 250, "gl_m": 2.8, "by": "human"},
         "items": [{"key": "取付部-1", "result": "要改善", "reason": "アンカーに赤さび", "photo_no": 2, "by": "ai"},
                   {"key": "取付部-2", "result": "経過観察", "reason": "コーキングが切れている", "photo_no": 1, "by": "human"},
                   {"key": "取付部-3", "result": "判断できず", "reason": "取付面が写っていません", "by": "ai"}],
         "notes": "さびあり"}
    b.update(kw)
    return b


class FieldSubmitTest(Server, unittest.TestCase):
    def setUp(self):
        self.setUp_server(sample=False, judge_factory=lambda rules: FakeJudge())

    def tearDown(self):
        self.tearDown_server()

    def send_photos(self):
        for slot, n, c in (("全景", 1, 1), ("正面", 1, 2), ("取付部", 1, 3), ("取付部", 2, 4)):
            st, d = self.req("/api/field/photo", photo(SID, slot, n, c))
            self.assertEqual((st, d["existed"]), (200, False), d)

    def test_photo_dedup_and_retake(self):
        st, d = self.req("/api/field/photo", photo(SID, "全景", 1, 1))
        self.assertEqual((st, d["existed"]), (200, False))
        self.assertTrue((self.data / "field" / SID).is_dir())          # 置き場は data_dir の下
        self.assertTrue(self.req("/api/field/photo", photo(SID, "全景", 1, 1))[1]["existed"])          # 同じ内容は重複させない
        st, d = self.req("/api/field/photo", photo(SID, "全景", 1, 9))                                  # 別の内容 = 撮り直し(置き換え)
        self.assertEqual((st, d["existed"]), (200, False))
        self.assertEqual(len(list((self.data / "field" / SID).glob("全景__1.*"))), 1)

    def test_photo_validation(self):
        for bad in (dict(slot="屋根"), dict(n=3), dict(submission_id="x"), dict(data="!!!")):
            self.assertEqual(self.req("/api/field/photo", {**photo(SID, "取付部"), **bad})[0], 400, bad)
        self.assertEqual(self.req("/api/field/photo", {**photo(SID, "取付部"), "data": b64(b"not an image")})[0], 400)

    def test_missing_photos_409(self):
        self.req("/api/field/photo", photo(SID, "全景", 1, 1))
        st, d = self.req("/api/field/submit", submit_body())
        self.assertEqual(st, 409)
        self.assertEqual(d["missing"], [{"slot": "正面", "n": 1}, {"slot": "取付部", "n": 1}, {"slot": "取付部", "n": 2}])
        self.assertIn("正面", d["error"])
        with self.app.ledger() as lg:
            self.assertEqual(lg.list_signs(), [])                       # 何も作らない

    def test_submit_creates_draft_and_h_count(self):
        self.send_photos()
        st, d = self.req("/api/field/submit", submit_body())
        self.assertEqual(st, 200, d)
        self.assertEqual((d["sign_id"], d["status"], d["existed"], d["site_created"], d["photos"], d["items"]), ("fw-0123abcd", "draft", False, True, 4, 2))
        with self.app.ledger() as lg:
            s = lg.get_sign(d["sign_id"])
            self.assertEqual((s.status, s.type, s.rank, s.draft_rank), ("draft", "壁面", None, None))
            self.assertTrue(s.draft_by.startswith("field_wizard"))
            self.assertEqual((s.size_w, s.size_h, s.size_d, s.gl_mm, s.height_m, s.size_source), (3600, 900, 250, 2800, 3.7, "measured"))
            self.assertEqual(s.items, {"取付部-1": "要改善", "取付部-2": "経過観察"})   # 判断できず は items に入れない
            self.assertEqual(s.evidence["取付部-1"]["photos"], [4])      # 看板の写真番号(全景1・正面2・取付部 3,4)
            self.assertEqual(s.evidence["取付部-1"]["text"], "アンカーに赤さび")
            self.assertEqual(s.evidence["取付部-3"]["guide"], "unknown")  # AI 判定。束の下書きが後で埋める
            self.assertEqual(s.evidence["取付部-2"]["field"], "human")
            self.assertEqual(len(s.photos), 4)
            tags = [r["tag"] for r in lg.conn.execute("SELECT tag FROM pages ORDER BY page_no")]
            self.assertEqual(tags, ["overview", "正面", "取付部", "取付部"])
            self.assertEqual(lg.get_bundle(d["bundle_id"])["site_id"], d["site_id"])
            site = next(x for x in api.sites(lg) if x["id"] == d["site_id"])
            self.assertEqual((site["draft_count"], site["sign_count"]), (1, 1))        # H の確認待ちに載る
            self.assertTrue(lg.resolve_path(s.photos[0]).exists())
        self.assertEqual(self.req("/api/sites")[1][0]["draft_count"], 1)
        self.assertEqual(self.req(f"/api/signs/{d['sign_id']}")[1]["status"], "draft")

    def test_submit_twice_is_idempotent_and_same_address_same_site(self):
        self.send_photos()
        a = self.req("/api/field/submit", submit_body())[1]
        b = self.req("/api/field/submit", submit_body())[1]
        self.assertEqual((b["existed"], b["sign_id"], b["site_id"]), (True, a["sign_id"], a["site_id"]))
        sid2 = "ffff0000-4567-89ef-0123-456789abcdef"
        for slot, n, c in (("全景", 1, 5), ("正面", 1, 6)):
            self.req("/api/field/photo", photo(sid2, slot, n, c))
        c = self.req("/api/field/submit", submit_body(submission_id=sid2, items=[], size=None,
                                                      photos=[{"slot": "全景", "n": 1}, {"slot": "正面", "n": 1}]))[1]
        self.assertEqual((c["site_created"], c["site_id"]), (False, a["site_id"]))     # 住所が同じ既存の現場
        with self.app.ledger() as lg:
            self.assertEqual(len(lg.list_signs()), 2)
            self.assertEqual(lg.get_sign(c["sign_id"]).size_w, None)

    def test_submit_validation(self):
        self.send_photos()
        for bad in (dict(address=""), dict(kind="ufo"), dict(photos=[]),
                    dict(items=[{"key": "取付部-9", "result": "良好"}]),
                    dict(items=[{"key": "取付部-1", "result": "最高"}]),
                    dict(items=[{"key": "取付部-1", "result": "良好", "photo_no": 5}]),
                    dict(size={"w": -1, "h": 900})):
            self.assertEqual(self.req("/api/field/submit", submit_body(**bad))[0], 400, bad)

    def test_bundle_draft_keeps_field_answers(self):
        # 束の下書きは、現場の答えのある項目を残す(ガイド付き点検と同じ)
        self.send_photos()
        d = self.req("/api/field/submit", submit_body())[1]
        st, out = self.req(f"/api/bundles/{d['bundle_id']}/draft", {})
        self.assertEqual(st, 200, out)
        self.assertTrue(out["only_missing"])


if __name__ == "__main__":
    unittest.main()
