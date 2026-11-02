import contextlib
import io
import json
import unittest

from kanban_karte.legal import cli


def run(*argv):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = cli.main(list(argv))
    return rc, buf.getvalue()


class CliTest(unittest.TestCase):
    def test_municipality(self):
        rc, out = run("municipality", "--address", "埼玉県越谷市南越谷1-1")
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["ordinance_key"], "越谷市")
        self.assertEqual(d["municipality"], {"prefecture": "埼玉県", "city": "越谷市"})

    def test_judge_json(self):
        rc, out = run("judge", "--address", "東京都豊島区見本町1-2-3", "--use", "self", "--area", "8.5", "--height", "3.2", "--json")
        self.assertEqual(rc, 0)
        d = json.loads(out)
        self.assertEqual(d["ordinance_key"], "東京都")
        self.assertIn(d["permit_required"], ("要", "不要", "要実測"))
        self.assertIn("検算前", d["status"])

    def test_judge_text_and_none_area(self):
        rc, out = run("judge", "--municipality", "越谷市", "--use", "self", "--area", "none")
        self.assertEqual(rc, 0)
        self.assertIn("要実測", out)

    def test_judge_needs_location(self):
        with self.assertRaises(SystemExit):
            run("judge", "--use", "self")

    def test_extract_unknown_municipality(self):
        rc, _ = run("extract", "--municipality", "豊島区")
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
