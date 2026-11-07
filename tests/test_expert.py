"""有資格者のアノテーション画面(/expert/): ルート、保存→読み戻し、export、入力検証。写真は一時フォルダの小さな JPEG。"""
import csv
import json
import tempfile
import threading
import unittest
from unittest import mock
from urllib.parse import quote
from pathlib import Path

from kanban_karte.web import expert
from kanban_karte.web.server import App, make_server, seed_sample, SAMPLE_JSON
from tests.test_web import WebTest

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001400010000000000000000000000000000000affc400141001000000000000000000000000000000ffda0008010100003f00d2cf20ffd9")


@mock.patch.object(expert, "OUTDOOR_FILTER", False)   # 並びの絞り込みは ExpertOutdoorTest で見る
class ExpertTest(WebTest):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.ex_td = tempfile.TemporaryDirectory()
        root = Path(cls.ex_td.name)
        for d, n in (("fs-通常1", 3), ("fs-他2", 1)):
            (root / "field_intake" / "photos" / d).mkdir(parents=True)
            for i in range(n):
                (root / "field_intake" / "photos" / d / f"{i + 1:02d}.jpg").write_bytes(JPEG)
        (root / "field_intake" / "pages" / "000001").mkdir(parents=True)
        (root / "field_intake" / "pages" / "000001" / "p-001.jpg").write_bytes(JPEG)
        (root / "scans").mkdir()
        cls.root = root
        cls.catalog = expert.Catalog(private=root, scans=root / "scans", with_eval=False)
        cls.store = expert.Store(root / "expert_notes.sqlite")
        cls.app._expert = expert.Expert(cls.store, cls.catalog)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.ex_td.cleanup()

    def test_routes_200(self):
        st, body, h = self.req("/expert/", raw=True)
        self.assertEqual(st, 200)
        self.assertIn("text/html", h["Content-Type"])
        self.assertIn("有資格者".encode(), body)
        st, _, h = self.req("/expert", raw=True)    # 末尾スラッシュ無しは 308 を urllib が追って 200
        self.assertEqual(st, 200)
        for f in ("expert.js", "expert.css"):
            self.assertEqual(self.req("/static/" + f, raw=True)[0], 200)

    def test_queue_item_image(self):
        _, q, _ = self.req("/api/expert/queue")
        self.assertEqual(q["total"], 3)
        self.assertEqual(q["counts"]["field"], {"signs": 2, "photos": 4})
        self.assertEqual(q["counts"]["page"], {"signs": 1, "photos": 1})
        iid = "field/fs-通常1"
        self.assertIn(iid, [r[0] for r in q["queue"]])
        _, it, _ = self.req("/api/expert/item?id=" + quote(iid))
        self.assertEqual(it["n_photos"], 3)
        st, img, h = self.req(f"/api/expert/img?id={quote(iid)}&n=2", raw=True)
        self.assertEqual((st, h["Content-Type"]), (200, "image/jpeg"))
        self.assertEqual(self.req(f"/api/expert/img?id={quote(iid)}&n=3", raw=True)[0], 404)
        self.assertEqual(self.req("/api/expert/img?id=../../etc/passwd&n=0", raw=True)[0], 404)   # 台帳にない id は開かない

    def test_save_and_readback(self):
        _, q, _ = self.req("/api/expert/queue")
        iid = q["queue"][1][0]
        note = {"id": iid, "rank": 3, "score": 70, "reasons": "ボルトの錆が広い", "unknowns": "裏側は見えない",
                "signs": ["錆", "ボルトの変色", "ありえない"], "kind": "壁面"}
        st, saved, _ = self.req("/api/expert/save", note)
        self.assertEqual(st, 200)
        self.assertEqual(saved["signs"], ["錆", "ボルトの変色"])          # 選択肢にない物は落とす
        _, it, _ = self.req("/api/expert/item?id=" + quote(iid))
        self.assertEqual((it["saved"]["rank"], it["saved"]["score"], it["saved"]["reasons"]), (3, 70, "ボルトの錆が広い"))
        self.assertEqual(len(it["saved"]["photos"]), it["n_photos"])
        self.assertRegex(it["saved"]["created_at"], r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d$")
        # 上書き: 件数は増えない
        note["reasons"] = "直した"
        self.req("/api/expert/save", note)
        rows = [r for r in self.store.all() if r["sign_key"] == iid.split("/", 1)[1]]
        self.assertEqual((len(rows), rows[0]["reasons"]), (1, "直した"))
        _, q2, _ = self.req("/api/expert/queue")
        self.assertEqual(dict(q2["queue"])[iid], 1)

    def test_skip_and_validation(self):
        _, q, _ = self.req("/api/expert/queue")
        iid = q["queue"][2][0]
        st, r, _ = self.req("/api/expert/save", {"id": iid, "skipped": True, "rank": 4, "score": 99})
        self.assertEqual((st, r["skipped"], r["rank"], r["score"]), (200, 1, None, None))
        for bad in ({"rank": 9}, {"score": 101}, {"score": "x"}, {"kind": "海"}):
            self.assertEqual(self.req("/api/expert/save", dict(bad, id=iid))[0], 400, bad)
        self.assertEqual(self.req("/api/expert/save", {"id": "nope/1"})[0], 404)

    def test_export_cli(self):
        self.store.save("field", "fs-通常1", ["/x.jpg"], {"rank": 2, "score": 40, "reasons": "a,b\n\"c\"", "signs": ["錆"], "kind": "自立"})
        out = Path(self.ex_td.name) / "out"
        r = expert.export(self.store.path, out)
        self.assertGreaterEqual(r["rows"], 1)
        rows = [json.loads(x) for x in Path(r["jsonl"]).read_text(encoding="utf-8").splitlines()]
        self.assertEqual(len(rows), r["rows"])
        mine = [x for x in rows if x["sign_key"] == "fs-通常1"][0]
        self.assertEqual((mine["signs"], mine["reasons"]), (["錆"], 'a,b\n"c"'))
        with open(r["csv"], encoding="utf-8-sig", newline="") as f:
            cr = list(csv.DictReader(f))
        self.assertEqual(list(cr[0].keys()), expert.COLS)
        self.assertEqual([x for x in cr if x["sign_key"] == "fs-通常1"][0]["reasons"], 'a,b\n"c"')
        self.assertEqual(expert.main(["export", "--db", str(self.store.path), "--out", str(out)]), 0)

    def test_guess_kind(self):
        self.assertEqual(expert.guess_kind("1.屋上チャンネルサイン(3面)"), "屋上")
        self.assertEqual(expert.guess_kind("自立看板①"), "自立")
        self.assertEqual(expert.guess_kind("ほげ"), "")


@mock.patch.object(expert, "OUTDOOR_FILTER", False)
class ExpertWebTest(WebTest):
    """外の写真(expert_web/index.json): キュー先頭、状態の混ぜ、出典、相対パス保存、export の列。"""
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.td2 = tempfile.TemporaryDirectory()
        root = Path(cls.td2.name)
        (root / "expert_web").mkdir()
        rows = []
        # 危ない側 30 → 中間 20 → 良好 20 の順で並べた台帳(並びのままだと先頭 30 が全部危ない側)
        for i, cond in enumerate(["危ない側"] * 30 + ["中間"] * 20 + ["良好"] * 20):
            (root / "expert_web" / f"{i + 1}.jpg").write_bytes(JPEG)
            rows.append({"file": f"{i + 1}.jpg", "source_type": "gov" if i % 2 else "cc", "title": f"見本{i + 1}", "url": f"https://example.com/{i + 1}",
                         "document": "資料", "page": "3", "license": "CC BY 4.0", "author": "作者", "fetched": "2026-11-04",
                         "kind_guess": "壁面" if i == 0 else "", "condition_guess": cond, "note": "x"})
        rows.append({"file": "missing.jpg", "condition_guess": "不明"})      # 実体なしは載せない
        (root / "expert_web" / "index.json").write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        (root / "field_intake" / "photos" / "fs-通常1").mkdir(parents=True)
        (root / "field_intake" / "photos" / "fs-通常1" / "01.jpg").write_bytes(JPEG)
        (root / "scans").mkdir()
        cls.root = root
        cls.catalog = expert.Catalog(private=root, scans=root / "scans", with_eval=False)
        cls.store = expert.Store(root / "n.sqlite")
        cls.app._expert = expert.Expert(cls.store, cls.catalog)

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        cls.td2.cleanup()

    def test_queue_web_first_and_mixed(self):
        _, q, _ = self.req("/api/expert/queue")
        ids = [r[0] for r in q["queue"]]
        self.assertEqual(q["counts"]["web"], {"signs": 70, "photos": 70})
        self.assertEqual(len(ids), 71)
        self.assertTrue(all(i.startswith("web/") for i in ids[:70]))
        self.assertEqual(ids[70], "field/fs-通常1")
        cond = lambda i: self.catalog.items[i].web["condition_guess"]
        head = [cond(i) for i in ids[:50]]
        self.assertEqual(set(head), {"危ない側", "中間", "良好"})
        self.assertLessEqual(max(len(list(g)) for _, g in __import__("itertools").groupby(head)), 1)   # 同じ状態が続かない
        self.assertEqual(len(set(ids[:70])), 70)
        self.assertEqual(ids[:50], [i.id for i in expert.Catalog(private=self.root, scans=self.root / "scans", with_eval=False).items.values()][:50])  # 種固定

    def test_credit_not_leaking_condition_and_save_relative(self):
        iid = "web/1"
        _, it, _ = self.req("/api/expert/item?id=" + quote(iid))
        self.assertEqual(it["web"]["license"], "CC BY 4.0")
        self.assertEqual(it["web"]["url"], "https://example.com/1")
        self.assertNotIn("condition_guess", it["web"])
        self.assertEqual(it["kind_default"], "壁面")
        st, r, _ = self.req("/api/expert/save", {"id": iid, "rank": 3, "reasons": "r"})
        self.assertEqual((st, r["photos"], r["license"], r["source_type"]), (200, ["expert_web/1.jpg"], "CC BY 4.0", "cc"))
        self.assertEqual(self.req("/api/expert/img?id=web/1&n=0", raw=True)[0], 200)

    def test_export_has_columns(self):
        self.req("/api/expert/save", {"id": "web/2", "rank": 1})
        r = expert.export(self.store.path, Path(self.td2.name) / "out")
        with open(r["csv"], encoding="utf-8-sig", newline="") as f:
            cr = list(csv.DictReader(f))
        row = [x for x in cr if x["sign_key"] == "2"][0]
        self.assertEqual((row["source_type"], row["license"]), ("gov", "CC BY 4.0"))
        jl = [json.loads(x) for x in Path(r["jsonl"]).read_text(encoding="utf-8").splitlines()]
        self.assertIn("license", jl[0])

    def test_old_db_gets_columns(self):
        import sqlite3
        p = Path(self.td2.name) / "old.sqlite"
        c = sqlite3.connect(p)
        c.execute("CREATE TABLE expert_notes (id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, sign_key TEXT NOT NULL, photos TEXT NOT NULL DEFAULT '[]', score INTEGER, rank INTEGER, reasons TEXT NOT NULL DEFAULT '', unknowns TEXT NOT NULL DEFAULT '', signs TEXT NOT NULL DEFAULT '[]', kind TEXT NOT NULL DEFAULT '', skipped INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(source, sign_key))")
        c.commit(); c.close()
        s = expert.Store(p)
        self.assertEqual(s.save("web", "9", [], {"rank": 2}, {"license": "L"})["license"], "L")


if __name__ == "__main__":
    unittest.main()


class ExpertDraftTest(unittest.TestCase):
    """判定役の下書き: 所見の候補(否定の文は数えない)、評価セットの先頭の散らばり、入力済みは後ろ、下書きのランクは出さない。"""

    def test_parse_draft_skips_negated(self):
        d = expert.parse_draft({"rank_pred": "3", "reasons": ["箱体底部に広い範囲の錆と腐食がある", "固定ボルトに錆は見られない", "基礎は写っておらず判定できない"]})
        self.assertEqual(d["rank"], 3)
        self.assertEqual(d["suggest"], ["錆", "腐食"])
        self.assertNotIn("ボルト", d["cats"])
        self.assertEqual(expert.parse_draft({"rank_pred": None, "reasons": "表示面に汚れがある"})["suggest"], ["その他"])

    def test_load_drafts_missing_file(self):
        self.assertEqual(expert.load_drafts("/nonexistent/x.json"), {})
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / "r.json"
            f.write_text(json.dumps({"records": [{"sign_id": "xlsx_center:0019", "rank_pred": "2", "reasons": ["錆がある"]}]}), encoding="utf-8")
            self.assertEqual(expert.load_drafts(f)["xlsx_center-0019"]["suggest"], ["錆"])

    def _items(self):
        items, drafts = [], {}
        # 元の順: 全部が同じ出どころ・良好の物が先に 20 件、その後に危ない物 5 件
        for i in range(25):
            grp = "paper_scan" if i < 20 else "xlsx_center"
            it = expert.Item("eval", f"k{i:02d}", [Path("x.jpg")], "壁面" if i < 20 else "袖", group=grp)
            items.append(it)
            drafts[it.key] = {"rank": 1 if i < 20 else 4, "suggest": [], "cats": ["錆"] if i < 20 else ["取付"]}
        return items, drafts

    def test_spread_mixes_head(self):
        items, drafts = self._items()
        out = expert.spread(items, drafts, 10)
        self.assertEqual(len(out), 25)
        self.assertEqual(len({id(i) for i in out}), 25)
        head = out[:10]
        self.assertGreaterEqual(sum(1 for i in head if drafts[i.key]["rank"] >= 3), 3)   # 危ない物が先頭に混ざる
        self.assertEqual({i.group for i in head}, {"paper_scan", "xlsx_center"})
        self.assertEqual([i.key for i in out], [i.key for i in expert.spread(items, drafts, 10)])   # 種固定

    def test_order_done_last_and_suggest(self):
        items, drafts = self._items()
        with tempfile.TemporaryDirectory() as td, mock.patch.object(expert, "OUTDOOR_FILTER", False):
            cat = expert.Catalog(private=Path(td), scans=Path(td) / "none", with_eval=False, drafts=None)
            cat._add(expert.Item("web", "w1", [Path("w.jpg")]))
            for it in items:
                cat._add(it)
            cat.drafts = drafts
            store = expert.Store(Path(td) / "n.sqlite")
            store.save("eval", "k21", [], {"rank": 3})
            ex = expert.Expert(store, cat)
            ids = [r[0] for r in ex.queue()["queue"]]
            self.assertEqual(ids[0], "web/w1")
            self.assertEqual(ids[-1], "eval/k21")                       # 入力済みは後ろ
            self.assertEqual(len(ids), 26)
            store.save("eval", ids[1][5:], [], {"rank": 1})
            self.assertEqual([r[0] for r in ex.queue()["queue"]], ids)  # 保存しても並びは動かない
            drafts["k20"]["suggest"] = ["錆"]
            it = ex.item("eval/k20")
            self.assertEqual(it["suggest_signs"], ["錆"])
            self.assertNotIn("rank", json.dumps({k: v for k, v in it.items() if k != "saved"}))   # 下書きのランクは出さない


class ExpertOutdoorTest(unittest.TestCase):
    """残りのキューは屋外の看板(壁面・袖・自立・屋上)だけ: 除く物は出さない、分からない物は後ろ、入力済みは残す。"""

    def test_outdoor_class(self):
        self.assertEqual(expert.outdoor_class("袖", ""), ("outdoor", "袖"))
        self.assertEqual(expert.outdoor_class("", "2本支柱の自立看板で、傾斜は見られない"), ("outdoor", "自立"))
        self.assertEqual(expert.outdoor_class("壁面", "ドアガラスにカッティングシートを貼った表示"), ("exclude", "カッティングシート"))
        self.assertEqual(expert.outdoor_class("", "屋内の壁面に平付けされた表示板"), ("exclude", "室内"))
        self.assertEqual(expert.outdoor_class("その他", "メッシュフェンスに結束した板"), ("unknown", ""))

    def test_queue_outdoor_first_unknown_back_excluded_hidden(self):
        with tempfile.TemporaryDirectory() as td:
            cat = expert.Catalog(private=Path(td), scans=Path(td) / "none", with_eval=False, drafts=None)
            for key, kind in (("u1", ""), ("o1", "壁面"), ("x1", ""), ("d1", ""), ("o2", "")):
                cat._add(expert.Item("field", key, [Path("f.jpg")], kind))
            cat.hints = {"field/x1": "店内の天井から吊った案内", "field/o2": "屋上の塔屋看板", "field/d1": "店内の案内"}
            store = expert.Store(Path(td) / "n.sqlite")
            store.save("field", "d1", [], {"rank": 1})
            with mock.patch.object(expert, "OUTDOOR_FILTER", True):
                q = expert.Expert(store, cat).queue()
            self.assertEqual([r[0] for r in q["queue"]], ["field/o1", "field/o2", "field/u1", "field/d1"])   # 入力済み d1 は最後尾(2026-11-06 15:55 の直し)
            self.assertEqual(q["outdoor"], {"outdoor": 2, "unknown": 1, "exclude": {"室内": 1}})


class ExpertNasTest(unittest.TestCase):
    """点検記録(nas): 実データの直後・報告書ページの前、屋外を先にランクを交ぜる、記録済みランクは画面に出さない、重複は載せない。"""

    ROWS = [("1.屋上チャンネルサイン", "3", 2), ("2.自立サイン", "1", 1), ("3.袖看板", "2", 8), ("4.壁面サイン", "1", 1),
            ("5.ポスターパネルA型サイン", "4", 1), ("6.ドアのカッティングシート", "1", 1), ("7.掲示板", "1", 1), ("8.写真なしの看板", "1", 0)]

    def _catalog(self, td: Path, dup: bool = False):
        nas = td / "eval" / "nas"
        nas.mkdir(parents=True)
        (nas / "expert_sets.txt").write_text("set_a\nset_b\n")
        for s in ("set_a", "set_b"):
            (nas / s / "photos").mkdir(parents=True)
            with open(nas / s / "items.tsv", "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter="\t")
                w.writerow(["source_file", "sheet", "client", "site", "inspection_date", "sign_name", "sign_no", "sign_rank_normalized", "photo_files"])
                for n, (name, rank, k) in enumerate(self.ROWS):
                    files = [f"photos/{n}_{i}.jpg" for i in range(k)]
                    for i, x in enumerate(files):
                        (nas / s / x).write_bytes(JPEG + f"{s}{n}{i}".encode())   # 中身を変えて重複にしない
                    w.writerow([f"{s}.xlsx", "表", "社名", "現場", "2022-08-23", name, str(n + 1), rank, json.dumps(files)])
        (td / "field_intake" / "photos" / "fs-1").mkdir(parents=True)
        (td / "field_intake" / "photos" / "fs-1" / "01.jpg").write_bytes(JPEG + (b"set_a00" if dup else b"field"))
        (td / "field_intake" / "pages" / "000001").mkdir(parents=True)
        (td / "field_intake" / "pages" / "000001" / "p-001.jpg").write_bytes(JPEG)
        return expert.Catalog(private=td, scans=td / "scans", with_eval=False, drafts=None)

    def test_position_mix_and_classes(self):
        with tempfile.TemporaryDirectory() as td:
            cat = self._catalog(Path(td))
            src = [i.split("/")[0] for i in cat.order]
            self.assertEqual(sorted(set(src), key=src.index), ["field", "nas", "page"])
            self.assertEqual(cat.counts["nas"], {"signs": 14, "photos": 26, "dup_skipped": 0})   # 写真なしは載せない、袖の 8 枚は 6 枚まで
            nas = [cat.items[i] for i in cat.order if i.startswith("nas/")]
            self.assertTrue(all("社名" not in it.key and "現場" not in it.key for it in nas))
            out = [it for it in nas if it.cls == "outdoor"]
            self.assertEqual(len(out), 8)
            self.assertEqual(nas[: len(out)], out)   # 屋外が先
            ranks = [cat.nas_meta[it.key]["rank"] for it in out]
            self.assertGreaterEqual(ranks[0], 3)      # 要対応→良好→経過観察→良好…
            self.assertEqual(ranks[1], 1)
            self.assertEqual(ranks[2], 2)
            self.assertEqual({cat.nas_meta[it.key]["sign_name"][:2]: it.kind for it in out},
                             {"1.": "屋上", "2.": "自立", "3.": "袖", "4.": "壁面"})
            self.assertEqual(max(len(it.files) for it in nas), expert.NAS_PHOTOS)
            store = expert.Store(Path(td) / "n.sqlite")
            with mock.patch.object(expert, "OUTDOOR_FILTER", True):
                ex = expert.Expert(store, cat)
                q = ex.queue()
            ids = [r[0] for r in q["queue"]]
            names = {it.id: cat.nas_meta[it.key]["sign_name"] for it in nas}
            self.assertFalse(any("カッティング" in names.get(i, "") for i in ids))       # カッティングシートは出さない
            self.assertLess(ids.index(out[-1].id), ids.index("page/000001-p-001"))
            back = [i for i in ids if names.get(i, "").startswith(("5.", "7."))]
            self.assertEqual(len(back), 4)                                                 # 置き型・掲示板は後ろ(消さない)
            self.assertGreater(min(ids.index(i) for i in back), ids.index(out[-1].id))
            item = ex.item(out[0].id)
            self.assertEqual(item["source_label"], "点検記録")
            blob = json.dumps(item, ensure_ascii=False)
            self.assertNotIn("rank\": 3", blob)
            self.assertNotIn("屋上チャンネル", blob)                                          # 看板名・記録済みランクは画面に出さない
            self.assertIsNone(item["saved"])

    def test_insp2017_before_nas(self):
        with tempfile.TemporaryDirectory() as td:
            pol = Path(td) / "eval" / "insp2017"
            (pol / "signs").mkdir(parents=True)
            (pol / "signs" / "a.jpg").write_bytes(JPEG + b"insp2017")
            with open(pol / "items.tsv", "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f, delimiter="\t")
                w.writerow(["source_file", "sheet", "site", "sign_name", "sign_no", "sign_rank_normalized", "photo_files"])
                w.writerow(["reports/x.pdf", "", "営業所", "ポール袖看板", "1", "3", json.dumps(["signs/a.jpg"])])
            cat = self._catalog(Path(td))
            src = [i.split("/")[0] for i in cat.order]
            self.assertEqual(sorted(set(src), key=src.index), ["field", "insp2017", "nas", "page"])   # 2017 報告書は NAS より先(2026-11-08)
            pid = next(i for i in cat.order if i.startswith("insp2017/"))
            self.assertEqual((cat.items[pid].cls, cat.items[pid].kind), ("outdoor", "袖"))
            self.assertEqual(expert.Expert(expert.Store(Path(td) / "n.sqlite"), cat).item(pid)["source_label"], "点検記録(2017)")

    def test_duplicate_of_field_photo_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            cat = self._catalog(Path(td), dup=True)
            self.assertEqual(cat.counts["nas"]["dup_skipped"], 1)
            self.assertEqual(cat.counts["nas"]["signs"], 13)

    def test_nas_meta_cli(self):
        with tempfile.TemporaryDirectory() as td:
            cat = self._catalog(Path(td))
            out = Path(td) / "meta.tsv"
            with mock.patch.object(expert, "Catalog", return_value=cat):
                self.assertEqual(expert.main(["nas-meta", "--out", str(out)]), 0)
            rows = list(csv.DictReader(open(out, encoding="utf-8"), delimiter="\t"))
            self.assertEqual(len(rows), 14)   # 写真の無い行は列にも表にも入れない
            self.assertEqual({r["rank"] for r in rows}, {"1", "2", "3", "4"})
