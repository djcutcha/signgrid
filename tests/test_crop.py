import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from kanban_karte.eval import crop
from kanban_karte.eval import run_eval

PHOTO = (110, 150, 90)


def make_page(width=745, height=1053, photos=None, yellow_under=()):
    """白い用紙に、ヘッダの罫線・文字っぽい線、写真(緑の塗り)、写真下のキャプション欄を描く。"""
    im = Image.new("RGB", (width, height), "white")
    d = ImageDraw.Draw(im)
    # ヘッダ帯: 細い罫線と文字行(密度が低い)
    for y in (60, 90, 120, 150):
        d.line((70, y, width - 70, y), fill=(60, 60, 60), width=1)
    for x in range(80, width - 80, 14):
        d.rectangle((x, 100, x + 6, 110), fill=(30, 30, 30))
    for k, (x0, y0, x1, y1) in enumerate(photos or []):
        d.rectangle((x0, y0, x1, y1), fill=PHOTO)
        # キャプション欄(写真の下の 2 本の罫線と文字)
        d.line((x0, y1 + 12, x1, y1 + 12), fill=(0, 0, 0))
        d.line((x0, y1 + 30, x1, y1 + 30), fill=(0, 0, 0))
        d.text((x0 + 4, y1 + 16), "caption", fill=(0, 0, 0))
        if k in yellow_under:
            d.rectangle((x0, y1 + 1, x1, y1 + 11), fill=(255, 255, 0))
    return im


GRID = [(70, 210, 350, 400), (390, 210, 680, 400), (70, 440, 350, 630), (390, 440, 680, 630)]


class FindCellsTest(unittest.TestCase):
    def test_finds_only_photo_rects(self):
        cells = crop.find_cells(make_page(photos=GRID))
        self.assertEqual(len(cells), 4)
        for c, (x0, y0, x1, y1) in zip(cells, GRID):
            self.assertLessEqual(abs(c.x0 - x0), 2)
            self.assertLessEqual(abs(c.x1 - (x1 + 1)), 2)
            self.assertLessEqual(abs(c.y0 - y0), 2)
            self.assertLess(c.y1, y1 + 2)  # 下端は写真の端より内側(キャプション欄に入らない)
            self.assertGreater(c.y0, 200)  # ヘッダ帯(y<160)は入らない

    def test_blank_page_and_empty_frames(self):
        self.assertEqual(crop.find_cells(make_page()), [])
        im = make_page()
        d = ImageDraw.Draw(im)
        for x0, y0, x1, y1 in GRID:  # 空の写真枠(線だけ)
            d.rectangle((x0, y0, x1, y1), outline=(0, 0, 0))
        self.assertEqual(crop.find_cells(im), [])

    def test_yellow_caption_band_trimmed(self):
        cells = crop.find_cells(make_page(photos=GRID, yellow_under={0}))
        self.assertEqual(len(cells), 4)
        self.assertLessEqual(cells[0].y1, GRID[0][3] - crop.BOTTOM_MARGIN + 2)

    def test_two_up_halves(self):
        photos = [(800, 210, 1100, 400), (1140, 210, 1440, 400)]  # 右面だけ
        im = make_page(width=1489, photos=photos)
        cells = crop.find_cells(im)
        self.assertEqual([c.half for c in cells], ["R", "R"])
        self.assertEqual(crop.find_cells(im, "L"), [])
        self.assertEqual(len(crop.find_cells(im, "R")), 2)

    def test_half_on_single_page_uses_whole_page(self):
        self.assertEqual(len(crop.find_cells(make_page(photos=GRID), "L")), 4)

    def test_weak_row_is_regularized(self):
        # 2 段目の左の写真を、上半分が白(検出されない)にした → 右の写真の高さに合わせて広げられる
        im = make_page(photos=GRID)
        d = ImageDraw.Draw(im)
        d.rectangle((70, 440, 350, 520), fill="white")
        cells = crop.find_cells(im)
        left2 = [c for c in cells if c.x0 < 100 and c.y0 > 400]
        self.assertEqual(len(left2), 1)
        self.assertLessEqual(left2[0].y0, 442)

    def test_min_size_and_aspect(self):
        im = make_page(photos=[(100, 250, 130, 400)])  # 細すぎる
        self.assertEqual(crop.find_cells(im), [])


class SpecAndItemTest(unittest.TestCase):
    def test_parse_page_spec(self):
        self.assertEqual(crop.parse_page_spec("11R"), [(11, "R")])
        self.assertEqual(crop.parse_page_spec("3L,3R"), [(3, "L"), (3, "R")])
        self.assertEqual(crop.parse_page_spec("7,8"), [(7, None), (8, None)])
        self.assertEqual(crop.parse_page_spec(""), [])

    def test_crop_item_writes_cells_and_applies_rotation(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            pages = root / "pages" / "B1"
            pages.mkdir(parents=True)
            make_page(photos=GRID).rotate(90, expand=True).save(pages / "p-001.jpg", quality=95)
            # 回転角 270 で起こす(90 度回して保存したページを元に戻す)
            paths = crop.crop_item(pages, "1", root / "out", max_cells=3, rots={("B1", 1): 270})
            self.assertEqual(len(paths), 3)  # max_cells で打ち切る
            self.assertTrue(all(Path(p).exists() for p in paths))
            self.assertEqual(crop.crop_item(pages, "9", root / "out2"), [])  # ページなし → 空

    def test_load_rotations(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "page_index.tsv"
            p.write_text("id\tpage\tkind\tbasis\trot\nB1\t3\tx\ty\t270\nB1\t4\tx\ty\t0\n", encoding="utf-8")
            self.assertEqual(crop.load_rotations(p), {("B1", 3): 270})


class LogTest(unittest.TestCase):
    def test_log_row_and_append(self):
        o = {"n": 10, "exact_rate": 0.5, "within1_rate": 0.9, "miss_rate": 0.25, "false_alarm_rate": 0.1,
             "no_answer": 1, "items": {"cells": 20, "cells_unanswered": 3, "item_agreement_rate": 0.4}}
        r = {"created": "2026-11-03T10:00:00", "judge": "claude", "source": "all", "split": "eval",
             "limit_per_source": 30, "seed": 0, "model": "m", "effort": "low", "overall": o,
             "usage": {"signs_with_usage": 10, "input_tokens": 123456, "cost_usd": 0.5}}
        row = run_eval.log_row(r, "テスト", 6)
        self.assertEqual(row.count("|"), 14)
        for want in ("2026-11-03", "limit=30 seed=0", "写真6枚まで", "50.0%", "看板1/項目3", "123,456", "$0.50", "low"):
            self.assertIn(want, row)
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "EVAL.md"
            p.write_text("# x\n\n" + run_eval.LOG_HEADING + "\n\n| a |\n|---|", encoding="utf-8")
            run_eval.append_log(row, p)
            run_eval.append_log(row, p)
            lines = p.read_text(encoding="utf-8").splitlines()
            self.assertEqual(lines[-1], row)
            self.assertEqual(lines[-2], row)
            self.assertEqual(lines[-3], "|---|")
            with self.assertRaises(RuntimeError):
                p.write_text("no heading", encoding="utf-8")
                run_eval.append_log(row, p)


if __name__ == "__main__":
    unittest.main()
