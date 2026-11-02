import unittest

from kanban_karte.ledger.item_map import MAPPINGS, Mapping, mappings_for, render_markdown, review_items
from kanban_karte.ledger.items import ITEM_KEYS


class ItemMapTest(unittest.TestCase):
    def test_center_covers_all_18_items_once(self):
        ident = [m for m in mappings_for("xlsx_center") if m.target]
        self.assertEqual(sorted(m.target for m in ident), sorted(ITEM_KEYS))
        self.assertTrue(all(m.confidence == "high" and not m.needs_review for m in ident))

    def test_no_forced_mapping(self):
        # 同じ東京都の項目に、同じ様式の複数の項目を対応させない
        for src in ("xlsx_center", "xlsx_karte", "paper_scan"):
            tg = [m.target for m in mappings_for(src) if m.target]
            self.assertEqual(len(tg), len(set(tg)), src)
        # 紙の総合ランクは項目には対応させない
        self.assertTrue(all(m.target is None for m in mappings_for("paper_scan")))

    def test_karte_five_items(self):
        names = {m.source_item for m in mappings_for("xlsx_karte")}
        self.assertEqual(names, {"球切れ", "広告面の退色", "広告面の損傷", "看板本体の損傷", "その他"})

    def test_review_flags(self):
        self.assertEqual(len(review_items()), 7)
        self.assertTrue(all(m.confidence != "high" for m in review_items()))

    def test_validation(self):
        with self.assertRaises(ValueError):
            Mapping("x", "a", "でたらめ-1", "high", False)
        with self.assertRaises(ValueError):
            Mapping("x", "a", None, "high", False)
        with self.assertRaises(ValueError):
            Mapping("x", "a", ITEM_KEYS[0], "very-high", False)
        self.assertEqual(len(MAPPINGS), 18 + 4 + 5 + 1)

    def test_markdown_mentions_every_non_identity_row(self):
        md = render_markdown()
        for m in MAPPINGS:
            if m.source != "xlsx_center" or not m.target:
                self.assertIn(m.source_item, md)


if __name__ == "__main__":
    unittest.main()
