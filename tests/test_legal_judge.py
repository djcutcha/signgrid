import copy
import json
import unittest
from pathlib import Path

from kanban_karte.legal import schema as S
from kanban_karte.legal.judge import NEEDS_MEASURE, NOT_REQUIRED, REQUIRED, judge
from kanban_karte.legal.rules_extract import DATA_DIR, ORDINANCE_FILES, rules_path

CITE = {"source": "施行規則", "article": "別表第2", "quote": "表示面積は10平方メートル以下であること。", "quote_verified": True}


def std(kind="広告塔・広告板", zone="permit_zone", area=10.0, height=5.0, gc=None, labels=(), conf=0.9, cond=None):
    return {"kind": kind, "zone": zone, "zone_note": None, "zone_labels": list(labels), "area_max_m2": area,
            "area_basis": "unspecified", "height_max_m": height, "ground_clearance_min_m": gc, "conditions": cond,
            "citations": [dict(CITE)], "confidence": conf}


def make_rules(standards, *, inspection=None, term=None, prohibited_labels=("第一種低層住居専用地域",), types=None):
    return {
        "self_use": {"definition": "自家用", "exempt_from_permit": True, "standards": standards,
                     "prohibited_zone_permit": {"required": "permit_possible", "summary": "s", "citations": [dict(CITE)],
                                                "confidence": 0.8}},
        "permit_required_types": types if types is not None else [
            {"kind": "広告物全般", "area_threshold_m2": None, "height_threshold_m": None,
             "ground_clearance_threshold_m": None, "description": "d", "citations": [dict(CITE)], "confidence": 0.8}],
        "permit_term": term or {"default_years": 3, "items": [
            {"kind": "広告塔", "years": 3, "condition": None, "is_maximum": True, "citations": [dict(CITE)], "confidence": 0.8},
            {"kind": "掛看板", "years": 1, "condition": None, "is_maximum": True, "citations": [dict(CITE)], "confidence": 0.8}]},
        "inspection": inspection or {"required": "conditional", "conditions": [
            {"description": "高さ4m以上", "area_min_m2": None, "height_min_m": 4.0, "ground_clearance_min_m": None}],
            "applies_to_self_use": None, "qualification": "屋外広告士", "form": "様式2", "interval_years": None,
            "citations": [dict(CITE)], "confidence": 0.7},
        "prohibited_zones": {"summary": "s", "labels": list(prohibited_labels), "citations": [dict(CITE)], "confidence": 0.8},
    }


def J(rules, **kw):
    kw.setdefault("municipality", "越谷市")
    return judge(rules=rules, **kw)


class SelfUseBoundaryTest(unittest.TestCase):
    def setUp(self):
        self.r = make_rules([std()])

    def test_area_threshold_boundary(self):
        for area, want in ((9.99, NOT_REQUIRED), (10.0, NOT_REQUIRED), (10.01, REQUIRED), (50, REQUIRED), (0.1, NOT_REQUIRED)):
            with self.subTest(area=area):
                res = J(self.r, area_m2=area, height_m=3.0)
                self.assertEqual(res.permit_required, want)
                self.assertEqual(res.is_self_use_exempt, want == NOT_REQUIRED)
                self.assertEqual(res.permit_term_years is None, want == NOT_REQUIRED)

    def test_height_threshold_boundary(self):
        self.assertEqual(J(self.r, area_m2=5, height_m=5.0).permit_required, NOT_REQUIRED)
        self.assertEqual(J(self.r, area_m2=5, height_m=5.01).permit_required, REQUIRED)

    def test_none_dimensions_need_measurement(self):
        res = J(self.r, area_m2=None, height_m=None)
        self.assertEqual(res.permit_required, NEEDS_MEASURE)
        self.assertIsNone(res.is_self_use_exempt)
        self.assertEqual(J(self.r, area_m2=None, height_m=3.0).permit_required, NEEDS_MEASURE)
        # 面積は基準内でも高さが None -> 要実測
        self.assertEqual(J(self.r, area_m2=5, height_m=None).permit_required, NEEDS_MEASURE)
        # 面積が基準を超えていれば、高さが None でも 要 が確定する
        self.assertEqual(J(self.r, area_m2=11, height_m=None).permit_required, REQUIRED)

    def test_ground_clearance(self):
        r = make_rules([std(kind="突出し広告", area=3.0, height=None, gc=2.5)])
        self.assertEqual(J(r, area_m2=2, ground_clearance_m=2.5).permit_required, NOT_REQUIRED)
        self.assertEqual(J(r, area_m2=2, ground_clearance_m=2.4).permit_required, REQUIRED)
        self.assertEqual(J(r, area_m2=2, ground_clearance_m=None).permit_required, NEEDS_MEASURE)

    def test_citations_and_confidence(self):
        res = J(self.r, area_m2=5, height_m=3)
        self.assertTrue(res.citations)
        for c in res.citations:
            self.assertTrue(c["article"] and c["quote"])
        self.assertAlmostEqual(res.confidence, 0.7)  # 根拠のうち最小
        self.assertIn("検算前", res.status)
        d = res.to_dict()
        for k in ("is_self_use_exempt", "permit_required", "permit_term_years", "inspection_required", "citations", "confidence"):
            self.assertIn(k, d)
        json.dumps(d, ensure_ascii=False)

    def test_unverified_quote_lowers_confidence(self):
        r = copy.deepcopy(self.r)
        r["self_use"]["standards"][0]["citations"][0]["quote_verified"] = False
        self.assertAlmostEqual(J(r, area_m2=5, height_m=3).confidence, 0.5)


class ZoneAndKindTest(unittest.TestCase):
    def setUp(self):
        self.r = make_rules([
            std(zone="permit_zone", area=10.0, height=10.0),
            std(zone="prohibited_zone", area=5.0, height=7.0),
            std(kind="壁面広告", zone="permit_zone", area=None, height=None, cond="壁面面積の5分の1以下"),
            std(kind="掛看板", zone="permit_zone", area=2.0, height=None),
            std(kind="広告塔・広告板", zone="other", area=20.0, height=None, labels=("商業地域",)),
        ])

    def test_unknown_place_splits_into_branches(self):
        res = J(self.r, area_m2=8, height_m=3, kind="自立")
        self.assertEqual(res.permit_required, NEEDS_MEASURE)
        self.assertEqual({b["verdict"] for b in res.branches}, {NOT_REQUIRED, REQUIRED})

    def test_place_resolves(self):
        self.assertEqual(J(self.r, area_m2=8, height_m=3, kind="自立", place="許可区域").permit_required, NOT_REQUIRED)
        self.assertEqual(J(self.r, area_m2=8, height_m=3, kind="自立", place="禁止区域").permit_required, REQUIRED)
        # 用途地域名: 禁止区域のラベルに入っていれば禁止側の基準
        self.assertEqual(J(self.r, area_m2=8, height_m=3, kind="自立", place="第一種低層住居専用地域").permit_required, REQUIRED)
        self.assertEqual(J(self.r, area_m2=8, height_m=3, kind="自立", place="準工業地域").permit_required, NOT_REQUIRED)

    def test_other_zone_label_matches(self):
        # 商業地域 の基準は 20㎡ -> 15㎡ は 許可区域一般の10㎡を超えるが 20㎡内: 割れるので 要実測
        res = J(self.r, area_m2=15, height_m=3, kind="自立", place="商業地域")
        self.assertEqual(res.permit_required, NEEDS_MEASURE)

    def test_kind_without_numeric_standard_needs_measurement(self):
        res = J(self.r, area_m2=3, height_m=3, kind="壁面", place="許可区域")
        self.assertEqual(res.permit_required, NEEDS_MEASURE)

    def test_kind_aliases(self):
        self.assertEqual(J(self.r, area_m2=1.5, kind="掛看板", place="許可区域").permit_required, NOT_REQUIRED)
        self.assertEqual(J(self.r, area_m2=2.5, kind="掛", place="許可区域").permit_required, REQUIRED)

    def test_bad_place(self):
        with self.assertRaises(ValueError):
            J(self.r, area_m2=1, place="どこか")

    def test_prohibited_zone_note(self):
        res = J(self.r, area_m2=8, height_m=3, kind="自立", place="禁止区域")
        self.assertTrue(any("許可を受ければ" in n for n in res.notes))


class GeneralUseAndTermTest(unittest.TestCase):
    def test_general_needs_permit_regardless_of_size(self):
        r = make_rules([std()])
        for area in (None, 0.1, 1000):
            res = J(r, use="general", area_m2=area)
            self.assertEqual(res.permit_required, REQUIRED)
            self.assertFalse(res.is_self_use_exempt)

    def test_permit_term_by_kind(self):
        r = make_rules([std(), std(kind="掛看板", area=2.0, height=None)])
        self.assertEqual(J(r, use="general", kind="掛看板").permit_term_years, 1)
        self.assertEqual(J(r, use="general", kind="自立").permit_term_years, 3)  # 自立 -> 広告塔 が一致しなくても標準
        self.assertEqual(J(r, use="general").permit_term_years, 3)

    def test_invalid_use(self):
        with self.assertRaises(ValueError):
            J(make_rules([std()]), use="x")


class InspectionTest(unittest.TestCase):
    def test_threshold(self):
        r = make_rules([std(height=None)])
        self.assertEqual(J(r, use="general", area_m2=5, height_m=3.9).inspection_required, NOT_REQUIRED)
        self.assertEqual(J(r, use="general", area_m2=5, height_m=4.0).inspection_required, REQUIRED)
        self.assertEqual(J(r, use="general", area_m2=5, height_m=None).inspection_required, NEEDS_MEASURE)

    def test_no_threshold_follows_permit(self):
        ins = {"required": "yes", "conditions": [], "applies_to_self_use": None, "qualification": None, "form": None,
               "interval_years": None, "citations": [dict(CITE)], "confidence": 0.7}
        r = make_rules([std(height=None)], inspection=ins)
        self.assertEqual(J(r, area_m2=20).inspection_required, REQUIRED)           # 許可が要る -> 点検も
        self.assertEqual(J(r, area_m2=5).inspection_required, NEEDS_MEASURE)       # 許可不要の自家用 -> 原文だけでは決まらない
        r2 = copy.deepcopy(r); r2["inspection"]["applies_to_self_use"] = False
        self.assertEqual(J(r2, area_m2=5).inspection_required, NOT_REQUIRED)

    def test_unknown_and_no(self):
        for req, want in (("unknown", NEEDS_MEASURE), ("no", NOT_REQUIRED)):
            ins = {"required": req, "conditions": [], "applies_to_self_use": None, "qualification": None, "form": None,
                   "interval_years": None, "citations": [], "confidence": 0.5}
            self.assertEqual(J(make_rules([std()], inspection=ins), area_m2=5, height_m=1).inspection_required, want)


class NoOrdinanceTest(unittest.TestCase):
    def test_unsupported_prefecture(self):
        res = judge("千葉県市川市八幡1-1", area_m2=5)
        self.assertEqual(res.permit_required, NEEDS_MEASURE)
        self.assertIsNone(res.ordinance_key)
        self.assertTrue(res.notes)

    def test_unresolved_address(self):
        self.assertEqual(judge("中央区銀座", area_m2=5).permit_required, NEEDS_MEASURE)

    def test_bad_ordinance_key(self):
        with self.assertRaises(ValueError):
            judge(municipality="豊島区", area_m2=1)


class ExtractedRulesTest(unittest.TestCase):
    """コミット済みの rules_<自治体>.json(AI抽出の第1版)がスキーマどおりか、判定が通るか。"""

    def test_schema_of_committed_files(self):
        for key in ORDINANCE_FILES:
            with self.subTest(key=key):
                p = rules_path(key)
                self.assertTrue(p.exists(), p)
                doc = json.loads(p.read_text(encoding="utf-8"))
                self.assertEqual(S.validate_file(doc), [])
                self.assertEqual(doc["ordinance_key"], key)
                self.assertIn("検算前", doc["status"])
                cc = doc["citation_check"]
                self.assertGreater(cc["total"], 0)
                self.assertEqual(cc["verified"], cc["total"], "原文に一致しない引用がある")
                for src in doc["sources"]:
                    self.assertTrue((DATA_DIR / src["file"]).exists())

    def test_judge_runs_on_every_ordinance(self):
        for key in ORDINANCE_FILES:
            for use in ("self", "general"):
                with self.subTest(key=key, use=use):
                    res = judge(municipality=key, use=use, area_m2=8.5, height_m=3.2)
                    self.assertIn(res.permit_required, (REQUIRED, NOT_REQUIRED, NEEDS_MEASURE))
                    self.assertIn(res.inspection_required, (REQUIRED, NOT_REQUIRED, NEEDS_MEASURE))
                    self.assertTrue(res.citations)
                    self.assertTrue(0 <= res.confidence <= 1)

    def test_none_dimensions_on_real_rules(self):
        for key in ORDINANCE_FILES:
            with self.subTest(key=key):
                res = judge(municipality=key, use="self", area_m2=None, height_m=None)
                self.assertEqual(res.permit_required, NEEDS_MEASURE)

    def test_validator_rejects_bad_shapes(self):
        doc = json.loads(rules_path("越谷市").read_text(encoding="utf-8"))
        bad = copy.deepcopy(doc)
        bad["rules"]["self_use"]["standards"][0]["zone"] = "nowhere"
        bad["rules"]["permit_term"]["default_years"] = "3年"
        bad["rules"]["inspection"]["required"] = "maybe"
        bad["rules"]["prohibited_zones"]["citations"][0]["quote"] = "あ" * 101
        bad["status"] = "確定版"
        errs = "\n".join(S.validate_file(bad))
        for frag in ("zone は", "default_years", "inspection.required", "100 字", "検算前"):
            self.assertIn(frag, errs)


if __name__ == "__main__":
    unittest.main()
