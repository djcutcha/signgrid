import unittest

from kanban_karte.intake.features import page_features
from kanban_karte.intake.ocr_classifier import decide
from kanban_karte.intake.ocr_grouper import HeaderKey, group_keys, parse_header, same_sign
from kanban_karte.ocr.vision import Line
from PIL import Image


def header_lines(seq: str | None = "P.1", item: str | None = None, photo: str | None = None,
                 name: str | None = "壁面パネル") -> list[Line]:
    ls = [Line("申請No.", 1.0, 0.12, 0.45, 0.04, 0.04), Line("アイテムNo.", 1.0, 0.17, 0.45, 0.05, 0.04),
          Line("写真No.", 1.0, 0.24, 0.45, 0.04, 0.04), Line("店舗No.", 1.0, 0.23, 0.32, 0.05, 0.04)]
    if item:
        ls.append(Line(item, 1.0, 0.19, 0.50, 0.01, 0.04))
    if photo:
        ls.append(Line(photo, 1.0, 0.25, 0.50, 0.01, 0.04))
    if name:
        ls.append(Line(name, 1.0, 0.15, 0.59, 0.10, 0.06))
    if seq:
        ls.append(Line(seq, 1.0, 0.90, 0.31, 0.03, 0.05))
    return ls


class ParseHeaderTest(unittest.TestCase):
    def test_values_follow_their_label_column(self):
        k = parse_header(header_lines(item="5", photo="4"))
        self.assertEqual((k.item, k.photo, k.name, k.seq), ("5", "4", "壁面パネル", 1))

    def test_value_under_application_no_is_ignored(self):
        ls = header_lines() + [Line("1", 1.0, 0.13, 0.50, 0.01, 0.03)]  # 申請No の列
        self.assertIsNone(parse_header(ls).item)

    def test_survey_format(self):
        k = parse_header([Line("店舗名", 1, .1, .3, .1, .1), Line("調査日", 1, .3, .3, .1, .1)])
        self.assertTrue(k.survey)


class GroupKeysTest(unittest.TestCase):
    def test_seq_increments_continue_and_p1_starts_new(self):
        keys = [HeaderKey(seq=n) for n in (1, 2, 3, 1, 2, 1)]
        self.assertEqual(group_keys(keys), [[0, 1, 2], [3, 4], [5]])

    def test_unreadable_seq_falls_back_to_fields(self):
        a = HeaderKey(item="2", photo="1", name="グリーンベルト")
        self.assertTrue(same_sign(a, HeaderKey(item="2", photo="1", name="グリーンベルト")))
        self.assertFalse(same_sign(a, HeaderKey(item="5", photo="1", name="グリーンベルト")))
        self.assertFalse(same_sign(a, HeaderKey()))  # 共通に読めた項目が無い → 混ぜない

    def test_survey_is_one_sign_per_face(self):
        keys = [HeaderKey(survey=True)] * 3
        self.assertEqual(group_keys(keys), [[0], [1], [2]])


class DecideTest(unittest.TestCase):
    F = {"n_cells": 0.0}

    def test_phrases(self):
        self.assertEqual(decide("危険予知活動", self.F, 3)[0], "危険予知")
        self.assertEqual(decide("作業 指示書 NO", self.F, 1)[0], "指示書")
        self.assertEqual(decide("広告物カルテ", self.F, 2)[0], "点検表")

    def test_report_needs_fields_and_photo_cells(self):
        self.assertEqual(decide("店舗No. アイテムNo.", self.F, 2)[0], "完成報告書")
        self.assertEqual(decide("店舗名 調査日", self.F, 2)[0], "不明")
        self.assertEqual(decide("店舗名 調査日", {"n_cells": 3.0}, 2)[0], "完成報告書")

    def test_fallbacks(self):
        self.assertEqual(decide("", self.F, 1)[0], "指示書")
        self.assertEqual(decide("", self.F, 5)[0], "不明")


class FeaturesTest(unittest.TestCase):
    def test_blank_page(self):
        f = page_features(Image.new("RGB", (450, 636), "white"), 90.0)
        self.assertEqual((f["n_cells"], f["ink"]), (0.0, 0.0))


if __name__ == "__main__":
    unittest.main()
