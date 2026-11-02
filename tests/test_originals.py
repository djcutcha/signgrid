import base64
import io
import os
import tempfile
import unicodedata
import unittest
from pathlib import Path
from unittest import mock

import numpy as np
from PIL import Image

from kanban_karte.eval import originals as og
from kanban_karte.eval import run_eval
from kanban_karte.eval.claude_judge import ClaudeJudge
from kanban_karte.eval.dataset import EvalItem
from kanban_karte.eval.judge import build_judge


V = og.Vocab(karte_strip=("甲社会館", "甲社"), karte_year_dir=r"甲社(?:目視)?点検(\d{4})",
             center_strip=("乙不動産ステップ", "乙不動産"), center_root="乙不動産ステップ", center_root_within="_済み")


def scene(seed: int, size=(400, 300)) -> Image.Image:
    """シードで決まるブロック模様(別シードとは相関が低い)。"""
    rng = np.random.RandomState(seed)
    small = rng.randint(0, 255, (12, 16, 3), dtype=np.uint8)
    return Image.fromarray(small).resize(size, Image.NEAREST)


def save(im: Image.Image, path: Path, size=None) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    (im.resize(size, Image.LANCZOS) if size else im).save(path, "JPEG", quality=92)
    return str(path)


def item(sid, source, site, name, date, photos, rank=2) -> EvalItem:
    return EvalItem(source=source, sign_id=sid, photo_paths=photos, rank_true=rank,
                    item_judgments_true=None, split="eval",
                    meta={"site": site, "sign_name": name, "inspection_date": date})


class NameTest(unittest.TestCase):
    def test_kaikan_key_matches_eval_and_folder_names(self):
        folders = ["3.たまプラーザ会館", "1.甲社　 仙川会館", "10.東久留米会館", "5.鶴見営業所", "写真"]
        sites = ["甲社 たまプラーザ会館", "甲社 仙川会館", "甲社会館 東久留米", "甲社 鶴見営業所"]
        self.assertEqual([og.kaikan_key(f, V) for f in folders[:4]], ["たまプラーザ", "仙川", "東久留米", "鶴見"])
        self.assertEqual([og.kaikan_key(s, V) for s in sites], ["たまプラーザ", "仙川", "東久留米", "鶴見"])

    def test_nfd_dakuten_is_normalized(self):
        nfd = unicodedata.normalize("NFD", "3.プラザ会館")
        self.assertNotEqual(nfd, "3.プラザ会館")
        self.assertEqual(og.kaikan_key(nfd, V), og.kaikan_key("甲社 プラザ会館", V))

    def test_center_key(self):
        self.assertEqual(og.center_key("新百合ヶ丘営業センター", V), "新百合ヶ丘")
        self.assertEqual(og.center_key("乙不動産ステップ小石川", V), "小石川")

    def test_sign_name_match_ignores_numbering(self):
        self.assertTrue(og.names_match("1.壁面袖看板", "1.袖看板"))
        self.assertTrue(og.names_match("２.チャンネル文字", "②チャンネル文字"))
        self.assertTrue(og.names_match("壁面看板", "①壁面看板"))
        self.assertFalse(og.names_match("3.館銘板", "4.パンフレットケース"))
        self.assertFalse(og.names_match("", "袖看板"))
        self.assertFalse(og.names_match("1", "2.袖看板"))  # 番号だけの名前は鍵が空


class MatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cache = og.SigCache(None, workers=2)

    def tearDown(self):
        self.tmp.cleanup()

    def test_signature_is_scale_invariant_and_orientation_aware(self):
        a = save(scene(1), self.root / "a.jpg")
        b = save(scene(1), self.root / "b.jpg", size=(160, 120))
        c = save(scene(2), self.root / "c.jpg")
        va, ra, la = og.signature(a)
        vb, rb, lb = og.signature(b)
        vc, _, _ = og.signature(c)
        self.assertGreater(float(va @ vb), 0.98)
        self.assertLess(float(va @ vc), 0.6)
        self.assertEqual((la, lb), (400, 160))
        self.assertAlmostEqual(ra, rb, places=1)

    def test_match_prefers_name_folder_and_larger_duplicate(self):
        big = save(scene(1), self.root / "orig/袖看板/1.jpg")
        save(scene(1), self.root / "orig/その他/dup.jpg", size=(200, 150))
        other = save(scene(2), self.root / "orig/袖看板/2.jpg")
        pasted = save(scene(1), self.root / "pasted/p.jpg", size=(120, 90))
        paths = og.list_images(str(self.root / "orig"))
        pool = og.Pool.build(paths, self.cache)
        r = og.match_sign([pasted], "1.袖看板", pool, self.cache)
        self.assertEqual([pool.paths[i] for i in r["idx"]], [big])
        self.assertEqual(r["vias"], ["name"])
        self.assertNotIn(other, [pool.paths[i] for i in r["idx"]])
        # 看板名が合わなければ会館全体から探す(phash)。複製のうち長辺が大きい方
        r2 = og.match_sign([pasted], "ポスト", pool, self.cache)
        self.assertEqual([pool.paths[i] for i in r2["idx"]], [big])
        self.assertEqual(r2["vias"], ["phash"])

    def test_below_threshold_is_none(self):
        save(scene(2), self.root / "orig/x.jpg")
        pasted = save(scene(1), self.root / "p.jpg", size=(100, 75))
        pool = og.Pool.build(og.list_images(str(self.root / "orig")), self.cache)
        r = og.match_sign([pasted], "x", pool, self.cache)
        self.assertEqual(r["idx"], [])
        row = og._row("s:0", "2024", "f", r, pool)
        self.assertEqual((row["basis"], row["reason"], row["n_pasted"]), ("none", "below_threshold", 1))

    def test_aspect_gate(self):
        save(scene(1, (300, 400)), self.root / "orig/portrait.jpg")
        pasted = save(scene(1, (400, 300)), self.root / "p.jpg")
        pool = og.Pool.build(og.list_images(str(self.root / "orig")), self.cache)
        self.assertEqual(og.match_sign([pasted], "", pool, self.cache)["idx"], [])

    def test_partial_match_keeps_matched_only_and_dedupes(self):
        o1 = save(scene(1), self.root / "orig/a/1.jpg")
        pa = save(scene(1), self.root / "pa.jpg", size=(100, 75))
        pb = save(scene(1), self.root / "pb.jpg", size=(120, 90))  # 同じ原本に当たる
        pc = save(scene(9), self.root / "pc.jpg", size=(100, 75))  # 原本なし
        pool = og.Pool.build(og.list_images(str(self.root / "orig")), self.cache)
        r = og.match_sign([pa, pb, pc], "a", pool, self.cache)
        row = og._row("s:1", "2024", "f", r, pool)
        self.assertEqual(row["originals"], o1)
        self.assertEqual((row["n_pasted"], row["n_matched"], row["basis"]), (3, 1, "name"))
        self.assertEqual(row["long_edge"], 400)

    def test_build_index_end_to_end_with_reasons(self):
        nas = self.root / "nas"
        k24 = nas / "甲社点検 2024"
        o_ok = save(scene(1), k24 / "7.テスト会館" / "7.テスト会館 写真" / "1.袖看板" / "001.JPG")
        save(scene(2), k24 / "7.テスト会館" / "7.テスト会館 写真" / "2.壁面" / "002.JPG")
        save(scene(3), k24 / "8.別会館" / "8.別会館 写真" / "x.JPG")
        dirs_file = self.root / "dirs.txt"
        dirs_file.write_bytes("\n".join([str(k24), str(k24 / "7.テスト会館")]).encode("utf-8"))
        p1 = save(scene(1), self.root / "pasted/1.jpg", size=(100, 75))
        p2 = save(scene(8), self.root / "pasted/2.jpg", size=(100, 75))
        items = [
            item("xlsx_karte:0001", "xlsx_karte", "甲社 テスト会館", "1.袖看板", "2024-05-01", [p1]),
            item("xlsx_karte:0002", "xlsx_karte", "甲社 テスト会館", "9.なし", "2024-05-01", [p2]),
            item("xlsx_karte:0003", "xlsx_karte", "甲社 無い会館", "1.袖看板", "2024-05-01", [p1]),
            item("xlsx_karte:0004", "xlsx_karte", "甲社 テスト会館", "1.袖看板", "2022-05-01", [p1]),
            item("xlsx_karte:0005", "xlsx_karte", "甲社 テスト会館", "1.袖看板", "", [p1]),
            item("xlsx_karte:0006", "xlsx_karte", "甲社 テスト会館", "1.袖看板", "2024-05-01", []),
        ]
        rows = og.build_index(items, og.read_dirs(dirs_file), self.cache, V)
        by = {r["sign_id"]: r for r in rows}
        self.assertEqual((by["xlsx_karte:0001"]["basis"], by["xlsx_karte:0001"]["originals"]), ("name", o_ok))
        self.assertEqual(by["xlsx_karte:0002"]["reason"], "below_threshold")
        self.assertEqual(by["xlsx_karte:0003"]["reason"], "no_site_folder")
        self.assertEqual(by["xlsx_karte:0004"]["reason"], "no_year_folder")
        self.assertEqual(by["xlsx_karte:0005"]["reason"], "no_year_folder")
        self.assertEqual(by["xlsx_karte:0006"]["reason"], "no_pasted_photos")
        # TSV の往復
        out = self.root / "idx.tsv"
        og.write_index(rows, out)
        back = og.read_index(out)
        self.assertEqual(back["xlsx_karte:0001"]["originals"], [o_ok])
        self.assertEqual(back["xlsx_karte:0002"]["originals"], [])

    def test_center_pool_filters_by_key(self):
        nas = self.root / "nas" / "_済み" / "乙不動産　ステップ"
        o = save(scene(1), nas / "ステップ　写真" / "ステップ　小石川" / "①袖看板" / "a.jpg")
        save(scene(2), nas / "ステップ　写真" / "ステップ　赤羽" / "①袖看板" / "b.jpg")
        dirs_file = self.root / "dirs.txt"
        dirs_file.write_bytes(str(nas).encode("utf-8"))
        p = save(scene(1), self.root / "p.jpg", size=(100, 75))
        items = [item("xlsx_center:0001", "xlsx_center", "小石川営業センター", "袖看板", "2025-11-27", [p])]
        rows = og.build_index(items, og.read_dirs(dirs_file), self.cache, V)
        self.assertEqual((rows[0]["originals"], rows[0]["basis"]), (o, "name"))


class OverrideTest(unittest.TestCase):
    def test_apply_override_replaces_and_empties(self):
        a = item("xlsx_karte:0001", "xlsx_karte", "s", "n", "2024-01-01", ["/p/a.jpg"])
        b = item("xlsx_karte:0002", "xlsx_karte", "s", "n", "2024-01-01", ["/p/b.jpg"])
        c = item("paper_scan:x", "paper_scan", "s", "n", "2024-01-01", ["/p/c.jpg"])
        index = {"xlsx_karte:0001": {"originals": ["/o/a1.jpg", "/o/a2.jpg"]},
                 "xlsx_karte:0002": {"originals": []}}
        out = og.apply_override([a, b, c], index)
        self.assertEqual(out[0].photo_paths, ["/o/a1.jpg", "/o/a2.jpg"])
        self.assertTrue(out[0].meta["original_photos"])
        self.assertEqual(out[0].meta["pasted_photo_paths"], ["/p/a.jpg"])
        self.assertEqual(out[1].photo_paths, [])
        self.assertFalse(out[1].meta["original_photos"])
        self.assertEqual(out[2].photo_paths, [])  # 索引に無い(紙など)
        self.assertEqual(a.photo_paths, ["/p/a.jpg"])  # 元は変えない


class RunOverrideTest(unittest.TestCase):
    """--photo-override: 対応のない看板は母数に数えない。API は呼ばない(constant 判定役)。"""

    def test_run_excludes_unmatched_and_records_counts(self):
        with tempfile.TemporaryDirectory() as d:
            idx = Path(d) / "idx.tsv"
            rows = [{"sign_id": "xlsx_karte:0001", "year": "2024", "folder": "f", "originals": "/o/a.jpg",
                     "basis": "name", "similarity": "0.99", "long_edge": 1600, "n_pasted": 1, "n_matched": 1, "reason": ""},
                    {"sign_id": "xlsx_karte:0002", "year": "2024", "folder": "", "originals": "",
                     "basis": "none", "similarity": "", "long_edge": "", "n_pasted": 1, "n_matched": 0,
                     "reason": "no_site_folder"}]
            og.write_index(rows, idx)
            items = [item(f"xlsx_karte:000{i}", "xlsx_karte", "s", "n", "2024-01-01", ["/p/x.jpg"], rank=2)
                     for i in (1, 2, 3)]
            with mock.patch.object(run_eval.dataset, "load_all", return_value=items):
                r = run_eval.run(build_judge("constant"), "constant", "xlsx_karte", None, "eval", 0, None,
                                 override=str(idx))
        self.assertEqual(r["overall"]["n"], 1)
        po = r["config"]["photo_override"]
        self.assertEqual((po["matched"], po["unmatched"]), ({"xlsx_karte": 1}, {"xlsx_karte": 2}))
        self.assertEqual([x["sign_id"] for x in r["records"]], ["xlsx_karte:0001"])
        self.assertIn("原本写真・対応 1 基", run_eval.log_row(r))

    def test_without_override_nothing_changes(self):
        items = [item("xlsx_karte:0001", "xlsx_karte", "s", "n", "2024-01-01", ["/p/x.jpg"])]
        with mock.patch.object(run_eval.dataset, "load_all", return_value=items):
            r = run_eval.run(build_judge("constant"), "constant", "xlsx_karte", None, "eval", 0, None)
        self.assertNotIn("photo_override", r["config"])


class CompareTest(unittest.TestCase):
    def test_compare_and_coverage(self):
        def mk(i, rank, src="xlsx_karte", split="eval"):
            it = item(f"{src}:{i:04d}", src, "s", "n", "2024-01-01", ["/p/x.jpg"], rank=rank)
            it.split = split
            return it
        items = [mk(1, 3), mk(2, 4), mk(3, 1), mk(4, 3, "xlsx_center"), mk(5, 2), mk(6, 2, "paper_scan")]
        idx = {s.sign_id: {"year": "2024", "originals": ["/o/x.jpg"], "n_matched": 1, "n_pasted": 1, "reason": ""}
               for s in items[:4]}
        idx["xlsx_karte:0005"] = {"year": "2024", "originals": [], "n_matched": 0, "n_pasted": 1,
                                  "reason": "no_site_folder"}

        def rec(sid, pred, cost=0.01):
            return {"sign_id": sid, "rank_pred": pred, "usage": {"cost_usd": cost}, "reasons": ["あ" * 60]}
        first = {"records": [rec("xlsx_karte:0001", 2), rec("xlsx_karte:0002", 4), rec("xlsx_karte:0003", 3),
                             rec("xlsx_center:0004", 2)]}
        run = {"records": [rec("xlsx_karte:0001", 3, 0.02), rec("xlsx_karte:0002", 2, 0.02),
                           rec("xlsx_karte:0003", 1, 0.02), rec("xlsx_center:0004", 2, 0.02)]}
        md, rescued = og.compare(items, idx, first, [run])
        self.assertEqual([(r["sign_id"], r["rank_true"], r["pasted_pred"], r["original_pred"]) for r in rescued],
                         [("xlsx_karte:0001", 3, 2, 3)])
        self.assertEqual(len(rescued[0]["reason"]), 40)
        self.assertIn("貼り付けで見逃し → 原本で救えた: 1 基 / 逆(貼り付けで拾い、原本で見逃し): 1 基 / どちらでも見逃し: 1 基", md)
        self.assertIn("| 客先B 2024 | 貼り付け | 3 |", md)
        self.assertIn("| 客先A | 原本 | 1 |", md)
        cov = og.coverage(items, idx)
        self.assertIn("| 客先B 2024 | 4 | 3 | 1 | no_site_folder 1 |", cov)
        self.assertIn("| 紙(原本が NAS に無い) | 1 | - | - | 対象外 1 |", cov)


class LongEdgeTest(unittest.TestCase):
    def test_max_long_edge_applies_to_judged_photos(self):
        with tempfile.TemporaryDirectory() as d:
            p = save(scene(1, (3000, 2000)), Path(d) / "big.jpg")
            it = item("xlsx_karte:0001", "xlsx_karte", "s", "n", "2024-01-01", [p])

            def sent_size(j):
                blocks = [b for b in j.build_messages(it)[0]["content"] if b["type"] == "image"]
                return Image.open(io.BytesIO(base64.b64decode(blocks[0]["source"]["data"]))).size

            self.assertEqual(max(sent_size(ClaudeJudge(client=object()))), 1280)
            self.assertEqual(max(sent_size(ClaudeJudge(client=object(), max_long_edge=1568))), 1568)


if __name__ == "__main__":
    unittest.main()
