import unittest

from kanban_karte.eval import calib, postproc
from kanban_karte.eval.run_eval import select_spread


def rec(sid, src, true, pred, split="example", items=None):
    r = {"source": src, "sign_id": sid, "split": split, "rank_true": true, "rank_pred": pred}
    if items is not None:
        r["item_judgments_pred"] = items
    return r


class CalibTest(unittest.TestCase):
    def test_mode_map_per_source(self):
        train = [rec("a1", "A", 1, 2), rec("a2", "A", 1, 2), rec("a3", "A", 2, 2), rec("a4", "A", 3, 3),
                 rec("b1", "B", 2, 2)]
        m = calib.fit(train, "mode")
        self.assertEqual(m["map"]["A"], {"2": 1, "3": 3})
        self.assertEqual(calib.apply(m, "A", 2), 1)
        self.assertEqual(calib.apply(m, "A", 4), 4)  # 学習に無い予測値はそのまま
        self.assertEqual(calib.apply(m, "B", 2), 2)
        self.assertEqual(calib.apply(m, "C", 3), 3)  # 学習に無い様式
        self.assertIsNone(calib.apply(m, "A", None))

    def test_mode_tie_prefers_closer_then_larger(self):
        m = calib.fit([rec("x", "A", 1, 3), rec("y", "A", 4, 3), rec("z", "A", 2, 3), rec("w", "A", 3, 3)], "mode")
        self.assertEqual(m["map"]["A"]["3"], 3)  # 同数なら予測に近い 3
        m = calib.fit([rec("x", "A", 1, 2), rec("y", "A", 3, 2)], "mode")
        self.assertEqual(m["map"]["A"]["2"], 3)  # 距離同じなら大きい方

    def test_shift(self):
        train = [rec("a1", "A", 1, 2), rec("a2", "A", 2, 3), rec("a3", "A", 1, 2)]
        m = calib.fit(train, "shift")
        self.assertEqual(m["map"]["A"]["delta"], -1)
        self.assertEqual(calib.apply(m, "A", 1), 1)  # 1 未満にならない
        self.assertEqual(calib.apply(m, "A", 4), 3)

    def test_none_predictions_not_used(self):
        m = calib.fit([rec("a1", "A", 1, None), rec("a2", "A", 2, 2)], "mode")
        self.assertEqual(m["n"], {"A": 1})

    def test_refuses_non_example_training(self):
        with self.assertRaises(ValueError):
            calib.fit([rec("e1", "A", 1, 1, split="eval")], "mode")

    def test_train_and_eval_sign_ids_must_not_overlap(self):
        train = {"records": [rec("a1", "A", 1, 2), rec("a2", "A", 2, 2)]}
        ev = {"records": [rec("a2", "A", 2, 2, split="eval")]}
        with self.assertRaises(ValueError):
            postproc.process(ev, "model", "mode", train)
        ev = {"records": [rec("a9", "A", 2, 2, split="eval")]}
        recs, _ = postproc.process(ev, "model", "mode", train)
        self.assertEqual(recs[0]["rank_pred"], calib.apply(calib.fit(train["records"], "mode"), "A", 2))

    def test_rank_from_applies_to_both_sides(self):
        train = {"records": [rec("a1", "A", 3, 2, items={"x": 3}), rec("a2", "A", 3, 2, items={"x": 3})]}
        ev = {"records": [rec("a3", "A", 3, 2, split="eval", items={"x": 3})]}
        recs, m = postproc.process(ev, "items", "mode", train)
        self.assertEqual(m["map"]["A"], {"3": 3})
        self.assertEqual(recs[0]["rank_pred"], 3)

    def test_items_required_when_missing(self):
        with self.assertRaises(ValueError):
            postproc.process({"records": [rec("a", "A", 1, 1)]}, "items")

    def test_spread_sites_round_robin(self):
        class It:
            def __init__(self, sid, g):
                self.sign_id, self.meta = sid, {"group_key": g}
        pool = [It(f"s{i}", "g0") for i in range(10)] + [It("t0", "g1"), It("u0", "g2")]
        got = select_spread(pool, 3, 1, "A")
        self.assertEqual({i.meta["group_key"] for i in got}, {"g0", "g1", "g2"})
        self.assertEqual(select_spread(pool, 3, 1, "A"), got)


if __name__ == "__main__":
    unittest.main()
