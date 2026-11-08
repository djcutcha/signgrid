"""UI v3 のバックエンド: 相対 data_dir の画像 URL、ホームの既定の現場、現場の属性(PATCH)、画面の取り込み(/api/intake/)、
現場ウィザード(/api/field/)。API は呼ばない(偽の判定役・偽のクライアント)。"""
import base64
import io
import json
import os
import sqlite3
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from PIL import Image

from kanban_karte.eval.judge import Judge, Prediction
from kanban_karte.gov.geocode import Geocoder
from kanban_karte.intake.classifier import Classification, PageClassifier
from kanban_karte.intake.pdf_bundle import PerPageGrouper
from kanban_karte.ledger import Ledger
from kanban_karte.ledger.db import Sign, Site
from kanban_karte.pipeline import field_draft
from kanban_karte.web import api
from kanban_karte.web.server import App, make_server


def png_bytes(n: int = 0, size=(64, 48)) -> bytes:
    b = io.BytesIO()
    Image.new("RGB", size, (n % 256, (n // 256) % 256, 9)).save(b, "PNG")
    return b.getvalue()


def b64(b: bytes) -> str:
    return base64.b64encode(b).decode()


def write_png(p: Path, n: int = 0) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(png_bytes(n))
    return p


class Server:
    """一時の台帳と data_dir で App を立てる。"""

    def setUp_server(self, **kw):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.db = self.root / "l.sqlite"
        self.data = self.root / "data"
        self.app = App(self.db, self.data, **kw)
        self.srv = make_server(self.app, "127.0.0.1", 0)
        self.base = f"http://127.0.0.1:{self.srv.server_address[1]}"
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()

    def tearDown_server(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.td.cleanup()

    def req(self, path, body=None, method=None, raw=False):
        data = None if body is None else json.dumps(body).encode()
        r = urllib.request.Request(self.base + path, data=data, method=method,
                                   headers={"Content-Type": "application/json"} if data is not None else {})
        try:
            with urllib.request.urlopen(r) as resp:
                b = resp.read()
                return resp.status, (b if raw else json.loads(b))
        except urllib.error.HTTPError as e:
            b = e.read()
            e.close()
            return e.code, (b if raw else json.loads(b))


class RelativeDataDirTest(unittest.TestCase):
    """--data-dir data/private(相対)で起動し、台帳の写真は絶対パス(LINE の受信箱)のとき、画像の URL が null にならない。"""

    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name).resolve()
        self.cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, self.cwd)
        self.addCleanup(self.td.cleanup)
        self.photo = write_png(self.root / "data/private/line_inbox/Uabc/2026-11-05/m1.png")

    def ledger(self):
        return Ledger(self.root / "l.sqlite", data_dir=Path("data/private"))

    def test_url_for_absolute_photo_under_relative_data_dir(self):
        with self.ledger() as lg:
            lg.upsert_site(Site("s1", "現場"))
            lg.upsert_sign(Sign(id="gbd9e-1", site_id="s1", photos=[str(self.photo)]))
            d = api.sign_detail(lg, "gbd9e-1")
        self.assertEqual(d["photos"][0]["url"], "/media/line_inbox/Uabc/2026-11-05/m1.png")

    def test_rel_path_and_resolve_path(self):
        with self.ledger() as lg:
            self.assertTrue(lg.data_dir.is_absolute())
            self.assertEqual(lg.rel_path(self.photo), "line_inbox/Uabc/2026-11-05/m1.png")
            self.assertEqual(lg.rel_path("data/private/line_inbox/Uabc/2026-11-05/m1.png"), "line_inbox/Uabc/2026-11-05/m1.png")
            self.assertEqual(lg.rel_path("pages/000001/p-001.jpg"), "pages/000001/p-001.jpg")   # 取り込みが書く相対パスはそのまま
            self.assertEqual(lg.resolve_path("line_inbox/Uabc/2026-11-05/m1.png"), self.photo)

    def test_outside_data_dir_gets_no_url(self):
        out = write_png(self.root / "elsewhere/x.png")
        with self.ledger() as lg:
            lg.upsert_sign(Sign(id="x-1", photos=[str(out)]))
            self.assertIsNone(api.sign_detail(lg, "x-1")["photos"][0]["url"])

    def test_media_path_still_refuses_outside_and_non_images(self):
        app = App(self.root / "l.sqlite", "data/private")
        self.assertTrue(app.data_dir.is_absolute())
        self.assertEqual(app.media_path("line_inbox/Uabc/2026-11-05/m1.png"), self.photo)
        write_png(self.root / "data/secret.png")
        (self.root / "data/private/notes.txt").write_text("x")
        self.assertIsNone(app.media_path("../secret.png"))
        self.assertIsNone(app.media_path("notes.txt"))
        self.assertIsNone(app.media_path("missing.png"))
        self.assertIsNone(app.media_path(str(self.root / "data/secret.png")))   # 絶対パスで外を指す

    def test_served_over_http(self):
        with self.ledger() as lg:
            lg.upsert_sign(Sign(id="gbd9e-1", photos=[str(self.photo)]))
        app = App(self.root / "l.sqlite", "data/private")
        srv = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        with urllib.request.urlopen(base + "/api/signs/gbd9e-1") as r:
            url = json.loads(r.read())["photos"][0]["url"]
        self.assertEqual(url, "/media/line_inbox/Uabc/2026-11-05/m1.png")
        with urllib.request.urlopen(base + url) as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")
        with self.assertRaises(urllib.error.HTTPError) as cm:
            urllib.request.urlopen(base + "/media/%2e%2e/secret.png")
        self.assertEqual(cm.exception.code, 404)
        cm.exception.close()


class HomeDefaultSiteTest(unittest.TestCase):
    def setUp(self):
        self.lg = Ledger(":memory:")
        self.addCleanup(self.lg.close)

    def sign(self, sid, site, updated):
        self.lg.upsert_sign(Sign(id=sid, site_id=site))
        self.lg.conn.execute("UPDATE signs SET updated=? WHERE id=?", (updated, sid))
        self.lg.conn.commit()

    def test_latest_active_site_with_signs_first_and_empty_last(self):
        # 名前順だと「LINE受信 ...」が先頭になる(空の現場)。既定は看板のある最新の現場
        self.lg.upsert_site(Site("l", "LINE受信 2026-11-02"))
        self.lg.upsert_site(Site("o", "古い現場"))
        self.lg.upsert_site(Site("n", "新しい現場"))
        self.sign("o-1", "o", "2026-10-01 09:00:00")
        self.sign("n-1", "n", "2026-11-05 13:06:33")
        self.sign("n-2", "n", "2026-11-04 08:00:00")
        got = api.sites(self.lg)
        self.assertEqual([s["id"] for s in got], ["n", "o", "l"])
        self.assertEqual(got[0]["last_active"], "2026-11-05 13:06:33")
        self.assertEqual(got[2]["sign_count"], 0)

    def test_new_bundle_counts_as_activity(self):
        self.lg.upsert_site(Site("a", "A"))
        self.lg.upsert_site(Site("b", "B"))
        self.sign("a-1", "a", "2026-10-01 09:00:00")
        self.sign("b-1", "b", "2026-09-01 09:00:00")
        bid = self.lg.add_bundle("line://x", "sha-b", 1)
        self.lg.set_bundle(bid, site_id="b")
        self.lg.conn.execute("UPDATE bundles SET created='2026-11-03 10:00:00' WHERE id=?", (bid,))
        self.lg.conn.commit()
        self.assertEqual([s["id"] for s in api.sites(self.lg)], ["b", "a"])

    def test_ties_and_empty_ledger(self):
        self.assertEqual(api.sites(self.lg), [])
        self.lg.upsert_site(Site("z", "Z"))
        self.lg.upsert_site(Site("y", "Y"))
        self.assertEqual({s["id"] for s in api.sites(self.lg)}, {"y", "z"})


class SiteAttrsTest(Server, unittest.TestCase):
    def setUp(self):
        self.setUp_server(sample=False)
        with self.app.ledger() as lg:
            lg.upsert_site(Site("s1", "サンプル現場", address="埼玉県越谷市"))
            lg.upsert_site(Site("s2", "別の現場"))
        self.addCleanup(self.tearDown_server)

    def test_defaults_in_get(self):
        _, sites = self.req("/api/sites")
        s = next(x for x in sites if x["id"] == "s1")
        self.assertEqual([s[k] for k in ("orderer_type", "chain_brand", "contact_person", "contact")], [None] * 4)

    def test_patch_updates_and_returns_site(self):
        st, s = self.req("/api/sites/s1", {"orderer_type": "代理店", "chain_brand": "ミスタードーナツ",
                                           "contact_person": " 山田 ", "contact": "048-000-0000"}, method="PATCH")
        self.assertEqual(st, 200)
        self.assertEqual((s["id"], s["orderer_type"], s["chain_brand"], s["contact_person"], s["contact"]),
                         ("s1", "代理店", "ミスタードーナツ", "山田", "048-000-0000"))
        _, sites = self.req("/api/sites")
        self.assertEqual(next(x for x in sites if x["id"] == "s1")["orderer_type"], "代理店")
        self.assertIsNone(next(x for x in sites if x["id"] == "s2")["orderer_type"])   # 他の現場は変わらない

    def test_partial_patch_keeps_other_keys_and_null_clears(self):
        self.req("/api/sites/s1", {"orderer_type": "直", "contact": "a@example.com"}, method="PATCH")
        _, s = self.req("/api/sites/s1", {"chain_brand": "X"}, method="PATCH")
        self.assertEqual((s["orderer_type"], s["chain_brand"], s["contact"]), ("直", "X", "a@example.com"))
        _, s = self.req("/api/sites/s1", {"contact": None, "orderer_type": ""}, method="PATCH")
        self.assertEqual((s["orderer_type"], s["contact"], s["chain_brand"]), (None, None, "X"))

    def test_english_aliases(self):
        _, s = self.req("/api/sites/s1", {"orderer_type": "direct"}, method="PATCH")
        self.assertEqual(s["orderer_type"], "直")
        _, s = self.req("/api/sites/s1", {"orderer_type": "agency"}, method="PATCH")
        self.assertEqual(s["orderer_type"], "代理店")

    def test_validation(self):
        self.assertEqual(self.req("/api/sites/s1", {"orderer_type": "元請"}, method="PATCH")[0], 400)
        self.assertEqual(self.req("/api/sites/s1", {"chain_brand": 3}, method="PATCH")[0], 400)
        self.assertEqual(self.req("/api/sites/s1", {"contact": "x" * 201}, method="PATCH")[0], 400)
        self.assertEqual(self.req("/api/sites/s1", {"name": "改名"}, method="PATCH")[0], 400)   # 属性以外は直せない
        self.assertEqual(self.req("/api/sites/none", {"contact": "x"}, method="PATCH")[0], 404)
        self.assertEqual(self.req("/api/sites/s1", {"contact": "x"})[0], 405)                  # POST では直せない
        _, s = self.req("/api/sites")
        self.assertIsNone(next(x for x in s if x["id"] == "s1")["contact"])                    # 失敗は何も書かない

    def test_upsert_site_does_not_wipe_attrs(self):
        self.req("/api/sites/s1", {"orderer_type": "直", "chain_brand": "X"}, method="PATCH")
        with self.app.ledger() as lg:
            lg.upsert_site(Site("s1", "サンプル現場(改)", address="埼玉県越谷市"))   # LINE の束が現場を作り直す経路
            s = lg.get_site("s1")
        self.assertEqual((s.name, s.orderer_type, s.chain_brand), ("サンプル現場(改)", "直", "X"))


class SiteMigrationTest(unittest.TestCase):
    def test_old_ledger_gets_columns_and_reopen_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "old.sqlite"
            with Ledger(db) as lg:
                lg.upsert_site(Site("s", "現場"))
            c = sqlite3.connect(db)   # 属性の列が無い古い台帳にする(sites を作り直す)
            c.executescript("""CREATE TABLE s2 AS SELECT id, name, address, lat, lng, municipality, created FROM sites;
                DROP TABLE sites; ALTER TABLE s2 RENAME TO sites;""")
            c.commit()
            c.close()
            for _ in range(2):
                with Ledger(db) as lg:
                    cols = {r[1] for r in lg.conn.execute("PRAGMA table_info(sites)")}
                    self.assertTrue({"orderer_type", "chain_brand", "contact_person", "contact"} <= cols)
                    lg.set_site_attrs("s", {"orderer_type": "直"})
                    self.assertEqual(lg.get_site("s").orderer_type, "直")
            with Ledger(db) as lg, self.assertRaises(ValueError):
                lg.set_site_attrs("s", {"orderer_type": "不明"})


# ---- 取り込み ----
class AllPhotos(PageClassifier):
    name = "fake"

    def classify(self, page):
        return Classification("写真", "fake: 写真")


class FakeJudge(Judge):
    name = "fake"

    def judge(self, item):
        return Prediction(2, {}, 0.5, raw={"reasons": ["見本"], "item_notes": {}}, usage={})


def pdf_bytes(n_pages=1) -> bytes:
    ims = [Image.new("RGB", (600, 800), (200, 200 - i, 200)) for i in range(n_pages)]
    b = io.BytesIO()
    ims[0].save(b, "PDF", save_all=True, append_images=ims[1:], resolution=100)
    return b.getvalue()


def files(*items):
    return {"files": [{"name": n, "data": b64(b)} for n, b in items]}


class IntakeTest(Server, unittest.TestCase):
    def setUp(self):
        self.setUp_server(sample=False, judge_factory=lambda rules: FakeJudge(),
                          intake_parts=lambda log: (AllPhotos(), PerPageGrouper()))
        self.addCleanup(self.tearDown_server)

    def upload(self, body):
        st, r = self.req("/api/intake/upload", body)
        if st == 200:
            self.app.intake_jobs.wait(r["bundle_id"], 30)
        return st, r

    def test_photo_folder_runs_whole_pipeline(self):
        st, r = self.upload(files(*[(f"IMG_{i}.png", png_bytes(i)) for i in range(3)]))
        self.assertEqual((st, r["status"], r["existed"], r["kind"], r["files"]), (200, "running", False, "images", 3))
        st, p = self.req(f"/api/intake/{r['bundle_id']}/progress")
        self.assertEqual((st, p["status"]), (200, "done"))
        self.assertEqual({s["key"]: (s["done"], s["total"]) for s in p["stages"]},
                         {"sort": (3, 3), "transcribe": (None, None), "draft": (1, 1), "verify": (None, None)})
        self.assertEqual([s["label"] for s in p["stages"]], ["仕分け", "転記", "下書き", "反証役"])
        self.assertEqual(len(p["log"]), 6)
        self.assertIn("下書き完了", p["log"][-1])
        self.assertEqual(p["sign_count"], 1)
        with self.app.ledger() as lg:
            s = lg.list_signs(bundle_id=r["bundle_id"])[0]
            self.assertEqual((s.status, s.draft_rank, len(s.photos)), ("draft", 2, 3))

    def test_single_paper_photo_is_one_page_bundle(self):
        st, r = self.upload(files(("paper.png", png_bytes(1, (600, 800)))))
        self.assertEqual(st, 200)
        _, p = self.req(f"/api/intake/{r['bundle_id']}/progress")
        self.assertEqual(p["status"], "done")
        self.assertEqual(p["stages"][0]["total"], 1)
        with self.app.ledger() as lg:
            self.assertEqual(lg.get_bundle(r["bundle_id"])["pages"], 1)

    def test_pdf_goes_the_same_path(self):
        st, r = self.upload(files(("scan.pdf", pdf_bytes(2))))
        self.assertEqual((st, r["kind"]), (200, "pdf"))
        _, p = self.req(f"/api/intake/{r['bundle_id']}/progress")
        self.assertEqual((p["status"], p["stages"][0]["done"], p["stages"][0]["total"]), ("done", 2, 2))
        self.assertEqual(p["bundle_status"], "signs_drafted")

    def test_same_upload_is_not_redone(self):
        body = files(("a.png", png_bytes(5)))
        _, a = self.upload(body)
        _, b = self.upload(body)
        self.assertEqual((b["bundle_id"], b["existed"], b["status"]), (a["bundle_id"], True, "done"))
        self.assertEqual(len(self.req("/api/bundles")[1]), 1)

    def test_validation(self):
        self.assertEqual(self.req("/api/intake/upload", {})[0], 400)
        self.assertEqual(self.req("/api/intake/upload", {"files": []})[0], 400)
        self.assertEqual(self.req("/api/intake/upload", {"files": [{"name": "a", "data": "!!"}]})[0], 400)
        self.assertEqual(self.req("/api/intake/upload", files(("a.txt", b"hello")))[0], 400)
        self.assertEqual(self.req("/api/intake/upload", files(("a.pdf", pdf_bytes()), ("b.png", png_bytes())))[0], 400)   # 混在
        self.assertEqual(self.req("/api/intake/upload", files(("a.pdf", pdf_bytes()), ("b.pdf", pdf_bytes(2))))[0], 400)
        self.assertEqual(self.req("/api/intake/upload", {**files(("a.png", png_bytes())), "site_id": "nope"})[0], 404)
        self.assertEqual(self.req("/api/bundles")[1], [])   # 失敗は束を作らない
        self.assertEqual(self.req("/api/intake/99/progress")[0], 404)

    def test_broken_pdf_marks_error_and_progress_says_so(self):
        st, r = self.upload(files(("bad.pdf", b"%PDF-1.4 not really")))
        self.assertEqual(st, 200)
        _, p = self.req(f"/api/intake/{r['bundle_id']}/progress")
        self.assertEqual(p["status"], "error")
        self.assertTrue(p["error"])
        self.assertEqual(p["bundle_status"], "error")

    def test_site_id_is_bound(self):
        with self.app.ledger() as lg:
            lg.upsert_site(Site("s1", "現場"))
        _, r = self.upload({**files(("a.png", png_bytes(8))), "site_id": "s1"})
        _, p = self.req(f"/api/intake/{r['bundle_id']}/progress")
        self.assertEqual(p["site_id"], "s1")

    def test_no_key_keeps_sort_result_and_reports_error(self):
        self.app.judge_factory = None
        with mock.patch.object(api, "can_draft", return_value=False):
            _, r = self.upload(files(("a.png", png_bytes(9))))
        _, p = self.req(f"/api/intake/{r['bundle_id']}/progress")
        self.assertEqual(p["status"], "error")
        self.assertIn("ANTHROPIC_API_KEY", p["error"])
        self.assertEqual(p["stages"][0]["done"], 1)
        self.assertEqual((p["stages"][2]["done"], p["stages"][2]["total"]), (None, None))   # 写真だけの束は、下書きの時に枠ができる。まだ記録なし

    def test_progress_of_bundle_not_run_here(self):
        with self.app.ledger() as lg:
            bid = lg.add_bundle("line://x", "sha-line", 2)
            lg.set_bundle(bid, status="pages_registered")
        _, p = self.req(f"/api/intake/{bid}/progress")
        self.assertEqual((p["status"], p["log"]), ("pending", []))
        self.assertTrue(all(s["done"] is None and s["total"] is None for s in p["stages"]))


# ---- 現場ウィザード ----
def fake_client(*texts, usage=(1200, 300)):
    calls = []
    seq = list(texts)

    def create(**kw):
        calls.append(kw)
        t = seq.pop(0) if len(seq) > 1 else seq[0]
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=t)], stop_reason="end_turn",
                               usage=SimpleNamespace(input_tokens=usage[0], output_tokens=usage[1]))
    return SimpleNamespace(messages=SimpleNamespace(create=create), calls=calls)


def answer(part, result="良好", evidence="写真1の面板に傷みは見えない"):
    return json.dumps({"items": {f"{g}-{n}": {"result": result, "evidence": evidence}
                                 for g, n, _t in field_draft.items_of(part)}}, ensure_ascii=False)


class FieldDraftTest(unittest.TestCase):
    def drafter(self, *texts, **kw):
        c = fake_client(*texts, **kw)
        return field_draft.FieldDrafter(client=c), c

    def test_parts_follow_the_form(self):
        self.assertEqual(field_draft.PARTS, ["基礎部・上部構造", "支持部", "取付部", "表示部", "照明装置", "その他"])
        self.assertEqual([len(field_draft.items_of(p)) for p in field_draft.PARTS], [3, 2, 3, 4, 3, 3])
        self.assertEqual(sum(len(p["items"]) for p in api.field_parts()), 18)

    def test_draft_only_that_parts_items(self):
        d, c = self.drafter(answer("表示部", "経過観察"))
        r = d.draft("表示部", [png_bytes(1), png_bytes(2)], "袖看板")
        self.assertEqual([i["key"] for i in r["items"]], ["表示部-1", "表示部-2", "表示部-3", "表示部-4"])
        self.assertEqual({i["result"] for i in r["items"]}, {"経過観察"})
        self.assertEqual(r["items"][0]["text"], "表示面板・切り文字等の腐食、破損、変形、ビス等の欠落")
        self.assertEqual(r["usage"]["input_tokens"], 1200)
        self.assertAlmostEqual(r["usage"]["cost_usd"], (1200 * 2.0 + 300 * 10.0) / 1e6)
        kw = c.calls[0]
        self.assertEqual((kw["model"], kw["max_tokens"]), (field_draft.DEFAULT_MODEL, field_draft.MAX_TOKENS))
        self.assertLessEqual(kw["max_tokens"], 2000)
        sent = kw["messages"][0]["content"]
        self.assertEqual(sum(b["type"] == "image" for b in sent), 2)
        self.assertNotIn("支持部", sent[-1]["text"])   # 他の部位の項目は聞かない
        self.assertIn("袖看板", sent[-1]["text"])

    def test_undecided_and_not_applicable_are_valid(self):
        d, _ = self.drafter(json.dumps({"items": {
            "照明装置-1": {"result": "対象外", "evidence": "照明の無い看板"},
            "照明装置-2": {"result": "判断できず", "evidence": "暗くて見えない"},
            "照明装置-3": {"result": "要改善", "evidence": "配線の被覆が破れている"}}}, ensure_ascii=False))
        r = d.draft("照明装置", [png_bytes()])
        self.assertEqual([i["result"] for i in r["items"]], ["対象外", "判断できず", "要改善"])

    def test_bad_json_is_retried_once(self):
        d, c = self.drafter("これはJSONではない", answer("支持部"))
        r = d.draft("支持部", [png_bytes()])
        self.assertEqual(r["usage"]["attempts"], 2)
        self.assertEqual(r["usage"]["input_tokens"], 2400)
        self.assertEqual(len(c.calls), 2)

    def test_missing_item_or_bad_result_fails_with_502(self):
        for bad in (json.dumps({"items": {"支持部-1": {"result": "良好", "evidence": "x"}}}),
                    answer("支持部", "ほぼ良好")):
            d, _ = self.drafter(bad)
            with self.assertRaises(field_draft.FieldDraftError) as cm:
                d.draft("支持部", [png_bytes()])
            self.assertEqual(cm.exception.status, 502)

    def test_api_validation_and_errors(self):
        ok = {"part": "取付部", "photos": [b64(png_bytes())]}
        factory = lambda: field_draft.FieldDrafter(client=fake_client(answer("取付部")))   # noqa: E731
        self.assertEqual(len(api.field_draft(ok, factory)["items"]), 3)
        for bad in ({**ok, "part": "屋根"}, {**ok, "part": None}, {**ok, "photos": []}, {**ok, "photos": [ok["photos"][0]] * 3},
                    {**ok, "photos": ["!!"]}, {**ok, "photos": [1]}, {"part": "取付部"}, {**ok, "photos": [b64(b"")]}):
            with self.assertRaises(api.ApiError) as cm:
                api.field_draft(bad, factory)
            self.assertEqual(cm.exception.status, 400, bad)
        with mock.patch.object(api, "can_draft", return_value=False), self.assertRaises(api.ApiError) as cm:
            api.field_draft(ok)
        self.assertEqual(cm.exception.status, 503)

        def boom():
            raise RuntimeError("x")
        with self.assertRaises(api.ApiError) as cm:
            api.field_draft(ok, boom)
        self.assertEqual(cm.exception.status, 502)

    def test_broken_image_is_400(self):
        with self.assertRaises(api.ApiError) as cm:
            api.field_draft({"part": "取付部", "photos": [b64(b"not an image")]},
                            lambda: field_draft.FieldDrafter(client=fake_client(answer("取付部"))))
        self.assertEqual(cm.exception.status, 400)

    def test_over_http(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        app = App(Path(td.name) / "l.sqlite", Path(td.name) / "d",
                  field_drafter_factory=lambda: field_draft.FieldDrafter(client=fake_client(answer("表示部"))))
        srv = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        r = urllib.request.Request(f"http://127.0.0.1:{srv.server_address[1]}/api/field/draft", method="POST",
                                   data=json.dumps({"part": "表示部", "photos": [b64(png_bytes())]}).encode(),
                                   headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(r) as resp:
            out = json.loads(resp.read())
        self.assertEqual(len(out["items"]), 4)
        self.assertIn("cost_usd", out["usage"])


class FieldLocateTest(unittest.TestCase):
    def geocoder(self, res, td, calls=None):
        def fetch(lat, lng):
            if calls is not None:
                calls.append((lat, lng))
            if isinstance(res, Exception):
                raise res
            return res
        return Geocoder(Path(td) / "g.json", fetch=fetch, min_interval=0)

    def test_address_and_municipality(self):
        with tempfile.TemporaryDirectory() as td:
            calls = []
            g = self.geocoder({"results": {"muniCd": "11222", "lv01Nm": "越ヶ谷"}}, td, calls)
            r = api.field_locate("35.891", "139.7908", g)
            self.assertEqual((r["address"], r["municipality"], r["prefecture"], r["muni_cd"], r["in_scope"]),
                             ("埼玉県越谷市越ヶ谷", "越谷市", "埼玉県", "11222", True))
            api.field_locate("35.891", "139.7908", g)
            self.assertEqual(len(calls), 1)   # 2回目はキャッシュ(町名もキャッシュから)
            self.assertEqual(api.field_locate("35.891", "139.7908", g)["town"], "越ヶ谷")

    def test_gov_lookup_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            g = self.geocoder({"results": {"muniCd": "13104", "lv01Nm": "西新宿"}}, td)
            got, place = g.lookup(35.69, 139.69)
            self.assertEqual((got, place.city), (True, "新宿区"))

    def test_errors(self):
        with tempfile.TemporaryDirectory() as td:
            g = self.geocoder({"results": {"muniCd": "11222", "lv01Nm": "越ヶ谷"}}, td)
            for lat, lng in ((None, "1"), ("a", "1"), ("91", "1"), ("35", "181")):
                with self.assertRaises(api.ApiError) as cm:
                    api.field_locate(lat, lng, g)
                self.assertEqual(cm.exception.status, 400)
            with self.assertRaises(api.ApiError) as cm:
                api.field_locate("30.0", "135.0", self.geocoder({}, td))
            self.assertEqual(cm.exception.status, 404)   # 海上
            with self.assertRaises(api.ApiError) as cm:
                api.field_locate("35.5", "139.5", self.geocoder(OSError("down"), td))
            self.assertEqual(cm.exception.status, 502)   # 通信エラー(キャッシュしない)

    def test_over_http(self):
        td = tempfile.TemporaryDirectory()
        self.addCleanup(td.cleanup)
        app = App(Path(td.name) / "l.sqlite", Path(td.name) / "d",
                  geocoder=self.geocoder({"results": {"muniCd": "13104", "lv01Nm": "西新宿"}}, td.name))
        srv = make_server(app, "127.0.0.1", 0)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        with urllib.request.urlopen(f"http://127.0.0.1:{srv.server_address[1]}/api/field/locate?lat=35.69&lng=139.69") as r:
            out = json.loads(r.read())
        self.assertEqual((out["address"], out["municipality"]), ("東京都新宿区西新宿", "新宿区"))


class KPageTest(Server, unittest.TestCase):
    """K 学んだこと(k.html): ページが返る、上の枠の「学んだこと」が k.html を指す、画面が使う規則の API が通る。"""

    def setUp(self):
        self.setUp_server()

    def tearDown(self):
        self.tearDown_server()

    def test_page_and_assets_200(self):
        st, body = self.req("/v3/k.html", raw=True)
        self.assertEqual(st, 200)
        html = body.decode("utf-8")
        self.assertIn("学んだこと", html)
        self.assertIn('data-page="k"', html)
        self.assertIn('src="k.js"', html)
        for path in ("/v3/k.js", "/v3/k.css"):
            self.assertEqual(self.req(path, raw=True)[0], 200, path)
        js = self.req("/v3/k.js", raw=True)[1].decode("utf-8")
        self.assertIn("/api/rules", js)
        self.assertIn("まだ規則はありません", js)   # 見本(規則 0 件)の空状態

    def test_nav_points_to_k(self):
        shell = self.req("/v3/shell.js", raw=True)[1].decode("utf-8")
        self.assertIn('<a href="k.html"${page === "k" ? \' aria-current="page"\' : ""}>学んだこと</a>', shell)
        self.assertNotIn("/index.html#/rules", shell)

    def test_rules_api_used_by_page(self):
        with Ledger(self.db) as lg:
            a = lg.add_rule("支持部-1 ボルト: 「赤錆」は要改善(下書きは経過観察)", "s-1 / 有資格者")
            b = lg.add_rule("表示部-4 表示面: 「退色」は経過観察(下書きは良好)", "s-2 / 有資格者")
        st, rules = self.req("/api/rules")
        self.assertEqual((st, [r["status"] for r in rules]), (200, ["candidate", "candidate"]))
        self.assertEqual(self.req(f"/api/rules/{a}/approve", {"by": "有資格者"})[1]["status"], "approved")
        self.assertEqual(self.req(f"/api/rules/{b}/reject", {"by": "有資格者"})[1]["status"], "rejected")
        self.assertEqual(self.req(f"/api/rules/{a}/approve", {})[0], 400)   # 名前なしは記録できない

    def test_rejected_rule_with_approved_partner_is_kept_not_reapproved(self):
        # 却下した規則の相手が承認済みなら、承認し直しは 409。画面は「こちらを残す」(resolve)を出し、それは通る
        with Ledger(self.db) as lg:
            a = lg.add_rule("支持部-1: 「表面さび」は経過観察(下書きは要改善)", "s-1 / 有資格者")
            b = lg.add_rule("支持部-1: 「表面さび」は要改善(下書きは判断できず)", "s-2 / 有資格者", conflicts_with=[a])
        self.assertEqual(self.req("/api/rules/resolve", {"keep": b, "drop": a, "by": "有資格者"})[0], 200)
        self.assertEqual(self.req(f"/api/rules/{a}/approve", {"by": "有資格者"})[0], 409)
        st, pair = self.req("/api/rules/resolve", {"keep": a, "drop": b, "by": "有資格者"})
        self.assertEqual((st, [r["status"] for r in pair]), (200, ["approved", "rejected"]))
        js = self.req("/v3/k.js", raw=True)[1].decode("utf-8")
        self.assertIn("shownConflicts(r)) await api(\"/api/rules/resolve\"", js)


if __name__ == "__main__":
    unittest.main()
