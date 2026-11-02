import unittest

from kanban_karte.eval.aggregate import synth_rank


class SynthRankTest(unittest.TestCase):
    def test_model_keeps_rank(self):
        self.assertEqual(synth_rank(2, {"a": 4}, "model"), 2)

    def test_items_is_worst_of_items(self):
        self.assertEqual(synth_rank(2, {"a": 1, "b": 3, "c": None}, "items"), 3)
        self.assertEqual(synth_rank(4, {"a": 1, "b": 2}, "items"), 2)

    def test_items_falls_back_to_model_when_no_values(self):
        self.assertEqual(synth_rank(3, {}, "items"), 3)
        self.assertEqual(synth_rank(3, {"a": None}, "items"), 3)
        self.assertEqual(synth_rank(3, None, "items"), 3)  # 紙(項目なし)

    def test_blend_takes_larger(self):
        self.assertEqual(synth_rank(2, {"a": 3}, "blend"), 3)
        self.assertEqual(synth_rank(4, {"a": 1}, "blend"), 4)
        self.assertEqual(synth_rank(None, {"a": 2}, "blend"), 2)
        self.assertEqual(synth_rank(2, None, "blend"), 2)

    def test_none_model_without_items_stays_none(self):
        self.assertIsNone(synth_rank(None, {}, "items"))

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            synth_rank(1, {}, "x")


if __name__ == "__main__":
    unittest.main()
