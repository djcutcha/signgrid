"""verify(手元の正解との照合)と table3(東京都 別表第三の再抽出)のオフライン検査。実データの値は使わない。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kanban_karte.legal import rules_extract as RX
from kanban_karte.legal import schema as S
from kanban_karte.legal import table3_extract as T3
from kanban_karte.legal import verify as V
from kanban_karte.legal.judge import judge


class NumberParseTest(unittest.TestCase):
    def test_kanji_and_units(self):
        t = V.truth_numbers("表示面積は十五平方メートル以下、高さ四・五メートル、期間は二年。三月以内。幅1.2m、面積30㎡")
        self.assertEqual(t["area"], {15.0, 30.0})
        self.assertEqual(t["length"], {4.5, 1.2})
        self.assertEqual(t["years"], {2.0, 0.25})

    def test_area_is_not_length(self):
        t = V.truth_numbers("10平方メートル")
        self.assertEqual(t["length"], set())

    def test_to_num(self):
        for s, v in (("百二十", 120), ("二十五", 25), ("十", 10), ("〇・五", 0.5), ("七", 7)):
            self.assertEqual(V._to_num(s), v, s)


class VerifyKeyTest(unittest.TestCase):
    def test_counts_and_names_without_values(self):
        doc = json.loads(RX.rules_path("越谷市").read_text(encoding="utf-8"))
        rules = copy.deepcopy(doc["rules"])
        rules["self_use"]["standards"][0]["area_max_m2"] = 777  # 正解にも原文にも無い
        with tempfile.TemporaryDirectory() as d:
            (Path(d) / "koshigaya_x.md").write_text("面積は10平方メートル以下。高さ12メートル。3年。", encoding="utf-8")
            r = V.verify_key("越谷市", rules, Path(d), ["koshigaya_x.md"])
        self.assertEqual(r["truth_files"], 1)
        self.assertTrue(any("standards[0]" in n and "area_max_m2" in n for n in r["nowhere"]))
        self.assertNotIn("777", json.dumps(r, ensure_ascii=False))
        self.assertEqual(r["total"] - r["matched"], len(r["mismatch"]))
        self.assertEqual(set(r["mismatch"]), set(r["only_source"]) | set(r["nowhere"]))

    def test_stamp(self):
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / "rules_越谷市.json").write_text(RX.rules_path("越谷市").read_text(encoding="utf-8"), encoding="utf-8")
            V.stamp({"key": "越谷市", "matched": 3, "total": 4, "mismatch": ["x"], "only_source": ["x"]}, dd)
            doc = json.loads((dd / "rules_越谷市.json").read_text(encoding="utf-8"))
        self.assertEqual(doc["status"], "第2版(手元の正解と照合済み、一致 3/4)")
        self.assertEqual(S.validate_file(doc), [])


class Table3Test(unittest.TestCase):
    def test_section_is_cut(self):
        t = T3.table3_text()
        self.assertTrue(t.startswith("別表第三"))
        self.assertIn("広告塔及び広告板", t)
        self.assertNotIn("別表第四", t.splitlines()[0])
        self.assertLess(len(t), 20000)

    def _fake_client(self, payload):
        text = json.dumps(payload, ensure_ascii=False)
        resp = SimpleNamespace(usage=SimpleNamespace(input_tokens=1000, output_tokens=500), stop_reason="end_turn",
                               content=[SimpleNamespace(type="text", text=text)])

        class St:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def get_final_message(s):
                return resp
        return SimpleNamespace(messages=SimpleNamespace(stream=lambda **kw: St()))

    def test_extract_and_apply(self):
        quote = T3.table3_text().splitlines()[3][:30]
        item = dict(kind="広告塔・広告板(土地に直接設置)", when=None, self_use_only=False, height_max_m=10,
                    ground_clearance_min_m=None, protrusion_max_m=None, area_max_m2=None, conditions=None,
                    citations=[dict(source="施行規則", article="別表第三 一(一)1", quote=quote)], confidence=0.9)
        res, u = T3.extract_table3(client=self._fake_client(dict(permit_standards=[item], extraction_notes=["n"])))
        self.assertEqual(res["citation_check"], dict(total=1, verified=1))
        with tempfile.TemporaryDirectory() as d:
            dd = Path(d)
            (dd / "rules_東京都.json").write_text(RX.rules_path("東京都").read_text(encoding="utf-8"), encoding="utf-8")
            T3.apply_table3(res, dd)
            doc = json.loads((dd / "rules_東京都.json").read_text(encoding="utf-8"))
        self.assertEqual(len(doc["rules"]["permit_standards"]), 1)
        self.assertEqual(S.validate_rules(doc["rules"]), [])

    def test_schema_rejects_bad_permit_standards(self):
        errs = "\n".join(S.validate_permit_standards([dict(kind="", self_use_only="no", height_max_m="10")]))
        for frag in ("kind", "self_use_only", "height_max_m"):
            self.assertIn(frag, errs)


class JudgePermitStandardsTest(unittest.TestCase):
    def test_height_over_standard_is_noted_not_decisive(self):
        r = judge(municipality="東京都", use="self", area_m2=3, height_m=40, kind="広告塔")
        self.assertTrue(any("許可の基準" in n for n in r.notes))
        self.assertTrue(any(c["field"] == "permit_standard" for c in r.citations))

    def test_no_note_without_kind(self):
        r = judge(municipality="東京都", use="self", area_m2=3, height_m=40)
        self.assertFalse(any("許可の基準" in n for n in r.notes))

    def test_ground_clearance_below_standard(self):
        r = judge(municipality="東京都", use="self", area_m2=3, ground_clearance_m=1.0, kind="突出")
        self.assertTrue(any("地上高" in n and "許可の基準" in n for n in r.notes))


if __name__ == "__main__":
    unittest.main()
