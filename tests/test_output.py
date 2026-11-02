import io
import json
import re
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook
from PIL import Image

from kanban_karte.ledger import Ledger, Sign, Site
from kanban_karte.ledger.items import ITEM_KEYS, RESULTS
from kanban_karte.output import client_a_form, common, tokyo_form
from kanban_karte.output.cli import main as cli_main

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "ledger.json"
SAMPLE_DOC = json.loads(SAMPLE.read_text(encoding="utf-8"))
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def photos_of(ws):
    """写真(JPEG)だけを数える。透かしは PNG なので混ざらない。"""
    return [i for i in ws._images if i.format == "jpeg"]


def item_rows(ws, col="B"):
    """18項目それぞれの行番号。項目名の文字列で探す(行位置を決め打ちしない)。"""
    rows = {}
    for key, _, text in common.ITEM_ROWS:
        hits = [c.row for c in ws[col] if c.value == text]
        assert len(hits) == 1, (key, hits)
        rows[key] = hits[0]
    return rows


def is_red(cell):
    return cell.font.color is not None and cell.font.color.rgb.endswith("C00000")


class SampleBase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ledger = Ledger(":memory:")
        self.ledger.import_json(SAMPLE)
        self.signs = {s["id"]: s for s in SAMPLE_DOC["signs"]}

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()


class TokyoFormTest(SampleBase):
    def make(self):
        out = self.dir / "t.xlsx"
        names = tokyo_form.write_tokyo_form(self.ledger, "demo-site-1", out)
        return load_workbook(out), names

    def test_sheets_per_sign(self):
        wb, names = self.make()
        self.assertEqual(names, ["様式_demo-001", "写真_demo-001", "様式_demo-002", "写真_demo-002",
                                 "様式_demo-003", "写真_demo-003"])
        self.assertEqual(wb.sheetnames, names)

    def test_18_rows_and_judgements_land_in_right_cells(self):
        wb, _ = self.make()
        for sid, sign in self.signs.items():
            ws = wb[f"様式_{sid}"]
            rows = item_rows(ws)
            self.assertEqual(len(rows), 18)
            self.assertEqual(list(rows) , ITEM_KEYS)
            for key, r in rows.items():
                marks = [ws.cell(r, col).value for col in range(3, 7)]  # C..F = 良好 経過観察 要改善 対象外
                res = sign["items"].get(key)
                if res is None:
                    self.assertEqual(marks, [None] * 4, (sid, key))
                else:
                    self.assertEqual(marks[RESULTS.index(res)], "○", (sid, key))
                    self.assertEqual(sum(m is not None for m in marks), 1, (sid, key))

    def test_unjudged_is_red_and_judged_is_not(self):
        wb, _ = self.make()
        ws = wb["様式_demo-003"]  # 4項目しか入っていない
        rows = item_rows(ws)
        unj = [k for k in ITEM_KEYS if k not in self.signs["demo-003"]["items"]]
        self.assertEqual(len(unj), 14)
        for k in unj:
            cell = ws.cell(rows[k], 7)
            self.assertEqual(cell.value, "未判定")
            self.assertTrue(is_red(cell))
        for k in self.signs["demo-003"]["items"]:
            self.assertNotEqual(ws.cell(rows[k], 7).value, "未判定")

    def test_items_to_write_marked(self):
        wb, _ = self.make()
        ws = wb["様式_demo-001"]
        rows = item_rows(ws)
        self.assertEqual(ws.cell(rows["支持部-1"], 7).value, "要記入")  # 要改善
        self.assertEqual(ws.cell(rows["基礎部・上部構造-3"], 7).value, "要記入")  # 経過観察
        self.assertIsNone(ws.cell(rows["基礎部・上部構造-1"], 7).value)  # 良好

    def test_header_fields_dates_and_inputs(self):
        wb, _ = self.make()
        ws = wb["様式_demo-001"]
        texts = {c.value for row in ws.iter_rows() for c in row if c.value}
        self.assertIn("自立", texts)
        self.assertIn("東京都豊島区見本町1-2-3", texts)
        self.assertIn("2026-11-05", texts)  # 点検年月日
        self.assertIn("下書き", "".join(str(t) for t in texts))
        for label in ("点検実施者 氏名", "資格(名称・番号)", "資格"):
            self.assertIn(label, texts)
        # 点検実施者の氏名・資格は空欄の入力欄
        for r in range(1, ws.max_row + 1):
            if ws.cell(r, 1).value in ("点検実施者 氏名", "資格(名称・番号)"):
                self.assertIsNone(ws.cell(r, 3).value)
        self.assertIn("下書き", ws.oddHeader.center.text)
        # 総合
        self.assertIn("3 要改善", texts)
        self.assertIn(self.signs["demo-001"]["remark"], texts)
        # 日付らしい文字列は全て YYYY-MM-DD
        for t in texts:
            if isinstance(t, str) and re.fullmatch(r"[\d/.\-]{8,10}", t):
                self.assertRegex(t, DATE)

    def test_photo_counts(self):
        wb, _ = self.make()
        self.assertEqual(len(photos_of(wb["写真_demo-001"])), 6)
        self.assertEqual(len(photos_of(wb["写真_demo-002"])), 3)
        self.assertEqual(len(photos_of(wb["写真_demo-003"])), 2)
        self.assertEqual(len(photos_of(wb["様式_demo-001"])), 0)  # 様式本体に写真は載せない

    def test_watermark_when_font_available(self):
        wb, _ = self.make()
        ws = wb["様式_demo-001"]
        pngs = [i for i in ws._images if i.format == "png"]
        if common._watermark_bytes() is None:
            self.assertEqual(pngs, [])  # フォントが無い環境ではバナーと印刷ヘッダだけ
        else:
            self.assertEqual(len(pngs), 1)


class ClientATest(SampleBase):
    def make(self):
        out = self.dir / "a.xlsx"
        names = client_a_form.write_client_a(self.ledger, "demo-site-1", out)
        return load_workbook(out), names

    def test_sheets(self):
        _, names = self.make()
        self.assertEqual(names, ["表紙", "看板一覧", "看板_demo-001", "看板_demo-002", "看板_demo-003"])

    def test_cover(self):
        wb, _ = self.make()
        ws = wb["表紙"]
        vals = {ws.cell(r, 1).value: ws.cell(r, 2).value for r in range(1, 20)}
        self.assertEqual(vals["現場名"], "見本ストア 北見台店")
        self.assertEqual(vals["調査日(YYYY-MM-DD)"], "2026-11-05")
        self.assertEqual(vals["総合評価"], "C")  # rank 3 が最悪
        self.assertEqual(vals["看板の数"], "3 基")
        # 内訳: A=1 B=1 C=1 D=0 未判定=0
        row = [r for r in range(1, 20) if ws.cell(r, 2).value == "A 良好"][0]
        self.assertEqual([ws.cell(row + 1, c).value for c in range(2, 7)], ["1 基", "1 基", "1 基", "0 基", "0 基"])

    def test_list_grades(self):
        wb, _ = self.make()
        ws = wb["看板一覧"]
        head = [c.value for c in ws[4]]
        rows = {ws.cell(r, 2).value: {h: ws.cell(r, i + 1).value for i, h in enumerate(head)} for r in range(5, 8)}
        d1 = rows["demo-001"]
        self.assertEqual((d1["腐食"], d1["破損"], d1["固定"], d1["総合"]), ("C", "C", "A", "C"))
        self.assertEqual(d1["前回との差"], "前回 A から悪化")
        self.assertEqual(d1["写真"], 6)
        self.assertEqual(d1["未判定項目"], 0)
        d2 = rows["demo-002"]
        self.assertEqual(d2["総合"], "B")
        self.assertEqual(d2["腐食"], "A(暫定)")  # 項目の一部が未記入
        self.assertEqual(d2["未判定項目"], "12 / 18")
        self.assertEqual(rows["demo-003"]["写真"], 2)

    def test_sign_page_items_and_photos(self):
        wb, _ = self.make()
        for sid, sign in self.signs.items():
            ws = wb[f"看板_{sid}"]
            rows = item_rows(ws)
            self.assertEqual(len(rows), 18)
            for key, r in rows.items():
                res = sign["items"].get(key)
                cell = ws.cell(r, 5)
                if res is None:
                    self.assertEqual(cell.value, "未判定")
                    self.assertTrue(is_red(cell))
                    self.assertIsNone(ws.cell(r, 6).value)
                else:
                    self.assertEqual(cell.value, res)
            self.assertEqual(len(photos_of(ws)), min(len(sign["photos"]), 6))
        ws = wb["看板_demo-001"]
        rows = item_rows(ws)
        self.assertEqual(ws.cell(rows["支持部-1"], 6).value, "C")
        self.assertEqual(ws.cell(rows["取付部-1"], 6).value, "-")  # 対象外
        self.assertEqual(ws.cell(rows["基礎部・上部構造-3"], 6).value, "B")

    def test_every_sheet_has_draft_banner(self):
        wb, _ = self.make()
        for ws in wb.worksheets:
            self.assertIn("下書き", ws["A1"].value, ws.title)
            self.assertIn("下書き", ws.oddHeader.center.text, ws.title)


class GradeLogicTest(unittest.TestCase):
    def test_perspective_grade(self):
        s = Sign("x", items={k: "良好" for k in ITEM_KEYS})
        self.assertEqual(client_a_form.perspective_grade(s, "腐食"), ("A", False))
        s.items["表示部-3"] = "経過観察"
        self.assertEqual(client_a_form.perspective_grade(s, "腐食"), ("B", False))
        self.assertEqual(client_a_form.perspective_grade(s, "固定"), ("A", False))  # 表示部-3 は固定に入らない
        s.items["支持部-2"] = "要改善"
        self.assertEqual(client_a_form.perspective_grade(s, "固定"), ("C", False))
        # 対象外だけ
        for k in client_a_form.PERSPECTIVES["固定"]:
            s.items[k] = "対象外"
        self.assertEqual(client_a_form.perspective_grade(s, "固定"), ("-", False))
        # 何も無い
        self.assertEqual(client_a_form.perspective_grade(Sign("y"), "破損"), (None, True))
        # 一部だけ
        self.assertEqual(client_a_form.perspective_grade(Sign("z", items={"表示部-4": "要改善"}), "破損"), ("C", True))

    def test_every_item_in_some_perspective(self):
        used = {k for ks in client_a_form.PERSPECTIVES.values() for k in ks}
        self.assertEqual(used, set(ITEM_KEYS))

    def test_site_grade(self):
        a, b = Sign("a", rank=1), Sign("b", rank=4)
        self.assertEqual(client_a_form.site_grade([a, b]), ("D", None))
        g, note = client_a_form.site_grade([a, Sign("c")])
        self.assertIsNone(g)
        self.assertIn("1/2", note)
        self.assertIn("A", note)


class EdgeCaseTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.ledger = Ledger(":memory:", data_dir=self.dir)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def png(self, name, size):
        Image.new("RGB", size, (30, 120, 200)).save(self.dir / name)
        return name

    def test_empty_sign_everything_unjudged(self):
        self.ledger.upsert_site(Site("s", "空の店"))
        self.ledger.upsert_sign(Sign("e1", site_id="s"))
        wb = tokyo_form.build_workbook(common.load_site(self.ledger, "s"))
        ws = wb["様式_e1"]
        red = [c for row in ws.iter_rows() for c in row if c.value == "未判定"]
        # 18項目 + 種類 + 場所 + 点検年月日 + 総合判定
        self.assertEqual(len(red), 18 + 4)
        self.assertTrue(all(is_red(c) for c in red))
        for r in item_rows(ws).values():
            self.assertEqual([ws.cell(r, col).value for col in range(3, 7)], [None] * 4)
        a = client_a_form.build_workbook(common.load_site(self.ledger, "s"))
        self.assertEqual(a["表紙"]["B5"].value, "空の店")
        un = [c.value for row in a["表紙"].iter_rows() for c in row if c.value == "未判定"]
        self.assertGreaterEqual(len(un), 3)  # 所在地・調査日・総合評価

    def test_photos_resized_to_640_and_capped_at_six(self):
        self.ledger.upsert_site(Site("s", "店"))
        names = [self.png(f"p{i}.png", (2000, 1000)) for i in range(8)]
        self.ledger.upsert_sign(Sign("p1", site_id="s", photos=names, rank=1, last_inspected="2026-11-05"))
        out = self.dir / "t.xlsx"
        tokyo_form.write_tokyo_form(self.ledger, "s", out)
        ws = load_workbook(out)["写真_p1"]
        imgs = photos_of(ws)
        self.assertEqual(len(imgs), 6)
        for i in imgs:
            w, h = Image.open(io.BytesIO(i._data())).size
            self.assertEqual((w, h), (640, 320))
        text = " ".join(str(c.value) for row in ws.iter_rows() for c in row if c.value)
        self.assertIn("8 枚", text)  # 省略の注記

    def test_small_photo_not_enlarged_and_missing_file(self):
        self.ledger.upsert_site(Site("s", "店"))
        self.ledger.upsert_sign(Sign("p1", site_id="s", photos=[self.png("small.png", (200, 100)), "nothing.png"]))
        out = self.dir / "a.xlsx"
        client_a_form.write_client_a(self.ledger, "s", out)
        ws = load_workbook(out)["看板_p1"]
        imgs = photos_of(ws)
        self.assertEqual(len(imgs), 1)
        self.assertEqual(Image.open(io.BytesIO(imgs[0]._data())).size, (200, 100))
        text = [str(c.value) for row in ws.iter_rows() for c in row if c.value]
        self.assertIn("写真ファイルを開けません", text)
        self.assertIn("写真なし", text)  # 空き枠

    def test_no_photos_noted(self):
        self.ledger.upsert_site(Site("s", "店"))
        self.ledger.upsert_sign(Sign("n1", site_id="s"))
        wb = client_a_form.build_workbook(common.load_site(self.ledger, "s"))
        text = " ".join(str(c.value) for row in wb["看板_n1"].iter_rows() for c in row if c.value)
        self.assertIn("写真が未登録です", text)

    def test_load_site_errors(self):
        with self.assertRaises(LookupError):
            common.load_site(self.ledger, "none")
        self.ledger.upsert_site(Site("s", "店"))
        with self.assertRaises(ValueError):
            common.load_site(self.ledger, "s")

    def test_date_format(self):
        self.assertEqual(common.fmt_date("2026-1-5"), "2026-01-05")
        self.assertEqual(common.fmt_date("2026/11/05"), "2026-11-05")
        self.assertIsNone(common.fmt_date(None))
        self.assertIsNone(common.fmt_date(""))

    def test_long_and_duplicate_sheet_names(self):
        names = common.unique_sheet_names("看板", ["x" * 40, "x" * 40, "a/b"])
        self.assertTrue(all(len(n) <= 31 for n in names))
        self.assertEqual(len(set(names)), 3)
        self.assertNotIn("/", names[2])


class CliTest(unittest.TestCase):
    def run_cli(self, *args):
        out = io.StringIO()
        return cli_main(list(args), out), out.getvalue()

    def test_both_forms_from_sample(self):
        with tempfile.TemporaryDirectory() as d:
            for cmd in ("tokyo", "client-a"):
                out = Path(d) / "sub" / f"{cmd}.xlsx"
                rc, text = self.run_cli(cmd, "--from-json", str(SAMPLE), "--site", "demo-site-1", "--out", str(out))
                self.assertEqual(rc, 0)
                self.assertTrue(out.exists())
                self.assertIn("下書き", text)
                load_workbook(out)  # 開ける

    def test_option_before_subcommand_and_db(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "l.sqlite"
            with Ledger(db) as lg:
                lg.import_json(SAMPLE)
            out = Path(d) / "o.xlsx"
            rc, _ = self.run_cli("--db", str(db), "client-a", "--site", "demo-site-1", "--out", str(out))
            self.assertEqual(rc, 0)
            rc, _ = self.run_cli("tokyo", "--db", str(db), "--site", "demo-site-1", "--out", str(out))
            self.assertEqual(rc, 0)

    def test_unknown_site_and_missing_db(self):
        import contextlib
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stderr(io.StringIO()) as err:
                rc, _ = self.run_cli("tokyo", "--from-json", str(SAMPLE), "--site", "nope", "--out", str(Path(d) / "x.xlsx"))
                self.assertEqual(rc, 1)
                rc, _ = self.run_cli("tokyo", "--db", str(Path(d) / "none.sqlite"), "--site", "s", "--out", str(Path(d) / "x.xlsx"))
                self.assertEqual(rc, 1)
            self.assertIn("nope", err.getvalue())


class SampleOutputTest(unittest.TestCase):
    """コミット済みの見本出力が、今のコードで作り直したものと同じ内容であること(様式を変えたら作り直す)。"""

    def test_committed_samples_match_regenerated(self):
        out_dir = SAMPLE.parent / "out"
        pairs = (("tokyo_demo-site-1.xlsx", tokyo_form.write_tokyo_form),
                 ("client_a_demo-site-1.xlsx", client_a_form.write_client_a))
        with tempfile.TemporaryDirectory() as d:
            lg = Ledger(":memory:")
            lg.import_json(SAMPLE)
            for name, fn in pairs:
                self.assertTrue((out_dir / name).exists(), name)
                fn(lg, "demo-site-1", Path(d) / name)
                a, b = load_workbook(out_dir / name), load_workbook(Path(d) / name)
                self.assertEqual(a.sheetnames, b.sheetnames)
                for wa, wb_ in zip(a.worksheets, b.worksheets):
                    va = [[c.value for c in row] for row in wa.iter_rows()]
                    vb = [[c.value for c in row] for row in wb_.iter_rows()]
                    self.assertEqual(va, vb, f"{name}:{wa.title}")
                    self.assertEqual(len(wa._images), len(wb_._images))


if __name__ == "__main__":
    unittest.main()
