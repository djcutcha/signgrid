import shutil
import tempfile
import unittest
from pathlib import Path

import pypdf
from PIL import Image

from kanban_karte.eval import crop
from kanban_karte.intake.classifier import Classification, PageClassifier, HeuristicClassifier
from kanban_karte.intake.pdf_bundle import PerPageGrouper, SignGrouper, intake_pdf, sha256_file
from kanban_karte.ledger import Ledger
from tests.test_crop import GRID, make_page


def make_pdf(path: Path, pages: list[Image.Image], resolution: float = 90.0) -> Path:
    first, rest = pages[0].convert("RGB"), [p.convert("RGB") for p in pages[1:]]
    first.save(path, "PDF", save_all=True, append_images=rest, resolution=resolution)
    return path


class IntakeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        # 1ページ目: 指示書(写真なし)、2ページ目: 完成報告書(写真4)、3ページ目: 何もない
        self.pdf = make_pdf(self.root / "b.pdf", [Image.new("RGB", (745, 1053), "white"),
                                                  make_page(photos=GRID), Image.new("RGB", (745, 1053), "white")])
        self.ledger = Ledger()

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_register_classify_and_draft(self):
        r = intake_pdf(self.ledger, self.pdf, self.root / "data")
        self.assertFalse(r.existed)
        self.assertEqual(r.pages, 3)
        self.assertEqual(r.kind_counts, {"不明": 1, "完成報告書": 1, "指示書": 1})
        pages = self.ledger.list_pages(r.bundle_id)
        self.assertEqual([p["page_no"] for p in pages], [1, 2, 3])
        self.assertEqual([p["kind"] for p in pages], ["指示書", "完成報告書", "不明"])
        self.assertTrue(all(Path(p["image_path"]).exists() for p in pages))
        self.assertEqual(pages[1]["image_source"], "embedded")
        self.assertAlmostEqual(pages[1]["dpi"], 90.0, delta=0.5)
        self.assertIn("heuristic", pages[1]["kind_basis"])
        self.assertEqual(self.ledger.get_bundle(r.bundle_id)["status"], "signs_drafted")
        # 完成報告書ページの写真セル → 看板の下書き(判定は未確定)
        self.assertEqual(len(r.signs), 1)
        s = self.ledger.get_sign(r.signs[0])
        self.assertEqual(len(s.photos), 4)
        self.assertTrue(all(Path(p).exists() for p in s.photos))
        self.assertEqual((s.rank, s.items, s.source_bundle), (None, {}, r.bundle_id))

    def test_same_pdf_not_imported_twice(self):
        a = intake_pdf(self.ledger, self.pdf, self.root / "data")
        copy = shutil.copy(self.pdf, self.root / "copy.pdf")
        b = intake_pdf(self.ledger, copy, self.root / "data")
        self.assertTrue(b.existed)
        self.assertEqual(b.bundle_id, a.bundle_id)
        self.assertEqual(len(self.ledger.list_bundles()), 1)
        self.assertEqual(len(self.ledger.list_signs()), 1)

    def test_max_pages(self):
        r = intake_pdf(self.ledger, self.pdf, self.root / "data", max_pages=2)
        self.assertEqual(r.pages, 2)

    def test_custom_classifier_and_grouper(self):
        class AllPhotos(PageClassifier):
            name = "all-photos"

            def classify(self, page):
                return Classification("写真", f"{self.name}: テスト")

        r = intake_pdf(self.ledger, self.pdf, self.root / "data", classifier=AllPhotos())
        self.assertEqual(r.kind_counts, {"写真": 3})
        self.assertEqual(r.signs, [])  # 完成報告書がなければ看板の下書きも作らない

        class OneSignPerCell(SignGrouper):
            def group(self, pages):
                return [[(no, c)] for no, _h, cells in pages for c in cells]

        led2 = Ledger()
        r2 = intake_pdf(led2, self.pdf, self.root / "data2", grouper=OneSignPerCell())
        self.assertEqual(len(r2.signs), 4)
        led2.close()

    def test_bad_pdf_marks_bundle_error(self):
        bad = self.root / "bad.pdf"
        bad.write_bytes(b"not a pdf")
        with self.assertRaises(Exception):
            intake_pdf(self.ledger, bad, self.root / "data")
        b = self.ledger.list_bundles()[0]
        self.assertEqual(b["status"], "error")
        self.assertTrue(b["error"])

    def test_page_without_embedded_image(self):
        w = pypdf.PdfWriter()
        w.add_blank_page(595, 842)
        p = self.root / "blank.pdf"
        with open(p, "wb") as f:
            w.write(f)
        r = intake_pdf(self.ledger, p, self.root / "data")
        pg = self.ledger.list_pages(r.bundle_id)[0]
        if shutil.which("pdftoppm"):  # 埋め込み画像が無ければ 150dpi で描画する
            self.assertEqual(pg["image_source"], "rendered")
            self.assertAlmostEqual(pg["dpi"], 150.0, delta=0.1)
            self.assertTrue(Path(pg["image_path"]).exists())
        else:
            self.assertIsNone(pg["image_path"])
        self.assertEqual(pg["kind"], "不明" if not pg["image_path"] else "指示書")


class ClassifierTest(unittest.TestCase):
    def test_heuristic(self):
        from kanban_karte.intake.classifier import PageImage
        c = HeuristicClassifier()
        photo = PageImage(3, 5, make_page(photos=GRID), 90.0, "x.pdf")
        blank = Image.new("RGB", (745, 1053), "white")
        self.assertEqual(c.classify(photo).kind, "完成報告書")
        self.assertEqual(c.classify(PageImage(1, 5, blank, 90.0, "x.pdf")).kind, "指示書")
        self.assertEqual(c.classify(PageImage(2, 5, blank, 90.0, "x.pdf")).kind, "不明")
        with self.assertRaises(ValueError):
            Classification("へんな種別", "x")

    def test_crop_reuse_scales_with_dpi(self):
        # 高解像度ページでも同じセル数が取れる(eval の crop を再利用していること)
        hi = make_page(photos=GRID).resize((745 * 3, 1053 * 3))
        self.assertEqual(len(crop.cut_cells(hi, 270.0)), 4)

    def test_sha(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "a"
            p.write_bytes(b"abc")
            self.assertEqual(sha256_file(p), "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
        self.assertEqual(PerPageGrouper().group([(1, "-", [])]), [])


if __name__ == "__main__":
    unittest.main()
