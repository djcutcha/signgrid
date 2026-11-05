"""ガイド付き点検: 状態機械、台帳への書き込み、draft の only_missing、simulate の一周、webhook の分岐。API・LINE には繋がない。"""
import json
import sqlite3
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from kanban_karte.eval.judge import Judge, Prediction
from kanban_karte.intake.line import richmenu
from kanban_karte.intake.line import shoot as S
from kanban_karte.intake.line.guide import ANSWER_LABEL, PARTS, Guide, GuideState
from kanban_karte.intake.line.guide_ledger import LedgerSink, draft_by
from kanban_karte.intake.line.simulate import DirContentClient, build_guide_deliveries
from kanban_karte.intake.line.webhook import LineApp
from kanban_karte.ledger import Ledger, Site
from kanban_karte.ledger.items import ITEM_KEYS
from kanban_karte.pipeline import draft
from kanban_karte.web import api as web_api
from tests.test_line import SECRET, T0, USER, Base, body_of, img_event, jpeg, sign

REPO = Path(__file__).resolve().parents[1]
PLAN = REPO / "data" / "sample" / "guide_plan.json"
KEY = {k: i for i, k in enumerate(ITEM_KEYS)}


def pb(data, user=USER, token="tok-pb"):
    return {"type": "postback", "replyToken": token, "source": {"type": "user", "userId": user},
            "postback": {"data": data}}


def txt(mid, text, user=USER):
    return {"type": "message", "replyToken": f"tok-{mid}", "source": {"type": "user", "userId": user},
            "message": {"type": "text", "id": mid, "text": text}}


class GuideBase(Base):
    """LineApp を通して(状態機械・台帳・webhook を一度に)動かす。"""

    def setUp(self):
        super().setUp()
        self.n = 0
        self.app_ = self.app()

    def send(self, *events):
        code, res = self.post(self.app_, *events)
        self.assertEqual(code, 200)
        self.assertEqual(res.errors, [])
        return res

    def last(self):
        return self.client.replies[-1][1]

    def last_quick(self):
        """最後の返信のクイックリプライ(どの問いにも付く「戻る」を除く。戻るは last_quick_raw)。"""
        return [q for q in self.last_quick_raw() if q["action"]["label"] != "戻る"]

    def last_quick_raw(self):
        return self.client.extras[-1]["quick_replies"]

    skip_measure = True  # 既存の流れの試験は、正面は「撮れない」、種類は「その他」、実測の最初の問いは「飛ばす」で通す(実測は MeasureTest が試す)

    def photo(self):
        self.n += 1
        self.client.contents[f"p{self.n}"] = jpeg()
        res = self.send(img_event(f"p{self.n}", token=f"tok-p{self.n}"))
        st = self.app_.guide.load(USER)
        if self.skip_measure and st is not None and st.step == "front" and st.photos == 0:
            self.tap(front="skip")  # 正面と種類は ShootTest が試す。既存の流れの試験は通す
            res = self.tap(kind="other")
            st = self.app_.guide.load(USER)
        if self.skip_measure and st is not None and st.step == "measure" and st.m_sub == "ask":
            res = self.tap(m="skip")
            st = self.app_.guide.load(USER)
        if self.skip_measure and st is not None and st.step == "lighting":
            res = self.tap(light="outer")  # 照明の問いも通す(外照式は撮る場所が従来のまま)
        return res

    def tap(self, **q):
        from urllib.parse import urlencode
        return self.send(pb(urlencode({"act": "guide", **q})))

    def say(self, text):
        self.n += 1
        return self.send(txt(f"t{self.n}", text))

    def start_with_site(self):
        """位置つきの写真で現場を決めてから始める(現場の選択を飛ばす)。"""
        with Ledger(self.db) as lg:
            lg.upsert_site(Site("job-J-001", "サンプル駅前店"))
        self.client.contents["g0"] = jpeg(gps=(35.6, 139.7))
        self.send(img_event("g0"))
        self.tap(start=1)

    def ledger(self):
        return Ledger(self.db)


class StateMachineTest(GuideBase):
    def test_order_and_texts(self):
        self.start_with_site()
        self.assertEqual(self.last(), "看板の名前か番号を送ってください(例: ① 正面の袖看板)")
        self.say("① 正面の袖看板")
        self.assertEqual(self.last(), "全景を1枚撮ってください")
        self.photo()
        self.assertEqual(self.last(), "基礎部・上部構造を撮ってください(近くで1〜3枚)\n"
                                      "地面との境目(基礎)を近くで。傾きは全景で見ます")  # 種類「その他」の撮る場所
        self.assertEqual([i["action"]["label"] for i in self.last_quick()], ["この部位は無い"])
        self.photo()
        self.assertEqual([i["action"]["label"] for i in self.last_quick()], ["次へ"])
        self.tap(next=1, p=0)
        self.assertEqual(self.last(), "基礎部・上部構造 1/3\n上部構造全体の傾斜、ぐらつき(傾きは全景で、ぐらつきは手の届く所で)")
        self.assertEqual([i["action"]["label"] for i in self.last_quick()], list(ANSWER_LABEL.values()))
        self.assertEqual(self.last_quick()[0]["action"]["data"], "act=guide&ans=a&item=%E5%9F%BA%E7%A4%8E%E9%83%A8%E3%83%BB%E4%B8%8A%E9%83%A8%E6%A7%8B%E9%80%A0-1")

    def test_part_order_matches_items(self):
        self.assertEqual([p for p, _ in PARTS], ["基礎部・上部構造", "支持部", "取付部", "表示部", "照明装置", "その他"])
        self.assertEqual([k for _, its in PARTS for k, _ in its], ITEM_KEYS)

    def test_three_photos_advance_without_next(self):
        self.start_with_site()
        self.say("A")
        self.photo()
        for _ in range(3):
            self.photo()
        self.assertTrue(self.last().startswith("基礎部・上部構造 3枚\n基礎部・上部構造 1/3\n"))

    def test_no_part_marks_items_na_and_moves_on(self):
        self.start_with_site()
        self.say("A")
        self.photo()
        self.tap(nopart=1, p=0)
        self.assertTrue(self.last().startswith("支持部を撮ってください"))
        with self.ledger() as lg:
            s = lg.list_signs()[0]
            self.assertEqual({k: v["guide"] for k, v in s.evidence.items()},
                             {"基礎部・上部構造-1": "na", "基礎部・上部構造-2": "na", "基礎部・上部構造-3": "na"})
            self.assertEqual(s.items, {})

    def test_stale_or_wrong_button_does_not_advance(self):
        self.start_with_site()
        self.say("A")
        self.photo()
        self.photo()
        self.tap(next=1, p=0)
        before = self.last()
        self.tap(ans="a", item="支持部-1")           # 今の問いと違う項目
        self.assertEqual(self.last(), before)
        self.tap(ans="zzz", item="基礎部・上部構造-1")  # 知らない答え
        self.assertEqual(self.last(), before)
        self.tap(next=1, p=0)                        # 古い「次へ」
        self.assertEqual(self.last(), before)

    def test_back_skip_quit(self):
        self.start_with_site()
        self.say("A")
        self.say("飛ばす")
        self.assertTrue(self.last().startswith("ここは飛ばせません"))
        self.photo()
        self.photo()
        self.say("飛ばす")  # 写真の段階の飛ばす = 項目へ
        self.assertTrue(self.last().startswith("基礎部・上部構造 1/3"))
        self.say("飛ばす")  # 項目の飛ばす = 分からない
        self.assertTrue(self.last().startswith("基礎部・上部構造 2/3"))
        with self.ledger() as lg:
            self.assertEqual(lg.list_signs()[0].evidence["基礎部・上部構造-1"]["guide"], "unknown")
        self.say("戻る")
        self.assertTrue(self.last().startswith("基礎部・上部構造 1/3"))
        self.say("戻る")
        self.assertTrue(self.last().startswith("基礎部・上部構造 1枚"))  # 写真の段階に戻る
        self.say("やめる")
        self.assertTrue(self.last().startswith("ガイドをやめました"))
        self.assertFalse(self.app_.guide.active(USER))
        with self.ledger() as lg:
            self.assertEqual(len(lg.list_signs()), 1)  # ここまでの分は残る

    def test_free_text_is_memo(self):
        self.start_with_site()
        self.say("A")
        self.photo()
        self.photo()
        self.tap(next=1, p=0)
        self.say("柱の根元に水たまり")
        self.assertEqual(self.last(), "メモを受け取りました\n基礎部・上部構造 1/3\n上部構造全体の傾斜、ぐらつき(傾きは全景で、ぐらつきは手の届く所で)")
        msgs = list(self.inbox.glob("*/*/messages.jsonl"))
        self.assertIn("柱の根元に水たまり", msgs[0].read_text(encoding="utf-8"))
        self.assertNotIn("戻る", msgs[0].read_text(encoding="utf-8"))

    def test_resume_after_restart(self):
        self.start_with_site()
        self.say("A")
        self.photo()
        self.photo()
        self.tap(next=1, p=0)
        self.tap(ans="b", item="基礎部・上部構造-1")
        fresh = Guide(self.inbox)  # サーバを立て直した状態
        st = fresh.load(USER)
        self.assertEqual((st.step, st.part, st.item), ("ask", 0, 1))
        self.assertEqual(fresh.prompt(st).text.splitlines()[0], "基礎部・上部構造 2/3")
        self.app_ = self.app()
        self.tap(ans="c", item="基礎部・上部構造-2")
        self.assertTrue(self.last().startswith("基礎部・上部構造 3/3"))

    def test_resend_does_not_double_step(self):
        self.start_with_site()
        self.say("A")
        self.photo()
        self.photo()
        self.tap(next=1, p=0)
        ev = txt("same", "戻る")
        self.send(ev)
        n = len(self.client.replies)
        self.send(ev)  # 同じメッセージIDの再送
        self.assertEqual(len(self.client.replies), n)

    def test_site_choice_then_done_and_next_sign(self):
        self.tap(start=1)
        self.assertEqual(self.last(), "今日の現場はどれですか")
        labels = [i["action"]["label"] for i in self.last_quick()]
        self.assertEqual(labels[-1], "その他(現在地を送る)")
        self.assertIn("act=guide&site=", self.last_quick()[0]["action"]["data"])
        self.tap(site="J-001")
        self.assertTrue(self.last().startswith("サンプル駅前店で受け付けました\n看板の名前"))
        with self.ledger() as lg:
            self.assertIsNotNone(lg.get_site("job-J-001"))
        # 最後まで: 全部位に写真と答え
        self.say("A")
        self.photo()
        for pi, (_, its) in enumerate(PARTS):
            self.photo()
            self.tap(next=1, p=pi)
            for k, _ in its:
                self.tap(ans="a", item=k)
        self.assertEqual(self.last(), "この看板は終わりです。次の看板を始めますか")
        self.assertEqual([i["action"]["label"] for i in self.last_quick()], ["次の看板", "終了"])
        self.tap(more=1)
        self.assertTrue(self.last().startswith("看板の名前"))
        self.say("B")
        with self.ledger() as lg:
            self.assertEqual(len(lg.list_signs()), 2)
        self.tap(end=1)
        self.assertTrue(self.last().startswith("点検のまとめ(サンプル駅前店)"))
        self.assertTrue(self.last().endswith("お疲れさまでした"))
        self.assertFalse(self.app_.guide.active(USER))

    def test_location_during_site_step(self):
        self.tap(start=1)
        self.send({"type": "message", "replyToken": "tok-loc", "source": {"type": "user", "userId": USER},
                   "message": {"type": "location", "id": "loc1", "latitude": 35.6, "longitude": 139.7}})
        self.assertTrue(self.last().startswith("位置を受け取りました\n看板の名前"))

    def test_start_by_text(self):
        self.say("点検開始")
        self.assertEqual(self.last(), "今日の現場はどれですか")
        self.assertTrue(self.app_.guide.active(USER))
        self.say("点検開始")  # 進行中に押し直すと今の問いに戻る
        self.assertEqual(self.last(), "今日の現場はどれですか")

    def test_reply_is_at_most_three_lines(self):
        self.start_with_site()
        self.say("A")
        self.photo()
        for _ in range(3):
            self.photo()
        for _, r in self.client.replies:
            self.assertLessEqual(len(r.splitlines()), 3, r)


class NotGuidedTest(GuideBase):
    def test_unguided_user_keeps_bundling(self):
        self.client.contents["m1"] = jpeg()
        self.send(img_event("m1"))
        self.assertIn("受け取りました。写真 1 枚", self.client.replies[0][1])
        self.say("メモです")
        self.assertIn("受け取りました", self.last())
        self.assertFalse(self.app_.guide.active(USER))

    def test_guided_user_images_join_same_bundle(self):
        self.client.contents["m0"] = jpeg(gps=(35.6, 139.7))
        self.send(img_event("m0"))
        self.tap(start=1)
        self.say("A")
        self.photo()
        self.assertEqual(len(self.bundles()), 1)
        self.assertEqual(self.bundles()[0]["pages"], 2)

    def test_other_user_unaffected(self):
        self.start_with_site()
        other = "U" + "b" * 32
        self.client.contents["o1"] = jpeg()
        self.send(img_event("o1", user=other))
        self.assertIn("受け取りました。写真 1 枚", self.client.replies[-1][1])


class LedgerTest(GuideBase):
    def run_part(self):
        self.start_with_site()
        self.say("① 袖看板")
        self.photo()   # 全景
        self.photo()
        self.photo()
        self.tap(next=1, p=0)
        self.tap(ans="b", item="基礎部・上部構造-1")
        self.tap(ans="unknown", item="基礎部・上部構造-2")
        self.tap(ans="na", item="基礎部・上部構造-3")

    def test_sign_pages_items_corrections(self):
        self.run_part()
        with self.ledger() as lg:
            s = lg.list_signs()[0]
            self.assertEqual((s.name, s.status, s.rank), ("① 袖看板", "draft", None))
            self.assertEqual(s.draft_by, "line_guide:" + USER[-4:])
            self.assertEqual(s.items, {"基礎部・上部構造-1": "経過観察"})
            self.assertEqual(len(s.photos), 3)
            tags = [r["tag"] for r in lg.list_pages(s.source_bundle)]
            self.assertEqual(tags, [None, "overview", "基礎部・上部構造", "基礎部・上部構造"])  # 先頭は始める前に送った位置つきの写真
            self.assertEqual(s.evidence["基礎部・上部構造-1"]["photos"], [1, 2, 3])  # 全景(傾きを見る)+部位の写真
            cs = lg.list_corrections(s.id)
            self.assertEqual([(c["item"], c["old_value"], c["new_value"], c["corrected_by"]) for c in cs],
                             [("基礎部・上部構造-1", None, "経過観察", "line_guide")])

    def test_changing_an_answer_logs_old_value(self):
        self.run_part()
        for _ in range(3):  # 支持部の写真 → 3/3 → 2/3 → 1/3
            self.say("戻る")
        self.tap(ans="c", item="基礎部・上部構造-1")
        with self.ledger() as lg:
            s = lg.list_signs()[0]
            self.assertEqual(s.items["基礎部・上部構造-1"], "要改善")
            self.assertEqual([(c["old_value"], c["new_value"]) for c in lg.list_corrections(s.id)],
                             [(None, "経過観察"), ("経過観察", "要改善")])

    def test_site_bound_to_bundle_and_sign(self):
        self.tap(start=1)
        self.tap(site="J-001")
        self.say("A")
        self.photo()
        with self.ledger() as lg:
            s = lg.list_signs()[0]
            self.assertEqual(s.site_id, "job-J-001")
            self.assertEqual(lg.get_bundle(s.source_bundle)["site_id"], "job-J-001")

    def test_old_ledger_gets_columns(self):
        db = self.root / "old.sqlite"
        c = sqlite3.connect(db)
        c.executescript("CREATE TABLE sites(id TEXT PRIMARY KEY, name TEXT NOT NULL, address TEXT, lat REAL, lng REAL, "
                        "municipality TEXT, created TEXT NOT NULL DEFAULT (datetime('now')));"
                        "CREATE TABLE bundles(id INTEGER PRIMARY KEY AUTOINCREMENT, file TEXT NOT NULL, sha256 TEXT NOT NULL UNIQUE, "
                        "pages INTEGER NOT NULL DEFAULT 0, kind_counts TEXT NOT NULL DEFAULT '{}', status TEXT NOT NULL DEFAULT 'new', "
                        "error TEXT, created TEXT NOT NULL DEFAULT (datetime('now')));"
                        "CREATE TABLE pages(id INTEGER PRIMARY KEY AUTOINCREMENT, bundle_id INTEGER NOT NULL, page_no INTEGER NOT NULL, "
                        "kind TEXT NOT NULL DEFAULT '不明', kind_basis TEXT, image_path TEXT, image_source TEXT, "
                        "src_rotate INTEGER NOT NULL DEFAULT 0, rotation INTEGER NOT NULL DEFAULT 0, width INTEGER, height INTEGER, dpi REAL, "
                        "UNIQUE(bundle_id, page_no));"
                        "CREATE TABLE signs(id TEXT PRIMARY KEY, site_id TEXT, name TEXT, type TEXT, size_w INTEGER, size_h INTEGER, "
                        "address TEXT, lat REAL, lng REAL, municipality TEXT, is_self_use INTEGER, permit_required INTEGER, "
                        "permit_expires TEXT, next_inspection TEXT, rank INTEGER, items TEXT NOT NULL DEFAULT '{}', remark TEXT, "
                        "photos TEXT NOT NULL DEFAULT '[]', last_inspected TEXT, prev_rank INTEGER, source_bundle INTEGER, "
                        "created TEXT NOT NULL DEFAULT (datetime('now')), updated TEXT NOT NULL DEFAULT (datetime('now')));")
        c.commit()
        c.close()
        with Ledger(db) as lg:
            cols = {r[1] for r in lg.conn.execute("PRAGMA table_info(pages)")}
            self.assertIn("tag", cols)
            self.assertIn("draft_by", {r[1] for r in lg.conn.execute("PRAGMA table_info(signs)")})


class _Judge(Judge):
    """答えのある項目をすべて「要改善(3)」と返す偽の判定役。尋ねられた項目名を覚える。"""
    name = "fake"

    def __init__(self):
        self.asked = []

    def judge(self, item):
        names = item.meta["form_items"]
        self.asked.append(names)
        return Prediction(2, {n: 3 for n in names}, 0.6,
                          raw={"reasons": ["写真から下書き"], "item_notes": {n: {"evidence": "写真1", "needs_onsite": False} for n in names}},
                          usage={"cost_usd": 0.0})


class OnlyMissingTest(GuideBase):
    def drafted(self, only_missing):
        self.start_with_site()
        self.say("A")
        self.photo()
        self.photo()
        self.tap(next=1, p=0)
        self.tap(ans="a", item="基礎部・上部構造-1")
        self.tap(ans="unknown", item="基礎部・上部構造-2")
        self.tap(ans="na", item="基礎部・上部構造-3")
        j = _Judge()
        with self.ledger() as lg:
            sign = lg.list_signs()[0]
            res = draft.draft_bundle(lg, sign.source_bundle, j, only_missing=only_missing)
            return j, res, lg.get_sign(sign.id)

    def test_only_missing_keeps_answers_and_skips_na(self):
        j, res, s = self.drafted(True)
        self.assertEqual(len(j.asked[0]), 16)  # 18 − 答えのある1 − 対象外1(分からないは尋ねる)
        self.assertEqual(s.items["基礎部・上部構造-1"], "良好")           # 現場の答えは残る
        self.assertEqual(s.items["基礎部・上部構造-2"], "要改善")         # 分からない → 写真から
        self.assertNotIn("基礎部・上部構造-3", s.items)                    # 対象外は触らない
        self.assertEqual(s.evidence["基礎部・上部構造-1"]["guide"], "a")
        self.assertEqual(s.evidence["基礎部・上部構造-3"]["guide"], "na")
        self.assertEqual(s.rank, 3)  # 判定役の2と、items から出した3の重いほう
        self.assertEqual(s.status, "draft")
        self.assertEqual(res[0].undecided, 1)

    def test_web_draft_uses_only_missing_for_guide_signs(self):
        # Web の「下書きを作る」(api.draft_bundle)は、ガイドの看板がある束では現場の答えを上書きしない
        from kanban_karte.web import api
        self.start_with_site()
        self.say("A")
        self.photo()   # 全景
        self.photo()   # 部位の写真
        self.tap(next=1, p=0)
        self.tap(ans="a", item="基礎部・上部構造-1")
        judges = []

        def factory(rules):
            judges.append(_Judge())
            return judges[-1]
        with self.ledger() as lg:
            sign = lg.list_signs()[0]
            out = api.draft_bundle(lg, sign.source_bundle, factory)
            self.assertTrue(out["only_missing"])
            self.assertNotIn("④基礎部・上部構造-1 上部構造全体の傾斜、ぐらつき", judges[0].asked[0])
            self.assertEqual(lg.get_sign(sign.id).items["基礎部・上部構造-1"], "良好")

    def test_default_is_unchanged_full_draft(self):
        j, res, s = self.drafted(False)
        self.assertEqual(len(j.asked[0]), 18)
        self.assertEqual(s.items["基礎部・上部構造-1"], "要改善")  # 従来どおり上書き

    def test_all_answered_does_not_call_judge(self):
        with self.ledger() as lg:
            lg.upsert_site(Site("s", "x"))
            bid = lg.add_bundle("line://u/x/1", "sha-x", 1)
            from kanban_karte.ledger import Sign
            lg.upsert_sign(Sign(id="g1", status="draft", source_bundle=bid, photos=["p.jpg"],
                                items={k: "良好" for k in ITEM_KEYS}))
            j = _Judge()
            res = draft.draft_bundle(lg, bid, j, only_missing=True)
            self.assertEqual((j.asked, res[0].status, res[0].undecided), ([], "draft", 0))


class SimulateTest(unittest.TestCase):
    def test_round_trip(self):
        with tempfile.TemporaryDirectory() as td:
            td = Path(td)
            plan = build_guide_deliveries(PLAN, td / "content", run_id="t1")
            client = DirContentClient(td / "content")
            app = LineApp(SECRET, client, lambda: Ledger(td / "l.sqlite"), td / "inbox", now=lambda: T0)
            for body in plan.deliveries:
                code, res = app.handle(body, sign(SECRET, body))
                self.assertEqual((code, res.errors), (200, []))
            with Ledger(td / "l.sqlite") as lg:
                signs = lg.list_signs()
                s = signs[0]
                self.assertEqual(s.name, "① 正面の袖看板")
                self.assertEqual(len(s.photos), 8)  # 全景1+正面1+部位6(基礎部は「この部位は無い」)
                self.assertEqual((s.size_w, s.size_h, s.size_d, s.gl_mm, s.height_m, s.size_source, s.mount, s.type),
                                 (3600, 900, 300, 2500, 3.4, "measured", "アンカーボルト", "突出"))
                self.assertEqual([x.name for x in signs], ["① 正面の袖看板", "② 側面の看板", "① 正面の袖看板"])
                self.assertIsNone(signs[1].size_w)  # ② は図面で送った(数字は事務所で入れる)
                self.assertEqual((signs[1].type, len(lg.split_photos(signs[1])[1])), ("壁面", 1))
                self.assertEqual((signs[2].size_w, signs[2].size_source), (3600, "measured"))  # ③ は①の実測を引き継いだ
                self.assertEqual(len(s.items), 8)
                self.assertEqual(sum(1 for v in s.evidence.values() if v["guide"] == "unknown"), 2)
                self.assertEqual(sum(1 for v in s.evidence.values() if v["guide"] == "na"), 8)  # 基礎部3・照明装置3(この部位は無い)、表示部-4、その他-3
            self.assertTrue(client.replies[-1][1].startswith("点検のまとめ"))
            self.assertTrue(client.replies[-1][1].endswith("お疲れさまでした"))
            self.assertFalse(app.guide.active("Usimulated0000000000000000000000"))

    def test_operation_counts(self):
        with tempfile.TemporaryDirectory() as td:
            plan = build_guide_deliveries(PLAN, Path(td) / "c", run_id="t2")
            self.assertEqual(plan.stats["photos"], 12)  # ①全景+正面+部位6、②全景+図面、③全景+正面
            self.assertEqual(plan.stats["texts"], 8)    # 看板の名前3+①の幅・高さ・厚み・GL・支柱


class KarteBadgeTest(GuideBase):
    def test_guide_badge_and_expert_correction(self):
        self.start_with_site()
        self.say("A")
        self.photo()
        self.photo()
        self.tap(next=1, p=0)
        self.tap(ans="a", item="基礎部・上部構造-1")
        self.tap(ans="unknown", item="基礎部・上部構造-2")
        self.tap(ans="na", item="基礎部・上部構造-3")
        with self.ledger() as lg:
            sid = lg.list_signs()[0].id
            d = web_api.sign_detail(lg, sid)
            by = {i["key"]: i for i in d["items"]}
            self.assertEqual(by["基礎部・上部構造-1"]["guide"], "a")
            self.assertFalse(by["基礎部・上部構造-1"]["corrected"])  # 現場の回答は「訂正済み」ではない
            self.assertEqual(by["基礎部・上部構造-2"]["guide"], "unknown")
            self.assertTrue(by["基礎部・上部構造-2"]["yellow"])
            self.assertFalse(by["基礎部・上部構造-3"]["yellow"])      # 対象外は黄色にしない
            self.assertEqual(d["draft_by"], "line_guide:" + USER[-4:])
            from kanban_karte.pipeline import review
            review.apply_correction(lg, sid, "基礎部・上部構造-1", "要改善", "有資格者A")
            by = {i["key"]: i for i in web_api.sign_detail(lg, sid)["items"]}
            self.assertTrue(by["基礎部・上部構造-1"]["corrected"])
            self.assertIsNone(by["基礎部・上部構造-1"]["guide"])



class ShootTest(GuideBase):
    """全景 → 正面(各面)→ 看板の種類 → 寸法 → 部位(種類ごとの撮る場所)。"""
    skip_measure = False

    def to_front(self, name="① 袖看板"):
        self.start_with_site()
        self.say(name)
        self.photo()  # 全景

    def test_front_then_kind_then_measure(self):
        self.to_front()
        self.assertEqual(self.last(), "全景を受け取りました\n表示面を正面から撮ってください(文字や絵が読めるように。面が複数あれば各面)")
        self.assertEqual([q["action"]["label"] for q in self.last_quick()], ["正面から撮れない"])
        self.photo()
        self.assertEqual(self.last(), "正面 1枚\n別の面があれば続けて撮るか、「次へ」を押してください")
        self.photo()  # 2面目
        self.tap(front="next")
        self.assertEqual(self.last(), "看板の種類を選んでください")
        self.assertEqual([q["action"]["label"] for q in self.last_quick()], [label for _, label, _ in S.KINDS])
        self.tap(kind="pole")
        self.assertIn("寸法を測りますか", self.last())
        self.tap(m="skip")
        self.assertEqual(self.last(), "寸法の実測は飛ばしました\n照明はありますか")
        self.tap(light="outer")
        self.assertEqual(self.last(), "基礎部・上部構造を撮ってください(近くで1〜3枚)\n" + S.SHOOT["基礎部・上部構造"]["pole"])
        with self.ledger() as lg:
            s = lg.list_signs()[0]
            tags = {lg.rel_path(r["image_path"]): r["tag"] for r in lg.conn.execute("SELECT image_path, tag FROM pages")}
            self.assertEqual([tags[p] for p in s.photos], ["overview", S.FRONT_TAG, S.FRONT_TAG])
            self.assertEqual(s.type, "ポール")

    def test_front_photos_count_for_the_display_items(self):
        self.to_front()
        self.photo()  # 正面
        self.tap(front="next")
        self.tap(kind="wall")
        self.tap(m="skip")
        self.tap(light="outer")
        for p in range(3):
            self.tap(nopart=1, p=p)
        self.assertEqual(self.last(), "表示部を撮ってください(近くで1〜3枚)\n表示面の傷み・側板・底と水抜き穴を近くで。正面の写真は撮ってあります")
        self.photo()
        self.tap(next=1, p=3)
        self.tap(ans="b", item="表示部-1")
        with self.ledger() as lg:
            self.assertEqual(lg.list_signs()[0].evidence["表示部-1"]["photos"], [2, 3])  # 正面=2、表示部の写真=3

    def test_front_skipped_drops_the_front_note(self):
        self.to_front()
        self.tap(front="skip")
        self.tap(kind="wall")
        self.tap(m="skip")
        self.tap(light="outer")
        for p in range(3):
            self.tap(nopart=1, p=p)
        self.assertEqual(self.last(), "表示部を撮ってください(近くで1〜3枚)\n表示面の傷み・側板・底と水抜き穴を近くで")

    def test_four_fronts_advance_and_stale_buttons(self):
        self.to_front()
        self.tap(front="next")  # まだ1枚も無い: 進まない
        self.assertIn("表示面を正面から", self.last())
        for _ in range(S.MAX_FRONT_PHOTOS):
            self.photo()
        self.assertEqual(self.last(), f"正面 {S.MAX_FRONT_PHOTOS}枚\n看板の種類を選んでください")
        self.tap(front="skip")  # 古いボタン
        self.assertEqual(self.last(), "看板の種類を選んでください")
        self.tap(kind="nope")
        self.assertEqual(self.last(), "看板の種類を選んでください")

    def test_kind_by_text_skip_and_back(self):
        self.to_front()
        self.say("飛ばす")  # 正面は飛ばせる
        self.say("戻る")
        self.assertIn("表示面を正面から", self.last())
        self.tap(front="skip")
        self.say("突出(袖)")
        self.assertIn("寸法を測りますか", self.last())
        self.say("戻る")
        self.assertEqual(self.last(), "看板の種類を選んでください")
        self.say("飛ばす")  # 種類も飛ばせる(撮る場所は共通の言い方)
        self.tap(m="skip")
        self.tap(light="outer")
        self.assertTrue(self.last().endswith(S.SHOOT["基礎部・上部構造"]["*"]))
        with self.ledger() as lg:
            self.assertEqual(lg.list_signs()[0].type, "突出")  # 先に選んだ種類は台帳に残る(飛ばしても消さない)

    def test_kind_does_not_overwrite_the_ledger(self):
        self.to_front()
        with self.ledger() as lg:
            s = lg.list_signs()[0]
            s.type = "野立て"
            lg.upsert_sign(s)
        self.tap(front="skip")
        self.tap(kind="wall")
        with self.ledger() as lg:
            self.assertEqual(lg.list_signs()[0].type, "野立て")

    def test_every_hint_keeps_replies_within_three_lines(self):
        g = Guide(self.inbox)
        for kind in (*S.KIND_KEYS, None):
            for part in range(len(PARTS)):
                st = GuideState(step="photo", part=part, kind=kind)
                self.assertLessEqual(len(g._with("寸法の実測は飛ばしました", g.prompt(st)).text.splitlines()), 3)
        for k, label, _ in S.KINDS:
            self.assertLessEqual(len(label), 20)


if __name__ == "__main__":
    unittest.main()
