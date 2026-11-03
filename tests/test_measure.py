"""ガイドの「寸法の実測」工程: 出る条件、飛ばす、数字の受け方、台帳の列、引き継ぎ、法規の再判定、下書き入力からの除外。API・LINE には繋がない。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from kanban_karte.intake.line import measure as M
from kanban_karte.intake.line.guide import PARTS
from kanban_karte.ledger import Ledger, Sign, Site
from kanban_karte.ledger.db import FIGURE_TAG, MEASURE_TAG
from kanban_karte.legal.judge import judge
from kanban_karte.web import api
from tests.test_guide import GuideBase
from tests.test_line import USER


class ParseLengthTest(unittest.TestCase):
    def mm(self, text, **kw):
        r = M.parse_length(text, **kw)
        return r.mm, bool(r.confirm), bool(r.error)

    def test_mm_is_taken_as_is(self):
        self.assertEqual(self.mm("3600"), (3600, False, False))
        self.assertEqual(self.mm("3600mm"), (3600, False, False))
        self.assertEqual(self.mm("1,200"), (1200, False, False))

    def test_units(self):
        self.assertEqual(self.mm("3.6m"), (3600, False, False))
        self.assertEqual(self.mm("360cm"), (3600, False, False))
        self.assertEqual(self.mm("900ミリ"), (900, False, False))
        self.assertEqual(self.mm("約3.6メートルです"), (3600, False, False))

    def test_full_width(self):
        self.assertEqual(self.mm("３６００"), (3600, False, False))
        self.assertEqual(self.mm("３．６ｍ"), (3600, False, False))
        self.assertEqual(self.mm("９００ｍｍ"), (900, False, False))

    def test_unitless_three_digits_or_less_asks_to_confirm(self):
        self.assertEqual(self.mm("3.6"), (3600, True, False))   # m とみなす
        self.assertEqual(self.mm("3"), (3000, True, False))
        self.assertEqual(self.mm("30"), (30000, True, False))
        self.assertEqual(self.mm("900"), (900, True, False))    # m だと 50 m 超なので mm とみなす
        self.assertIn("3.6 m(3600 mm)", M.parse_length("3.6").confirm)
        self.assertIn("900 mm(0.9 m)", M.parse_length("900").confirm)

    def test_out_of_range_and_garbage(self):
        self.assertTrue(self.mm("60")[2])        # m でも mm でも不自然
        self.assertTrue(self.mm("60000")[2])
        self.assertTrue(self.mm("50mm")[2])
        self.assertTrue(self.mm("高い")[2])
        self.assertTrue(self.mm("")[2])

    def test_gl_allows_zero(self):
        self.assertEqual(self.mm("0", lo=0, allow_zero=True), (0, False, False))
        self.assertEqual(self.mm("2500", lo=0, hi=M.GL_MAX_MM, allow_zero=True), (2500, False, False))

    def test_post(self):
        self.assertEqual(M.parse_post("□50"), "□50")
        self.assertEqual(M.parse_post("なし"), "無し")
        self.assertIsNone(M.parse_post("あ" * 31))


class MeasureBase(GuideBase):
    skip_measure = False

    def to_overview(self, name="① 正面の袖看板", before=()):
        """現場を決めて看板の名前を送り、全景まで。before=台帳に先に入れる看板(site_id は今の現場に付ける)。"""
        self.start_with_site()
        site = self.app_.guide.load(USER).site_id
        with self.ledger() as lg:
            lg.upsert_site(Site("other", "別の店"))
            for sg in before:
                if sg.site_id is None:
                    sg.site_id = site
                lg.upsert_sign(sg)
        self.say(name)
        self.photo()  # 全景

    def measure_all(self, w="3.6m", h="900mm", gl="2500", mount=0, post="□50", photos=1):
        self.tap(m="go")
        for v in (w, h, gl):
            self.say(v)
        self.tap(mount=mount)
        self.say(post)
        for _ in range(photos):
            self.photo()
        if photos == 1:
            self.tap(m="nopic")

    def sign(self, i=-1):
        with self.ledger() as lg:
            return lg.list_signs()[i]


class StepConditionTest(MeasureBase):
    def test_new_sign_without_size_gets_the_step(self):
        self.to_overview()
        self.assertEqual(self.last(), "全景を受け取りました\nこの看板の寸法を測りますか(初回の点検では、測って台帳に残します)")
        self.assertEqual([q["action"]["label"] for q in self.last_quick()], ["測る", "飛ばす"])

    def test_sign_with_size_in_ledger_skips_the_step_and_says_so_at_the_end(self):
        self.to_overview(before=[Sign(id="old", name="① 正面の袖看板", size_w=3600, size_h=900,
                                      size_source="measured", size_by="line_guide:zzzz", size_at="2026-04-01",
                                      gl_mm=2500, gl_edge="bottom", height_m=3.4, mount="ビス", post="□50")])
        self.assertEqual(self.last(), "全景を受け取りました\n基礎部・上部構造を撮ってください(近くで1〜3枚)")
        with self.ledger() as lg:
            s = [x for x in lg.list_signs() if x.id != "old"][0]
            self.assertEqual((s.size_w, s.size_h, s.size_source, s.size_at, s.gl_mm, s.height_m, s.mount, s.post),
                             (3600, 900, "measured", "2026-04-01", 2500, 3.4, "ビス", "□50"))
            self.assertEqual(lg.get_sign("old").size_at, "2026-04-01")  # 前回の看板は触らない
        for p in range(len(PARTS)):
            self.tap(nopart=1, p=p)
        self.assertEqual(self.last(), "寸法は前回(2026-04-01)の実測を引き継ぎました\nこの看板は終わりです。次の看板を始めますか")

    def test_same_name_at_another_site_or_different_name_is_not_inherited(self):
        self.to_overview(before=[Sign(id="o1", site_id="other", name="① 正面の袖看板", size_w=3600, size_h=900),
                                 Sign(id="o2", name="② 別の看板", size_w=3600, size_h=900)])
        self.assertIn("寸法を測りますか", self.last())

    def test_sign_with_only_a_width_is_still_asked(self):
        self.to_overview(before=[Sign(id="o1", name="① 正面の袖看板", size_w=3600)])
        self.assertIn("寸法を測りますか", self.last())

    def test_known_size_without_a_measured_source_is_inherited_with_its_label(self):
        self.to_overview(before=[Sign(id="old", name="① 正面の袖看板", size_w=3000, size_h=800, size_source="drawing")])
        for p in range(len(PARTS)):
            self.tap(nopart=1, p=p)
        self.assertIn("寸法は台帳の図面の値を使いました", self.last())


class SkipTest(MeasureBase):
    def test_skip_at_the_first_question_writes_nothing(self):
        self.to_overview()
        self.tap(m="skip")
        self.assertEqual(self.last(), "寸法の実測は飛ばしました\n基礎部・上部構造を撮ってください(近くで1〜3枚)")
        s = self.sign()
        self.assertEqual((s.size_w, s.size_h, s.size_source, s.gl_mm, s.mount, s.post), (None,) * 6)

    def test_skip_command_at_the_first_question(self):
        self.to_overview()
        self.say("飛ばす")
        self.assertIn("寸法の実測は飛ばしました", self.last())
        self.assertIsNone(self.sign().size_w)

    def test_skip_command_at_width_skips_the_whole_step(self):
        self.to_overview()
        self.tap(m="go")
        self.say("飛ばす")
        self.assertIn("寸法の実測は飛ばしました", self.last())
        self.assertIsNone(self.sign().size_w)

    def test_skip_after_size_keeps_it_and_skips_only_that_question(self):
        self.to_overview()
        self.tap(m="go")
        self.say("3600")
        self.say("900mm")  # 900 に単位付き: 確認なし
        self.say("飛ばす")  # GL を飛ばす
        self.assertEqual(self.last(), "取付方法を選んでください")
        s = self.sign()
        self.assertEqual((s.size_w, s.size_h, s.gl_mm, s.height_m, s.size_source), (3600, 900, None, None, "measured"))

    def test_back_and_quit(self):
        self.to_overview()
        self.tap(m="go")
        self.say("3600")
        self.say("戻る")
        self.assertIn("幅(mm)", self.last())
        self.say("戻る")
        self.assertIn("寸法を測りますか", self.last())
        self.say("戻る")
        self.assertEqual(self.last(), "全景を1枚撮ってください")
        self.photo()
        self.tap(m="go")
        self.say("3600")
        self.say("900mm")
        self.say("やめる")
        self.assertFalse(self.app_.guide.active(USER))
        self.assertEqual((self.sign().size_w, self.sign().size_h), (3600, 900))  # やめても入力済みの分は残る

    def test_back_from_first_part_returns_to_the_measure_step(self):
        self.to_overview()
        self.tap(m="skip")
        self.say("戻る")
        self.assertIn("寸法を測りますか", self.last())


class InputTest(MeasureBase):
    def test_unitless_value_asks_to_confirm_and_waits_for_yes(self):
        self.to_overview()
        self.tap(m="go")
        self.say("3.6")
        self.assertEqual(self.last(), "「3.6」を 3.6 m(3600 mm)として受け取ります。合っていますか")
        self.assertEqual([q["action"]["label"] for q in self.last_quick()], ["はい", "入れ直す"])
        self.assertIsNone(self.sign().size_w)
        self.say("はい")
        self.assertIn("高さ(mm)", self.last())

    def test_redo_asks_again(self):
        self.to_overview()
        self.tap(m="go")
        self.say("900")
        self.tap(m="redo")
        self.assertIn("幅(mm)", self.last())
        self.say("３６００")
        self.assertIn("高さ(mm)", self.last())

    def test_new_number_replaces_the_pending_one(self):
        self.to_overview()
        self.tap(m="go")
        self.say("900")
        self.say("3600mm")
        self.assertIn("高さ(mm)", self.last())

    def test_invalid_number_is_asked_again(self):
        self.to_overview()
        self.tap(m="go")
        self.say("大きい")
        self.assertTrue(self.last().startswith("数字が読み取れませんでした"))
        self.assertIn("幅(mm)", self.last())

    def test_gl_unknown_and_zero(self):
        self.to_overview()
        self.tap(m="go")
        self.say("3600")
        self.say("900mm")
        self.tap(m="glx")
        self.assertEqual(self.last(), "取付方法を選んでください")
        s = self.sign()
        self.assertEqual((s.gl_mm, s.gl_edge, s.height_m), (None, None, None))

    def test_gl_zero_is_a_value(self):
        self.to_overview()
        self.tap(m="go")
        self.say("3600")
        self.say("900mm")
        self.say("0")
        s = self.sign()
        self.assertEqual((s.gl_mm, s.gl_edge, s.height_m), (0, "bottom", 0.9))

    def test_mount_unknown_is_null_and_no_post_is_a_value(self):
        self.to_overview()
        self.tap(m="go")
        self.say("3600")
        self.say("900mm")
        self.say("2500")
        self.tap(mount=M.MOUNTS.index("分からない"))
        self.tap(m="nopost")
        s = self.sign()
        self.assertEqual((s.mount, s.post), (None, "無し"))

    def test_stale_button_does_not_advance(self):
        self.to_overview()
        self.tap(m="go")
        self.tap(m="go")  # 幅の問いで「測る」を押し直しても進まない
        self.assertIn("幅(mm)", self.last())
        self.tap(m="nopic")
        self.assertIn("幅(mm)", self.last())


class LedgerTest(MeasureBase):
    def test_columns_after_a_full_measure(self):
        self.to_overview()
        self.measure_all()
        s = self.sign()
        self.assertEqual((s.size_w, s.size_h, s.gl_mm, s.gl_edge, s.mount, s.post), (3600, 900, 2500, "bottom", "ビス", "□50"))
        self.assertEqual((s.size_source, s.size_by, s.size_at), ("measured", "line_guide:" + USER[-4:], "2026-11-03"))
        self.assertEqual(s.height_m, 3.4)  # GL(下端まで)+ 高さ
        self.assertEqual(self.last(), "基礎部・上部構造を撮ってください(近くで1〜3枚)")

    def test_tape_photos_are_tagged_and_kept_at_the_end(self):
        self.to_overview()
        self.measure_all(photos=2)
        self.assertEqual(self.last(), "巻尺の写真 2枚\n基礎部・上部構造を撮ってください(近くで1〜3枚)")  # 2枚で自動的に次へ
        self.photo()  # 部位の写真
        with self.ledger() as lg:
            s = lg.list_signs()[0]
            tags = {lg.rel_path(r["image_path"]): r["tag"] for r in lg.conn.execute("SELECT image_path, tag FROM pages")}
            self.assertEqual([tags[p] for p in s.photos], ["overview", "基礎部・上部構造", MEASURE_TAG, MEASURE_TAG])
            photos, figures = lg.split_photos(s)
            self.assertEqual(photos, s.photos[:2])
            self.assertEqual(figures, [])

    def test_photo_numbers_in_evidence_ignore_tape_photos(self):
        self.to_overview()
        self.measure_all(photos=1)
        self.photo()
        self.tap(next=1, p=0)
        self.tap(ans="a", item="基礎部・上部構造-1")
        self.assertEqual(self.sign().evidence["基礎部・上部構造-1"]["photos"], [2])  # 全景=1、部位の写真=2

    def test_no_photo_button(self):
        self.to_overview()
        self.measure_all(photos=0)
        self.tap(m="nopic")
        self.assertEqual(self.last(), "基礎部・上部構造を撮ってください(近くで1〜3枚)")

    def test_old_ledger_gets_the_new_columns(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "old.sqlite"
            c = sqlite3.connect(db)
            c.executescript("""CREATE TABLE signs (id TEXT PRIMARY KEY, site_id TEXT, name TEXT, type TEXT, size_w INTEGER,
                size_h INTEGER, address TEXT, lat REAL, lng REAL, municipality TEXT, is_self_use INTEGER,
                permit_required INTEGER, permit_expires TEXT, next_inspection TEXT, rank INTEGER,
                items TEXT NOT NULL DEFAULT '{}', remark TEXT, photos TEXT NOT NULL DEFAULT '[]', last_inspected TEXT,
                prev_rank INTEGER, source_bundle INTEGER, created TEXT DEFAULT '', updated TEXT DEFAULT '');
                INSERT INTO signs(id, size_w, size_h) VALUES ('a', 3000, 800);""")
            c.commit()
            c.close()
            with Ledger(db) as lg:
                cols = {r[1] for r in lg.conn.execute("PRAGMA table_info(signs)")}
                self.assertTrue({"size_source", "size_by", "size_at", "gl_mm", "gl_edge", "mount", "post"} <= cols)
                s = lg.get_sign("a")
                self.assertEqual((s.size_w, s.size_source), (3000, None))
                self.assertEqual(api.size_info(s)["source"], "legacy")

    def test_size_source_is_validated(self):
        with self.assertRaises(ValueError):
            Sign(id="x", size_source="guess").validate()


class KarteBadgeTest(MeasureBase):
    def test_source_badges(self):
        self.assertEqual(api.size_info(Sign(id="a"))["source"], "none")
        self.assertEqual(api.size_info(Sign(id="a", size_w=1, size_h=1, size_source="estimated"))["source"], "estimated")
        self.assertEqual(api.size_info(Sign(id="a", size_w=1, size_h=1, size_source="drawing"))["source"], "drawing")
        self.assertEqual(api.size_info(Sign(id="a", size_w=1, size_source="measured"))["source"], "none")  # 幅×高さが揃っていない

    def test_detail_shows_measured_badge_and_separates_tape_photos(self):
        self.to_overview()
        self.measure_all()
        self.photo()
        sid = self.sign().id
        with self.ledger() as lg:
            d = api.sign_detail(lg, sid)
            self.assertEqual(d["size_info"]["source"], "measured")
            self.assertEqual((d["size_info"]["at"], d["size_info"]["gl_mm"], d["size_info"]["mount"]), ("2026-11-03", 2500, "ビス"))
            self.assertEqual([p["measure"] for p in d["photos"]], [False, False, True])
            self.assertEqual(d["photo_count"], 2)  # 実測写真は「写真 n 枚」に数えない
            self.assertEqual(api.signs(lg, None)[0]["photo_count"], 2)


class LegalRejudgeTest(MeasureBase):
    def test_measure_resolves_the_need_to_measure(self):
        self.to_overview()
        sid = self.sign().id
        with self.ledger() as lg:
            s = lg.get_sign(sid)
            s.address = "東京都豊島区"
            lg.upsert_sign(s)
            self.assertEqual(api.sign_detail(lg, sid)["legal"]["permit_required"], "要実測")
        self.measure_all()
        with self.ledger() as lg:
            L = api.sign_detail(lg, sid)["legal"]
            self.assertEqual((L["area_m2"], L["permit_required"]), (3.24, "不要"))

    def test_full_height_resolves_the_height_rule(self):
        # 高さの基準がある条例(埼玉県系): 面積だけでは要実測のまま、全高(GL + 高さ)が入ると有資格者の要否が決まる
        self.assertEqual(judge("埼玉県", use="self", area_m2=3.24).qualified_inspector, "要実測")
        self.assertEqual(judge("埼玉県", use="self", area_m2=3.24, height_m=3.4).qualified_inspector, "不要")
        self.to_overview()
        sid = self.sign().id
        with self.ledger() as lg:
            s = lg.get_sign(sid)
            s.address = "埼玉県"
            lg.upsert_sign(s)
        self.measure_all()
        with self.ledger() as lg:
            L = api.sign_detail(lg, sid)["legal"]
            self.assertEqual(L["qualified_inspector"], "不要")


class DraftInputTest(MeasureBase):
    def test_measure_and_figure_photos_are_not_draft_input(self):
        self.to_overview()
        self.measure_all()
        self.photo()
        with self.ledger() as lg:
            s = lg.list_signs()[0]
            fig = s.photos[0]  # 図面として扱う頁を1枚
            lg.conn.execute("UPDATE pages SET tag=? WHERE image_path=?", (FIGURE_TAG, fig))
            lg.conn.commit()
            photos, figures = lg.split_photos(s)
            self.assertEqual(figures, [fig])
            self.assertEqual(photos, [s.photos[1]])
            self.assertNotIn(s.photos[2], photos)  # 実測
            from kanban_karte.pipeline import draft
            item = draft.eval_item_for(lg, s)
            self.assertEqual(len(item.photo_paths), 1)


if __name__ == "__main__":
    unittest.main()
