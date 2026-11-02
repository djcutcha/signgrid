"""一周の実測(客先の 2026年 1店舗)の部品: 転記の照合・フォルダ名・索引・正解表・評価ソース line_paper。
小さな固定データだけ。画像・API は使わない。"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import field_index as fi  # noqa: E402
import field_transcribe as ft  # noqa: E402

from kanban_karte.eval import dataset  # noqa: E402


def rec(pdf, page, kind, no, rows=(), **kw):
    return {"pdf": pdf, "page": page, "sheet_kind": kind, "item_no": no, "sign_name": "x", "inspection_date": "d",
            "size": {"w_mm": 100}, "rows": [{"part": p, "check": c, "judgment": j, "confidence": 0.9} for p, c, j in rows], **kw}


class TranscribeTest(unittest.TestCase):
    def test_parse_page_normalizes_marks_and_numbers(self):
        d = ft.parse_page('```json\n{"sheet_kind":"通常","item_no":"3","rows":[{"part":"鉄骨","check":"錆","judgment":"△"},'
                          '{"part":"板金","check":"錆","judgment":"d"},{"part":"x","check":"y","judgment":"?"}]}\n```')
        self.assertEqual(d["item_no"], 3)
        self.assertEqual([r["judgment"] for r in d["rows"]], ["B", "D", "空"])

    def test_compare_cells(self):
        a = rec("通常", 1, "通常", 1, [("鉄骨", "錆", "D"), ("板金", "錆", "C"), ("照明", "錆", "空")])
        b = rec("通常", 1, "通常", 1, [("1.鉄骨", "錆", "D"), ("板金", "錆", "B"), ("照明", "錆", "空")])
        c = ft.compare(a, b)
        self.assertEqual((c["cells"], c["cells_match"]), (3, 2))
        self.assertEqual((c["marked_cells"], c["marked_match"]), (2, 1))  # 空同士は判定ありに数えない
        self.assertEqual(c["diffs"][0]["sonnet"], "C")
        self.assertTrue(c["item_no_match"])

    def test_duplicate_cells_are_indexed(self):
        r = rec("通常", 1, "通常", 1, [("支柱", "錆", "D"), ("支柱", "錆", "C")])
        self.assertEqual(list(ft.cell_map(r).values()), ["D", "C"])

    def test_pick_verify_is_stratified(self):
        recs = {("通常", i): rec("通常", i, "通常", i) for i in range(1, 9)}
        recs.update({("その他1", i): rec("その他1", i, "その他", i) for i in range(1, 20)})
        recs[("その他1", 30)] = rec("その他1", 30, "写真のみ", None)
        pages = ft.pick_verify(recs)
        self.assertEqual(sum(1 for p in pages if p[0] == "通常"), 5)
        self.assertEqual(sum(1 for p in pages if p[0] == "その他1"), 10)
        self.assertNotIn(("その他1", 30), pages)


class IndexTest(unittest.TestCase):
    def test_parse_folder(self):
        self.assertEqual(fi.parse_folder("12"), ("通常", [12], "single"))
        self.assertEqual(fi.parse_folder("他１"), ("他", [1], "single"))
        self.assertEqual(fi.parse_folder("他４　5"), ("他", [4, 5], "merged"))
        self.assertEqual(fi.parse_folder("他24　25"), ("他", [24, 25], "merged"))
        self.assertEqual(fi.parse_folder("他52 -2"), ("他", [52], "variant"))
        self.assertEqual(fi.parse_folder("KY")[0], "?")

    def test_shot_order_is_numeric(self):
        names = [Path(f"a_260901_{n}.jpg") for n in (10, 2, 1)]
        self.assertEqual([shot_n(p) for p in sorted(names, key=fi.shot_order)], [1, 2, 10])

    def test_group_pages_attaches_photo_page_and_unnumbered(self):
        recs = {("通常", 1): rec("通常", 1, "通常", 1), ("その他1", 1): rec("その他1", 1, "その他", 1),
                ("その他1", 2): rec("その他1", 2, "写真のみ", None), ("その他1", 3): rec("その他1", 3, "その他", None)}
        by_key, un = fi.group_pages(recs)
        self.assertEqual(sorted(by_key), [("他", 1), ("通常", 1)])
        self.assertEqual(by_key[("他", 1)][0]["photo_pages"], [2])
        self.assertEqual(len(un), 1)

    def test_build_index_and_items(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            from PIL import Image
            for folder, n in (("1", 3), ("2", 1), ("他1", 2), ("他3　4", 1), ("他9", 1)):
                (base / "raw" / folder).mkdir(parents=True)
                for i in range(n):
                    Image.new("RGB", (40, 30), (90, 120, 150)).save(base / "raw" / folder / f"L_260901_{n - i}.jpg")
            (base / "transcribed").mkdir()
            rows = [rec("通常", 1, "通常", 1, [("鉄骨", "錆", "D"), ("板金", "錆", "B"), ("ロゴ", "錆", "対象外"), ("照明", "錆", "空")]),
                    rec("その他1", 1, "その他", 1, [("取付方法", "ビス", "C")]),
                    rec("その他1", 2, "その他", 3, [("取付方法", "ビス", "D")]),
                    rec("その他1", 3, "その他", 4, [("取付方法", "ビス", "A")]),
                    rec("その他1", 4, "その他", 7, [("取付方法", "ビス", "D")])]
            (base / "transcribed" / "pages.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
            s = fi.build(base)
            idx = {r["key"]: r for r in csv.DictReader(open(base / "index.tsv", encoding="utf-8"), delimiter="\t")}
            self.assertEqual(idx["通常1"]["status"], "matched")
            self.assertEqual(idx["通常2"]["status"], "folder_only")
            self.assertEqual(idx["他3"]["status"], "merged_folder")
            self.assertEqual(idx["他4"]["status"], "merged_folder")
            self.assertEqual(idx["他7"]["status"], "pages_only")
            self.assertEqual(idx["他9"]["status"], "folder_only")
            self.assertEqual((s["matched"], s["folder_only"], s["pages_only"], s["merged_folder_signs"]), (2, 2, 1, 2))
            # 正解表 → 評価ソース
            cfg = base / "sources.toml"
            cfg.write_text(f'[eval]\nscan_root="{d}"\nnas_root="{d}"\n[eval.dirs]\nline_paper="{d}"\n', encoding="utf-8")
            items = {i.sign_id: i for i in dataset.load_all(cfg, ("line_paper",))}
            one = items["line_paper:通常1"]
            self.assertEqual(one.rank_true, 3)  # 最悪値 B
            self.assertEqual(one.item_judgments_true, {"鉄骨 錆": 1, "板金 錆": 3})
            self.assertEqual([Path(p).name for p in one.photo_paths], ["L_260901_1.jpg", "L_260901_2.jpg", "L_260901_3.jpg"])
            self.assertEqual(one.meta["form_items"], ["鉄骨 錆", "板金 錆", "ロゴ 錆", "照明 錆"])
            self.assertEqual(one.meta["sign_name"], "看板 ①")
            self.assertEqual(items["line_paper:他3"].photo_paths, [])  # 合体フォルダは写真なし(評価から除外)
            self.assertEqual(items["line_paper:他7"].photo_paths, [])
            self.assertIn(one.split, ("eval", "example"))
            self.assertEqual(dataset.summarize(list(items.values()))["line_paper"]["total"], 5)

    def test_line_paper_not_in_aggregate(self):
        self.assertIn("line_paper", dataset.RANKED_SOURCES)
        self.assertNotIn("line_paper", dataset.AGGREGATE_SOURCES)
        self.assertNotIn("line_paper", dataset.ALL_SOURCES)

    def test_paper_photo_rule(self):
        from PIL import Image, ImageDraw
        with tempfile.TemporaryDirectory() as d:
            paper = Image.new("RGB", (300, 400), "white")
            dr = ImageDraw.Draw(paper)
            for y in range(20, 400, 25):
                dr.line([(10, y), (290, y)], fill="black", width=3)
            paper.save(Path(d) / "p.jpg")
            Image.new("RGB", (300, 400), (60, 110, 160)).save(Path(d) / "s.jpg")
            self.assertTrue(fi.is_paper_photo(Path(d) / "p.jpg"))
            self.assertFalse(fi.is_paper_photo(Path(d) / "s.jpg"))


def shot_n(p: Path) -> int:
    return int(p.stem.rsplit("_", 1)[1])


if __name__ == "__main__":
    unittest.main()
