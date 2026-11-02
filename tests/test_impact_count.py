import unittest

from kanban_karte.impact import count as C

# (指示書ID, 件名, 仕様文, 物件の得意先名, 得意先住所, 見積の得意先名)
ROWS = [
    ("1", "社名看板", "壁面看板 W1000×H500", "山田商店", "東京都豊島区北大塚2-2-10", None),      # 自家用・小さい → 不要
    ("2", "広告塔", "W1000×H3000", "鈴木商店", "東京都豊島区北大塚2-2-10", None),                 # 一般 → 要
    ("3", "看板", "W900×H900", "佐藤商店", "東京都豊島区北大塚2-2-10", None),                    # 用途不明
    ("4", "社名看板", "壁面看板", "田中商店", "さいたま市大宮区1-1", None),                      # 寸法なし → 要実測
    ("5", "社名看板", "壁面看板 W1000×H500", "広告社", "東京都豊島区北大塚2-2-10", None),        # 代理店(一般語)
    ("6", "シート貼り", "W400×H300", "山田商店", "東京都豊島区北大塚2-2-10", None),              # 看板でない
    ("7", "社名看板", "壁面看板 W1000×H500", "山田商店", "神奈川県横浜市1-1", None),              # 対象外
    ("8", "社名看板", "壁面看板 W1000×H500", "山田商店", "東京都豊島区北大塚2-2-10", "ABC工房"),  # 見積側が代理店
]


class AgencyTest(unittest.TestCase):
    def test_matcher(self):
        m = C.AgencyMatcher(("工房",))
        self.assertTrue(m.is_agency("株式会社 ABC看板"))
        self.assertTrue(m.is_agency("山田", "ABC工房"))
        self.assertTrue(m.is_agency("", None))        # 名前なしは住所未確定側
        self.assertFalse(m.is_agency("山田商店"))


class RunTest(unittest.TestCase):
    def setUp(self):
        self.rep = C.run("x.db", agency_words=("ABC工房",), rows=iter(ROWS))

    def test_totals(self):
        r = self.rep
        self.assertEqual(r.total_instructions, 8)
        self.assertEqual(r.agency_instructions, 2)
        self.assertEqual(r.agency_signs, 2)

    def test_tokyo(self):
        g = self.rep.groups["東京都"]
        self.assertEqual(g.instructions, 4)       # 1,2,3,6
        self.assertEqual(g.base, 3)               # 1,2,3
        self.assertEqual(g.with_size, 3)
        self.assertEqual(g.outcome[C.SELF_EXEMPT], 1)
        self.assertEqual(g.outcome[C.PERMIT], 1)
        self.assertEqual(g.outcome[C.USE_UNKNOWN], 1)
        self.assertEqual(sum(g.outcome.values()), g.base)

    def test_saitama_no_size(self):
        g = self.rep.groups["さいたま市"]
        self.assertEqual(g.base, 1)
        self.assertEqual(g.with_size, 0)
        self.assertEqual(g.outcome[C.MEASURE], 1)

    def test_other_prefecture_not_judged(self):
        self.assertEqual(self.rep.groups[C.OTHER].base, 0)
        self.assertEqual(self.rep.groups[C.OTHER].instructions, 1)

    def test_render(self):
        txt = C.render(self.rep)
        self.assertIn("東京都", txt)
        self.assertIn("住所未確定", txt)


if __name__ == "__main__":
    unittest.main()
