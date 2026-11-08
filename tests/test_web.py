import json
import re
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from kanban_karte.eval.judge import Judge, Prediction
from kanban_karte.ledger import Ledger
from kanban_karte.web import api
from kanban_karte.web.server import SAMPLE_JSON, App, make_server, seed_sample

SAMPLE_DIR = SAMPLE_JSON.parent


class FakeJudge(Judge):
    name = "fake"

    def judge(self, item):
        return Prediction(2, {}, 0.5, raw={"reasons": ["見本"], "item_notes": {}}, usage={})


class WebTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.td = tempfile.TemporaryDirectory()
        cls.db = Path(cls.td.name) / "l.sqlite"
        seed_sample(cls.db)
        cls.app = App(cls.db, SAMPLE_DIR, sample=True, judge_factory=lambda rules: FakeJudge())
        cls.srv = make_server(cls.app, "127.0.0.1", 0)
        cls.base = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        cls.srv.server_close()
        cls.td.cleanup()

    def req(self, path, body=None, ctype="application/json", raw=False, method=None):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, headers={"Content-Type": ctype} if data is not None else {}, method=method)
        try:
            with urllib.request.urlopen(r) as resp:
                b = resp.read()
                return resp.status, (b if raw else json.loads(b)), resp.headers
        except urllib.error.HTTPError as e:
            b = e.read()
            e.close()
            return e.code, (b if raw else json.loads(b)), e.headers


class SampleStartTest(WebTest):
    def test_index_and_meta(self):
        st, body, h = self.req("/index.html", raw=True)   # 旧い台帳画面(/ は /v3/ へ転送、下で確かめる)
        self.assertEqual(st, 200)
        self.assertIn("text/html", h["Content-Type"])
        st, body, h = self.req("/", raw=True)   # / は /v3/(UI v3 のホーム)へ転送される(urlopen が追う)
        self.assertEqual(st, 200)
        self.assertIn("今日やること", body.decode("utf-8"))
        st, meta, _ = self.req("/api/meta")
        self.assertTrue(meta["sample"])
        self.assertEqual(len(meta["items"]), 18)

    def test_sites_and_signs(self):
        _, sites, _ = self.req("/api/sites")
        self.assertEqual([s["id"] for s in sites], ["demo-site-1"])
        self.assertEqual(sites[0]["sign_count"], 3)
        _, signs, _ = self.req("/api/signs?site=demo-site-1")
        self.assertEqual([s["id"] for s in signs], ["demo-001", "demo-002", "demo-003"])
        s1 = signs[0]
        self.assertEqual((s1["status"], s1["rank"], s1["photo_count"], s1["undecided"]), ("draft", 3, 6, 0))
        self.assertEqual(signs[1]["undecided"], 12)
        self.assertEqual(self.req("/api/signs?site=nope")[0], 404)

    def test_detail_marks_yellow(self):
        _, d, _ = self.req("/api/signs/demo-001")
        yellow = [i["key"] for i in d["items"] if i["yellow"]]
        self.assertEqual(yellow, ["基礎部・上部構造-3", "表示部-3"])  # 下書きが「要実測」とした2マス
        self.assertEqual(len(d["items"]), 18)
        self.assertTrue(d["editable"])
        _, d2, _ = self.req("/api/signs/demo-002")
        self.assertEqual(d2["yellow_count"], 13)  # 判断できず12 + 要実測1(取付部-3 は値あり)
        self.assertEqual(self.req("/api/signs/none")[0], 404)

    def test_media_resolves_relative_paths(self):
        _, d, _ = self.req("/api/signs/demo-001")
        urls = [p["url"] for p in d["photos"]]
        self.assertEqual(urls[0], "/media/photos/plain_a.jpg")
        st, body, h = self.req(urls[0], raw=True)
        self.assertEqual((st, h["Content-Type"]), (200, "image/png"))
        self.assertTrue(body.startswith(b"\x89PNG"))
        for bad in ("/media/../ledger.json", "/media/%2e%2e/ledger.json", "/media/ledger.json", "/media/photos/none.png",
                    "/media/photos"):
            self.assertEqual(self.req(bad, raw=True)[0], 404, bad)

    def test_static_traversal(self):
        self.assertEqual(self.req("/static/../server.py", raw=True)[0], 404)
        self.assertEqual(self.req("/v3/../../server.py", raw=True)[0], 404)

    def test_v3_entrances(self):
        for path, want in (("/v3/", "今日やること"), ("/v3/field/", "現場"), ("/v3/n5.html", "N5")):
            st, body, _ = self.req(path, raw=True)
            self.assertEqual(st, 200, path)
            self.assertIn(want, body.decode("utf-8"), path)
        st, body, _ = self.req("/field", raw=True)   # 308 → /v3/field/(urllib は転送をたどる)
        self.assertEqual(st, 200)
        self.assertIn("現場", body.decode("utf-8"))

    def test_post_needs_json(self):
        st, _, _ = self.req("/api/signs/demo-001/items", {"item": "rank", "value": 2, "by": "x"}, ctype="text/plain")
        self.assertEqual(st, 415)

    def test_rules_bundles_empty(self):
        self.assertEqual(self.req("/api/rules")[1], [])
        self.assertEqual(self.req("/api/bundles")[1], [])
        self.assertEqual(self.req("/api/bundles/9/draft", {})[0], 404)

    def test_export_xlsx(self):
        for kind in ("tokyo", "client-a"):
            st, body, h = self.req(f"/api/sites/demo-site-1/output/{kind}.xlsx", raw=True)
            self.assertEqual(st, 200)
            self.assertTrue(body.startswith(b"PK"))
            self.assertIn("attachment", h["Content-Disposition"])
        self.assertEqual(self.req("/api/sites/nope/output/tokyo.xlsx")[0], 404)
        self.assertEqual(self.req("/api/sites/demo-site-1/output/other.xlsx")[0], 404)


class EntryTest(WebTest):
    """使う人ごとの3入口: 社内(/)・行政(/gov/)・オーナー(/owner/)。ナビのタブとタイトルが入口ごとに違う。"""

    def page(self, path):
        st, body, h = self.req(path, raw=True)
        self.assertEqual(st, 200)
        self.assertIn("text/html", h["Content-Type"])
        return body.decode("utf-8")

    @staticmethod
    def tabs(html):
        return re.findall(r'<a href="[^"]*" data-nav="[^"]*">([^<]*)</a>', html)

    def test_internal_tabs(self):
        html = self.page("/index.html")   # 旧い台帳画面(/ は UI v3 へ転送)
        self.assertIn('data-entry="internal"', html)
        self.assertEqual(self.tabs(html), ["受付", "台帳", "規則と出力"])
        self.assertIn("<title>SignGrid</title>", html)
        self.assertIn('id="sample-badge"', html)

    def test_gov_entry(self):
        html = self.page("/gov/")
        self.assertIn('data-entry="gov"', html)
        self.assertEqual(self.tabs(html), [])
        self.assertNotIn("<nav", html)
        self.assertIn('class="brand" href="#/gov">SignGrid 行政<', html)
        self.assertIn("<title>SignGrid 行政</title>", html)
        self.assertIn('id="sample-badge"', html)

    def test_owner_entry(self):
        html = self.page("/owner/")
        self.assertIn('data-entry="owner"', html)
        self.assertEqual(self.tabs(html), [])
        self.assertNotIn("<nav", html)
        self.assertIn("看板の持ち主(店舗・企業)向け", html)
        self.assertIn("<title>SignGrid 看板の持ち主向け</title>", html)
        self.assertIn('id="sample-badge"', html)

    def test_redirect_without_trailing_slash(self):
        import http.client
        for path in ("/gov", "/owner"):
            c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1])
            c.request("GET", path + "?today=2026-11-02")
            r = c.getresponse()
            r.read()
            self.assertEqual(r.status, 308)
            self.assertEqual(r.getheader("Location"), path + "/?today=2026-11-02")
            c.close()

    def test_api_and_static_unchanged(self):
        st, meta, _ = self.req("/api/meta")
        self.assertEqual(st, 200)
        self.assertTrue(meta["sample"])
        for f in ("app.js", "gov.js", "owner.js"):
            st, _, h = self.req("/static/" + f, raw=True)
            self.assertEqual(st, 200)


class RoundTripTest(unittest.TestCase):
    """訂正 → 確定 → 一覧の状態変化(HTTP 越し、専用の台帳)。"""

    def test_correct_finalize_list(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "l.sqlite"
            seed_sample(db)
            app = App(db, SAMPLE_DIR, sample=True)
            srv = make_server(app, "127.0.0.1", 0)
            base = f"http://127.0.0.1:{srv.server_address[1]}"
            threading.Thread(target=srv.serve_forever, daemon=True).start()
            try:
                def call(path, body=None):
                    r = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                               headers={"Content-Type": "application/json"})
                    try:
                        with urllib.request.urlopen(r) as resp:
                            return resp.status, json.loads(resp.read())
                    except urllib.error.HTTPError as e:
                        body = json.loads(e.read())
                        e.close()
                        return e.code, body

                # 判断できず(黄色)のマスを直す: 値が入り、黄色でなくなり、履歴に出る。規則候補が積まれる
                st, d = call("/api/signs/demo-002/items", {"item": "支持部-1", "value": "要改善", "by": "見本 太郎",
                                                           "note": "根元の錆"})
                self.assertEqual(st, 200)
                it = {i["key"]: i for i in d["items"]}["支持部-1"]
                self.assertEqual((it["value"], it["yellow"], it["corrected"]), ("要改善", False, True))
                self.assertEqual(d["rank"], 3)  # 項目の最悪値に従う(review.apply_correction)
                self.assertEqual(d["corrections"][0]["old_value"], None)
                self.assertIsNotNone(d["rule_id"])
                self.assertEqual(len(call("/api/rules")[1]), 1)
                # 同じ値は記録しない
                st, d = call("/api/signs/demo-002/items", {"item": "支持部-1", "value": "要改善", "by": "見本 太郎"})
                self.assertEqual((st, d["changed"], len(d["corrections"])), (200, False, 1))
                # 不正: 名前なし・区分違い・未知の項目
                self.assertEqual(call("/api/signs/demo-002/items", {"item": "支持部-1", "value": "良好", "by": ""})[0], 400)
                self.assertEqual(call("/api/signs/demo-002/items", {"item": "支持部-1", "value": "最悪", "by": "x"})[0], 400)
                self.assertEqual(call("/api/signs/demo-002/items", {"item": "存在しない", "value": "良好", "by": "x"})[0], 400)
                # 確定: 名前が要る。確定後は編集不可(409)
                self.assertEqual(call("/api/signs/demo-002/finalize", {"by": " "})[0], 400)
                st, d = call("/api/signs/demo-002/finalize", {"by": "見本 太郎"})
                self.assertEqual((st, d["status"], d["confirmed_by"], d["editable"]), (200, "confirmed", "見本 太郎", False))
                self.assertEqual(call("/api/signs/demo-002/items", {"item": "支持部-1", "value": "良好", "by": "x"})[0], 409)
                self.assertEqual(call("/api/signs/demo-002/finalize", {"by": "x"})[0], 409)
                self.assertEqual(call("/api/signs/demo-003/items", {"item": "rank", "value": 2, "by": "x"})[0], 409)
                # 確定の取り消し: 名前が要る。下書きには 409。取り消すと editable に戻り、訂正が通る
                self.assertEqual(call("/api/signs/demo-003/reopen", {"by": " "})[0], 400)
                self.assertEqual(call("/api/signs/demo-001/reopen", {"by": "x"})[0], 409)
                st, d = call("/api/signs/demo-003/reopen", {"by": "見本 太郎"})
                self.assertEqual((st, d["status"], d["confirmed_by"], d["editable"]), (200, "draft", None, True))
                self.assertEqual(call("/api/signs/demo-003/items", {"item": "rank", "value": 2, "by": "x"})[0], 200)
                self.assertEqual(call("/api/signs/demo-003/finalize", {"by": "見本 太郎"})[1]["status"], "confirmed")
                # 一覧と現場の集計に反映
                signs = {s["id"]: s for s in call("/api/signs?site=demo-site-1")[1]}
                self.assertEqual((signs["demo-002"]["status"], signs["demo-002"]["rank"]), ("confirmed", 3))
                self.assertEqual(signs["demo-001"]["status"], "draft")
                site = call("/api/sites")[1][0]
                self.assertEqual((site["draft_count"], site["confirmed_count"]), (1, 2))
                # 台帳にも同じものが入っている
                with Ledger(db, data_dir=SAMPLE_DIR) as lg:
                    self.assertEqual(lg.get_sign("demo-002").status, "confirmed")
                    self.assertEqual(len(lg.list_corrections("demo-002")), 1)
            finally:
                srv.shutdown()
                srv.server_close()

    def test_draft_button(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "l.sqlite"
            seed_sample(db)
            with Ledger(db, data_dir=SAMPLE_DIR) as lg:
                bid = lg.add_bundle("scan.pdf", "sha1", 0)
                lg.conn.execute("UPDATE signs SET source_bundle=?, status='new' WHERE id='demo-003'", (bid,))
                lg.conn.commit()
                out = api.draft_bundle(lg, bid, lambda rules: FakeJudge())
                self.assertEqual(out["results"][0]["status"], "draft")
                self.assertEqual(lg.get_sign("demo-003").status, "draft")
                self.assertEqual(api.bundles(lg)[0]["draft_count"], 1)
                with self.assertRaises(api.ApiError) as cm:
                    api.draft_bundle(lg, 99)
                self.assertEqual(cm.exception.status, 404)

    def test_draft_disabled_without_key(self):
        import os
        from unittest import mock
        with tempfile.TemporaryDirectory() as td:
            with Ledger(Path(td) / "l.sqlite") as lg:
                bid = lg.add_bundle("scan.pdf", "sha1", 0)
                with mock.patch.dict(os.environ, {}, clear=True), mock.patch("kanban_karte.web.api.can_draft", return_value=False):
                    with self.assertRaises(api.ApiError) as cm:
                        api.draft_bundle(lg, bid)
                    self.assertEqual(cm.exception.status, 503)


class SampleBadgeTest(unittest.TestCase):
    """「見本データ」の帯は hidden 属性で消す。CSS の display 指定が hidden に勝つと実データでも帯が出る(2026-11-03 の一周の画面で発覚)。"""

    STATIC = Path(__file__).resolve().parents[1] / "kanban_karte" / "web" / "static"

    def test_hidden_attribute_beats_display_rules(self):
        css = (self.STATIC / "app.css").read_text(encoding="utf-8")
        self.assertIn("[hidden]{display:none !important}", css)
        html = (self.STATIC / "index.html").read_text(encoding="utf-8")
        self.assertRegex(html, r'id="sample-badge"[^>]*\bhidden\b')

    def test_meta_sample_false_for_real_ledger(self):
        from kanban_karte.web import api
        self.assertFalse(api.meta(False)["sample"])
        self.assertTrue(api.meta(True)["sample"])


if __name__ == "__main__":
    unittest.main()
