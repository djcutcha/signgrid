"""スケッチ(点検票の次頁の手描き)の対応付け・items.tsv の列・全高の計算・図面タグの除外。画像・API は使わない。"""
import csv
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import field_index as fi  # noqa: E402
import field_legal as fl  # noqa: E402
import field_sketch as fs  # noqa: E402

from kanban_karte.eval.judge import Judge, Prediction  # noqa: E402
from kanban_karte.ledger import Ledger, Sign, Site  # noqa: E402
from kanban_karte.ledger.db import FIGURE_TAG  # noqa: E402
from kanban_karte.pipeline import draft  # noqa: E402
from kanban_karte.web import api  # noqa: E402


def page(pdf, p, kind, no):
    return (pdf, p), {"pdf": pdf, "page": p, "sheet_kind": kind, "item_no": no, "rows": [], "size": {}}


class PairTest(unittest.TestCase):
    def test_sketch_follows_its_table_page_including_several_pages(self):
        r = dict([page("通常", 8, "通常", 8), page("通常", 9, "写真のみ", None), page("通常", 10, "通常", 9),
                  page("その他1", 1, "その他", 1), page("その他1", 2, "写真のみ", None), page("その他1", 3, "写真のみ", None),
                  page("その他1", 4, "その他", "他2")])
        paired, unpaired = fs.pair_pages(r)
        self.assertEqual(paired, {("通常", 9): "通常8", ("その他1", 2): "他1", ("その他1", 3): "他1"})
        self.assertEqual(unpaired, [])

    def test_page_without_preceding_table_stays_unpaired(self):
        # PDF の先頭、転記が抜けた頁の次(n+1 が連続でない)は付けない。別の PDF の頁には跨がない
        r = dict([page("通常", 1, "写真のみ", None), page("通常", 2, "通常", 1), page("通常", 4, "写真のみ", None),
                  page("その他1", 1, "写真のみ", None)])
        paired, unpaired = fs.pair_pages(r)
        self.assertEqual(paired, {})
        self.assertEqual([(u["pdf"], u["page"]) for u in unpaired], [("通常", 1), ("通常", 4), ("その他1", 1)])

    def test_size_check_and_merge(self):
        self.assertEqual(fs.size_check({"w_mm": 1000, "h_mm": 500, "gl_mm": 1450}, {"w_mm": 1020, "h_mm": 700, "gl_mm": None}),
                         {"w": "match", "h": "differ", "gl": "none"})
        a = {"pdf": "通常", "page": 9, "is_sketch": True, "printed": False, "confidence": 0.5, "w_mm": 600, "h_mm": None,
             "gl_mm": None, "gl_edge": "unknown", "mount": "不明", "post": None, "display_text": None}
        b = {**a, "page": 10, "printed": True, "confidence": 0.9, "w_mm": 700, "h_mm": 300, "gl_mm": 2000, "gl_edge": "bottom", "mount": "ビス"}
        c = {**a, "page": 11, "is_sketch": False, "printed": False, "w_mm": 9999}  # 表の頁: 寸法にも図面にも使わない
        m = fs.merge_sign([a, b, c])
        self.assertEqual((m["w_mm"], m["h_mm"], m["gl_mm"], m["gl_edge"], m["mount"]), (700, 300, 2000, "bottom", "ビス"))
        self.assertEqual([p["page"] for p in m["pages"]], [9, 10])
        self.assertTrue(m["printed"])

    def test_parse_sketch_normalizes(self):
        d = fs.parse_sketch('{"is_sketch":true,"w_mm":"1350","h_mm":450.4,"gl_mm":null,"gl_edge":"top","mount":"ねじ","printed":false}')
        self.assertEqual((d["w_mm"], d["h_mm"], d["gl_mm"]), (1350, 450, None))
        self.assertEqual((d["gl_edge"], d["mount"]), ("unknown", "不明"))  # GL が無ければ縁は unknown、未知の取付は不明


class FullHeightTest(unittest.TestCase):
    def test_edges(self):
        self.assertEqual(fl.full_height_mm(2000, "bottom", 600), 2600)
        self.assertEqual(fl.full_height_mm(6465, "top", 3360), 6465)
        self.assertIsNone(fl.full_height_mm(2000, "unknown", 600))
        self.assertIsNone(fl.full_height_mm(None, "top", 600))
        self.assertIsNone(fl.full_height_mm(2000, "bottom", None))

    def test_dims_priority_and_before(self):
        row = {"size": json.dumps({"w_mm": 1000, "h_mm": 500, "gl_mm": 145}), "sketch_w_mm": "1000", "sketch_h_mm": "400",
               "sketch_gl_mm": "1450", "sketch_gl_edge": "bottom"}
        d = fl.dims(row)  # W×H は表、GL はスケッチ(縁あり)。bottom は同じスケッチの H を足す
        self.assertEqual((d["area_m2"], d["height_m"], d["size_src"]), (0.5, 1.85, "table"))
        self.assertEqual(fl.dims(row, prefer="sketch")["area_m2"], 0.4)
        self.assertEqual(fl.before_dims(row), {"area_m2": 0.5, "height_m": None})
        self.assertIsNone(fl.dims({"size": "{}"})["area_m2"])


class ItemsColumnsTest(unittest.TestCase):
    def test_items_tsv_has_sketch_columns(self):
        with tempfile.TemporaryDirectory() as d:
            base = Path(d)
            (base / "raw").mkdir()
            (base / "transcribed").mkdir()
            rows = [page("通常", 1, "通常", 1)[1], page("通常", 2, "写真のみ", None)[1], page("通常", 3, "通常", 2)[1]]
            (base / "transcribed" / "pages.jsonl").write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), encoding="utf-8")
            sk = {"pdf": "通常", "page": 2, "sign_key": "通常1", "is_sketch": True, "printed": False, "confidence": 0.8,
                  "w_mm": 900, "h_mm": 450, "d_mm": None, "gl_mm": 1450, "gl_edge": "top", "mount": "バンセン", "post": "□50",
                  "display_text": None}
            (base / "transcribed" / "sketches.jsonl").write_text(json.dumps(sk, ensure_ascii=False), encoding="utf-8")
            fi.build(base)
            items = {r["key"]: r for r in csv.DictReader(open(base / "items.tsv", encoding="utf-8"), delimiter="\t")}
            a, b = items["通常1"], items["通常2"]
            self.assertEqual((a["sketch_pages"], a["sketch_w_mm"], a["sketch_h_mm"], a["sketch_gl_mm"], a["sketch_gl_edge"]),
                             ("通常:2", "900", "450", "1450", "top"))
            self.assertEqual((a["post"], a["mount"], a["sketch_confidence"]), ("□50", "バンセン", "0.8"))
            self.assertEqual([b[k] for k in ("sketch_pages", "sketch_w_mm", "sketch_gl_edge", "post", "mount")], [""] * 5)


class FigureTagTest(unittest.TestCase):
    def _ledger(self, td):
        lg = Ledger(Path(td) / "l.sqlite", data_dir=td)
        lg.upsert_site(Site(id="s", name="s"))
        bid = lg.add_bundle("pdf", "sha")
        for i in (1, 2):
            (Path(td) / f"{i}.jpg").write_bytes(b"x")
            lg.upsert_page(bid, i, image_path=f"{i}.jpg")
        lg.set_page_tag(bid, 2, FIGURE_TAG)
        lg.upsert_sign(Sign(id="a", site_id="s", name="a", photos=["1.jpg", "2.jpg"]))
        return lg

    def test_split_and_draft_input_exclude_figures(self):
        with tempfile.TemporaryDirectory() as td:
            lg = self._ledger(td)
            s = lg.get_sign("a")
            self.assertEqual(lg.split_photos(s), (["1.jpg"], ["2.jpg"]))
            item = draft.eval_item_for(lg, s)
            self.assertEqual([Path(p).name for p in item.photo_paths], ["1.jpg"])  # 図面は判定役に渡らない

            seen = []

            class Spy(Judge):
                name = "spy"

                def judge(self, it):
                    seen.append(it)
                    return Prediction(2, {}, 0.5, raw={}, usage={})

            lg.upsert_sign(Sign(id="b", site_id="s", name="b", photos=["2.jpg"]))
            bid = lg.add_bundle("p2", "sha2")
            lg.set_bundle(bid, site_id="s")
            lg.conn.execute("UPDATE signs SET source_bundle=? WHERE id IN ('a','b')", (bid,))
            lg.conn.commit()
            res = {r.sign_id: r.status for r in draft.draft_bundle(lg, bid, Spy())}
            self.assertEqual(res["b"], "needs_photos")  # 図面しか無い看板は、写真なしとして下書きしない
            self.assertEqual(res["a"], "draft")
            self.assertTrue(seen and all("2.jpg" not in " ".join(i.photo_paths) for i in seen))

    def test_api_marks_figures(self):
        with tempfile.TemporaryDirectory() as td:
            lg = self._ledger(td)
            d = api.sign_detail(lg, "a")
            self.assertEqual([(p["n"], p["figure"]) for p in d["photos"]], [(1, False), (2, True)])
            self.assertEqual(d["photo_count"], 1)


if __name__ == "__main__":
    unittest.main()
