"""種類ごとの判定(壁面率・上端・出幅・下端の地上高・道路占用の注意)。数値は試験用の仮の基準で、条例の値ではない。"""
import copy
import unittest

from kanban_karte.legal import kinds as K
from kanban_karte.legal import schema as S
from kanban_karte.legal.judge import NEEDS_MEASURE, NOT_REQUIRED, REQUIRED

from tests.test_legal_judge import J, make_rules, std


def wall_std(ratio=0.2, floor=10, zone="permit_zone", top="eave", **kw):
    s = std(kind="壁面広告", zone=zone, area=None, height=None, **kw)
    s.update({"wall_ratio_max": ratio, "wall_ratio_floor_m2": floor, "top_limit": top, "verified": "unverified"})
    return s


def proj_std(wall=1.2, road=None, allowed=False, sw=None, rw=None, top="wall", zone="permit_zone"):
    s = std(kind="突出し広告", zone=zone, area=None, height=None)
    s.update({"protrusion_from_wall_max_m": wall, "protrusion_from_road_boundary_max_m": road, "road_protrusion_allowed": allowed,
              "ground_clearance_sidewalk_min_m": sw, "ground_clearance_roadway_min_m": rw, "top_limit": top})
    return s


def item(res, name):
    return next(r for r in res.kind_checks if r["item"] == name)


class CheckBandTest(unittest.TestCase):
    def test_max_band(self):
        # 基準 1.0 の ±15%(0.85〜1.15)は要実測
        for v, want in ((0.84, K.OK), (0.85, K.MEASURE), (1.0, K.MEASURE), (1.15, K.MEASURE), (1.16, K.OVER), (None, K.MISSING)):
            with self.subTest(v=v):
                self.assertEqual(K.check(v, 1.0, "max"), want)

    def test_min_band(self):
        for v, want in ((2.12, K.OVER), (2.2, K.MEASURE), (2.5, K.MEASURE), (2.88, K.OK)):
            with self.subTest(v=v):
                self.assertEqual(K.check(v, 2.5, "min"), want)

    def test_zero_limit_uses_absolute_band(self):
        self.assertEqual(K.check(-0.2, 0, "max"), K.OK)
        self.assertEqual(K.check(0.1, 0, "max"), K.MEASURE)
        self.assertEqual(K.check(0.3, 0, "max"), K.OVER)

    def test_category(self):
        self.assertEqual(K.category("壁面"), K.WALL)
        self.assertEqual(K.category("袖"), K.PROJECTING)
        self.assertEqual(K.category("突出し広告"), K.PROJECTING)
        self.assertIsNone(K.category("自立"))
        self.assertIsNone(K.category("屋上"))
        self.assertIsNone(K.category(None))


class WallTest(unittest.TestCase):
    def setUp(self):
        self.r = make_rules([wall_std()])

    def test_ratio_within_and_over(self):
        # 壁面 100 ㎡ × 1/5 = 20 ㎡。±15% は 17〜23 ㎡
        res = J(self.r, kind="壁面", area_m2=10, wall_area_m2=100, height_m=3, eave_height_m=6)
        self.assertEqual(item(res, "wall_ratio")["verdict"], K.OK)
        self.assertEqual(item(res, "wall_ratio")["value"], 0.1)
        self.assertEqual(res.permit_required, NOT_REQUIRED)
        res = J(self.r, kind="壁面", area_m2=30, wall_area_m2=100, height_m=3, eave_height_m=6)
        self.assertEqual(item(res, "wall_ratio")["verdict"], K.OVER)
        self.assertEqual(res.permit_required, REQUIRED)

    def test_ratio_near_boundary_is_measure(self):
        res = J(self.r, kind="壁面", area_m2=21, wall_area_m2=100, height_m=3, eave_height_m=6)
        self.assertEqual(item(res, "wall_ratio")["verdict"], K.MEASURE)
        self.assertEqual(res.permit_required, NEEDS_MEASURE)

    def test_floor_area(self):
        # 壁面 30 ㎡ × 1/5 = 6 ㎡ < 10 ㎡ -> 10 ㎡ まで可
        res = J(self.r, kind="壁面", area_m2=8, wall_area_m2=30, height_m=3, eave_height_m=6)
        self.assertEqual(item(res, "wall_ratio")["verdict"], K.OK)
        self.assertIn("10 ㎡ 未満なら 10 ㎡ まで", item(res, "wall_ratio")["note"])

    def test_top_above_eave(self):
        res = J(self.r, kind="壁面", area_m2=5, wall_area_m2=100, height_m=8, eave_height_m=6)
        self.assertEqual(item(res, "top_eave")["verdict"], K.OVER)
        self.assertEqual(res.permit_required, REQUIRED)

    def test_missing_wall_area(self):
        res = J(self.r, kind="壁面", area_m2=5, height_m=3, eave_height_m=6)
        self.assertEqual(item(res, "wall_ratio")["verdict"], K.MISSING)
        self.assertEqual(res.permit_required, NEEDS_MEASURE)

    def test_citations_and_verified(self):
        res = J(self.r, kind="壁面", area_m2=5, wall_area_m2=100, height_m=3, eave_height_m=6)
        row = item(res, "wall_ratio")
        self.assertTrue(row["citations"])
        self.assertEqual(row["verified"], "unverified")
        self.assertTrue(any(c["quote"] == row["citations"][0]["quote"] for c in res.citations))

    def test_no_kind_keeps_old_behavior(self):
        # 種類を渡さなければ種類ごとの判定はしない(今までどおり、数値の無い基準は要実測)
        res = J(self.r, area_m2=5)
        self.assertEqual(res.kind_checks, [])
        self.assertIsNone(res.kind_category)
        self.assertIsNone(res.road_occupancy)

    def test_zone_split_branches(self):
        # 区域が分からず 1/5 と 3/10 の両方が候補 -> 壁面率 0.25 は区域で結論が割れて要実測、場合分けが残る
        r = make_rules([wall_std(ratio=0.2, floor=None, top=None),
                        wall_std(ratio=0.3, floor=None, top=None, zone="other", labels=("商業地域",))])
        res = J(r, kind="壁面", area_m2=10, wall_area_m2=40)
        row = item(res, "wall_ratio")
        self.assertEqual(row["verdict"], K.MEASURE)
        self.assertEqual({b["verdict"] for b in row["branches"]}, {K.OVER, K.OK})
        # 区域が決まれば、その区域の基準だけで決まる(第一種住居地域は 1/5 だけ -> 0.25 は超過)
        res = J(r, kind="壁面", area_m2=10, wall_area_m2=40, place="第一種住居地域")
        self.assertEqual(item(res, "wall_ratio")["verdict"], K.OVER)

    def test_rules_without_keys_note(self):
        r = make_rules([std(kind="壁面広告", area=None, height=None, cond="壁面面積の5分の1以下")])
        res = J(r, kind="壁面", area_m2=5, wall_area_m2=100)
        self.assertEqual(res.kind_checks, [])
        self.assertTrue(any("数値がまだ入っていません" in n for n in res.notes))
        self.assertEqual(res.permit_required, NEEDS_MEASURE)


class ProjectingTest(unittest.TestCase):
    def setUp(self):
        self.r = make_rules([proj_std(wall=1.2, allowed=False)])

    def test_protrusion_wall_boundary(self):
        for v, want in ((0.9, K.OK), (1.2, K.MEASURE), (1.5, K.OVER)):
            with self.subTest(v=v):
                res = J(self.r, kind="袖", area_m2=1, protrusion_wall_m=v, protrusion_road_m=-0.5, height_m=4, wall_height_m=6)
                self.assertEqual(item(res, "protrusion_wall")["verdict"], want)

    def test_road_protrusion_not_allowed(self):
        res = J(self.r, kind="袖", area_m2=1, protrusion_wall_m=0.9, protrusion_road_m=0.6, height_m=4, wall_height_m=6)
        self.assertEqual(item(res, "road_protrusion")["verdict"], K.OVER)
        self.assertEqual(res.permit_required, REQUIRED)
        self.assertEqual(res.road_occupancy["status"], "要")
        self.assertIn("道路法 第32条", res.road_occupancy["text"])
        self.assertIn("警察署に確認", res.road_occupancy["text"])
        arts = [c["article"] for c in res.road_occupancy["citations"]]
        self.assertIn("道路法 第32条第1項", arts)
        self.assertIn("道路交通法 第77条第1項第2号", arts)

    def test_inside_boundary(self):
        res = J(self.r, kind="袖", area_m2=1, protrusion_wall_m=0.9, protrusion_road_m=-0.5, height_m=4, wall_height_m=6)
        self.assertEqual(res.road_occupancy["status"], "不要")
        self.assertEqual(res.permit_required, NOT_REQUIRED)

    def test_unknown_boundary_is_confirm(self):
        res = J(self.r, kind="袖", area_m2=1, protrusion_wall_m=0.9, height_m=4, wall_height_m=6)
        self.assertEqual(res.road_occupancy["status"], "要確認")
        self.assertEqual(res.permit_required, NEEDS_MEASURE)
        self.assertIn("道路の管理者", res.road_occupancy["text"])

    def test_clearance_sidewalk_roadway(self):
        r = make_rules([proj_std(wall=1.5, road=1.0, allowed=True, sw=3.5, rw=4.5, top=None)])
        res = J(r, kind="袖", area_m2=1, protrusion_wall_m=0.8, protrusion_road_m=0.5, ground_clearance_m=4.0, over="sidewalk")
        self.assertEqual(item(res, "clearance")["verdict"], K.MEASURE)   # 3.5 の +15% = 4.025 より下
        res = J(r, kind="袖", area_m2=1, protrusion_wall_m=0.8, protrusion_road_m=0.5, ground_clearance_m=4.2, over="sidewalk")
        self.assertEqual(item(res, "clearance")["verdict"], K.OK)
        res = J(r, kind="袖", area_m2=1, protrusion_wall_m=0.8, protrusion_road_m=0.5, ground_clearance_m=3.6, over="roadway")
        self.assertEqual(item(res, "clearance")["verdict"], K.OVER)
        # 歩道か車道か不明: 厳しい方(4.5)に収まれば適合、緩い方(3.5)にも足りなければ超過、間は要実測
        for gc, want in ((5.3, K.OK), (2.9, K.OVER), (4.2, K.MEASURE)):
            with self.subTest(gc=gc):
                res = J(r, kind="袖", area_m2=1, protrusion_wall_m=0.8, protrusion_road_m=0.5, ground_clearance_m=gc)
                self.assertEqual(item(res, "clearance")["verdict"], want)

    def test_road_boundary_protrusion(self):
        r = make_rules([proj_std(wall=1.5, road=1.0, allowed=True, top=None)])
        for v, want in ((0.5, K.OK), (1.0, K.MEASURE), (1.3, K.OVER)):
            with self.subTest(v=v):
                res = J(r, kind="袖", area_m2=1, protrusion_wall_m=0.8, protrusion_road_m=v)
                self.assertEqual(item(res, "protrusion_road")["verdict"], want)

    def test_road_occupancy_by_manager(self):
        r = make_rules([proj_std()])
        r["road_occupancy"] = {"by_manager": [
            {"manager": "都道", "protrusion_from_road_boundary_max_m": 1.0, "ground_clearance_sidewalk_min_m": 2.5,
             "ground_clearance_no_sidewalk_min_m": 4.5, "self_use_only": None, "general_not_allowed": None, "note": None,
             "citations": [{"source": "道路管理者の基準", "article": "占用基準", "quote": "袖看板 出幅 1m 以下"}], "confidence": 0.8,
             "verified": "unverified"},
            {"manager": "市道", "protrusion_from_road_boundary_max_m": 1.0, "citations": [{"source": "道路管理者の基準", "article": "x", "quote": "y"}],
             "confidence": 0.8}]}
        self.assertEqual(S.validate_rules(r), [])
        res = J(r, kind="袖", area_m2=1, protrusion_wall_m=0.9, protrusion_road_m=0.5, ground_clearance_m=3.0, over="sidewalk")
        self.assertEqual(len(res.road_occupancy["by_manager"]), 2)
        res = J(r, kind="袖", area_m2=1, protrusion_wall_m=0.9, protrusion_road_m=0.5, ground_clearance_m=3.0, over="sidewalk",
                road_manager="都道")
        bm = res.road_occupancy["by_manager"]
        self.assertEqual([b["manager"] for b in bm], ["都道"])
        self.assertEqual([row["verdict"] for row in bm[0]["rows"]], [K.OK, K.OK])
        self.assertNotIn("道路の管理者(国道", res.road_occupancy["text"])

    def test_wall_kind_has_no_road_notice(self):
        res = J(make_rules([wall_std()]), kind="壁面", area_m2=5, wall_area_m2=100, height_m=3, eave_height_m=6)
        self.assertIsNone(res.road_occupancy)

    def test_bad_over(self):
        with self.assertRaises(ValueError):
            J(self.r, kind="袖", area_m2=1, over="air")


class SchemaTest(unittest.TestCase):
    def test_valid_and_invalid_keys(self):
        r = make_rules([wall_std(), proj_std(sw=3.5, rw=4.5)])
        self.assertEqual(S.validate_rules(r), [])
        bad = copy.deepcopy(r)
        bad["self_use"]["standards"][0]["wall_ratio_max"] = 1.5
        bad["self_use"]["standards"][0]["top_limit"] = "roof"
        bad["self_use"]["standards"][1]["road_protrusion_allowed"] = "no"
        bad["self_use"]["standards"][1]["verified"] = "maybe"
        errs = S.validate_rules(bad)
        for k in ("wall_ratio_max", "top_limit", "road_protrusion_allowed", "verified"):
            self.assertTrue(any(k in e for e in errs), k)

    def test_road_occupancy_shape(self):
        r = make_rules([proj_std()])
        r["road_occupancy"] = {"by_manager": [{"manager": "私道", "citations": [], "confidence": 2}]}
        errs = S.validate_rules(r)
        self.assertTrue(any("manager" in e for e in errs))
        self.assertTrue(any("citations" in e for e in errs))
        self.assertTrue(any("confidence" in e for e in errs))

    def test_shipped_rules_still_valid(self):
        from kanban_karte.legal.rules_extract import ORDINANCE_FILES, load_rules
        for key in ORDINANCE_FILES:
            with self.subTest(key=key):
                self.assertEqual(S.validate_file(load_rules(key)), [])


if __name__ == "__main__":
    unittest.main()
