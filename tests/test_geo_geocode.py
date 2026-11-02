import json
import tempfile
import unittest
import urllib.error
from pathlib import Path

from kanban_karte.geo import geocode as G
from kanban_karte.gov import geocode as GG


def feature(lng, lat, title):
    return {"geometry": {"coordinates": [lng, lat], "type": "Point"}, "type": "Feature", "properties": {"title": title}}


class Clock:
    def __init__(self):
        self.t, self.slept = 0.0, []

    def now(self):
        return self.t

    def sleep(self, s):
        self.slept.append(s)
        self.t += s


class AddressGeocoderTest(unittest.TestCase):
    def make(self, fetch, cache=None):
        c = Clock()
        return G.AddressGeocoder(cache, fetch, sleep=c.sleep, clock=c.now), c

    def test_first_candidate_and_precision(self):
        g, _ = self.make(lambda q: [feature(139.79, 35.89, "埼玉県越谷市越ヶ谷四丁目２番１号"), feature(1, 2, "x")])
        got, loc = g.lookup("埼玉県越谷市越ヶ谷四丁目2-1")
        self.assertTrue(got)
        self.assertEqual((loc.lat, loc.lng, loc.candidates, loc.precision), (35.89, 139.79, 2, "lot"))

    def test_precision_levels(self):
        mk = lambda t: G.Located(0, 0, t, 1)
        self.assertEqual(mk("東京都新宿区西新宿二丁目").precision, "chome")
        self.assertEqual(mk("東京都新宿区西新宿").precision, "area")

    def test_no_result_is_cached_error_is_not(self):
        calls = []

        def fetch(q):
            calls.append(q)
            if len(calls) == 1:
                raise urllib.error.URLError("down")
            return []

        g, _ = self.make(fetch)
        self.assertEqual(g.lookup("どこか"), (False, None))
        self.assertEqual(g.lookup("どこか"), (True, None))
        self.assertEqual(g.lookup("どこか"), (True, None))
        self.assertEqual(len(calls), 2)

    def test_rate_limit_and_normalized_key(self):
        n = []
        g, c = self.make(lambda q: n.append(q) or [feature(1, 2, "a")])
        g.lookup("東京都 千代田区１-１")
        g.lookup("東京都千代田区1-1")       # 同じ住所(全角/空白違い)はキャッシュ
        g.lookup("東京都千代田区2-2")
        self.assertEqual(len(n), 2)
        self.assertEqual(n[0], "東京都千代田区1-1")
        self.assertEqual(len(c.slept), 1)
        self.assertAlmostEqual(c.slept[0], 1.0)

    def test_cache_file_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "c.json"
            g, _ = self.make(lambda q: [feature(139.0, 35.0, "t")], p)
            g.lookup("東京都千代田区1-1")
            g2, _ = self.make(lambda q: self.fail("キャッシュに当たるはず"), p)
            self.assertEqual(g2.lookup("東京都千代田区1-1")[1].title, "t")
            self.assertIn("東京都千代田区1-1", json.loads(p.read_text(encoding="utf-8")))

    def test_bad_payload_is_error(self):
        g, _ = self.make(lambda q: [{"oops": 1}])
        self.assertEqual(g.lookup("x"), (False, None))

    def test_muni_of_delegates_to_reverse_geocoder(self):
        rev = GG.Geocoder(None, lambda lat, lng: {"results": {"muniCd": "11222", "lv01Nm": "越ヶ谷"}}, sleep=lambda s: None)
        p = G.muni_of(rev, G.Located(35.89, 139.79, "t", 1))
        self.assertEqual((p.pref, p.city), ("埼玉県", "越谷市"))


if __name__ == "__main__":
    unittest.main()
