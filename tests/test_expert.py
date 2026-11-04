"""有資格者の言葉づけ画面(/expert/): ルート、保存→読み戻し、export、入力検証。写真は一時フォルダの小さな JPEG。"""
import csv
import json
import tempfile
import threading
import unittest
from urllib.parse import quote
from pathlib import Path

from kanban_karte.web import expert
from kanban_karte.web.server import App, make_server, seed_sample, SAMPLE_JSON
from tests.test_web import WebTest

JPEG = bytes.fromhex("ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001400010000000000000000000000000000000affc400141001000000000000000000000000000000ffda0008010100003f00d2cf20ffd9")


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


if __name__ == "__main__":
    unittest.main()
