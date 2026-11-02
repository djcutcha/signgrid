import tempfile
import unittest

from kanban_karte.impact.place import Placed
from kanban_karte.legal import locate as L

TOKYO = "東京都新宿区西新宿2-8-1"


class FakePlacer:
    def __init__(self, placed):
        self.placed, self.calls = placed, []

    def place(self, address, muni):
        self.calls.append((address, muni))
        return self.placed


class JudgeAtTest(unittest.TestCase):
    def test_no_placer_judges_without_place(self):
        r, placed = L.judge_at(TOKYO, use="self", area_m2=7.0)
        self.assertIsNone(placed)
        self.assertEqual(r.permit_required, "要実測")   # 5㎡超10㎡以下は用途地域が要る

    def test_zone_is_passed_as_place(self):
        p = FakePlacer(Placed("zone", "商業地域"))
        r, placed = L.judge_at(TOKYO, use="self", area_m2=7.0, placer=p)
        self.assertEqual(placed.status, "zone")
        self.assertEqual(r.permit_required, "不要")
        self.assertEqual(p.calls[0][1].city, "新宿区")

    def test_prohibited_zone_requires_permit(self):
        r, _ = L.judge_at(TOKYO, use="self", area_m2=7.0, placer=FakePlacer(Placed("zone", "第一種低層住居専用地域")))
        self.assertEqual(r.permit_required, "要")

    def test_fallback_without_zone(self):
        for st in ("outside", "no_data", "not_found", "error", "mismatch"):
            r, placed = L.judge_at(TOKYO, use="self", area_m2=7.0, placer=FakePlacer(Placed(st)))
            self.assertEqual(placed.status, st)
            self.assertEqual(r.permit_required, "要実測", st)

    def test_zone_status_without_vocab_is_not_passed(self):
        r, _ = L.judge_at(TOKYO, use="self", area_m2=7.0, placer=FakePlacer(Placed("zone", None)))
        self.assertEqual(r.permit_required, "要実測")

    def test_no_address_skips_placer(self):
        p = FakePlacer(Placed("zone", "商業地域"))
        _, placed = L.judge_at(None, use="self", area_m2=7.0, placer=p)
        self.assertIsNone(placed)
        self.assertEqual(p.calls, [])

    def test_place_info(self):
        self.assertEqual(L.place_dict(Placed("zone", "商業地域"))["zone"], "商業地域")
        self.assertIsNone(L.place_dict(Placed("outside"))["zone"])
        self.assertEqual(L.place_dict(None)["status"], "none")


class MakePlacerTest(unittest.TestCase):
    def test_no_gis_returns_none(self):
        with tempfile.TemporaryDirectory() as td:
            self.assertIsNone(L.make_placer(td))


if __name__ == "__main__":
    unittest.main()
