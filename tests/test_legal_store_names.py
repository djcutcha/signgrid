"""store_names のオフライン検査(チェーン名・件名は架空。実データは読まない)。"""
import sqlite3
import tempfile
import unittest
from pathlib import Path

from kanban_karte.legal import store_names as SN

CHAINS = ["見本マート", "架空ストア"]
RX = SN.chain_regex(CHAINS)


def name(s):
    r = SN.extract_store_name(s, RX)
    return None if r is None else (r["kind"], r["name"])


class ExtractTest(unittest.TestCase):
    def test_chain_forms(self):
        for s, want in (
            ("見本マート北原点検", "見本マート北原"),
            ("見本マート東浦店点検", "見本マート東浦店"),
            ("見本マート　南町店　点検作業", "見本マート南町店"),
            ("見本マート 池上8丁目点検\r\n", "見本マート池上8丁目"),
            ("架空ストア新座栗原店　点検", "架空ストア新座栗原店"),
            ("見本マート西口\r\n看板点検", "見本マート西口"),
            ("見本マート西口営業所 点検", "見本マート西口営業所"),
        ):
            with self.subTest(s=s):
                self.assertEqual(name(s), ("chain", want))

    def test_fullwidth_is_normalized(self):
        self.assertEqual(name("見本マート　ＡＢ店点検"), ("chain", "見本マートAB店"))

    def test_generic_forms(self):
        for s, want in (("若葉店　自立サイン点検", "若葉店"), ("某社 港支店点検", "港支店"),
                        ("某社川辺センター・看板点検", "某社川辺センター"), ("個別学院 青葉校 点検", "青葉校")):
            with self.subTest(s=s):
                self.assertEqual(name(s), ("generic", want))

    def test_not_a_store(self):
        for s in ("見本ビル　袖看板点検", "屋上看板点検", "新宿点検", "2024年 自立サイン点検作業", "", None,
                  "見本マート点検", "見本マート3月 点検", "見本マート2回目 点検"):
            with self.subTest(s=s):
                self.assertIsNone(name(s))

    def test_longest_chain_first(self):
        rx = SN.chain_regex(["見本", "見本マート"])
        r = SN.extract_store_name("見本マート北原点検", rx)
        self.assertEqual(r["chain"], "見本マート")

    def test_no_chains_means_generic_only(self):
        self.assertIsNone(SN.extract_store_name("見本マート北原点検", None))
        self.assertEqual(SN.extract_store_name("北原店 点検", None)["kind"], "generic")


class CountTest(unittest.TestCase):
    def test_count_has_no_names(self):
        subs = ["見本マート北原点検", "見本マート南町店点検", "北原店 点検", "見本ビル 点検", ""]
        r = SN.count_names(subs, CHAINS)
        self.assertEqual((r["subjects"], r["matched"], r["by_kind"]), (5, 3, {"chain": 2, "generic": 1}))
        self.assertEqual(r["ratio"], 0.6)
        self.assertNotIn("北原", str(r))

    def test_load_chains(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.txt"
            p.write_text("# メモ\nあい\nあいう\n\n", encoding="utf-8")
            self.assertEqual(SN.load_chains(p), ["あいう", "あい"])
            self.assertEqual(SN.load_chains(Path(d) / "none.txt"), [])


class DbTest(unittest.TestCase):
    def test_subjects_filter_and_readonly(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "t.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE r_shijisyo (sSHIJISYO TEXT, dJUUTYU TEXT)")
            con.executemany("INSERT INTO r_shijisyo VALUES (?,?)", [
                ("A店 点検", "2021-12-31"), ("B店 点検", "2022-01-01"), ("C店 製作", "2023-01-01"), ("D店 点検", "2024-05-05")])
            con.commit()
            con.close()
            self.assertEqual(SN.inspection_subjects(db), ["B店 点検", "D店 点検"])
            self.assertEqual(len(SN.inspection_subjects(db, since="2000-01-01")), 3)
            with self.assertRaises(sqlite3.OperationalError):  # 読み取り専用で開く
                c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
                c.execute("INSERT INTO r_shijisyo VALUES ('x','y')")


if __name__ == "__main__":
    unittest.main()
