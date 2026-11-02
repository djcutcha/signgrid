import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import eval_jitter  # noqa: E402


def rec(t, p):
    return {"rank_true": t, "rank_pred": p}


class JitterTest(unittest.TestCase):
    def test_compare(self):
        a = {"a": rec(1, 2), "b": rec(3, 2), "c": rec(2, 2), "d": rec(4, 4), "e": rec(1, 1)}
        b = {"a": rec(1, 1), "b": rec(3, 3), "c": rec(2, 3), "d": rec(4, None), "z": rec(1, 1)}
        r = eval_jitter.compare(a, b)
        self.assertEqual((r["common"], r["only_a"], r["only_b"]), (4, 1, 1))
        self.assertEqual(r["pred_changed"], 4)
        self.assertEqual((r["exact_hit_to_miss"], r["exact_miss_to_hit"]), (2, 2))
        self.assertEqual((r["miss_a"], r["miss_b"], r["miss_changed"]), (1, 1, 2))
        self.assertEqual((r["false_alarm_a"], r["false_alarm_b"], r["false_alarm_changed"]), (0, 1, 1))


if __name__ == "__main__":
    unittest.main()
