"""市民からの報告(/report/): 受付 → 保存 → 地図の層 → 状態の確認。写真は公開しない・EXIF の位置は使わない・送信の上限。"""
import base64
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from kanban_karte.citizen import BRAND
from kanban_karte.citizen import store as CS
from kanban_karte.ledger.db import Sign
from kanban_karte.web import citizen as WC
from tests.test_web import WebTest


def jpeg(gps=None, size=(320, 240)) -> bytes:
    im = Image.new("RGB", size, (180, 190, 200))
    ex = Image.Exif()
    if gps:
        from kanban_karte.gov.make_sample import _gps_ifd
        ex.get_ifd(0x8825).update(_gps_ifd(*gps))
        ex[0x0110] = "TestPhone"   # 機種
    buf = io.BytesIO()
    im.save(buf, "JPEG", exif=ex)
    return buf.getvalue()


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


SIGN_A = Sign(id="gov-001", name="見本商店", address="埼玉県越谷市見本1-1", lat=35.891090, lng=139.790850, last_inspected=None)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.st = CS.Store(Path(self.td.name) / "c.sqlite")

    def tearDown(self):
        self.td.cleanup()

    def test_add_rounds_and_strips_exif(self):
        r = self.st.add(jpeg(gps=(35.0, 139.0)), lat=35.891061, lng=139.790871, note="揺れていた", signs=["錆"], ledger_signs=[SIGN_A])
        self.assertRegex(r.code, r"^[A-Z0-9]{4}-[A-Z0-9]{4}$")
        self.assertTrue(set(r.code.replace("-", "")) <= set(CS.CODE_ALPHABET))
        self.assertEqual((r.lat_pub, r.lng_pub), (35.891, 139.791))
        self.assertEqual((r.status, r.matched_sign_id), (CS.KNOWN, "gov-001"))
        saved = Image.open(self.st.photo_dir / r.photo_path)
        self.assertEqual(saved.format, "JPEG")
        self.assertEqual(len(saved.getexif()), 0)          # 位置・機種は残らない
        self.assertNotIn(b"TestPhone", (self.st.photo_dir / r.photo_path).read_bytes())

    def test_exif_position_is_not_used(self):
        # 写真の EXIF は台帳の看板の位置、ブラウザの位置は遠く -> 未把握(EXIF で一致させない)
        r = self.st.add(jpeg(gps=(SIGN_A.lat, SIGN_A.lng)), lat=35.80, lng=139.70, ledger_signs=[SIGN_A])
        self.assertEqual(r.status, CS.UNKNOWN)
        with self.assertRaises(CS.ReportError):   # ブラウザの位置も住所も無ければ、EXIF に位置があっても受け付けない
            self.st.add(jpeg(gps=(SIGN_A.lat, SIGN_A.lng)))

    def test_statuses(self):
        self.assertEqual(self.st.add(jpeg(), address="どこか", ledger_signs=[SIGN_A]).status, CS.RECEIVED)
        self.assertEqual(self.st.add(jpeg(), address="埼玉県越谷市 見本1-1", ledger_signs=[SIGN_A]).status, CS.KNOWN)   # 住所の一致(空白は無視)
        inspected = Sign(id="x", lat=35.0, lng=139.0, last_inspected="2026-11-05")
        r = self.st.add(jpeg(), lat=35.0, lng=139.0, ledger_signs=[inspected], created_at="2026-11-04T10:00:00")
        self.assertEqual(r.status, CS.INSPECTED)
        r = self.st.add(jpeg(), lat=35.0, lng=139.0, ledger_signs=[inspected], created_at="2026-11-06T10:00:00")
        self.assertEqual(r.status, CS.KNOWN)       # 報告より前の点検は「点検済み」にしない

    def test_rematch(self):
        r = self.st.add(jpeg(), lat=SIGN_A.lat, lng=SIGN_A.lng, ledger_signs=[])
        self.assertEqual(r.status, CS.UNKNOWN)
        self.assertEqual(self.st.rematch([SIGN_A]), 1)
        self.assertEqual(self.st.by_code(r.code).status, CS.KNOWN)
        self.assertEqual(self.st.rematch([SIGN_A]), 0)

    def test_bad_inputs(self):
        for kw in ({"lat": 35.0}, {"lat": 200, "lng": 1}, {"lat": "x", "lng": 1}, {"address": "a", "signs": ["爆発"]}, {"address": "a", "signs": "錆"}):
            with self.subTest(kw=kw), self.assertRaises(CS.ReportError):
                self.st.add(jpeg(), **kw)
        for bad in (b"", b"not an image", b"x" * (CS.MAX_PHOTO_BYTES + 1)):
            with self.assertRaises(CS.ReportError):
                self.st.add(bad, address="a")
        gif = io.BytesIO()
        Image.new("RGB", (10, 10)).save(gif, "GIF")
        with self.assertRaises(CS.ReportError):
            self.st.add(gif.getvalue(), address="a")

    def test_long_text_is_cut_and_big_photo_shrunk(self):
        r = self.st.add(jpeg(size=(4000, 3000)), address="a" * 500, note="n" * 500)
        self.assertEqual((len(r.address), len(r.note)), (CS.MAX_ADDRESS, CS.MAX_NOTE))
        self.assertEqual(max(Image.open(self.st.photo_dir / r.photo_path).size), CS.LONG_EDGE)

    def test_public_and_pin_hide_private_fields(self):
        r = self.st.add(jpeg(), lat=35.891061, lng=139.790871, address="自宅の前", note="秘密の一言")
        pub = json.dumps(r.public(), ensure_ascii=False) + json.dumps(self.st.summary(), ensure_ascii=False)
        for secret in ("秘密の一言", "自宅の前", r.photo_path, "35.891061"):
            self.assertNotIn(secret, pub)
        self.assertEqual(self.st.summary()["pins"][0], {"lat": 35.891, "lng": 139.791, "status": CS.UNKNOWN, "date": r.created_at[:10]})

    def test_code_lookup(self):
        r = self.st.add(jpeg(), address="a")
        self.assertEqual(self.st.by_code(r.code.lower().replace("-", " ")).id, r.id)
        self.assertIsNone(self.st.by_code("ZZZZ-ZZZZ"))
        self.assertIsNone(self.st.by_code("短い"))

    def test_rate_limit(self):
        t = [0.0]
        rl = WC.RateLimit(limit=3, window=3600, clock=lambda: t[0])
        self.assertEqual([rl.allow("a") for _ in range(4)], [True, True, True, False])
        self.assertTrue(rl.allow("b"))          # IP ごと
        t[0] = 3600
        self.assertTrue(rl.allow("a"))          # 1 時間で戻る

    def test_sample_tool(self):
        import tools.citizen_sample as T
        from kanban_karte.gov.make_sample import LEDGER
        signs = [Sign(**{k: v for k, v in s.items() if k in Sign.__dataclass_fields__}) for s in LEDGER["signs"]]
        rs = T.seed(self.st, signs)
        self.assertEqual(len(rs), 5)
        self.assertEqual([r.status for r in rs], [CS.KNOWN, CS.UNKNOWN, CS.INSPECTED, CS.KNOWN, CS.RECEIVED])


class ReportWebTest(WebTest):
    """ログイン不要の入口。見本(--sample)では報告の保存先は台帳の隣(data/private を汚さない)。"""

    def setUp(self):
        self.app._citizen = CS.Store(Path(self.td.name) / f"c_{self._testMethodName}.sqlite")
        self.app._citizen_rate = WC.RateLimit()

    def post(self, body, headers=None):
        import urllib.error
        import urllib.request
        r = urllib.request.Request(self.base + "/api/report", data=json.dumps(body).encode(),
                                   headers={"Content-Type": "application/json", **(headers or {})})
        try:
            with urllib.request.urlopen(r) as resp:
                return resp.status, json.loads(resp.read())
        except urllib.error.HTTPError as e:
            b = e.read(); e.close()
            return e.code, json.loads(b)

    def test_pages(self):
        for p in ("/report/", "/report/status"):
            st, body, h = self.req(p, raw=True)
            self.assertEqual(st, 200)
            self.assertIn('data-entry="report"', body.decode())
        import http.client
        c = http.client.HTTPConnection(self.base.split("//")[1])
        c.request("GET", "/report")
        r = c.getresponse()
        self.assertEqual((r.status, r.getheader("Location")), (308, "/report/"))
        c.close()
        st, d, _ = self.req("/api/report/meta")
        self.assertEqual(d["brand"], BRAND)
        self.assertEqual(d["signs"], list(CS.SIGNS))
        js = (Path(WC.STATIC) / "report.js").read_text(encoding="utf-8")
        for s in ("撮影後に処理", "写真は公開されません", "/owner/?from=report", "/report/status?code=", "合言葉"):
            self.assertIn(s, js)
        self.assertNotIn("サインガーディアン", js)   # 呼び名は定数 1 か所(citizen/__init__.py)から受け取る

    def test_submit_then_status(self):
        st, d = self.post({"photo": b64(jpeg()), "address": "見本の住所(台帳に無い)", "note": "傾いている", "signs": ["傾き"]})
        self.assertEqual(st, 200)
        self.assertEqual(d["status"], CS.RECEIVED)       # 位置が無く、台帳の住所とも一致しない
        self.assertNotIn("note", d)
        st, s, _ = self.req(f"/api/report/status?code={d['code']}")
        self.assertEqual((st, s["status"], s["code"]), (200, CS.RECEIVED, d["code"]))
        self.assertEqual(set(s), {"code", "status", "text", "date"})   # 写真・一言・位置は返さない
        self.assertEqual(self.req("/api/report/status?code=ZZZZ-ZZZZ")[0], 404)
        row = sqlite3.connect(self.app._citizen.path).execute("SELECT note, signs, photo_path FROM citizen_reports").fetchone()
        self.assertEqual(row[0], "傾いている")
        self.assertTrue((self.app._citizen.photo_dir / row[2]).is_file())

    def test_bad_submit(self):
        for body in ({}, {"photo": "!!!", "address": "a"}, {"photo": b64(jpeg())}, {"photo": b64(b"xx"), "address": "a"}):
            self.assertEqual(self.post(body)[0], 400, body)

    def test_rate_limit_per_ip(self):
        self.app._citizen_rate = WC.RateLimit(limit=2)
        ok = {"photo": b64(jpeg()), "address": "a"}
        self.assertEqual([self.post(ok, {"X-Forwarded-For": "203.0.113.5"})[0] for _ in range(3)], [200, 200, 429])
        self.assertEqual(self.post(ok, {"CF-Connecting-IP": "198.51.100.7"})[0], 200)

    def test_ledger_db_untouched(self):
        with self.app.ledger() as lg:
            before = lg.conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0]
        self.post({"photo": b64(jpeg()), "address": "a"})
        with self.app.ledger() as lg:
            self.assertEqual(lg.conn.execute("SELECT count(*) FROM sqlite_master WHERE name LIKE 'citizen%'").fetchone()[0], 0)
            self.assertEqual(lg.conn.execute("SELECT count(*) FROM sqlite_master").fetchone()[0], before)


class CitizenGovLayerTest(WebTest):
    """/gov/ の地図に「市民からの報告」の層(ピンと件数だけ)。職員の巡回の束は今までどおり。"""
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
        cls.app._citizen = CS.Store(Path(cls.td.name) / "gov_c.sqlite")

    @classmethod
    def tearDownClass(cls):
        cls.gov_dir.cleanup()
        super().tearDownClass()

    def test_no_reports_no_layer(self):
        keep = self.app._citizen
        self.app._citizen = CS.Store(Path(self.td.name) / "gov_empty.sqlite")
        self.addCleanup(setattr, self.app, "_citizen", keep)
        st, body, _ = self.req(f"/api/gov/map.html?batch=1&today={self.TODAY}", raw=True)
        self.assertEqual(st, 200)
        self.assertNotIn("市民からの報告 <span", body.decode())
        self.assertEqual(self.req("/api/gov/citizen")[1]["total"], 0)

    def test_layer_after_reports(self):
        import tools.citizen_sample as T
        with self.app.ledger() as lg:
            signs = lg.list_signs()
        T.seed(self.app._citizen, signs)
        st, d, _ = self.req("/api/gov/citizen")
        self.assertEqual(st, 200)
        self.assertEqual(d["total"], 5)
        self.assertEqual(d["by_status"], {"受付": 1, "把握済み": 2, "未把握": 1, "点検済み": 1})
        self.assertEqual(len(d["pins"]), 4)
        self.assertEqual(set(d["pins"][0]), {"lat", "lng", "status", "date"})
        html = self.req(f"/api/gov/map.html?batch=1&today={self.TODAY}", raw=True)[1].decode()
        self.assertIn('市民からの報告 <span id="citizen-total">5</span> 件', html)
        self.assertIn('data-citizen="未把握">1', html)
        codes = [r.code for r in self.app._citizen.all()]
        for secret in ["支柱の根元", "揺れていました", "見本町の交差点", "35.89106"] + codes:   # 一言・住所・写真(受付番号.jpg)・丸める前の位置は地図に入れない
            self.assertNotIn(secret, html)
        self.assertIn('data-count="red">2', html)    # 職員の巡回の束はそのまま
        st, c, _ = self.req(f"/api/gov/candidates?batch=1&today={self.TODAY}")
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main()
