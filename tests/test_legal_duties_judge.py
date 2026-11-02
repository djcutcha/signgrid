"""4つの義務(管理 / 点検 / 有資格者 / 報告)の判定。合成した規則で、掛かり方の違いと境界値を固定する。"""
import copy
import unittest

from kanban_karte.legal.judge import EFFORT, NEEDS_MEASURE, NOT_REQUIRED, REQUIRED, judge
from tests.test_legal_duties_schema import cond, duties, new_rules
from tests.test_legal_judge import CITE, make_rules, std


def J(rules, **kw):
    kw.setdefault("municipality", "埼玉県")
    return judge(rules=rules, **kw)


def saitama_like():
    """点検は全部に3年周期、資格者は 4m 超かつ許可要が義務・4m 超なら努力、報告は許可・更新の申請者だけ。"""
    r = new_rules()
    r["inspection_duty"]["conditions"] = cond(excluded_kinds=["広告幕", "立看板"])
    r["report_duty"]["conditions"] = cond(permit_required_only=True)
    return r


def koshigaya_like():
    """点検をさせる義務(= 資格者の義務)は全部に掛かる。報告は許可・更新の申請者だけ。"""
    r = new_rules()
    r["inspection_duty"].update(interval_years=None, conditions=cond())
    r["qualified_inspector"].update(conditions=cond(), effort=None)
    return r


def tokyo_like():
    r = new_rules()
    r["inspection_duty"] = {"required": "no", "absent_in_text": True, "interval_years": None, "subject": None, "exclusions": [],
                            "conditions": cond(), "citations": [], "confidence": 0.8}
    r["qualified_inspector"] = {"required": "no", "conditions": cond(), "effort": None, "qualification": None,
                                "citations": [], "confidence": 0.8}
    r["report_duty"] = {"required": "conditional", "trigger": "許可・更新を受けるとき", "form": "別記第二号様式",
                        "permit_required_only": True,
                        "conditions": cond(permit_required_only=True, kinds=["広告塔", "広告板", "アーチ", "装飾街路灯"]),
                        "citations": [dict(CITE)], "confidence": 0.8}
    return r


def vals(res):
    return (res.maintenance_duty, res.inspection_duty, res.qualified_inspector, res.report_duty)


class SaitamaLikeTest(unittest.TestCase):
    def setUp(self):
        self.r = saitama_like()
        self.r["self_use"]["standards"] = [std(area=10.0, height=None)]

    def test_self_use_no_permit_still_inspected(self):
        res = J(self.r, area_m2=5, height_m=3.0)
        self.assertEqual(res.permit_required, NOT_REQUIRED)
        self.assertEqual(vals(res), (REQUIRED, REQUIRED, NOT_REQUIRED, NOT_REQUIRED))
        self.assertEqual(res.inspection_interval_years, 3)
        self.assertIn("許可不要のため", res.duties["report"]["text"])

    def test_tall_no_permit_is_effort(self):
        res = J(self.r, area_m2=5, height_m=5.0)
        self.assertEqual(vals(res), (REQUIRED, REQUIRED, EFFORT, NOT_REQUIRED))

    def test_tall_with_permit_is_duty_and_report(self):
        res = J(self.r, area_m2=20, height_m=5.0)
        self.assertEqual(res.permit_required, REQUIRED)
        self.assertEqual(vals(res), (REQUIRED, REQUIRED, REQUIRED, REQUIRED))

    def test_height_boundary_exceeds_is_exclusive(self):
        self.assertEqual(J(self.r, area_m2=20, height_m=4.0).qualified_inspector, NOT_REQUIRED)
        self.assertEqual(J(self.r, area_m2=20, height_m=4.01).qualified_inspector, REQUIRED)
        self.assertEqual(J(self.r, area_m2=5, height_m=4.0).qualified_inspector, NOT_REQUIRED)
        self.assertEqual(J(self.r, area_m2=5, height_m=4.01).qualified_inspector, EFFORT)

    def test_unknown_height_needs_measure(self):
        for area in (5, 20):
            with self.subTest(area=area):
                res = J(self.r, area_m2=area, height_m=None)
                self.assertEqual(res.qualified_inspector, NEEDS_MEASURE)
                self.assertIn("全高", res.duties["qualified"]["reason"])
                self.assertEqual(res.inspection_duty, REQUIRED)  # 点検は高さによらない

    def test_inspection_required_is_report_duty(self):
        for area, h in ((5, 3.0), (20, 5.0), (None, None)):
            res = J(self.r, area_m2=area, height_m=h)
            self.assertEqual(res.inspection_required, res.report_duty)

    def test_excluded_kind_drops_inspection_and_qualified(self):
        res = J(self.r, area_m2=20, height_m=5.0, kind="広告幕")
        self.assertEqual((res.inspection_duty, res.qualified_inspector), (NOT_REQUIRED, NOT_REQUIRED))
        self.assertEqual(res.maintenance_duty, REQUIRED)

    def test_unknown_permit_makes_report_unknown(self):
        res = J(self.r, area_m2=None, height_m=5.0)
        self.assertEqual(res.permit_required, NEEDS_MEASURE)
        self.assertEqual(res.report_duty, NEEDS_MEASURE)
        self.assertEqual(res.qualified_inspector, NEEDS_MEASURE)  # 許可次第で 義務 か 努力


class KoshigayaLikeTest(unittest.TestCase):
    def test_everything_applies_without_permit(self):
        r = koshigaya_like()
        r["self_use"]["standards"] = [std(area=10.0, height=None)]
        res = J(r, municipality="越谷市", area_m2=5, height_m=2.0)
        self.assertEqual(res.permit_required, NOT_REQUIRED)
        self.assertEqual(vals(res), (REQUIRED, REQUIRED, REQUIRED, NOT_REQUIRED))
        self.assertIsNone(res.inspection_interval_years)


class TokyoLikeTest(unittest.TestCase):
    def setUp(self):
        self.r = tokyo_like()
        self.r["self_use"]["standards"] = [std(area=10.0, height=None)]

    def test_no_inspection_article(self):
        res = J(self.r, municipality="東京都", area_m2=5, height_m=5.0)
        self.assertEqual(vals(res), (REQUIRED, NOT_REQUIRED, NOT_REQUIRED, NOT_REQUIRED))
        self.assertIn("規定は見当たりません", res.duties["inspection"]["text"])

    def test_report_follows_permit_and_kind(self):
        g = dict(municipality="東京都", use="general", area_m2=5, height_m=5.0)
        self.assertEqual(J(self.r, **g).report_duty, NEEDS_MEASURE)               # 種類が分からない
        self.assertEqual(J(self.r, kind="自立", **g).report_duty, REQUIRED)       # 広告塔・広告板
        self.assertEqual(J(self.r, kind="壁面", **g).report_duty, NOT_REQUIRED)   # 対象の種類でない


class CompatTest(unittest.TestCase):
    def test_legacy_rules_still_judge(self):
        r = make_rules([std()])
        res = J(r, municipality="越谷市", area_m2=5, height_m=5.0, use="general")
        self.assertEqual(res.inspection_required, REQUIRED)
        self.assertEqual(res.report_duty, res.inspection_required)
        self.assertEqual((res.maintenance_duty, res.inspection_duty, res.qualified_inspector), (NEEDS_MEASURE,) * 3)

    def test_to_dict_has_four_duties(self):
        d = J(saitama_like(), area_m2=5, height_m=5.0).to_dict()
        for k in ("maintenance_duty", "inspection_duty", "inspection_interval_years", "qualified_inspector", "report_duty", "duties"):
            self.assertIn(k, d)
        self.assertEqual(set(d["duties"]), {"maintenance", "inspection", "qualified", "report"})
        for e in d["duties"].values():
            self.assertTrue(e["title"] and e["text"])

    def test_refs_are_formatted_with_ordinance_name(self):
        r = saitama_like()
        r["inspection_duty"]["citations"] = [{"source": "条例", "article": "第14条の2第1項", "quote": "q", "quote_verified": True},
                                             {"source": "施行規則", "article": "第10条の4第1項", "quote": "q", "quote_verified": True}]
        res = J(r, area_m2=5, height_m=3.0)
        self.assertEqual(res.duties["inspection"]["refs"], ["埼玉県条例 第14条の2 第1項", "規則 第10条の4 第1項"])
        self.assertIn("3年ごとに点検 義務あり(埼玉県条例 第14条の2 第1項、規則 第10条の4 第1項)", res.duties["inspection"]["text"])

    def test_effort_only_form(self):
        r = copy.deepcopy(saitama_like())
        r["qualified_inspector"].update(required="effort", conditions=cond(height_min_m=4, height_min_inclusive=False), effort=None)
        self.assertEqual(J(r, area_m2=20, height_m=5.0).qualified_inspector, EFFORT)
        self.assertEqual(J(r, area_m2=20, height_m=3.0).qualified_inspector, NOT_REQUIRED)
        self.assertEqual(J(r, area_m2=20, height_m=None).qualified_inspector, NEEDS_MEASURE)


class AnyOfTest(unittest.TestCase):
    """寸法の OR 条件(高さ4m超 または 面積10㎡超)。"""
    C = cond(height_min_inclusive=False, any_of=[{"height_min_m": 4, "height_min_inclusive": False},
                                                 {"area_min_m2": 10, "area_min_inclusive": False}])

    def ev(self, area, height):
        from kanban_karte.legal.duties import eval_conditions
        return eval_conditions(self.C, area=area, height=height, gc=None, permit=REQUIRED, words=None)[0]

    def test_cases(self):
        for area, height, want in ((5, 5, "hit"), (12, 3, "hit"), (12, None, "hit"), (None, 5, "hit"), (5, 3, "miss"),
                                   (10, 4, "miss"), (5, None, "unknown"), (None, 3, "unknown"), (None, None, "unknown")):
            with self.subTest(area=area, height=height):
                self.assertEqual(self.ev(area, height), want)

    def test_duty_kind_default_is_inspection(self):
        res = J(koshigaya_like(), municipality="越谷市", area_m2=5, height_m=3)
        self.assertNotIn("duty_kind", res.duties["qualified"])
        self.assertIn("有資格者に点検させる義務", res.duties["qualified"]["text"])


class RealRulesTest(unittest.TestCase):
    """抽出済みの rules_*.json で、条文が明確な物を固定値で。種類= 自立(広告塔・広告板)、場所= 許可区域。"""

    def go(self, key, use, area, height):
        return judge(municipality=key, use=use, area_m2=area, height_m=height, kind="自立", place="許可区域")

    def test_saitama_pref(self):
        for use, area, h, permit, want in (
                ("self", 1.0, 5.0, NOT_REQUIRED, (REQUIRED, REQUIRED, EFFORT, NOT_REQUIRED)),     # 許可不要でも 4m 超は努力義務
                ("self", 1.0, 3.0, NOT_REQUIRED, (REQUIRED, REQUIRED, NOT_REQUIRED, NOT_REQUIRED)),
                ("self", 30.0, 5.0, REQUIRED, (REQUIRED, REQUIRED, REQUIRED, REQUIRED)),           # 4m 超かつ許可要= 義務
                ("self", 30.0, 3.0, REQUIRED, (REQUIRED, REQUIRED, NOT_REQUIRED, REQUIRED)),
                ("general", 5.0, 5.0, REQUIRED, (REQUIRED, REQUIRED, REQUIRED, REQUIRED))):
            with self.subTest(use=use, area=area, h=h):
                res = self.go("埼玉県", use, area, h)
                self.assertEqual(res.permit_required, permit)
                self.assertEqual(vals(res), want)
                self.assertEqual(res.inspection_interval_years, 3)
        res = self.go("埼玉県", "self", 1.0, None)
        self.assertEqual(res.qualified_inspector, NEEDS_MEASURE)                                   # 全高が分からなければ 4m 判定ができない
        self.assertEqual((res.maintenance_duty, res.inspection_duty), (REQUIRED, REQUIRED))
        self.assertIn("埼玉県条例 第14条の2", self.go("埼玉県", "self", 1.0, 5.0).duties["qualified"]["text"])

    def test_koshigaya_and_saitama_city(self):
        for key in ("越谷市", "さいたま市"):
            for use, area, permit, report in (("self", 1.0, NOT_REQUIRED, NOT_REQUIRED), ("self", 30.0, REQUIRED, REQUIRED),
                                              ("general", 5.0, REQUIRED, REQUIRED)):
                with self.subTest(key=key, use=use, area=area):
                    res = self.go(key, use, area, 3.0)
                    self.assertEqual(res.permit_required, permit)
                    self.assertEqual(vals(res), (REQUIRED, REQUIRED, REQUIRED, report))         # 許可不要の自家用にも 点検・資格者 は掛かる
                    self.assertIsNone(res.inspection_interval_years)                            # 規則に周期なし

    def test_tokyo(self):
        for use, area, permit, report in (("self", 1.0, NOT_REQUIRED, NOT_REQUIRED), ("self", 30.0, REQUIRED, REQUIRED),
                                          ("general", 5.0, REQUIRED, REQUIRED)):
            with self.subTest(use=use, area=area):
                res = self.go("東京都", use, area, 5.0)
                self.assertEqual(res.permit_required, permit)
                self.assertEqual(vals(res), (REQUIRED, NOT_REQUIRED, report, report))           # 点検の条文なし。有資格者は『管理者を置く』(許可物件のみ)
        self.assertIn("見当たりません", self.go("東京都", "general", 5.0, 5.0).duties["inspection"]["text"])

    def test_tokyo_manager_text_and_size_or(self):
        t = self.go("東京都", "general", 5.0, 5.0).duties["qualified"]
        self.assertEqual(t["duty_kind"], "manager")
        self.assertIn("有資格の屋外広告物管理者を置く義務: 要", t["text"])
        self.assertIn("条例 第25条", t["text"])
        self.assertIn("規則 第2条", t["text"])
        small = self.go("東京都", "general", 5.0, 3.0)                                       # 3m かつ 5㎡: どちらも超えない
        self.assertEqual(small.qualified_inspector, NOT_REQUIRED)
        self.assertEqual(small.duties["inspection"]["value"], NOT_REQUIRED)
        self.assertEqual(self.go("東京都", "general", 12.0, 3.0).qualified_inspector, REQUIRED)   # 面積だけ超過
        self.assertEqual(self.go("東京都", "general", 12.0, None).qualified_inspector, REQUIRED)  # 片方が超えていれば全高不明でも確定
        self.assertEqual(self.go("東京都", "general", 5.0, None).qualified_inspector, NEEDS_MEASURE)
        self.assertEqual(self.go("東京都", "general", None, None).qualified_inspector, NEEDS_MEASURE)
        self.assertEqual(self.go("東京都", "general", None, 5.0).qualified_inspector, REQUIRED)   # 高さだけ超過
        self.assertEqual(self.go("東京都", "general", 10.0, 4.0).qualified_inspector, NOT_REQUIRED)  # 4m・10㎡ちょうどは『超える』でない

    def test_tokyo_manager_permit_exempt_self_use(self):
        res = self.go("東京都", "self", 1.0, 5.0)
        self.assertEqual(res.qualified_inspector, NOT_REQUIRED)
        self.assertIn("許可物件のみ", res.duties["qualified"]["text"])

    def test_tokyo_manager_unknown_kind_small_is_needs_measure(self):
        res = judge(municipality="東京都", use="general", area_m2=5, height_m=3, place="許可区域")
        self.assertEqual(res.qualified_inspector, NEEDS_MEASURE)   # アーチかもしれないので寸法で『不要』にしない

    def test_tokyo_manager_kind(self):
        g = dict(municipality="東京都", use="general", area_m2=5, height_m=3, place="許可区域")
        self.assertEqual(judge(kind="壁面", **g).qualified_inspector, NOT_REQUIRED)
        for kind in ("アーチ", "装飾街路灯"):
            self.assertEqual(judge(kind=kind, **g).qualified_inspector, REQUIRED)   # 寸法によらない
        self.assertEqual(judge(kind="自立", **g).qualified_inspector, NOT_REQUIRED)  # 3m かつ 5㎡ の広告塔・広告板

    def test_kind_not_in_tokyo_report_kinds(self):
        res = judge(municipality="東京都", use="general", area_m2=5, height_m=3, kind="壁面", place="許可区域")
        self.assertEqual(res.report_duty, NOT_REQUIRED)  # 広告塔・広告板・アーチ・装飾街路灯に限る


if __name__ == "__main__":
    unittest.main()
