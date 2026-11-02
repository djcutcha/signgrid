import io
import tempfile
import unittest
from pathlib import Path

from openpyxl import load_workbook

from kanban_karte.eval.judge import Judge, Prediction
from kanban_karte.ledger import Ledger, Sign, Site
from kanban_karte.ledger.items import ITEM_KEYS
from kanban_karte.output.tokyo_form import write_tokyo_form
from kanban_karte.pipeline import cli, draft, review

PHOTOS = Path(__file__).resolve().parents[1] / "data" / "sample" / "photos"
NAME = {k: n for n, k in draft.FORM_ITEMS.items()}


class FakeJudge(Judge):
    """偽の判定役。項目は「基礎部・上部構造-2=3」「表示部-4=2」だけ返し、その他-2 は要実測。"""
    name = "fake"

    def __init__(self, rank=3):
        self.rank = rank
        self.seen = []

    def judge(self, item):
        self.seen.append(item)
        notes = {NAME["基礎部・上部構造-2"]: {"evidence": "写真2の支柱の根元に赤錆が広がっている", "needs_onsite": False},
                 NAME["表示部-4"]: {"evidence": "写真1の表示面に軽い退色", "needs_onsite": False},
                 NAME["その他-2"]: {"evidence": "避雷針は写っていない", "needs_onsite": True}}
        return Prediction(self.rank, {NAME["基礎部・上部構造-2"]: 3, NAME["表示部-4"]: 2}, 0.7,
                          raw={"reasons": ["支柱の根元に腐食", "表示面は退色", "三つ目は捨てる"], "item_notes": notes},
                          usage={"cost_usd": 0.01})


def line_bundle(lg, n_photos=2, site=True, sha="sha-line"):
    """LINE の束(pages_registered、写真は未分類の「不明」)。"""
    bid = lg.add_bundle("line://u/2026-11-02/m1", sha, pages=n_photos)
    lg.set_bundle(bid, status="pages_registered")
    for i in range(n_photos):
        lg.upsert_page(bid, i + 1, kind="不明", image_path=str(PHOTOS / f"plain_{'abc'[i]}.png"), image_source="line")
    if site:
        lg.upsert_site(Site("line-1", "LINE受信", "東京都豊島区見本町1-2-3", municipality="東京都豊島区"))
        lg.set_bundle(bid, site_id="line-1")
    return bid


class DraftTest(unittest.TestCase):
    def setUp(self):
        self.lg = Ledger(":memory:")

    def tearDown(self):
        self.lg.close()

    def test_form_items_are_the_18(self):
        self.assertEqual(list(draft.FORM_ITEMS.values()), ITEM_KEYS)
        self.assertTrue(all(n.startswith("④") for n in draft.FORM_ITEMS))

    def test_line_bundle_becomes_one_provisional_sign_with_all_photos(self):
        bid = line_bundle(self.lg, 3)
        judge = FakeJudge()
        res = draft.draft_bundle(self.lg, bid, judge)
        self.assertEqual([(r.sign_id, r.status, r.rank) for r in res], [(f"b{bid}-s001", "draft", 3)])
        s = self.lg.get_sign(f"b{bid}-s001")
        self.assertEqual(len(s.photos), 3)
        self.assertEqual((s.site_id, s.municipality), ("line-1", "東京都豊島区"))
        self.assertEqual(len(judge.seen[0].photo_paths), 3)
        self.assertEqual(self.lg.get_bundle(bid)["status"], "signs_drafted")

    def test_draft_fields(self):
        bid = line_bundle(self.lg)
        res = draft.draft_bundle(self.lg, bid, FakeJudge())[0]
        s = self.lg.get_sign(res.sign_id)
        self.assertEqual((s.status, s.rank, s.draft_rank, s.confidence), ("draft", 3, 3, 0.7))
        self.assertEqual(s.items, {"基礎部・上部構造-2": "要改善", "表示部-4": "経過観察"})
        self.assertEqual(res.undecided, 16)
        self.assertEqual(s.remark, "支柱の根元に腐食。表示面は退色。")
        self.assertEqual(s.evidence["基礎部・上部構造-2"]["photos"], [2])
        self.assertTrue(s.evidence["その他-2"]["needs_onsite"])
        self.assertEqual(res.cost_usd, 0.01)

    def test_no_photos_is_needs_photos(self):
        bid = line_bundle(self.lg, 0)
        judge = FakeJudge()
        res = draft.draft_bundle(self.lg, bid, judge)
        self.assertEqual(res[0].status, "needs_photos")
        s = self.lg.get_sign(res[0].sign_id)
        self.assertEqual((s.status, s.rank, s.items), ("needs_photos", None, {}))
        self.assertEqual(judge.seen, [])

    def test_confirmed_sign_is_not_overwritten(self):
        bid = line_bundle(self.lg)
        sid = draft.draft_bundle(self.lg, bid, FakeJudge(3))[0].sign_id
        s = self.lg.get_sign(sid)
        s.status, s.rank = "confirmed", 2
        self.lg.upsert_sign(s)
        res = draft.draft_bundle(self.lg, bid, FakeJudge(4))
        self.assertEqual((res[0].status, res[0].rank), ("confirmed", 2))
        self.assertEqual(self.lg.get_sign(sid).rank, 2)

    def test_failed_judgement_leaves_sign_alone(self):
        class Fail(Judge):
            def judge(self, item):
                return Prediction(None, raw={"error": "image: x"})
        bid = line_bundle(self.lg)
        res = draft.draft_bundle(self.lg, bid, Fail())
        self.assertEqual((res[0].status, res[0].error), ("error", "image: x"))
        self.assertEqual(self.lg.get_sign(res[0].sign_id).status, "new")
        self.assertEqual(self.lg.get_bundle(bid)["status"], "pages_registered")

    def test_existing_signs_of_pdf_bundle_are_drafted_in_place(self):
        bid = self.lg.add_bundle("a.pdf", "sha-pdf", pages=1)
        for k in (1, 2):
            self.lg.upsert_sign(Sign(f"b{bid}-s00{k}", photos=[str(PHOTOS / "plain_a.png")], source_bundle=bid))
        res = draft.draft_bundle(self.lg, bid, FakeJudge(2))
        self.assertEqual([r.sign_id for r in res], [f"b{bid}-s001", f"b{bid}-s002"])
        self.assertEqual({s.status for s in self.lg.list_signs(bundle_id=bid)}, {"draft"})

    def test_rank4_item_clamped_but_rank_kept(self):
        class Four(FakeJudge):
            def judge(self, item):
                p = super().judge(item)
                p.item_judgments_pred = {NAME["表示部-1"]: 4}
                return p
        bid = line_bundle(self.lg)
        s = self.lg.get_sign(draft.draft_bundle(self.lg, bid, Four(4))[0].sign_id)
        self.assertEqual((s.rank, s.items), (4, {"表示部-1": "要改善"}))


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.lg = Ledger(":memory:")
        self.sid = draft.draft_bundle(self.lg, line_bundle(self.lg), FakeJudge(3))[0].sign_id

    def tearDown(self):
        self.lg.close()

    def test_resolve_item_forms(self):
        for t in ("支持部-2", "④支持部-2 鉄骨接合部（ボルト、ナット、ビス）のゆるみ、欠落", "支持部 2 鉄骨接合部（ボルト、ナット、ビス）のゆるみ、欠落",
                  "避雷針", "rank"):
            self.assertIn(review.resolve_item(t), ("支持部-2", "その他-2", "rank"))
        with self.assertRaises(ValueError):
            review.resolve_item("腐食")  # 複数に当たる
        with self.assertRaises(ValueError):
            review.resolve_item("存在しない")

    def test_correction_records_diff_rule_and_moves_rank(self):
        c = review.apply_correction(self.lg, self.sid, "基礎部・上部構造-2", 2, "有資格者A")
        s = self.lg.get_sign(self.sid)
        self.assertEqual((c.old_value, c.new_value, c.rank_before, c.rank_after), ("要改善", "経過観察", 3, 2))
        self.assertEqual((s.items["基礎部・上部構造-2"], s.rank, s.draft_rank), ("経過観察", 2, 3))
        row = self.lg.list_corrections(self.sid)[0]
        self.assertEqual((row["item"], row["corrected_by"], row["rule_id"]), ("基礎部・上部構造-2", "有資格者A", c.rule_id))
        rule = self.lg.list_rules()[0]
        self.assertIn("支柱の根元に赤錆が広がっている", rule["text"])  # evidence から「写真2の」を除いた所見
        self.assertNotIn("写真", rule["text"])
        self.assertTrue(rule["text"].startswith("基礎部・上部構造-2 基礎のクラック"))
        self.assertIn("は経過観察(下書きは要改善)", rule["text"])
        self.assertEqual(rule["source"], f"{self.sid} / 有資格者A")

    def test_correction_for_undecided_item_and_note_as_finding(self):
        c = review.apply_correction(self.lg, self.sid, "その他-2", "要改善", "有資格者A", note="避雷針の根元が腐食")
        self.assertIsNone(c.old_value)
        self.assertIn("「避雷針の根元が腐食」は要改善(下書きは判断できず)", self.lg.list_rules()[0]["text"])
        self.assertEqual(self.lg.get_sign(self.sid).rank, 3)

    def test_same_value_is_not_a_correction(self):
        self.assertIsNone(review.apply_correction(self.lg, self.sid, "表示部-4", "経過観察", "有資格者A"))
        self.assertEqual(self.lg.list_corrections(), [])
        self.assertEqual(self.lg.list_rules(), [])

    def test_same_rule_not_duplicated_and_conflict_flagged(self):
        review.apply_correction(self.lg, self.sid, "表示部-4", "要改善", "有資格者A", note="退色")
        review.apply_correction(self.lg, self.sid, "表示部-4", "経過観察", "有資格者A")  # 戻す(別の規則: 所見が違う)
        review.apply_correction(self.lg, self.sid, "表示部-4", "良好", "有資格者B", note="退色")
        rules = self.lg.list_rules()
        self.assertEqual(len(rules), 3)
        self.assertEqual(rules[2]["conflicts_with"], "[%d]" % rules[0]["id"])

    def test_rank_correction_makes_no_rule_and_keeps_4_on_item_changes(self):
        c = review.apply_correction(self.lg, self.sid, "rank", 4, "有資格者A", note="倒壊の恐れ")
        self.assertEqual((c.old_value, c.new_value, c.rule_id), ("3", "4", None))
        review.apply_correction(self.lg, self.sid, "表示部-4", "良好", "有資格者A")
        self.assertEqual(self.lg.get_sign(self.sid).rank, 4)
        self.assertEqual(len(self.lg.list_rules()), 1)

    def test_bad_input(self):
        for bad in (dict(new_value=5), dict(new_value="x"), dict(by="")):
            kw = dict(sign_id=self.sid, item="表示部-4", new_value=3, by="有資格者A") | bad
            with self.assertRaises(ValueError):
                review.apply_correction(self.lg, **kw)
        with self.assertRaises(LookupError):
            review.apply_correction(self.lg, "nope", "表示部-4", 3, "有資格者A")

    def test_finalize_and_locks(self):
        review.apply_correction(self.lg, self.sid, "基礎部・上部構造-2", 1, "有資格者A")
        review.finalize(self.lg, self.sid, "有資格者A")
        s = self.lg.get_sign(self.sid)
        self.assertEqual((s.status, s.confirmed_by, s.rank, s.draft_rank), ("confirmed", "有資格者A", 2, 3))
        self.assertRegex(s.confirmed_at, r"^\d{4}-\d\d-\d\dT")
        with self.assertRaises(ValueError):
            review.apply_correction(self.lg, self.sid, "表示部-4", 3, "有資格者A")
        with self.assertRaises(ValueError):
            review.finalize(self.lg, self.sid, "有資格者A")
        self.assertEqual(review.rank_changed_count(self.lg), (1, 1))

    def test_finalize_needs_draft(self):
        bid = line_bundle(self.lg, 0, sha="sha-empty")  # 写真なし → needs_photos
        sid = draft.draft_bundle(self.lg, bid, FakeJudge())[0].sign_id
        with self.assertRaises(ValueError):
            review.finalize(self.lg, sid, "有資格者A")

    def test_rank_changed_count_unchanged(self):
        review.finalize(self.lg, self.sid, "有資格者A")
        self.assertEqual(review.rank_changed_count(self.lg), (0, 1))


class FlowTest(unittest.TestCase):
    """見本データの写真で、束 → 下書き → 訂正 → 確定 → 様式出力 を CLI から一連で流す(判定役は偽)。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "l.sqlite")
        with Ledger(self.db) as lg:
            self.bid = line_bundle(lg, 3)
        self.judges = []

        def factory(rules, examples, cfg):
            self.judges.append((list(rules), examples))
            return FakeJudge(3)
        self.factory = factory

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv, code=0):
        out, err = io.StringIO(), io.StringIO()
        import contextlib
        with contextlib.redirect_stderr(err):
            rc = cli.main(["--db", self.db, *argv], out=out, judge_factory=self.factory)
        self.assertEqual(rc, code, err.getvalue())
        return out.getvalue()

    def test_full_flow(self):
        sid = f"b{self.bid}-s001"
        o = self.run_cli("draft", "--bundle", str(self.bid))
        self.assertIn(f"{sid}: 下書き ランク 3、判断できず 16/18 項目", o)
        self.assertEqual(self.judges, [([], 6)])  # 初回は規則なし、例示6

        o = self.run_cli("review", "--sign", sid, "--item", "基礎部・上部構造-2", "--value", "2", "--by", "有資格者A")
        self.assertIn("要改善 → 経過観察  ランク 3 → 2", o)
        self.assertIn("規則候補 #1 を積みました", o)
        self.run_cli("review", "--sign", sid, "--item", "照明装置-1", "--value", "良好", "--by", "有資格者A", "--note", "全灯")
        self.assertIn("下書きと同じ値", self.run_cli("review", "--sign", sid, "--item", "表示部-4", "--value", "2", "--by", "有資格者A"))

        o = self.run_cli("finalize", "--sign", sid, "--by", "有資格者A")
        self.assertIn("ランク 2(下書き 3)", o)
        self.assertIn("1 基のうち、ランクが下書きから変わった: 1 基", self.run_cli("changed"))
        self.run_cli("review", "--sign", sid, "--item", "表示部-4", "--value", "3", "--by", "有資格者A", code=1)  # 確定後は訂正できない

        # 次の束の下書きには、積んだ規則が先に渡る
        with Ledger(self.db) as lg:
            bid2 = line_bundle(lg, 1, sha="sha-2")
        self.run_cli("draft", "--bundle", str(bid2))
        self.assertEqual(len(self.judges[-1][0]), 2)
        self.run_cli("draft", "--bundle", str(bid2), "--no-rules")
        self.assertEqual(self.judges[-1][0], [])

        with Ledger(self.db) as lg:
            s = lg.get_sign(sid)
            self.assertEqual((s.status, s.rank, s.items["基礎部・上部構造-2"], s.items["照明装置-1"]), ("confirmed", 2, "経過観察", "良好"))
            self.assertEqual([c["item"] for c in lg.list_corrections(sid)], ["基礎部・上部構造-2", "照明装置-1"])
            out = Path(self.tmp.name) / "t.xlsx"
            self.assertEqual(write_tokyo_form(lg, "line-1", out), [f"様式_{sid}", f"写真_{sid}", f"様式_b{bid2}-s001", f"写真_b{bid2}-s001"])
        self.assertEqual(load_workbook(out).sheetnames[0], f"様式_{sid}")

    def test_missing_ledger_and_unknown_sign(self):
        self.assertEqual(cli.main(["--db", str(Path(self.tmp.name) / "none.sqlite"), "changed"], out=io.StringIO()), 1)
        self.run_cli("finalize", "--sign", "nope", "--by", "有資格者A", code=1)


if __name__ == "__main__":
    unittest.main()
