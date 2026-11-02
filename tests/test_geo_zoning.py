import tempfile
import unittest
from pathlib import Path

from kanban_karte.geo import zoning as Z
from kanban_karte.legal.schema import ZONE_VOCAB

SQUARE = [(0, 0), (10, 0), (10, 10), (0, 10)]
HOLE = [(4, 4), (6, 4), (6, 6), (4, 6)]
L_SHAPE = [(0, 0), (10, 0), (10, 4), (4, 4), (4, 10), (0, 10)]


class RayCastingTest(unittest.TestCase):
    def test_square(self):
        self.assertTrue(Z.point_in_ring(5, 5, SQUARE))
        self.assertFalse(Z.point_in_ring(11, 5, SQUARE))
        self.assertFalse(Z.point_in_ring(5, -1, SQUARE))

    def test_closed_ring_same_as_open(self):
        self.assertTrue(Z.point_in_ring(5, 5, SQUARE + [SQUARE[0]]))

    def test_concave(self):
        self.assertTrue(Z.point_in_ring(2, 8, L_SHAPE))
        self.assertTrue(Z.point_in_ring(8, 2, L_SHAPE))
        self.assertFalse(Z.point_in_ring(8, 8, L_SHAPE))   # L の欠けた角

    def test_ray_through_vertex(self):
        # 点と同じ高さに頂点がある(ひし形の右の頂点の真横)。二重に数えない
        diamond = [(0, 5), (5, 0), (10, 5), (5, 10)]
        self.assertTrue(Z.point_in_ring(5, 5, diamond))
        self.assertFalse(Z.point_in_ring(-1, 5, diamond))
        self.assertFalse(Z.point_in_ring(11, 5, diamond))
        self.assertTrue(Z.point_in_ring(1, 5, diamond))

    def test_degenerate(self):
        self.assertFalse(Z.point_in_ring(0, 0, [(0, 0), (1, 1)]))


class PolygonTest(unittest.TestCase):
    def test_hole_is_outside(self):
        p = Z.ZonePolygon.make("商業地域", [SQUARE, HOLE])
        self.assertTrue(p.contains(1, 1))
        self.assertFalse(p.contains(5, 5))

    def test_hole_orientation_does_not_matter(self):
        p = Z.ZonePolygon.make("商業地域", [SQUARE, list(reversed(HOLE))])
        self.assertFalse(p.contains(5, 5))
        self.assertTrue(p.contains(5, 1))

    def test_multipart(self):
        p = Z.ZonePolygon.make("商業地域", [SQUARE, [(20, 0), (30, 0), (30, 10), (20, 10)]])
        self.assertTrue(p.contains(25, 5))
        self.assertFalse(p.contains(15, 5))


class NormalizeTest(unittest.TestCase):
    def test_names_in_gis_data(self):
        cases = {
            "第１種低層住居専用地域": "第一種低層住居専用地域",
            "第２種低層住居専用地域": "第二種低層住居専用地域",
            "第１種中高層住居専用地域": "第一種中高層住居専用地域",
            "第２種中高層住居専用地域": "第二種中高層住居専用地域",
            "第１種住居地域": "第一種住居地域",
            "第２種住居地域": "第二種住居地域",
            "準住居地域": "準住居地域",
            "近隣商業地域": "近隣商業地域",
            "商業地域": "商業地域",
            "準工業地域": "準工業地域",
            "工業地域": "工業地域",
            "工業専用地域": "工業専用地域",
            "田園住居地域": "田園住居地域",
        }
        for raw, want in cases.items():
            self.assertEqual(Z.normalize_zone(raw), want)
            self.assertIn(want, ZONE_VOCAB)

    def test_unknown_is_none(self):
        self.assertIsNone(Z.normalize_zone("よく分からない地域"))
        self.assertIsNone(Z.normalize_zone(None))

    def test_file_code(self):
        self.assertEqual(Z.file_code("11107"), "11100")
        self.assertEqual(Z.file_code("11100"), "11100")
        self.assertEqual(Z.file_code("11222"), "11222")
        self.assertEqual(Z.file_code("13104"), "13104")


class IndexTest(unittest.TestCase):
    def setUp(self):
        self.idx = Z.ZoningIndex.from_polygons([
            Z.ZonePolygon.make("商業地域", [SQUARE], "99001"),
            Z.ZonePolygon.make("第１種低層住居専用地域", [[(10, 0), (20, 0), (20, 10), (10, 10)]], "99001"),
            Z.ZonePolygon.make("不明な名前", [[(30, 0), (40, 0), (40, 10), (30, 10)]], "99001"),
        ])

    def test_zone(self):
        r = self.idx.lookup(5, 5, "99001")
        self.assertEqual((r.status, r.name, r.vocab), ("zone", "商業地域", "商業地域"))
        r = self.idx.lookup(5, 15, "99001")
        self.assertEqual((r.status, r.vocab), ("zone", "第一種低層住居専用地域"))

    def test_name_outside_vocab_has_no_vocab(self):
        r = self.idx.lookup(5, 35, "99001")
        self.assertEqual((r.status, r.name, r.vocab), ("zone", "不明な名前", None))

    def test_outside_vs_no_data(self):
        self.assertEqual(self.idx.lookup(50, 50, "99001").status, "outside")   # データのある市の、どの用途地域にも入らない点
        self.assertEqual(self.idx.lookup(50, 50, "99002").status, "no_data")   # データを持たない市
        self.assertEqual(self.idx.lookup(50, 50).status, "no_data")            # 市が分からない

    def test_overlap_reports_others(self):
        idx = Z.ZoningIndex.from_polygons([Z.ZonePolygon.make("商業地域", [SQUARE]),
                                           Z.ZonePolygon.make("工業地域", [SQUARE])])
        r = idx.lookup(5, 5)
        self.assertEqual((r.name, r.others), ("商業地域", ("工業地域",)))


class LoadTest(unittest.TestCase):
    def test_load_shapefile(self):
        try:
            import shapefile
        except ImportError:
            self.skipTest("pyshp が無い")
        with tempfile.TemporaryDirectory() as d:
            sub = Path(d) / "11_埼玉県" / "99001_試験市"
            sub.mkdir(parents=True)
            w = shapefile.Writer(str(sub / "99001_youto"), shapeType=shapefile.POLYGON, encoding="utf-8")
            w.field("YoutoName", "C", size=80)
            w.poly([SQUARE, HOLE])
            w.record("第１種住居地域")
            w.poly([[(20, 0), (30, 0), (30, 10), (20, 10)]])
            w.record("商業地域")
            w.close()
            idx = Z.ZoningIndex.load(d)
        self.assertEqual(len(idx.polygons), 2)
        self.assertEqual(idx.lookup(1, 1, "99001").vocab, "第一種住居地域")
        self.assertEqual(idx.lookup(5, 5, "99001").status, "outside")   # 穴
        self.assertEqual(idx.lookup(lat=5, lng=25, muni_cd="99001").vocab, "商業地域")

    def test_missing_data_dir(self):
        try:
            import shapefile  # noqa: F401
        except ImportError:
            self.skipTest("pyshp が無い")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                Z.ZoningIndex.load(d)


if __name__ == "__main__":
    unittest.main()
