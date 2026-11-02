import unittest

from kanban_karte.geo import geocode as GC
from kanban_karte.geo import zoning as Z
from kanban_karte.gov import geocode as GG
from kanban_karte.impact import count as C
from kanban_karte.impact.place import Placed, ZonePlacer
from kanban_karte.legal.municipality import Municipality

# 越谷市(11222)の中に小さな商業地域と、第一種低層住居専用地域がある想定
KOSHIGAYA = (35.890, 139.791)
INDEX = Z.ZoningIndex.from_polygons([
    Z.ZonePolygon.make("商業地域", [[(139.790, 35.889), (139.792, 35.889), (139.792, 35.891), (139.790, 35.891)]], "11222"),
    Z.ZonePolygon.make("第１種低層住居専用地域", [[(139.800, 35.889), (139.802, 35.889), (139.802, 35.891), (139.800, 35.891)]], "11222"),
])


def feature(lng, lat, title="埼玉県越谷市越ヶ谷四丁目２番１号"):
    return {"geometry": {"coordinates": [lng, lat]}, "properties": {"title": title}}


def make(answers, muni_cd="11222"):
    """answers: 住所(正規化後) -> (lat, lng) | [] | 例外。muni_cd は逆ジオコーダが返すコード。"""
    def fetch(q):
        a = answers[q]
        if isinstance(a, Exception):
            raise a
        return [feature(a[1], a[0])] if a else []

    geocoder = GC.AddressGeocoder(None, fetch, sleep=lambda s: None)
    reverse = GG.Geocoder(None, lambda lat, lng: {"results": {"muniCd": muni_cd, "lv01Nm": "x"}}, sleep=lambda s: None)
    return ZonePlacer(geocoder, reverse, INDEX)


class PlacerTest(unittest.TestCase):
    def test_zone(self):
        p = make({"埼玉県越谷市越ヶ谷四丁目2-1": KOSHIGAYA}).place("埼玉県越谷市越ヶ谷四丁目2-1", Municipality("埼玉県", "越谷市"))
        self.assertEqual((p.status, p.vocab, p.name, p.precision), ("zone", "商業地域", "商業地域", "lot"))

    def test_low_rise_name_is_normalized_for_judge(self):
        p = make({"a": (35.890, 139.801)}).place("a", Municipality("埼玉県", "越谷市"))
        self.assertEqual(p.vocab, "第一種低層住居専用地域")

    def test_outside(self):
        p = make({"a": (35.900, 139.850)}).place("a", Municipality("埼玉県", "越谷市"))
        self.assertEqual((p.status, p.vocab), ("outside", None))

    def test_no_data_for_other_city(self):
        p = make({"a": (35.900, 139.850)}, muni_cd="11203").place("a", Municipality("埼玉県", "川口市"))
        self.assertEqual(p.status, "no_data")

    def test_not_found(self):
        self.assertEqual(make({"a": []}).place("a", Municipality("埼玉県", "越谷市")).status, "not_found")

    def test_mismatch_when_point_lands_in_other_city(self):
        p = make({"a": KOSHIGAYA}, muni_cd="11203").place("a", Municipality("埼玉県", "越谷市"))
        self.assertEqual((p.status, p.vocab), ("mismatch", None))

    def test_saitama_ward_matches_city(self):
        p = make({"a": (35.900, 139.850)}, muni_cd="11107").place("a", Municipality("埼玉県", "さいたま市"))
        self.assertNotEqual(p.status, "mismatch")

    def test_error_not_remembered(self):
        import urllib.error
        answers = {"a": urllib.error.URLError("down")}
        placer = make(answers)
        self.assertEqual(placer.place("a", None).status, "error")
        answers["a"] = KOSHIGAYA
        self.assertEqual(placer.place("a", None).status, "zone")


class FakePlacer:
    def __init__(self, table):
        self.table = table

    def place(self, address, muni):
        return self.table.get(address, Placed("not_found"))


# (指示書ID, 件名, 仕様文, 物件の得意先名, 得意先住所, 見積の得意先名)
ROWS = [
    ("1", "社名看板", "壁面看板 W1000×H500", "山田商店", "越谷A", None),
    ("2", "社名看板", "壁面看板 W1000×H500", "鈴木商店", "越谷B", None),
    ("3", "社名看板", "壁面看板 W1000×H500", "佐藤商店", "越谷C", None),
]


class RunWithZoningTest(unittest.TestCase):
    def run_rows(self, table):
        rows = [r[:4] + ("埼玉県越谷市" + r[4], r[5]) for r in ROWS]
        table = {"埼玉県越谷市" + k: v for k, v in table.items()}
        return C.run("x.db", agency_words=(), rows=iter(rows), placer=FakePlacer(table))

    def test_place_changes_verdict_only_where_given(self):
        rep = self.run_rows({"越谷A": Placed("zone", "第一種低層住居専用地域", "第１種低層住居専用地域", "lot"),
                             "越谷B": Placed("outside"),
                             "越谷C": Placed("error")})
        g = rep.groups["越谷市"]
        self.assertEqual(g.base, 3)
        self.assertEqual(g.with_place, 1)
        self.assertEqual(g.zoning["zone"], 1)
        self.assertEqual(g.zoning["outside"], 1)
        # 区分なしでは越谷市は要実測(前回の表)。低層住居専用地域(禁止区域側)の壁面は、区分を渡すと基準内と確定する
        self.assertEqual(g.base_outcome[C.MEASURE], 3)
        self.assertEqual(g.outcome[C.SELF_EXEMPT], 1)
        self.assertEqual(g.outcome[C.MEASURE], 2)
        self.assertEqual(sum(g.outcome.values()), g.base)

    def test_without_placer_base_equals_outcome(self):
        rep = C.run("x.db", agency_words=(), rows=iter([r[:4] + ("埼玉県越谷市" + r[4], r[5]) for r in ROWS]))
        g = rep.groups["越谷市"]
        self.assertEqual(g.outcome, g.base_outcome)
        self.assertFalse(rep.zoning_enabled)
        self.assertNotIn("区分あり", C.render(rep))

    def test_render_has_zoning_sections(self):
        rep = self.run_rows({"越谷A": Placed("zone", "商業地域", "商業地域", "lot")})
        txt = C.render(rep)
        for s in ("用途地域ベースの暫定", "区分あり", "座標化できた", "座標化できず"):
            self.assertIn(s, txt)


if __name__ == "__main__":
    unittest.main()
