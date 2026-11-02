import unittest

from kanban_karte.eval import dataset
from kanban_karte.eval.dataset import EvalItem
from kanban_karte.eval.judge import ConstantJudge, OracleJudge, Prediction
from kanban_karte.eval import metrics


class RankMetricsTest(unittest.TestCase):
    # 手作り: 正解 [1,2,3,4,3,2,1,4], 予測 [1,3,2,4,1,2,4,3]
    T = [1, 2, 3, 4, 3, 2, 1, 4]
    P = [1, 3, 2, 4, 1, 2, 4, 3]

    def test_values(self):
        m = metrics.rank_metrics(self.T, self.P)
        self.assertEqual(m["n"], 8)
        self.assertEqual(m["exact"], 3)  # 位置0,3,5
        self.assertAlmostEqual(m["exact_rate"], 3 / 8)
        # ±1: 0:o 1:o(2→3) 2:o(3→2) 3:o 4:x(3→1) 5:o 6:x(1→4) 7:o(4→3) → 6
        self.assertEqual(m["within1"], 6)
        # 要対応の正解: 位置2,3,4,7 の4基。予測が3/4でない: 位置2(2),4(1) → 見逃し2
        self.assertEqual(m["action_needed_true"], 4)
        self.assertEqual(m["action_needed_missed"], 2)
        self.assertAlmostEqual(m["miss_rate"], 0.5)
        # 要対応でない正解: 位置0,1,5,6 の4基。予測が3/4: 位置1(3),6(4) → 誤警報2
        self.assertEqual(m["not_action_true"], 4)
        self.assertEqual(m["false_alarm"], 2)
        self.assertAlmostEqual(m["false_alarm_rate"], 0.5)
        # 予測が要対応: 位置1,3,6,7 の4基、うち正解も要対応: 3,7 → 適合率 2/4
        self.assertAlmostEqual(m["action_precision"], 0.5)

    def test_confusion(self):
        m = metrics.rank_metrics(self.T, self.P)["confusion"]
        self.assertEqual(m, [[1, 0, 0, 1],
                             [0, 1, 1, 0],
                             [1, 1, 0, 0],
                             [0, 0, 1, 1]])
        self.assertEqual(sum(map(sum, m)), 8)

    def test_no_answer(self):
        m = metrics.rank_metrics([3, 2, 1], [None, 2, None])
        self.assertEqual(m["no_answer"], 2)
        self.assertEqual(m["exact"], 1)
        self.assertEqual(m["within1"], 1)
        self.assertEqual(m["miss_rate"], 1.0)  # 正解3に答えなし=見逃し
        self.assertEqual(m["false_alarm"], 0)
        self.assertIsNone(m["action_precision"])  # 予測の要対応が0件
        self.assertEqual(sum(map(sum, m["confusion"])), 1)

    def test_empty_denominators(self):
        m = metrics.rank_metrics([1, 2], [1, 2])
        self.assertIsNone(m["miss_rate"])  # 要対応の正解が0件
        self.assertEqual(m["false_alarm_rate"], 0.0)
        self.assertEqual(metrics.rank_metrics([], [])["exact_rate"], None)


class ItemMetricsTest(unittest.TestCase):
    def test_agreement(self):
        pairs = [
            ({"a": 1, "b": 2, "c": 3}, {"a": 1, "b": 1, "c": 3}),  # 3項目、一致2
            ({"a": 4}, None),  # 予測なし: 1項目が「判断できず」
            (None, {"a": 1}),  # 正解なし: 対象外
            ({"a": 2, "b": 2}, {"a": 2}),  # b 欠落 = 判断できず、一致1
            ({"a": 3}, {"a": None}),  # null も判断できず
        ]
        m = metrics.item_metrics(pairs)
        self.assertEqual(m["signs_with_item_truth"], 4)
        self.assertEqual(m["signs_with_item_pred"], 3)
        self.assertEqual(m["signs_without_item_pred"], 1)
        self.assertEqual(m["cells"], 7)
        self.assertEqual(m["cells_correct"], 3)
        self.assertEqual(m["cells_unanswered"], 3)
        self.assertEqual(m["signs_with_any_unanswered"], 3)
        self.assertAlmostEqual(m["cells_unanswered_rate"], 3 / 7)
        self.assertAlmostEqual(m["item_agreement_rate"], 3 / 7)  # 判断できずは不一致扱い
        self.assertAlmostEqual(m["item_agreement_answered_only_rate"], 3 / 4)  # 答えた4項目のうち3つ一致

    def test_answered_only_exact(self):
        m = metrics.rank_metrics([2, 3, 1, 4], [2, None, 2, None])
        self.assertAlmostEqual(m["exact_rate"], 1 / 4)
        self.assertAlmostEqual(m["exact_answered_only_rate"], 1 / 2)
        self.assertEqual(m["no_answer"], 2)


def _item(rank, ij=None, source="paper_scan", sid="x"):
    return EvalItem(source, sid, [], rank, ij, "eval")


class ComputeTest(unittest.TestCase):
    def test_excludes_no_truth(self):
        items = [_item(1), _item(None), _item(3), _item(None)]
        pairs = [(it, ConstantJudge(2).judge(it)) for it in items]
        m = metrics.compute(pairs)
        self.assertEqual(m["n_input"], 4)
        self.assertEqual(m["n_excluded_no_truth"], 2)
        self.assertEqual(m["n"], 2)
        self.assertEqual(m["exact"], 0)
        self.assertEqual(m["within1"], 2)
        self.assertEqual(m["miss_rate"], 1.0)

    def test_oracle_is_perfect(self):
        items = [_item(r, {"k": r}) for r in (1, 2, 3, 4, 4, 2)] + [_item(None)]
        m = metrics.compute([(it, OracleJudge().judge(it)) for it in items])
        self.assertEqual(m["exact_rate"], 1.0)
        self.assertEqual(m["miss_rate"], 0.0)
        self.assertEqual(m["false_alarm_rate"], 0.0)
        self.assertEqual(m["items"]["item_agreement_rate"], 1.0)

    def test_constant_item_values(self):
        it = _item(3, {"k1": 1, "k2": 3})
        pr = ConstantJudge(2).judge(it)
        self.assertEqual(pr.item_judgments_pred, {"k1": 2, "k2": 2})
        self.assertIsNone(ConstantJudge(2).judge(_item(3)).item_judgments_pred)


class DatasetHelperTest(unittest.TestCase):
    def test_split_deterministic_and_balanced(self):
        keys = [f"k{i}" for i in range(2000)]
        a = [dataset.assign_split(k) for k in keys]
        self.assertEqual(a, [dataset.assign_split(k) for k in keys])
        share = a.count("eval") / len(a)
        self.assertTrue(0.66 < share < 0.74, share)

    def test_parse_item_judgments(self):
        raw = ('{"a":"良好","b":"対象外","c":"","d":"良好/経過観察","e":"C","f":"即時修理",'
               '"g":"看板A\\n照明B"}')
        out, dropped = dataset._parse_item_judgments(raw)
        self.assertEqual(out, {"a": 1, "e": 3, "f": 4})
        self.assertEqual(dropped, 2)

    def test_scan_pages(self):
        self.assertEqual(dataset._scan_pages("11R"), [11])
        self.assertEqual(dataset._scan_pages("3L,3R"), [3])
        self.assertEqual(dataset._scan_pages("9,10,11"), [9, 10, 11])
        self.assertEqual(dataset._scan_pages(""), [])


if __name__ == "__main__":
    unittest.main()
