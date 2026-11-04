import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import date
from pathlib import Path

from PIL import Image

from kanban_karte.gov import batch_intake as bi
from kanban_karte.gov import geocode as gc
from kanban_karte.gov import make_sample
from kanban_karte.gov import match as mt
from kanban_karte.gov.cli import main as cli_main
from kanban_karte.gov.map import render_map
from kanban_karte.ledger import Ledger, Sign

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "data" / "sample" / "gov"
TODAY = date(2026, 11, 2)


def photo(rel, lat, lng, taken=None):
    return bi.PhotoMeta("/x/" + rel, rel, rel, taken, lat, lng)


def offset(lat, lng, north_m=0.0, east_m=0.0):
    """北・東へ約 m ずらした座標(緯度1度=約111.2km、経度1度は cos で縮む)。"""
    import math
    return lat + north_m / 111195.0, lng + east_m / (111195.0 * math.cos(math.radians(lat)))


class ExifTest(unittest.TestCase):
    def setUp(self):
        self.d = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.d, True)

    def test_gps_and_time_are_read(self):
        p = self.d / "a.jpg"
        make_sample.write_jpeg(p, 35.891, 139.7908, "2026-11-02 10:02:10")
        taken, lat, lng = bi.read_exif(p)
        self.assertEqual(taken, "2026-11-02T10:02:10")
        self.assertAlmostEqual(lat, 35.891, places=5)
        self.assertAlmostEqual(lng, 139.7908, places=5)

    def test_no_gps_is_none(self):
        p = self.d / "n.jpg"
        make_sample.write_jpeg(p, None, None, "2026-11-02 10:00:00")
        taken, lat, lng = bi.read_exif(p)
        self.assertEqual(taken, "2026-11-02T10:00:00")
        self.assertIsNone(lat)
        self.assertIsNone(lng)

    def test_no_exif_at_all(self):
        p = self.d / "plain.jpg"
        Image.new("RGB", (8, 8)).save(p)
        self.assertEqual(bi.read_exif(p), (None, None, None))

    def test_south_west_refs_are_negative(self):
        p = self.d / "sw.jpg"
        make_sample.write_jpeg(p, -33.8688, -70.5, None)
        _, lat, lng = bi.read_exif(p)
        self.assertAlmostEqual(lat, -33.8688, places=4)
        self.assertAlmostEqual(lng, -70.5, places=4)

    def test_zero_zero_is_treated_as_no_position(self):
        # 測位前の GPS が (0,0) を書く。位置ありにしない
        p = self.d / "z.jpg"
        make_sample.write_jpeg(p, 0.0, 0.0, None)
        self.assertEqual(bi.read_exif(p)[1:], (None, None))
        self.assertFalse(bi.valid_position(91, 0))
        self.assertTrue(bi.valid_position(35.0, 139.0))

    def test_scan_reports_unreadable_and_ignores_non_images(self):
        make_sample.write_jpeg(self.d / "ok.jpg", 35.0, 139.0, None)
        (self.d / "broken.jpg").write_bytes(b"not a jpeg")
        (self.d / "memo.txt").write_text("x")
        res = bi.scan_folder(self.d)
        self.assertEqual([p.rel for p in res.photos], ["ok.jpg"])
        self.assertEqual([r for r, _ in res.unreadable], ["broken.jpg"])

    @unittest.skipIf(bi.HEIC_AVAILABLE, "pillow-heif が入っている環境では別の動き")
    def test_heic_without_plugin_is_reported_not_dropped(self):
        (self.d / "iphone.HEIC").write_bytes(b"\x00\x00\x00\x18ftypheic")
        res = bi.scan_folder(self.d)
        self.assertEqual(res.photos, [])
        self.assertEqual(res.unreadable[0][0], "iphone.HEIC")
        self.assertIn("pillow-heif", res.unreadable[0][1])


@unittest.skipUnless(bi.HEIC_AVAILABLE, "pillow-heif 未導入(requirements-gov.txt)")
class HeicTest(unittest.TestCase):
    def test_heic_gps_is_read(self):
        with tempfile.TemporaryDirectory() as d:
            ex = Image.Exif()
            ex.get_ifd(0x8769)[0x9003] = "2026:11:02 10:00:00"
            ex.get_ifd(0x8825).update(make_sample._gps_ifd(35.891, 139.7908))
            Image.new("RGB", (64, 48)).save(Path(d) / "a.heic", exif=ex)
            res = bi.scan_folder(d)
            self.assertEqual(res.unreadable, [])
            self.assertAlmostEqual(res.photos[0].lat, 35.891, places=5)


class BundleTest(unittest.TestCase):
    LAT, LNG = 35.891, 139.7908

    def test_haversine(self):
        a = offset(self.LAT, self.LNG, north_m=100)
        self.assertAlmostEqual(bi.haversine_m(self.LAT, self.LNG, *a), 100, delta=0.5)

    def test_within_radius_bundles_and_beyond_splits(self):
        near = offset(self.LAT, self.LNG, east_m=10)
        far = offset(self.LAT, self.LNG, east_m=20)
        photos = [photo("a", self.LAT, self.LNG, "2026-11-02T10:00:00"),
                  photo("b", *near, "2026-11-02T10:00:30"),
                  photo("c", *far, "2026-11-02T10:01:00")]
        cands, nopos = bi.bundle_photos(photos)
        self.assertEqual(nopos, [])
        self.assertEqual(sorted(len(c.photos) for c in cands), [1, 2])
        # 半径を広げれば全部1束
        cands, _ = bi.bundle_photos(photos, radius_m=30)
        self.assertEqual([len(c.photos) for c in cands], [3])

    def test_time_gap_splits_same_place(self):
        photos = [photo("a", self.LAT, self.LNG, "2026-11-02T10:00:00"),
                  photo("b", self.LAT, self.LNG, "2026-11-02T10:05:00"),   # 5分後: 同じ束
                  photo("c", self.LAT, self.LNG, "2026-11-02T11:00:00")]   # 55分後: 別の束
        cands, _ = bi.bundle_photos(photos)
        self.assertEqual(sorted(len(c.photos) for c in cands), [1, 2])

    def test_missing_time_uses_distance_only(self):
        photos = [photo("a", self.LAT, self.LNG, None), photo("b", self.LAT, self.LNG, "2026-11-02T10:00:00")]
        cands, _ = bi.bundle_photos(photos)
        self.assertEqual([len(c.photos) for c in cands], [2])

    def test_nearest_bundle_wins(self):
        # 15m 以内に束が2つあるとき、近い方に入る
        p1 = photo("p1", self.LAT, self.LNG, "2026-11-02T10:00:00")
        p2 = photo("p2", *offset(self.LAT, self.LNG, east_m=25), "2026-11-02T10:00:10")
        mid = photo("m", *offset(self.LAT, self.LNG, east_m=17), "2026-11-02T10:00:20")
        cands, _ = bi.bundle_photos([p1, p2, mid])
        sizes = {c.photos[0].rel: len(c.photos) for c in cands}
        self.assertEqual(sizes, {"p1": 1, "p2": 2})

    def test_no_gps_photos_are_separate(self):
        photos = [photo("a", self.LAT, self.LNG, "2026-11-02T10:00:00"), photo("z", None, None, "2026-11-02T10:00:05")]
        cands, nopos = bi.bundle_photos(photos)
        self.assertEqual(len(cands), 1)
        self.assertEqual([p.rel for p in nopos], ["z"])

    def test_centroid_is_mean(self):
        photos = [photo("a", 35.0, 139.0, None), photo("b", 35.0001, 139.0001, None)]
        c = bi.bundle_photos(photos)[0][0]
        self.assertAlmostEqual(c.lat, 35.00005, places=7)
        self.assertAlmostEqual(c.lng, 139.00005, places=7)


class IntakeDbTest(unittest.TestCase):
    def test_intake_saves_and_skips_known(self):
        with tempfile.TemporaryDirectory() as d, Ledger(Path(d) / "l.sqlite") as lg:
            r1 = bi.intake(lg.conn, SAMPLE / "photos")
            self.assertEqual((r1.new_photos, r1.with_gps, r1.no_gps, r1.candidates), (10, 9, 1, 5))
            sizes = sorted(x[0] for x in lg.conn.execute("SELECT n_photos FROM gov_candidates"))
            self.assertEqual(sizes, [1, 1, 2, 2, 3])
            r2 = bi.intake(lg.conn, SAMPLE / "photos")  # 同じ写真をもう一度
            self.assertEqual((r2.new_photos, r2.skipped_known, r2.candidates), (0, 10, 0))
            self.assertEqual(lg.conn.execute("SELECT COUNT(*) FROM gov_photos").fetchone()[0], 10)
            self.assertEqual(lg.conn.execute("SELECT COUNT(*) FROM gov_photos WHERE candidate_id IS NULL").fetchone()[0], 1)

    def test_ledger_tables_untouched(self):
        with Ledger() as lg:
            bi.ensure_gov_schema(lg.conn)
            names = {r[0] for r in lg.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            self.assertTrue({"signs", "gov_batches", "gov_candidates", "gov_photos"} <= names)


class MuniTest(unittest.TestCase):
    def test_codes(self):
        self.assertEqual(gc.place_from_code("11222").city, "越谷市")
        self.assertEqual(gc.place_from_code("11100").city, "さいたま市")
        self.assertEqual(gc.place_from_code("13104").label, "東京都 新宿区")
        self.assertTrue(gc.place_from_code("11222").in_scope)
        p = gc.place_from_code("1101")  # 4桁でも5桁にそろえる
        self.assertEqual((p.pref, p.in_scope), ("北海道", False))
        self.assertIsNone(gc.place_from_code("99999"))


class GeocoderTest(unittest.TestCase):
    def make(self, fetch, **kw):
        self.t = [0.0]
        self.sleeps = []

        def sleep(s):
            self.sleeps.append(s)
            self.t[0] += s
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, True)
        return gc.Geocoder(Path(self.tmp) / "c.json", fetch, sleep=sleep, clock=lambda: self.t[0], **kw)

    def test_lookup_cache_and_rate_limit(self):
        calls = []

        def fetch(lat, lng):
            calls.append((lat, lng))
            return {"results": {"muniCd": "11222", "lv01Nm": "越ヶ谷"}}
        g = self.make(fetch)
        ok, place = g.lookup(35.8910, 139.7908)
        self.assertEqual((ok, place.city), (True, "越谷市"))
        ok, place = g.lookup(35.89103, 139.79081)  # 4桁で丸めると同じ → キャッシュ(通信しない)
        self.assertEqual(len(calls), 1)
        g.lookup(35.8, 139.7)   # 別の場所: 直前の通信から 0 秒 → 1 秒待つ
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(sum(self.sleeps), 1.0)
        # 開き直してもキャッシュが残る
        g2 = gc.Geocoder(g.cache_path, lambda *a: self.fail("通信しないはず"))
        self.assertTrue(g2.lookup(35.8910, 139.7908)[0])

    def test_error_is_not_cached(self):
        import urllib.error

        def boom(lat, lng):
            raise urllib.error.URLError("down")
        g = self.make(boom)
        self.assertEqual(g.lookup(35.0, 139.0), (False, None))
        g._fetch = lambda lat, lng: {"results": {"muniCd": "13104"}}
        self.assertEqual(g.lookup(35.0, 139.0)[1].city, "新宿区")

    def test_outside_japan_is_answered_but_none(self):
        g = self.make(lambda lat, lng: {})
        self.assertEqual(g.lookup(0.5, 0.5), (True, None))
        self.assertEqual(g.calls, 1)
        self.assertEqual(g.lookup(0.5, 0.5), (True, None))  # 「答えなし」もキャッシュ
        self.assertEqual(g.calls, 1)

    def test_geocode_candidates_fills_rows(self):
        g = self.make(lambda lat, lng: {"results": {"muniCd": "11222"}})
        with Ledger() as lg:
            bi.ensure_gov_schema(lg.conn)
            lg.conn.execute("INSERT INTO gov_batches(dir) VALUES ('x')")
            lg.conn.execute("INSERT INTO gov_candidates(batch_id, lat, lng, n_photos) VALUES (1, 35.891, 139.79, 1)")
            self.assertEqual(gc.geocode_candidates(lg.conn, g), (1, 0))
            self.assertEqual(tuple(lg.conn.execute("SELECT geocoded, pref, city FROM gov_candidates").fetchone()),
                             (1, "埼玉県", "越谷市"))
            self.assertEqual(gc.geocode_candidates(lg.conn, g), (0, 0))  # 済みは触らない


def sign(id, lat, lng, **kw):
    return Sign(id=id, name=id, lat=lat, lng=lng, **kw)


class MatchTest(unittest.TestCase):
    LAT, LNG = 35.891, 139.7908

    def test_nearest_within_radius(self):
        near = sign("near", *offset(self.LAT, self.LNG, north_m=10))
        nearer = sign("nearer", *offset(self.LAT, self.LNG, north_m=5))
        nopos = sign("nopos", None, None)
        s, d = mt.nearest_sign(self.LAT, self.LNG, [near, nopos, nearer])
        self.assertEqual(s.id, "nearer")
        self.assertAlmostEqual(d, 5, delta=0.3)

    def test_boundary_30m(self):
        s29 = sign("a", *offset(self.LAT, self.LNG, north_m=29))
        s31 = sign("b", *offset(self.LAT, self.LNG, north_m=31))
        self.assertEqual(mt.nearest_sign(self.LAT, self.LNG, [s29])[0].id, "a")
        s, d = mt.nearest_sign(self.LAT, self.LNG, [s31])
        self.assertIsNone(s)
        self.assertAlmostEqual(d, 31, delta=0.3)  # 最寄りまでの距離は返す

    def test_no_signs_at_all(self):
        self.assertEqual(mt.nearest_sign(self.LAT, self.LNG, []), (None, None))

    def test_permit_days_left(self):
        s = sign("s", 0, 0, permit_required=True, permit_expires="2027-01-31")
        st, left, renew, _ = mt.permit_state(s, TODAY)
        self.assertEqual((st, left), ("expiring", 90))
        self.assertEqual(renew, "2026-11-02")  # 期限の90日前

    def test_permit_status_boundaries(self):
        def st(exp):
            return mt.permit_state(sign("s", 0, 0, permit_required=True, permit_expires=exp), TODAY)[:2]
        self.assertEqual(st("2027-02-01"), ("permitted", 91))
        self.assertEqual(st("2027-01-31"), ("expiring", 90))
        self.assertEqual(st("2026-11-02"), ("expiring", 0))     # 今日が期限
        self.assertEqual(st("2026-11-01"), ("expired", -1))
        self.assertEqual(st("2027-11-02"), ("permitted", 365))

    def test_permit_without_date(self):
        self.assertEqual(mt.permit_state(sign("s", 0, 0, permit_required=False), TODAY)[0], "not_required")
        self.assertEqual(mt.permit_state(sign("s", 0, 0, permit_required=True), TODAY)[0], "permit_incomplete")
        self.assertEqual(mt.permit_state(sign("s", 0, 0), TODAY)[0], "permit_incomplete")
        self.assertEqual(mt.permit_state(sign("s", 0, 0, permit_expires="bad"), TODAY)[0], "permit_incomplete")

    def test_warn_days_is_configurable(self):
        s = sign("s", 0, 0, permit_required=True, permit_expires="2027-01-31")
        self.assertEqual(mt.permit_state(s, TODAY, warn_days=30)[0], "permitted")

    def test_match_candidates_statuses(self):
        signs = [sign("g", *offset(self.LAT, self.LNG, north_m=8), permit_required=True, permit_expires="2027-09-30")]
        cands = [mt.CandidateRow(1, self.LAT, self.LNG, ["a.jpg"]),
                 mt.CandidateRow(2, *offset(self.LAT, self.LNG, north_m=200), photos=["b.jpg"]),
                 mt.CandidateRow(None, None, None, ["c.jpg"])]
        r = mt.match_candidates(cands, signs, TODAY)
        self.assertEqual([x.status for x in r], ["permitted", "unregistered", "no_position"])
        self.assertEqual([x.color for x in r], ["green", "red", "gray"])
        self.assertEqual(r[0].days_left, 332)
        self.assertEqual(r[0].sign.id, "g")
        self.assertIn("台帳", r[1].label)
        self.assertGreater(r[1].distance_m, 150)

    def test_empty_ledger_makes_everything_red(self):
        r = mt.match_candidates([mt.CandidateRow(1, self.LAT, self.LNG, ["a.jpg"])], [], TODAY)
        self.assertEqual(r[0].status, "unregistered")


class MapTest(unittest.TestCase):
    def _results(self):
        with tempfile.TemporaryDirectory() as d, Ledger(Path(d) / "l.sqlite") as lg:
            make_sample.build(Path(d) / "s")
            lg.import_json(Path(d) / "s" / "ledger_gov.json")
            bi.intake(lg.conn, Path(d) / "s" / "photos")
            gc.geocode_candidates(lg.conn, gc.Geocoder(SAMPLE / "geocache.json", lambda *a: self.fail("通信")))
            return mt.match_candidates(mt.load_candidates(lg.conn), lg.list_signs(), TODAY), lg.list_signs()

    def test_html_shows_counts_attribution_and_gsi_tiles(self):
        results, signs = self._results()
        h = render_map(results, today=TODAY, ledger_signs=signs)
        for col, n in (("red", 2), ("orange", 2), ("green", 1), ("gray", 1)):
            self.assertIn(f'data-count="{col}">{n}</span>', h)
        self.assertIn("看板の候補 5 基 / 位置なしの写真 1 枚", h)
        self.assertIn("https://maps.gsi.go.jp/development/ichiran.html", h)
        self.assertIn("cyberjapandata.gsi.go.jp/xyz/pale/", h)
        self.assertNotIn("tile.openstreetmap.org", h)
        self.assertIn("leaflet/1.9.4/leaflet.min.js", h)
        self.assertIn("今回の巡回で写真と一致したのは 4 基", h)

    def test_embedded_data_cannot_break_out_of_script(self):
        c = mt.CandidateRow(1, 35.0, 139.0, ["</script><script>alert(1)</script>.jpg"], pref="埼玉県", city="越谷市", geocoded=True)
        s = Sign(id="x", name="</script><b>", lat=35.0, lng=139.0, permit_required=True, permit_expires="2027-12-31")
        h = render_map(mt.match_candidates([c], [s], TODAY), today=TODAY, ledger_signs=[s])
        self.assertEqual(h.count("</script>"), 2)  # Leaflet 読み込みとデータの2つだけ
        self.assertNotIn("<script>alert", h)

    def test_empty_map_renders(self):
        h = render_map([], today=TODAY)
        self.assertIn('data-count="red">0</span>', h)


class CliTest(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = cli_main(list(argv))
        return rc, out.getvalue(), err.getvalue()

    def test_intake_match_map_end_to_end(self):
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "l.sqlite")
            with redirect_stdout(io.StringIO()):
                make_sample.main(["--out", str(Path(d) / "s"), "--db", db])
            cache = str(Path(d) / "geocache.json")
            shutil.copy(SAMPLE / "geocache.json", cache)  # 通信しない
            rc, out, _ = self.run_cli("intake", str(Path(d) / "s" / "photos"), "--db", db, "--geocache", cache)
            self.assertEqual(rc, 0, out)
            self.assertIn("看板の候補 5 基", out)
            self.assertIn("自治体の判定: 5 基", out)
            rc, out, _ = self.run_cli("match", "--db", db, "--today", "2026-11-02")
            self.assertIn("unregistered", out)
            m = str(Path(d) / "out" / "map.html")
            rc, out, _ = self.run_cli("map", "--db", db, "--today", "2026-11-02", "--out", m)
            self.assertEqual(rc, 0)
            self.assertIn("赤 2 / 橙 2 / 緑 1 / 灰 1", out)
            self.assertIn('data-count="red">2', Path(m).read_text(encoding="utf-8"))

    def test_missing_dir_and_db(self):
        self.assertEqual(self.run_cli("intake", "/no/such/dir", "--db", "x.sqlite")[0], 2)
        self.assertEqual(self.run_cli("map", "--db", "/no/such.sqlite")[0], 2)

    def test_committed_sample_map_is_current(self):
        """見本の地図(data/sample/out/map.html)は、見本の写真と台帳から今のコードで作り直した物と同じ。"""
        with tempfile.TemporaryDirectory() as d:
            db = str(Path(d) / "l.sqlite")
            with redirect_stdout(io.StringIO()):
                make_sample.main(["--out", str(Path(d) / "s"), "--db", db])
            cache = str(Path(d) / "g.json")
            shutil.copy(SAMPLE / "geocache.json", cache)
            self.run_cli("intake", str(SAMPLE / "photos"), "--db", db, "--geocache", cache)
            m = Path(d) / "map.html"
            self.run_cli("map", "--db", db, "--today", make_sample.SAMPLE_TODAY, "--out", str(m))
            committed = (ROOT / "data" / "sample" / "out" / "map.html").read_text(encoding="utf-8")
            self.assertEqual(m.read_text(encoding="utf-8"), committed)

    def test_committed_sample_photos_match_generator(self):
        for name, lat, lng, t in make_sample.PHOTOS:
            taken, la, lo = bi.read_exif(SAMPLE / "photos" / name)
            self.assertEqual(taken, t.replace(" ", "T"))
            if lat is None:
                self.assertIsNone(la)
            else:
                self.assertAlmostEqual(la, lat, places=5)
                self.assertAlmostEqual(lo, lng, places=5)


if __name__ == "__main__":
    unittest.main()
