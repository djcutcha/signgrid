"""同じ台帳ファイルに 2 つの接続で書く(LINE の自動下書きのスレッドと webhook が重なる場合)。"""
import tempfile
import threading
import time
import unittest
from pathlib import Path

from kanban_karte.ledger.db import Ledger


class TwoWritersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "l.sqlite"
        self.a = Ledger(self.path)
        self.a.conn.execute("CREATE TABLE t (who TEXT)")
        self.a.conn.commit()

    def tearDown(self):
        self.a.conn.close()
        self.tmp.cleanup()

    def test_busy_timeout_is_30s(self):
        self.assertEqual(self.a.conn.execute("PRAGMA busy_timeout").fetchone()[0], 30000)

    def test_second_writer_waits_for_lock(self):
        # a が書き込みのロックを持ったまま 0.5 秒置いてから commit。その間に別スレッドで b(別接続)が書く
        self.a.conn.execute("BEGIN IMMEDIATE")
        self.a.conn.execute("INSERT INTO t VALUES ('draft')")
        out = {}

        def write_b():
            t0 = time.monotonic()
            b = Ledger(self.path)  # sqlite3 の接続は作ったスレッドでしか使えない
            try:
                b.conn.execute("INSERT INTO t VALUES ('webhook')")
                b.conn.commit()
                out["waited"] = time.monotonic() - t0
            except Exception as e:  # database is locked など
                out["error"] = e
            finally:
                b.conn.close()

        th = threading.Thread(target=write_b)
        th.start()
        time.sleep(0.5)
        self.a.conn.commit()
        th.join(10)
        self.assertNotIn("error", out)
        self.assertGreaterEqual(out["waited"], 0.4)  # ロックが外れるまで待ってから書けた
        rows = sorted(r[0] for r in self.a.conn.execute("SELECT who FROM t"))
        self.assertEqual(rows, ["draft", "webhook"])

if __name__ == "__main__":
    unittest.main()
