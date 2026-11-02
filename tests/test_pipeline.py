import unittest
from pathlib import Path

from kanban_karte.eval.judge import Judge, Prediction
from kanban_karte.ledger import Ledger, Sign, Site
from kanban_karte.ledger.items import ITEM_KEYS
from kanban_karte.pipeline import draft

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


def line_bundle(lg, n_photos=2, site=True):
    """LINE の束(pages_registered、写真は未分類の「不明」)。"""
    bid = lg.add_bundle("line://u/2026-11-02/m1", "sha-line", pages=n_photos)
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


if __name__ == "__main__":
    unittest.main()
