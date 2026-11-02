import io
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from kanban_karte.cli import format_sign, main
from kanban_karte.ledger import Sign
from tests.test_crop import GRID, make_page
from tests.test_intake import make_pdf

SAMPLE = Path(__file__).resolve().parents[1] / "data" / "sample" / "ledger.json"


def run(*args):
    out = io.StringIO()
    rc = main(list(args), out)
    return rc, out.getvalue()


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "l.sqlite")
        self.base = ["--db", self.db, "--data-dir", str(Path(self.tmp.name) / "data")]

    def tearDown(self):
        self.tmp.cleanup()

    def test_import_list_show(self):
        rc, o = run(*self.base, "ledger", "import", str(SAMPLE))
        self.assertEqual(rc, 0)
        self.assertIn("看板 3", o)
        rc, o = run(*self.base, "ledger", "list")
        self.assertIn("demo-001", o)
        self.assertIn("計 3 基", o)
        rc, o = run(*self.base, "ledger", "show", "demo-001")
        self.assertEqual(rc, 0)
        for want in ("メインサインポール", "許可: 要(期限 2027-04-30)", "3 要改善", "前回 1 良好 から悪化",
                     "避雷針の腐食、損傷: 対象外", "写真 6 枚"):
            self.assertIn(want, o)
        with redirect_stderr(io.StringIO()):
            rc, _ = run(*self.base, "ledger", "show", "no-such")
        self.assertEqual(rc, 1)

    def test_intake_command(self):
        pdf = make_pdf(Path(self.tmp.name) / "b.pdf", [make_page(), make_page(photos=GRID)])
        rc, o = run(*self.base, "intake", str(pdf))
        self.assertEqual(rc, 0)
        self.assertIn("2 ページ", o)
        self.assertIn("看板の下書き: 1 基", o)
        rc, o = run(*self.base, "intake", str(pdf))
        self.assertIn("取り込み済み", o)
        rc, o = run(*self.base, "ledger", "bundles")
        self.assertIn("b.pdf", o)
        rc, o = run(*self.base, "ledger", "list", "--bundle", "1")
        self.assertIn("計 1 基", o)

    def test_format_unknowns_are_shown_not_guessed(self):
        o = format_sign(Sign("x1"))
        self.assertIn("(名前未確定)", o)
        self.assertIn("ランク: 未確定(前回なし)", o)
        self.assertIn("許可: 未判定", o)
        self.assertIn("(未記入)", o)


if __name__ == "__main__":
    unittest.main()
