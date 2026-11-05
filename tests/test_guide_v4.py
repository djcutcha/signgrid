"""ガイド v4: 戻る(常設・写真の tag 外し)、終了のまとめ、照明、AI 判定の自動下書き、アクセスログの日時。API・LINE には繋がない。"""
import io
import os
import sqlite3
import threading
import unittest
from unittest import mock
from contextlib import redirect_stderr
from pathlib import Path
from types import SimpleNamespace

from kanban_karte.intake.line import guide as G
from kanban_karte.intake.line import measure as M
from kanban_karte.intake.line import shoot as S
from kanban_karte.intake.line.guide import PARTS, SignRow, Summary, split_text, summary_text
from kanban_karte.intake.line.webhook import LineApp
from kanban_karte.ledger import Ledger, Sign, Site
from tests.test_guide import GuideBase
from tests.test_line import SECRET, USER

REPO = Path(__file__).resolve().parents[1]
LIGHT_PART = S.LIGHTING_PART


def labels(quick):
    return [q["action"]["label"] for q in quick]


class V4Base(GuideBase):
    skip_measure = False
    drafted: list = []

    def setUp(self):
        self.drafted = []   # 自動下書きの呼び出し(実際の API は呼ばない)
        super().setUp()

    def app(self):
        return LineApp(SECRET, self.client, lambda: Ledger(self.db), self.inbox, now=lambda: self.clock[0],
                       auto_draft=self.drafted.append)

    def to_measure_skipped(self, name="A", kind="wall"):
        """現場 → 名前 → 全景 → 正面は撮れない → 種類 → 実測は飛ばす(この次が照明の問い)。"""
        self.start_with_site()
        self.say(name)
        self.photo()
        self.tap(front="skip")
        self.tap(kind=kind)
        self.tap(m="skip")

    def run_parts(self, lighting="outer", answer="a", unknown_at=None):
        """照明を答えて、全部位を 写真 1 枚 → 答え で通す(照明が「無し」の看板は照明装置が出ない)。"""
        self.tap(light=lighting)
        for pi, (part, its) in enumerate(PARTS):
            if part == LIGHT_PART and lighting == "none":
                continue
            self.photo()
            self.tap(next=1, p=pi)
            for k, _ in its:
                self.tap(ans="unknown" if k == unknown_at else answer, item=k)

    def sign(self, i=-1):
        with self.ledger() as lg:
            return lg.list_signs()[i]


class BackButtonTest(V4Base):
    def test_every_question_ends_with_back_and_fits_line(self):
        """どの問いの返事にも最後に「戻る」。クイックリプライは 13 個まで、ラベルは 20 字まで。"""
        g = G.Guide(self.root / "ib")
        steps = [dict(step="sign"), dict(step="overview"), dict(step="front"), dict(step="front", photos=1),
                 dict(step="kind"), dict(step="lighting"), dict(step="photo"), dict(step="photo", photos=2),
                 dict(step="ask"), dict(step="done"),
                 dict(step="measure", m_sub="ask"), dict(step="measure", m_sub="drawing"),
                 dict(step="measure", m_sub="drawing", m_photos=1), dict(step="measure", m_sub="w"),
                 dict(step="measure", m_sub="h"), dict(step="measure", m_sub="d"), dict(step="measure", m_sub="gl"),
                 dict(step="measure", m_sub="mount"), dict(step="measure", m_sub="mount", m_page=1),
                 dict(step="measure", m_sub="mount_text"), dict(step="measure", m_sub="light"),
                 dict(step="measure", m_sub="post"),
                 dict(step="measure", m_sub="w", m_pending={"f": "w", "mm": 3600, "text": "3.6m?"})]
        for spec in steps:
            st = G.GuideState(**spec)
            q = g.prompt(st).quick
            self.assertEqual(q[-1]["action"]["label"], "戻る", spec)
            self.assertLessEqual(len(q), 13, spec)
            self.assertTrue(all(len(x["action"]["label"]) <= 20 for x in q), spec)
        for part in range(len(PARTS)):
            q = g.prompt(G.GuideState(step="ask", part=part)).quick
            self.assertEqual((q[-1]["action"]["label"], len(q)), ("戻る", len(G.ANSWER_LABEL) + 1))

    def test_site_question_has_no_back(self):
        self.assertNotIn("戻る", labels(g_prompt(self, G.GuideState(step="site"))))

    def test_mount_is_two_pages_and_every_choice_is_reachable(self):
        self.to_measure_skipped()
        self.say("戻る")
        self.tap(m="go")
        for v in ("3600", "900mm", "300mm", "2500"):
            self.say(v)
        page1 = labels(self.last_quick_raw())
        self.assertEqual((len(page1), page1[-1]), (13, "戻る"))
        self.assertEqual(page1[-2], "次のページ(その他・分からない)")
        self.tap(m="mpage")
        self.assertEqual(labels(self.last_quick_raw()), list(M.MOUNTS[M.MOUNT_PAGE:]) + ["前のページ", "戻る"])
        self.tap(m="mprev")
        self.assertEqual(labels(self.last_quick_raw())[0], M.MOUNTS[0])
        self.assertEqual(sorted(page1[:-2] + list(M.MOUNTS[M.MOUNT_PAGE:])), sorted(M.MOUNTS))
        self.tap(m="mpage")
        self.say("戻る")  # 2 ページ目の「戻る」は 1 ページ目へ
        self.assertEqual(labels(self.last_quick_raw())[0], M.MOUNTS[0])

    def test_back_postback_goes_back_once_and_stale_one_does_not(self):
        self.start_with_site()
        self.say("A")
        old = self.last_quick_raw()[-1]["action"]["data"]   # 全景の問いの「戻る」
        self.assertIn("back=1", old)
        self.photo()
        self.assertEqual(self.app_.guide.load(USER).step, "front")
        self.send_data(old)  # 全景の問いの古いボタン: 戻らない
        self.assertEqual(self.app_.guide.load(USER).step, "front")
        self.assertIn("表示面を正面から", self.last())
        self.send_data(self.last_quick_raw()[-1]["action"]["data"])
        self.assertEqual(self.app_.guide.load(USER).step, "overview")

    def send_data(self, data):
        from tests.test_guide import pb
        return self.send(pb(data))

    def test_back_untags_the_last_photo_but_keeps_the_file(self):
        self.start_with_site()
        self.say("A")
        self.photo()                      # 全景
        self.photo()                      # 正面 1
        self.photo()                      # 正面 2
        self.tap(front="next")
        self.tap(kind="wall")
        self.tap(m="skip")
        self.tap(light="outer")
        self.photo()                      # 基礎部・上部構造 1
        self.photo()                      # 同 2
        with self.ledger() as lg:
            s = lg.list_signs()[0]
            tags = lambda: [r["tag"] for r in lg.conn.execute("SELECT tag FROM pages ORDER BY page_no")]
            n_photos = len(s.photos)
            self.assertEqual(tags()[-2:], [PARTS[0][0]] * 2)
        self.say("戻る")                  # 部位の写真 2 → 1 枚目だけ残る
        self.assertEqual(self.app_.guide.load(USER).photos, 1)
        with self.ledger() as lg:
            tags = [r["tag"] for r in lg.conn.execute("SELECT tag FROM pages ORDER BY page_no")]
            self.assertEqual((tags.count(PARTS[0][0]), tags[-1]), (1, None))
            s = lg.list_signs()[0]
            self.assertEqual(len(s.photos), n_photos)                       # signs.photos は減らさない
        self.say("戻る")
        self.assertEqual(self.app_.guide.load(USER).photos, 0)
        self.say("戻る")                  # 部位の写真が 0 枚: 前の問い(照明)へ
        self.assertEqual(self.app_.guide.load(USER).step, "lighting")
        self.say("戻る")                  # 照明 → 実測の最初の問い
        self.say("戻る")                  # → 種類
        self.say("戻る")                  # → 正面(2 枚に戻る)
        st = self.app_.guide.load(USER)
        self.assertEqual((st.step, st.photos), ("front", 2))
        self.say("戻る")
        self.say("戻る")
        self.say("戻る")                  # 正面 0 枚 → 全景に戻る時、全景の tag を外す
        self.assertEqual(self.app_.guide.load(USER).step, "overview")
        with self.ledger() as lg:
            self.assertEqual({r["tag"] for r in lg.conn.execute("SELECT tag FROM pages")}, {None})

    def test_back_from_the_first_answer_restores_the_photo_count(self):
        self.to_measure_skipped()
        self.tap(light="outer")
        self.photo()
        self.photo()
        self.tap(next=1, p=0)
        self.say("戻る")
        st = self.app_.guide.load(USER)
        self.assertEqual((st.step, st.photos), ("photo", 2))
        self.assertIn("基礎部・上部構造 2枚", self.last())


def g_prompt(test, st):
    return test.app_.guide.prompt(st).quick or []


class LightingTest(V4Base):
    def test_question_comes_after_mount_and_stores_the_value(self):
        self.start_with_site()
        self.say("A")
        self.photo()
        self.tap(front="skip")
        self.tap(kind="wall")
        self.tap(m="go")
        for v in ("3600", "900mm", "300mm", "2500"):
            self.say(v)
        self.tap(mount=0)
        self.assertEqual(self.last(), "照明はありますか")
        self.assertEqual(labels(self.last_quick()), ["内照式(中にLED・蛍光灯)", "外照式(スポット・投光器)", "無し", "分からない"])
        self.tap(light="inner")
        self.assertIn("支柱", self.last())
        self.assertEqual(self.sign().lighting, "内照式")

    def test_values_per_choice(self):
        for key, want in (("outer", "外照式"), ("none", "無し"), ("unknown", None)):
            with self.subTest(key):
                self.to_measure_skipped(name=key)
                self.tap(light=key)
                self.assertEqual(self.sign().lighting, want)
                self.tap(end=1)

    def test_inner_shoot_hint_and_outer_stays(self):
        self.to_measure_skipped(name="内")
        self.tap(light="inner")
        for pi in range(4):
            self.tap(nopart=1, p=pi)
        self.assertEqual(self.last(), "照明装置を撮ってください(近くで1〜3枚)\n"
                                      "点灯した状態の表示面(できれば)、面板の裏側や電源部が見える所、配線の引き込み")
        self.tap(end=1)
        self.to_measure_skipped(name="外")
        self.tap(light="outer")
        for pi in range(4):
            self.tap(nopart=1, p=pi)
        self.assertEqual(self.last(), "照明装置を撮ってください(近くで1〜3枚)\n" + S.SHOOT[LIGHT_PART]["*"])

    def test_none_marks_the_lighting_part_na_and_skips_it(self):
        self.to_measure_skipped()
        self.tap(light="none")
        for pi in range(3):
            self.tap(nopart=1, p=pi)
        self.photo()
        self.tap(next=1, p=3)
        for k, _ in PARTS[3][1]:
            self.tap(ans="a", item=k)
        st = self.app_.guide.load(USER)
        self.assertEqual((st.step, PARTS[st.part][0]), ("photo", "その他"))   # 照明装置は出ずに「その他」へ
        s = self.sign()
        for k in ("照明装置-1", "照明装置-2", "照明装置-3"):
            self.assertEqual(s.evidence[k]["guide"], "na")
            self.assertNotIn(k, s.items)
        self.say("戻る")                                                       # 戻る時も照明装置は飛び越える
        st = self.app_.guide.load(USER)
        self.assertEqual((st.step, PARTS[st.part][0], st.item), ("ask", "表示部", 3))

    def test_unknown_asks_the_lighting_part_as_before(self):
        self.to_measure_skipped()
        self.tap(light="unknown")
        for pi in range(4):
            self.tap(nopart=1, p=pi)
        self.assertTrue(self.last().startswith("照明装置を撮ってください"))

    def test_skip_command_counts_as_unknown_and_text_label_is_accepted(self):
        self.to_measure_skipped()
        self.say("飛ばす")
        self.assertEqual(self.app_.guide.load(USER).lighting, "unknown")
        self.assertTrue(self.last().startswith("基礎部・上部構造を撮ってください"))
        self.tap(end=1)
        self.to_measure_skipped(name="B")
        self.say("内照式(中にLED・蛍光灯)")
        self.assertEqual(self.sign().lighting, "内照式")

    def test_stale_light_button_does_not_move(self):
        self.to_measure_skipped()
        self.tap(light="outer")
        self.tap(light="none")                   # 照明の問いはもう過ぎている
        self.assertEqual(self.sign().lighting, "外照式")

    def test_old_ledger_gets_the_column(self):
        p = self.root / "old.sqlite"
        with Ledger(p) as lg:                    # lighting 列の無い古い台帳にする
            lg.conn.execute("ALTER TABLE signs DROP COLUMN lighting")
            lg.conn.commit()
            self.assertNotIn("lighting", {r[1] for r in lg.conn.execute("PRAGMA table_info(signs)")})
        with Ledger(p) as lg:
            cols = {r[1] for r in lg.conn.execute("PRAGMA table_info(signs)")}
        self.assertIn("lighting", cols)
        with Ledger(p) as lg:                    # 2 回目(列があっても落ちない)
            lg.upsert_sign(Sign(id="x", lighting="内照式"))
            self.assertEqual(lg.get_sign("x").lighting, "内照式")


class SummaryTest(V4Base):
    def test_summary_text_has_every_part_and_no_url(self):
        self.to_measure_skipped(name="① 正面の袖看板", kind="proj")
        self.run_parts(lighting="outer", answer="a", unknown_at="表示部-4")
        self.tap(end=1)
        text = self.last()
        self.assertTrue(text.startswith("点検のまとめ("))
        self.assertIn("■ ① 正面の袖看板", text)
        self.assertIn("突出 / 寸法 未入力 / 写真 7 枚", text)             # 全景 1+部位 6
        self.assertIn("要改善 0 / 経過観察 0 / 良好 17 / AI 判定 1", text)
        for line in ("AI 判定の項目は PC で下書きします",
                     "下書きとして台帳に保存しました。確認と確定は PC の台帳で行います",
                     "カルテと様式は PC の台帳で確認・訂正できます",
                     "次の看板はメニューの「ガイド付き点検」から",
                     "この現場の今日の合計: 看板 1 基 / 要改善 0 / 経過観察 0 / 良好 17 / AI 判定 1"):
            self.assertIn(line, text)
        self.assertTrue(text.endswith("お疲れさまでした"))
        self.assertNotIn("http", text)
        self.assertNotIn("確定しました", text)
        self.assertFalse(self.app_.guide.active(USER))

    def test_no_unknown_line_when_all_answered(self):
        self.to_measure_skipped()
        self.run_parts()
        self.tap(end=1)
        self.assertNotIn("PC で下書きします", self.last())
        self.assertIn("AI 判定 0", self.last())

    def test_dimensions_gl_and_counts(self):
        self.start_with_site()
        self.say("S")
        self.photo()
        self.tap(front="skip")
        self.tap(kind="pole")
        self.tap(m="go")
        for v in ("3600", "900mm", "300mm", "2500"):
            self.say(v)
        self.tap(mount=0)
        self.tap(light="outer")
        self.tap(m="nopost")
        for pi, (part, its) in enumerate(PARTS):
            self.tap(nopart=1, p=pi)
        self.tap(end=1)
        self.assertIn("ポール / W3600×H900×D300 mm、GL 2500 mm / 写真 1 枚", self.last())

    def test_site_total_counts_other_signs_of_the_site_today(self):
        self.to_measure_skipped()
        site = self.app_.guide.load(USER).site_id
        with self.ledger() as lg:
            lg.upsert_sign(Sign(id="other", site_id=site, name="別の人の看板", items={"表示部-1": "要改善", "表示部-2": "経過観察"}))
            lg.conn.execute("UPDATE signs SET created=? WHERE id='other'", (f"{self.clock[0]:%Y-%m-%d} 09:00:00",))
            lg.upsert_sign(Sign(id="old", site_id=site, name="昨日の看板", items={"表示部-1": "要改善"}))
            lg.conn.execute("UPDATE signs SET created='2000-01-01 09:00:00' WHERE id='old'")
            lg.conn.commit()
        self.run_parts(answer="b")
        self.tap(end=1)
        self.assertIn("この現場の今日の合計: 看板 2 基 / 要改善 1 / 経過観察 19 / 良好 0 / AI 判定 0", self.last())
        self.assertNotIn("別の人の看板", self.last())   # 看板ごとの欄はこの進行の看板だけ

    def test_split_text(self):
        self.assertEqual(split_text("あ\nい"), ["あ\nい"])
        long = "\n".join(["x" * 999] * 12)
        parts = split_text(long)
        self.assertTrue(all(len(p) <= 5000 for p in parts))
        self.assertEqual("\n".join(parts), long)
        self.assertEqual(len(parts), 3)
        self.assertEqual([len(p) for p in split_text("y" * 12000)], [5000, 5000, 2000])  # 1 行が上限を超える時は字数で

    def test_long_summary_is_cut_and_ends_with_the_closing_line(self):
        rows = [SignRow(name=f"看板{i}" + "あ" * 80, good=18) for i in range(200)]
        say = summary_text(Summary("現場", rows, SignRow("合計", good=3600), 200))
        msgs = [say.text] + (say.more or [])
        self.assertTrue(1 < len(msgs) <= G.REPLY_MAX_MESSAGES)
        self.assertTrue(all(len(m) <= G.TEXT_MAX for m in msgs))
        self.assertTrue(msgs[-1].endswith("お疲れさまでした"))
        short = summary_text(Summary("現場", rows[:3]))
        self.assertIsNone(short.more)

    def test_more_messages_reach_the_client_in_one_reply(self):
        rows = [SignRow(name=f"看板{i}" + "あ" * 80, good=18) for i in range(120)]
        say = summary_text(Summary("現場", rows))
        self.assertGreater(len(say.more or []), 0)
        self.app_._reply("tok", say.text, type("D", (), {"replies": [], "errors": []})(), more=say.more)
        self.assertEqual(self.client.extras[-1]["more"], say.more)


class AutoDraftTest(V4Base):
    """終了時の AI 判定の自動下書き。draft の呼び出しは差し替え(実際の API は呼ばない)。"""

    def run_one(self, unknown_at=None, lighting="outer"):
        self.to_measure_skipped()
        self.run_parts(lighting=lighting, unknown_at=unknown_at)
        self.tap(end=1)

    def env(self, on, key):
        env = {"ANTHROPIC_API_KEY": "sk-test"} if key else {}
        if on:
            env["LINE_GUIDE_AUTO_DRAFT"] = "1"
        cleared = {k: v for k, v in os.environ.items() if k not in ("ANTHROPIC_API_KEY", "LINE_GUIDE_AUTO_DRAFT")}
        p = mock.patch.dict(os.environ, {**cleared, **env}, clear=True)
        p.start()
        self.addCleanup(p.stop)
        q = mock.patch("kanban_karte.eval.claude_judge.load_dotenv", lambda *a, **k: None)  # repo の .env は読まない
        q.start()
        self.addCleanup(q.stop)

    def test_on_with_key_runs_draft_once_for_the_bundle_and_says_so(self):
        self.env(on=True, key=True)
        self.run_one(unknown_at="表示部-4")
        s = self.sign()
        self.assertEqual(self.drafted, [s.source_bundle])
        self.assertIn("AI 判定 1 項目は写真から下書き中(数分)。結果は PC の台帳で", self.last())
        self.assertNotIn("PC で下書きします", self.last())
        self.assertEqual(s.status, "draft")

    def test_on_without_key_does_not_run_and_says_pc(self):
        self.env(on=True, key=False)
        self.run_one(unknown_at="表示部-4")
        self.assertEqual(self.drafted, [])
        self.assertIn("AI 判定の項目は PC で下書きします", self.last())
        self.assertNotIn("下書き中", self.last())

    def test_off_does_not_run_even_with_key(self):
        self.env(on=False, key=True)
        self.run_one(unknown_at="表示部-4")
        self.assertEqual(self.drafted, [])
        self.assertIn("AI 判定の項目は PC で下書きします", self.last())

    def test_zero_ai_items_writes_nothing_and_does_not_run(self):
        self.env(on=True, key=True)
        self.run_one()
        self.assertEqual(self.drafted, [])
        self.assertNotIn("AI 判定 ", self.last().replace("/ AI 判定 0", ""))
        self.assertNotIn("PC で下書きします", self.last())
        self.assertNotIn("下書き中", self.last())
        self.assertIn("AI 判定 0", self.last())   # 数の見出しは出る

    def test_ending_is_draft_not_confirmed(self):
        self.env(on=False, key=False)
        self.run_one()
        text = self.last()
        self.assertEqual(self.sign().status, "draft")
        self.assertIn("下書きとして台帳に保存しました。確認と確定は PC の台帳で行います", text)
        self.assertNotIn("確定しました", text)
        self.assertTrue(text.endswith("お疲れさまでした"))

    def test_default_runner_calls_web_api_draft_bundle_in_a_thread(self):
        """既定の走らせ方は web.api.draft_bundle(Web の「下書きを作る」と同じ関数)を別スレッドで呼ぶ。"""
        self.env(on=True, key=True)
        calls, done = [], threading.Event()

        def fake(ledger, bundle_id, judge_factory=None):
            calls.append((threading.current_thread().name, bundle_id))
            done.set()
            return {}
        self.app_.auto_draft = self.app_._draft_in_background
        with mock.patch("kanban_karte.web.api.draft_bundle", fake):
            self.run_one(unknown_at="表示部-4")
            self.assertTrue(done.wait(5))
        self.assertEqual(calls, [(f"line-draft-{self.sign().source_bundle}", self.sign().source_bundle)])

    def test_draft_failure_is_logged_not_raised(self):
        self.env(on=True, key=True)
        self.app_.auto_draft = self.app_._draft_bundle
        with mock.patch("kanban_karte.web.api.draft_bundle", side_effect=RuntimeError("x")):
            with self.assertLogs("kanban_karte.line", "ERROR"):
                self.run_one(unknown_at="表示部-4")

    def test_enabled_needs_env_one(self):
        from kanban_karte.intake.line.webhook import auto_draft_enabled
        self.env(on=False, key=True)
        self.assertFalse(auto_draft_enabled())
        self.env(on=True, key=True)
        self.assertTrue(auto_draft_enabled())
        os.environ["LINE_GUIDE_AUTO_DRAFT"] = "true"   # 1 だけを ON とする
        self.assertFalse(auto_draft_enabled())


class AccessLogTest(unittest.TestCase):
    def test_log_line_starts_with_jst_datetime(self):
        from kanban_karte.web import server
        app = SimpleNamespace(verbose=True)
        h = server.make_handler(app)
        buf = io.StringIO()
        with redirect_stderr(buf):
            h.log_message(SimpleNamespace(command="GET", path="/api/signs"), "%s", "x")
        self.assertRegex(buf.getvalue(), r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2} GET /api/signs\n$")


if __name__ == "__main__":
    unittest.main()
