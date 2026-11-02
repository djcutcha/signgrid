"""4つの義務(管理 / 点検 / 有資格者 / 報告)のスキーマ検査と、旧 inspection だけの規則との互換。"""
import copy
import unittest

from kanban_karte.legal import schema as S
from tests.test_legal_judge import CITE, make_rules, std


def cond(**kw):
    c = {"area_min_m2": None, "height_min_m": None, "height_min_inclusive": True, "ground_clearance_min_m": None,
         "permit_required_only": False, "kinds": [], "excluded_kinds": [], "note": None}
    c.update(kw)
    return c


def duties():
    return {
        "maintenance_duty": {"required": "yes", "subject": "広告物の表示者等", "conditions": cond(),
                             "citations": [dict(CITE)], "confidence": 0.9},
        "inspection_duty": {"required": "yes", "absent_in_text": False, "interval_years": 3, "subject": "表示者",
                            "exclusions": ["はり紙"], "conditions": cond(), "citations": [dict(CITE)], "confidence": 0.9},
        "qualified_inspector": {"required": "duty", "conditions": cond(height_min_m=4, height_min_inclusive=False,
                                                                       permit_required_only=True),
                                "effort": {"conditions": cond(height_min_m=4, height_min_inclusive=False), "citations": [dict(CITE)]},
                                "qualification": "屋外広告士", "citations": [dict(CITE)], "confidence": 0.9},
        "report_duty": {"required": "conditional", "trigger": "許可・更新の申請時", "form": "様式第一号の二",
                        "permit_required_only": True, "conditions": cond(permit_required_only=True),
                        "citations": [dict(CITE)], "confidence": 0.9},
    }


def new_rules():
    r = make_rules([std()])
    r.pop("inspection")
    r.update(duties())
    return r


class DutiesSchemaTest(unittest.TestCase):
    def test_new_form_is_valid_without_legacy_inspection(self):
        self.assertEqual(S.validate_rules(new_rules()), [])

    def test_legacy_inspection_only_is_still_valid(self):
        self.assertFalse(S.has_duties(make_rules([std()])))
        self.assertEqual(S.validate_rules(make_rules([std()])), [])

    def test_both_forms_together_are_valid(self):
        r = new_rules()
        r["inspection"] = make_rules([std()])["inspection"]
        self.assertEqual(S.validate_rules(r), [])

    def test_missing_both_is_an_error(self):
        r = new_rules()
        for k in S.DUTY_KEYS:
            r.pop(k)
        self.assertTrue(any("inspection がない" in e for e in S.validate_rules(r)))

    def test_partial_duties_are_an_error(self):
        r = new_rules()
        r.pop("report_duty")
        self.assertTrue(any("report_duty がない" in e for e in S.validate_rules(r)))

    def test_value_domains(self):
        for key, bad, frag in (("maintenance_duty", "conditional", "maintenance_duty.required"),
                               ("inspection_duty", "duty", "inspection_duty.required"),
                               ("qualified_inspector", "yes", "qualified_inspector.required"),
                               ("report_duty", "duty", "report_duty.required")):
            with self.subTest(key=key):
                r = new_rules()
                r[key]["required"] = bad
                self.assertTrue(any(frag in e for e in S.validate_rules(r)), S.validate_rules(r))
        for key, good in (("inspection_duty", "conditional"), ("qualified_inspector", "effort"),
                          ("qualified_inspector", "no"), ("report_duty", "no"), ("maintenance_duty", "unknown")):
            with self.subTest(key=key, good=good):
                r = new_rules()
                r[key]["required"] = good
                if good in ("no", "unknown"):
                    r[key]["citations"] = []
                self.assertEqual(S.validate_rules(r), [])

    def test_conditions_types(self):
        r = new_rules()
        r["qualified_inspector"]["conditions"]["height_min_m"] = "四メートル"
        r["report_duty"]["conditions"]["permit_required_only"] = "yes"
        r["inspection_duty"]["conditions"]["kinds"] = "壁面"
        errs = " ".join(S.validate_rules(r))
        for frag in ("qualified_inspector.conditions.height_min_m", "report_duty.conditions.permit_required_only",
                     "inspection_duty.conditions.kinds"):
            self.assertIn(frag, errs)

    def test_duty_kind_and_any_of(self):
        r = new_rules()
        self.assertEqual(S.validate_rules(r), [])                                   # duty_kind が無ければ既定の inspection
        r["qualified_inspector"]["duty_kind"] = "manager"
        r["qualified_inspector"]["conditions"]["any_of"] = [{"height_min_m": 4, "height_min_inclusive": False}, {"area_min_m2": 10}]
        self.assertEqual(S.validate_rules(r), [])
        r["qualified_inspector"]["duty_kind"] = "x"
        self.assertTrue(any("duty_kind" in e for e in S.validate_rules(r)))
        r["qualified_inspector"]["duty_kind"] = "manager"
        r["qualified_inspector"]["conditions"]["any_of"] = [{"height_min_m": "4"}]
        self.assertTrue(any("any_of" in e for e in S.validate_rules(r)))

    def test_citations_required_when_duty_exists(self):
        r = new_rules()
        r["inspection_duty"]["citations"] = []
        r["qualified_inspector"]["effort"]["citations"] = []
        errs = " ".join(S.validate_rules(r))
        self.assertIn("inspection_duty.citations が空", errs)
        self.assertIn("qualified_inspector.effort.citations が空", errs)

    def test_absent_in_text_no_needs_no_citation(self):
        r = copy.deepcopy(new_rules())
        r["inspection_duty"].update(required="no", absent_in_text=True, citations=[], interval_years=None)
        self.assertEqual(S.validate_rules(r), [])

    def test_shape_text_mentions_all_four(self):
        for k in S.DUTY_KEYS:
            self.assertIn(f'"{k}"', S.RULES_SHAPE)
        self.assertNotIn('"inspection":', S.RULES_SHAPE)


if __name__ == "__main__":
    unittest.main()
