import unittest
from unittest import mock

from PIL import Image

from kanban_karte.intake.classifier import PageImage
from kanban_karte.pipeline.intake_run import PhotoFallbackClassifier


def _page(color):
    return PageImage(1, 1, Image.new("RGB", (800, 1100), color), 100.0, "t.jpg")


class PaperClassifyTest(unittest.TestCase):
    def test_white_paper_vs_dark_photo(self):
        self.enterContext(mock.patch("kanban_karte.pipeline.intake_run.looks_like_paper_by_ai", lambda im: False))  # API を呼ばない
        c = PhotoFallbackClassifier()
        self.assertEqual(c.classify(_page((250, 250, 250))).kind, "点検表")
        self.assertEqual(c.classify(_page((60, 70, 80))).kind, "写真")


if __name__ == "__main__":
    unittest.main()
