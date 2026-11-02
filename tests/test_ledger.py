import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from kanban_karte.ledger import Ledger, Sign, Site
from kanban_karte.ledger.items import ITEM_KEYS, ITEM_LABELS, RESULTS, validate_items

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "ledger.json"


class SchemaTest(unittest.TestCase):
    def test_create_and_idempotent(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "sub" / "l.sqlite"  # 親フォルダも作る
            with Ledger(p) as lg:
                names = {r[0] for r in lg.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                self.assertTrue({"sites", "signs", "bundles", "pages", "rules"} <= names)
                self.assertEqual(lg.conn.execute("PRAGMA user_version").fetchone()[0], 2)
                lg.upsert_site(Site("s1", "テスト店"))
            with Ledger(p) as lg:  # 開き直しても壊れない・データが残る
                self.assertEqual(lg.get_site("s1").name, "テスト店")

    def test_v1_ledger_gets_draft_columns(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "l.sqlite"
            Ledger(p).close()
            c = sqlite3.connect(p)  # 版1の signs(下書き・確定の列なし)を作り直す
            c.execute("DROP TABLE signs")
            c.execute("CREATE TABLE signs (id TEXT PRIMARY KEY, site_id TEXT, name TEXT, type TEXT, size_w INTEGER, "
                      "size_h INTEGER, address TEXT, lat REAL, lng REAL, municipality TEXT, is_self_use INTEGER, "
                      "permit_required INTEGER, permit_expires TEXT, next_inspection TEXT, rank INTEGER, "
                      "items TEXT NOT NULL DEFAULT '{}', remark TEXT, photos TEXT NOT NULL DEFAULT '[]', "
                      "last_inspected TEXT, prev_rank INTEGER, source_bundle INTEGER, "
                      "created TEXT NOT NULL DEFAULT (datetime('now')), updated TEXT NOT NULL DEFAULT (datetime('now')))")
            c.execute("INSERT INTO signs(id, rank) VALUES ('old', 2)")
            c.execute("PRAGMA user_version = 1")
            c.commit()
            c.close()
            with Ledger(p) as lg:
                s = lg.get_sign("old")
                self.assertEqual((s.rank, s.status, s.evidence), (2, "new", {}))
                self.assertIsNone(s.height_m)        # 全高の列は ALTER で足され、無ければ NULL
                s.height_m = 4.5
                lg.upsert_sign(s)
                self.assertEqual(lg.get_sign("old").height_m, 4.5)

    def test_newer_schema_refused(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "l.sqlite"
            Ledger(p).close()
            c = sqlite3.connect(p)
            c.execute("PRAGMA user_version = 99")
            c.commit()
            c.close()
            with self.assertRaises(RuntimeError):
                Ledger(p)

    def test_sign_columns_match_spec(self):
        with Ledger() as lg:
            cols = {r["name"] for r in lg.conn.execute("PRAGMA table_info(signs)")}
        want = {"id", "site_id", "name", "type", "size_w", "size_h", "address", "lat", "lng", "municipality",
                "is_self_use", "permit_required", "permit_expires", "next_inspection",
                "rank", "items", "remark", "photos", "last_inspected", "prev_rank", "source_bundle"}
        self.assertTrue(want <= cols, want - cols)


class SignTest(unittest.TestCase):
    def test_upsert_roundtrip_and_update(self):
        with Ledger() as lg:
            lg.upsert_site(Site("s1", "店", "東京都豊島区", 35.7, 139.7, "東京都豊島区"))
            s = Sign("a1", site_id="s1", name="ポール", type="自立", size_w=1950, size_h=600,
                     is_self_use=True, permit_required=False, permit_expires=None, rank=3,
                     items={"支持部-1": "要改善"}, remark="腐食", photos=["x.jpg"], prev_rank=1)
            lg.upsert_sign(s)
            got = lg.get_sign("a1")
            self.assertEqual(got, s)
            self.assertIs(got.is_self_use, True)
            self.assertIs(got.permit_required, False)  # False と None を区別する
            self.assertIsNone(got.permit_expires)
            s.rank = 2
            s.items = {"支持部-1": "経過観察", "表示部-4": "良好"}
            lg.upsert_sign(s)
            self.assertEqual(len(lg.list_signs()), 1)  # 同じ id は更新
            self.assertEqual(lg.get_sign("a1").rank, 2)
            self.assertEqual(lg.get_sign("a1").items["表示部-4"], "良好")
            self.assertIsNone(lg.get_sign("nope"))

    def test_none_columns_stay_none(self):
        with Ledger() as lg:
            lg.upsert_sign(Sign("b1"))
            g = lg.get_sign("b1")
            self.assertEqual((g.rank, g.is_self_use, g.size_w, g.items, g.photos), (None, None, None, {}, []))

    def test_validation(self):
        with Ledger() as lg:
            for bad in (Sign("x", rank=5), Sign("x", rank=0), Sign("x", prev_rank=9), Sign("x", rank=True),
                        Sign("x", items={"存在しない-1": "良好"}), Sign("x", items={"支持部-1": "まあまあ"})):
                with self.assertRaises(ValueError):
                    lg.upsert_sign(bad)
            self.assertEqual(lg.list_signs(), [])

    def test_site_filter_and_foreign_key(self):
        with Ledger() as lg:
            lg.upsert_site(Site("s1", "A"))
            lg.upsert_sign(Sign("a", site_id="s1"))
            lg.upsert_sign(Sign("b"))
            self.assertEqual([s.id for s in lg.list_signs(site_id="s1")], ["a"])
            with self.assertRaises(sqlite3.IntegrityError):
                lg.upsert_sign(Sign("c", site_id="no-such-site"))

    def test_check_constraints_in_db(self):
        with Ledger() as lg:
            with self.assertRaises(sqlite3.IntegrityError):
                lg.conn.execute("INSERT INTO signs(id, rank) VALUES ('z', 7)")


class ItemsTest(unittest.TestCase):
    def test_18_items_from_tokyo_form(self):
        self.assertEqual(len(ITEM_KEYS), 18)
        self.assertEqual(len(set(ITEM_KEYS)), 18)
        self.assertEqual(RESULTS, ("良好", "経過観察", "要改善", "対象外"))
        self.assertIn("避雷針の腐食、損傷", ITEM_LABELS["その他-2"])
        self.assertEqual(validate_items({"支持部-1": "良好"}), {"支持部-1": "良好"})


class RulesAndBundlesTest(unittest.TestCase):
    def test_rules(self):
        with Ledger() as lg:
            a = lg.add_rule("錆は表面のみなら経過観察", source="demo-001 の訂正")
            b = lg.add_rule("支柱根元の錆は要改善", conflicts_with=[a])
            rows = lg.list_rules()
            self.assertEqual([r["id"] for r in rows], [a, b])
            self.assertEqual(json.loads(rows[1]["conflicts_with"]), [a])

    def test_bundle_and_pages(self):
        with Ledger() as lg:
            b = lg.add_bundle("x.pdf", "abc")
            with self.assertRaises(sqlite3.IntegrityError):
                lg.add_bundle("y.pdf", "abc")  # 同じ sha256 は二重に取り込まない
            lg.upsert_page(b, 1, kind="指示書")
            lg.upsert_page(b, 2, kind="完成報告書", image_path="p.jpg", dpi=300.0)
            lg.upsert_page(b, 2, kind="完成報告書", image_path="q.jpg", dpi=300.0)  # 更新
            with self.assertRaises(ValueError):
                lg.upsert_page(b, 3, kind="へんな種別")
            self.assertEqual(lg.kind_counts(b), {"完成報告書": 1, "指示書": 1})
            self.assertEqual(lg.list_pages(b)[1]["image_path"], "q.jpg")
            lg.set_bundle(b, status="classified", kind_counts=lg.kind_counts(b))
            self.assertEqual(lg.get_bundle(b)["status"], "classified")
            with self.assertRaises(sqlite3.IntegrityError):
                lg.set_bundle(b, status="なぞ")


class SampleDataTest(unittest.TestCase):
    def test_sample_loads_and_is_fictional(self):
        with Ledger() as lg:
            n_sites, n_signs = lg.import_json(SAMPLE)
            self.assertEqual((n_sites, n_signs), (1, 3))
            signs = lg.list_signs()
            self.assertEqual(len(signs), 3)
            for s in signs:
                self.assertTrue(all(Path(p).exists() for p in s.photos), s.id)  # 無地の代用画像がある
            self.assertEqual(lg.get_site("demo-site-1").name, "見本ストア 北見台店")
            self.assertEqual(lg.get_sign("demo-001").rank, 3)
            self.assertEqual(len(lg.get_sign("demo-001").items), 18)


if __name__ == "__main__":
    unittest.main()


class RecentRulesTest(unittest.TestCase):
    def test_latest_n_oldest_first(self):
        with Ledger() as lg:
            for i in range(25):
                lg.add_rule(f"r{i}")
            self.assertEqual(lg.recent_rules(3), ["r22", "r23", "r24"])
            self.assertEqual(len(lg.recent_rules()), 20)
            self.assertEqual(lg.recent_rules()[0], "r5")
