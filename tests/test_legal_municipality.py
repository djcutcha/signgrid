import unittest

from kanban_karte.legal.municipality import (
    AddressResolver, Municipality, SAITAMA_MUNICIPALITIES, TOKYO_MUNICIPALITIES, NullResolver,
    coverage_note, normalize_address, ordinance_key,
)

R = AddressResolver()

# (住所, 都道府県, 市区町村, 条例キー)
CASES = [
    ("東京都豊島区見本町1-2-3", "東京都", "豊島区", "東京都"),
    ("東京都千代田区丸の内1-1-1", "東京都", "千代田区", "東京都"),
    ("東京都中央区銀座4-1", "東京都", "中央区", "東京都"),            # 都道府県付きなら 中央区 も確定
    ("〒163-8001 東京都新宿区西新宿2-8-1", "東京都", "新宿区", "東京都"),
    ("東京都八王子市元本郷町3-24-1", "東京都", "八王子市", "東京都"),
    ("東京都西東京市中町1-5-1", "東京都", "西東京市", "東京都"),     # 東京 は 西東京市 と 東久留米市 等の長い名前を取り違えない
    ("東京都西多摩郡日の出町大字平井2780", "東京都", "日の出町", "東京都"),
    ("東京都小笠原村父島字西町", "東京都", "小笠原村", "東京都"),
    ("埼玉県越谷市南越谷1-1-1", "埼玉県", "越谷市", "越谷市"),
    ("〒343-0000 埼玉県 越谷市 東越谷 1-2", "埼玉県", "越谷市", "越谷市"),
    ("埼玉県さいたま市浦和区高砂3-15-1", "埼玉県", "さいたま市", "さいたま市"),
    ("埼玉県さいたま市大宮区桜木町1-7", "埼玉県", "さいたま市", "さいたま市"),
    ("埼玉県川口市青木2-1-1", "埼玉県", "川口市", "埼玉県"),          # 市独自条例の有無は未確認 -> 県
    ("埼玉県入間郡三芳町大字藤久保1100-1", "埼玉県", "三芳町", "埼玉県"),
    ("埼玉県ふじみ野市福岡1-1-1", "埼玉県", "ふじみ野市", "埼玉県"),
    ("埼玉県", "埼玉県", None, "埼玉県"),
    ("越谷市東越谷10-1", "埼玉県", "越谷市", "越谷市"),               # 都道府県なしでも 越谷市 は一意
    ("豊島区南池袋1-1", "東京都", "豊島区", "東京都"),                # 20区は一意
    ("ＴＥＬ 東京都豊島区", None, None, None),                         # 先頭が住所でない -> 断定しない
    ("千葉県市川市八幡1-1", "千葉県", None, None),                    # 市川市 を 市 と取り違えない / 条例は未取得
]


class ResolveTest(unittest.TestCase):
    def test_cases(self):
        self.assertGreaterEqual(len(CASES), 20)
        for addr, pref, city, key in CASES:
            with self.subTest(addr=addr):
                m = R.resolve(addr)
                if pref is None:
                    self.assertIsNone(m)
                    self.assertIsNone(ordinance_key(m))
                    continue
                self.assertEqual((m.prefecture, m.city), (pref, city))
                self.assertEqual(ordinance_key(m), key)

    def test_ambiguous_without_prefecture_is_none(self):
        # 中央区・港区・北区・府中市 は他県にもある。都道府県が無ければ推測しない
        for a in ("中央区銀座4-1", "港区芝公園4-2-8", "北区王子1-1", "府中市宮町1-1", "", "   ", None):
            with self.subTest(a=a):
                self.assertIsNone(R.resolve(a))

    def test_other_prefecture_has_no_city(self):
        m = R.resolve("愛知県四日市市")  # 三重の 四日市市 を誤読しないための型。愛知県としては市は取らない
        self.assertEqual(m, Municipality("愛知県", None))
        self.assertIsNone(ordinance_key(m))
        self.assertIn("未取得", coverage_note(m))

    def test_coverage_notes(self):
        self.assertIsNone(coverage_note(R.resolve("埼玉県越谷市1-1")))
        self.assertIn("市独自条例の有無は未確認", coverage_note(R.resolve("埼玉県川口市1-1")))
        self.assertIn("特定できない", coverage_note(None))

    def test_normalize(self):
        self.assertEqual(normalize_address("〒１００－０００１　東京都 千代田区"), "東京都千代田区")

    def test_tables(self):
        self.assertEqual(len(TOKYO_MUNICIPALITIES), 62)
        self.assertEqual(len(SAITAMA_MUNICIPALITIES), 63)
        self.assertEqual(len(set(TOKYO_MUNICIPALITIES)), 62)
        self.assertEqual(len(set(SAITAMA_MUNICIPALITIES)), 63)

    def test_null_resolver(self):
        self.assertIsNone(NullResolver().resolve("東京都豊島区"))


if __name__ == "__main__":
    unittest.main()
