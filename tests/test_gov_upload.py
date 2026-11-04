"""行政のスマホ投入(POST /api/gov/upload): 検証、束ね、保存、位置の出どころ。PC 画面の DOM を変えていないことも静的に確かめる。通信しない(見本の geocache)。"""
import base64
import io
import re
import sqlite3
import unittest
from pathlib import Path

from PIL import Image

from kanban_karte.gov.batch_intake import GOV_SCHEMA
from kanban_karte.gov.upload import MAX_PHOTO_BYTES, MAX_UPLOAD_PHOTOS
from kanban_karte.web import api
from tests.test_web import WebTest

STATIC = Path(api.__file__).parent / "static"
# 見本の geocache にある地点(小数4桁)。越谷市役所の前
A = (35.8910, 139.7908)
TODAY = "2026-11-02"


def jpg(n: int = 0) -> str:
    """n ごとに中身の違う小さな画像(base64。JPEG は小さいと同じバイト列になるので可逆の PNG)。"""
    b = io.BytesIO()
    Image.new("RGB", (32, 24), (n % 256, (n // 256) % 256, 7)).save(b, "PNG")
    return base64.b64encode(b.getvalue()).decode()


def photo(n, pos=None, src=None, taken=None):
    p = {"data": jpg(n)}
    if pos:
        p["lat"], p["lng"] = pos
    if src:
        p["position_source"] = src
    if taken:
        p["taken_at"] = taken
    return p


class UploadTest(WebTest):
    def up(self, photos, **kw):
        return self.req("/api/gov/upload", {"photos": photos, **kw})

    def test_with_and_without_position(self):
        st, d, _ = self.up([photo(1, A, "exif", "2026-11-02T10:00:00"), photo(2, (A[0] + 0.00004, A[1]), "device", "2026-11-02T10:01:00"),
                            photo(3)], batch_name="朝の巡回")
        self.assertEqual(st, 200)
        self.assertEqual((d["candidates"], d["no_position"], d["photos"], d["skipped"]), (1, 1, 3, 0))
        self.assertEqual(d["position_sources"], {"exif": 1, "device": 1, "given": 0, "none": 1})
        self.assertEqual(d["geocode_pending"], 0)
        saved = sorted(p.name for p in (self.app.gov_store / str(d["batch_id"])).iterdir())
        self.assertEqual(saved, ["001.png", "002.png", "003.png"])
        b = [x for x in self.req(f"/api/gov/batches?today={TODAY}")[1]["batches"] if x["id"] == d["batch_id"]][0]
        self.assertEqual((b["name"], b["photo_count"], b["gps_count"], b["nogps_count"]), ("朝の巡回", 3, 2, 1))
        cs = self.req(f"/api/gov/candidates?batch={d['batch_id']}&today={TODAY}")[1]["candidates"]
        self.assertEqual(len(cs), 2)                                   # 位置あり1基 + 位置なし1枚
        self.assertEqual(sorted(c["photo_count"] for c in cs), [1, 2])
        self.assertIn("越谷市", [c["place"] for c in cs if c["has_position"]][0])  # 見本の geocache で市区町村を判定

    def test_bundling_counts(self):
        far = (35.8768, 139.8225)  # レイクタウン駅の近く(見本の geocache にある)
        st, d, _ = self.up([photo(10, A), photo(11, (A[0] + 0.00005, A[1])), photo(12, far), photo(13, (far[0], far[1] + 0.00005)),
                            photo(14, far)])
        self.assertEqual((st, d["candidates"], d["no_position"]), (200, 2, 0))

    def test_all_without_position_is_accepted(self):
        st, d, _ = self.up([photo(20), photo(21)])
        self.assertEqual((st, d["candidates"], d["no_position"]), (200, 0, 2))
        self.assertEqual(d["position_sources"]["none"], 2)

    def test_invalid_position_counts_as_none(self):
        st, d, _ = self.up([photo(30, (0, 0)), photo(31, (95, 10))])
        self.assertEqual((st, d["candidates"], d["no_position"]), (200, 0, 2))

    def test_same_content_different_place_is_kept_but_resend_is_refused(self):
        st, d, _ = self.up([{"data": jpg(40), "lat": A[0], "lng": A[1]}, {"data": jpg(40), "lat": 35.8768, "lng": 139.8225}])
        self.assertEqual((st, d["photos"]), (200, 2))
        st, d2, _ = self.up([{"data": jpg(40), "lat": A[0], "lng": A[1]}])
        self.assertEqual(st, 409)
        st, d3, _ = self.up([{"data": jpg(40), "lat": A[0], "lng": A[1]}, photo(41)])
        self.assertEqual((st, d3["photos"], d3["skipped"]), (200, 1, 1))

    def test_count_limits(self):
        self.assertEqual(self.up([])[0], 400)
        self.assertEqual(self.req("/api/gov/upload", {})[0], 400)
        self.assertEqual(self.up([photo(i) for i in range(MAX_UPLOAD_PHOTOS + 1)])[0], 400)
        st, d, _ = self.up([photo(100 + i, A) for i in range(MAX_UPLOAD_PHOTOS)])
        self.assertEqual((st, d["photos"]), (200, MAX_UPLOAD_PHOTOS))

    def test_bad_photos(self):
        self.assertEqual(self.up([{"data": "これは base64 ではない!!"}])[0], 400)
        self.assertEqual(self.up([{"data": ""}])[0], 400)
        self.assertEqual(self.up([{"data": 12}])[0], 400)
        self.assertEqual(self.up(["文字列"])[0], 400)
        text = base64.b64encode(b"just text, not an image").decode()
        st, d, _ = self.up([{"data": text}])
        self.assertEqual(st, 400)
        self.assertIn("画像", d["error"])

    def test_oversize_photo(self):
        big = base64.b64encode(b"\xff\xd8" + b"0" * MAX_PHOTO_BYTES).decode()   # 12MB を超える
        st, d, _ = self.up([{"data": big}])
        self.assertEqual(st, 400)
        self.assertIn("大きすぎ", d["error"])

    def test_bad_position_values(self):
        self.assertEqual(self.up([{"data": jpg(50), "lat": 35.0}])[0], 400)                 # 片方だけ
        self.assertEqual(self.up([{"data": jpg(51), "lat": "35.0", "lng": "139.0"}])[0], 400)  # 数値でない

    def test_nothing_saved_when_rejected(self):
        before = set(self.app.gov_store.iterdir()) if self.app.gov_store.exists() else set()
        self.up([photo(60, A), {"data": "!!"}])
        after = set(self.app.gov_store.iterdir()) if self.app.gov_store.exists() else set()
        self.assertEqual(before, after)

    def test_sample_geocoder_never_uses_network(self):
        # 見本の geocache に無い地点: 通信せず「未判定」として応答に出る(束は作る)
        st, d, _ = self.up([photo(70, (35.70, 139.70))])
        self.assertEqual((st, d["candidates"], d["geocode_pending"]), (200, 1, 1))


class OldSchemaTest(unittest.TestCase):
    def test_batches_without_name_column_still_listed(self):
        from kanban_karte.ledger import Ledger
        import tempfile
        with tempfile.TemporaryDirectory() as td, Ledger(Path(td) / "l.sqlite") as lg:
            lg.conn.executescript(GOV_SCHEMA)       # name 列が無い古い表
            lg.conn.execute("INSERT INTO gov_batches(dir) VALUES ('/x/y')")
            lg.conn.commit()
            self.assertIsNone(api.gov_batches(lg, TODAY)["batches"][0]["name"])


class PcDomUnchangedTest(unittest.TestCase):
    """ブラウザなしで確かめられる範囲: PC の並び(件数チップ → 地図 → 候補表)と、スマホ専用の CSS が @media の中だけにあること。"""

    def test_gov_view_order(self):
        js = (STATIC / "gov.js").read_text(encoding="utf-8")
        view = js[js.index("KK.views.gov"):]
        self.assertLess(view.index("countChips(data"), view.index('class: "card gov-map"'))
        self.assertLess(view.index('class: "card gov-map"'), view.index("candidatesTable(cand"))
        self.assertIn('class: "table-wrap"', js)
        self.assertIn('class: "ledger gov-table"', js)
        self.assertIn('class: "gov-chips"', js)
        self.assertIn("captureCard(tq)", view)           # 入口カードは見出し(page-head)の中。件数チップや表の並びには入らない

    def test_phone_only_rules_inside_media(self):
        css = (STATIC / "app.css").read_text(encoding="utf-8")
        i = css.index("@media (max-width:760px){\n  .gov-capture{width:100%}")
        pc = css[:i]
        self.assertNotIn("tr.cand{display", pc)
        self.assertIn(".gov-map iframe{width:100%;height:420px", pc)           # PC の地図の高さはそのまま
        self.assertRegex(css[i:], r"\.gov-map iframe\{height:72vh;min-height:520px\}")   # スマホは凡例の 4 色が地図の下に収まる高さ
        self.assertRegex(css[i:], r"\.gov-table tr\.cand\{display:grid")

    def test_capture_input_attributes(self):
        js = (STATIC / "gov.js").read_text(encoding="utf-8")
        for s in ('accept: "image/*"', 'capture: "environment"', "multiple: true", "navigator.geolocation.getCurrentPosition",
                  "/api/gov/upload"):
            self.assertIn(s, js)
        self.assertNotRegex(js, re.compile(r"https?://(?!maps\.gsi)"))   # 外部の部品は読まない


if __name__ == "__main__":
    unittest.main()
