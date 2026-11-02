import unittest

from kanban_karte.impact.extract import SELF, GENERAL, UNKNOWN, extract, extract_sizes


class SizeTest(unittest.TestCase):
    def test_wh_and_hw(self):
        self.assertEqual(extract_sizes("壁面看板 H1200×W6600×D145"), [(6600, 1200)])
        self.assertEqual(extract_sizes("Ｗ５００×Ｈ２５０インクジェット"), [(500, 250)])

    def test_units(self):
        self.assertEqual(extract_sizes("W1.2m×H0.6m"), [(1200, 600)])
        self.assertEqual(extract_sizes("W30cm x H20cm"), [(300, 200)])

    def test_comma_thousands(self):
        self.assertEqual(extract_sizes("W1,200×H600"), [(1200, 600)])

    def test_plain_pair_needs_big_sides(self):
        self.assertEqual(extract_sizes("外構自立サイン2000*3100*200"), [(2000, 3100)])
        self.assertEqual(extract_sizes("アルミアングル 30x30 L1200"), [])

    def test_unitless_decimals_dropped(self):
        self.assertEqual(extract_sizes("W45.055×H5.315→501,601"), [])

    def test_none(self):
        self.assertEqual(extract_sizes("支給品取付 1式"), [])


class KindUseTest(unittest.TestCase):
    def test_wall_sign_with_area(self):
        s = extract("社名内照式看板取付", "LED内照式看板 H600×W3000 D145 1式")
        self.assertTrue(s.is_outdoor_sign)
        self.assertEqual(s.area_m2, 1.8)
        self.assertEqual(s.use, SELF)

    def test_max_area_wins(self):
        s = extract("袖看板", "W759×H1175 2枚 W500×H250")
        self.assertEqual(s.kind, "突出")
        self.assertEqual(s.area_m2, round(759 * 1175 / 1e6, 4))

    def test_specific_kind_over_generic(self):
        self.assertEqual(extract("ポール看板是正", "").kind, "自立")

    def test_indoor_not_outdoor(self):
        s = extract("インターホンサイン 発送", "W45×H100 1式")
        self.assertFalse(s.is_outdoor_sign)
        self.assertEqual(s.kind, "屋内")

    def test_general_ad(self):
        s = extract("広告塔 新設", "W1000×H3000")
        self.assertEqual(s.use, GENERAL)
        self.assertEqual(s.kind, "自立")

    def test_use_unknown(self):
        self.assertEqual(extract("看板製作", "W900×H900").use, UNKNOWN)

    def test_shop_title_weak_self(self):
        s = extract("まつのや 立川店 看板", "")
        self.assertEqual(s.use, SELF)
        self.assertIn("弱", s.use_evidence)

    def test_not_a_sign(self):
        s = extract("シート貼り", "インクジェット出力 W400×H300")
        self.assertFalse(s.is_outdoor_sign)
        self.assertEqual(s.area_m2, 0.12)


if __name__ == "__main__":
    unittest.main()
