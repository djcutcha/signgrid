"""rules_extract のオフライン検査(API は呼ばない。偽クライアントで流れだけ確かめる)。"""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from kanban_karte.legal import rules_extract as RX
from kanban_karte.legal import schema as S


class CleanTextTest(unittest.TestCase):
    def test_drops_duplicate_trailer_and_junk(self):
        raw = "A条例\n第一条\n本文です\n\n\n\n第二条\n続き\niPadLoadStyle(\"x\")\n条項目次\n沿革\n第一条\n本文です\ne000001281\n"
        # 「条項目次」は行頭にあるときだけ切る。先頭行になる場合を避けるため改行が前に要る
        out = RX.clean_text(raw)
        self.assertIn("第二条", out)
        self.assertNotIn("沿革", out)
        self.assertNotIn("iPadLoadStyle", out)
        self.assertNotIn("\n\n\n", out)

    def test_real_files_are_trimmed(self):
        for key in RX.ORDINANCE_FILES:
            with self.subTest(key=key):
                texts = RX.load_texts(key)
                for role, t in texts.items():
                    self.assertGreater(len(t), 10000, role)
                    self.assertNotIn("条項目次", t)
                    self.assertNotIn("体系情報", t)
                    self.assertNotIn('id="primary"', t)

    def test_manifest(self):
        m = RX.manifest()
        self.assertEqual(len(m), 8)
        self.assertTrue(all(v["mtime"] == "2026-10-20" for v in m.values()))


class VerifyCitationTest(unittest.TestCase):
    def test_verify(self):
        texts = {"条例": "第一条\nこの条例は、\n屋外広告物について定める。", "施行規則": "第二条 五平方メートル以下"}
        rules = {"a": {"citations": [
            {"source": "条例", "article": "第1条", "quote": "この条例は、屋外広告物について"},   # 改行・空白差は無視
            {"source": "施行規則", "article": "第2条", "quote": "十平方メートル以下"},          # 原文に無い
            {"source": "条例", "article": "第1条", "quote": "五平方メートル以下"},              # 別の文書の文
        ]}, "b": [{"citations": [{"source": "施行規則", "article": "第2条", "quote": "五平方メートル以下"}]}]}
        r = RX.verify_citations(rules, texts)
        self.assertEqual(r, {"total": 4, "verified": 2})
        flags = [c["quote_verified"] for c in RX.iter_citations(rules)]
        self.assertEqual(flags, [True, False, False, True])


class ParseTest(unittest.TestCase):
    def test_parse_variants(self):
        self.assertEqual(RX.parse_json_object('{"a": 1}'), {"a": 1})
        self.assertEqual(RX.parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})
        self.assertEqual(RX.parse_json_object('前置き {"a": [1, 2]} 後書き'), {"a": [1, 2]})
        for bad in ("", "[]", "{", "no json"):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                RX.parse_json_object(bad)

    def test_cost(self):
        self.assertAlmostEqual(RX.cost_usd("claude-sonnet-5-5", 1_000_000, 100_000), 3.0)


def _rules_from_file(key="越谷市"):
    return json.loads((RX.DATA_DIR / f"rules_{key}.json").read_text(encoding="utf-8"))["rules"]


class _Stream:
    def __init__(self, msg): self.msg = msg
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self.msg


class FakeClient:
    def __init__(self, texts, stop_reasons=None):
        self.texts, self.calls = list(texts), []
        self.stop_reasons = list(stop_reasons or [])
        self.messages = self

    def stream(self, **kw):
        self.calls.append(kw)
        stop = self.stop_reasons.pop(0) if self.stop_reasons else "end_turn"
        msg = SimpleNamespace(content=[SimpleNamespace(type="text", text=self.texts.pop(0))], stop_reason=stop,
                              usage=SimpleNamespace(input_tokens=1000, output_tokens=200))
        return _Stream(msg)


class ExtractFlowTest(unittest.TestCase):
    def good(self):
        rules = copy.deepcopy(_rules_from_file())
        for c in RX.iter_citations(rules):
            c.pop("quote_verified", None)
        return json.dumps({"rules": rules, "extraction_notes": ["メモ"]}, ensure_ascii=False)

    def test_success_first_try(self):
        c = FakeClient([self.good()])
        doc, usage = RX.extract("越谷市", client=c)
        self.assertEqual(S.validate_file(doc), [])
        self.assertEqual(doc["status"], S.STATUS_FIRST_DRAFT)
        self.assertEqual(doc["citation_check"]["verified"], doc["citation_check"]["total"])
        self.assertEqual(usage["attempts"], 1)
        kw = c.calls[0]
        self.assertEqual(kw["model"], "claude-sonnet-5-5")
        for banned in ("temperature", "top_p", "top_k"):
            self.assertNotIn(banned, kw)
        self.assertIn("越谷市", kw["messages"][0]["content"])

    def test_retry_on_broken_json_then_ok(self):
        c = FakeClient(['{"rules": {', self.good()])
        doc, usage = RX.extract("越谷市", client=c)
        self.assertEqual(usage["attempts"], 2)
        self.assertEqual(len(usage["attempt_errors"]), 1)
        # 2回目のメッセージにエラー文が載る
        self.assertIn("前の出力は使えません", c.calls[1]["messages"][-1]["content"])

    def test_retry_on_schema_violation(self):
        bad = json.loads(self.good())
        bad["rules"]["inspection"]["required"] = "maybe"
        c = FakeClient([json.dumps(bad, ensure_ascii=False), self.good()])
        _, usage = RX.extract("越谷市", client=c)
        self.assertEqual(usage["attempts"], 2)
        self.assertIn("inspection.required", usage["attempt_errors"][0])

    def test_gives_up_after_max_attempts(self):
        with self.assertRaises(RuntimeError):
            RX.extract("越谷市", client=FakeClient(["x", "y"]))

    def test_fabricated_quote_is_flagged_not_hidden(self):
        d = json.loads(self.good())
        d["rules"]["prohibited_zones"]["citations"][0]["quote"] = "原文に存在しない作り話の条文"
        doc, _ = RX.extract("越谷市", client=FakeClient([json.dumps(d, ensure_ascii=False)]))
        self.assertEqual(doc["citation_check"]["total"] - doc["citation_check"]["verified"], 1)
        self.assertFalse(doc["rules"]["prohibited_zones"]["citations"][0]["quote_verified"])

    def test_refusal_and_truncation(self):
        with self.assertRaises(RuntimeError):
            RX.extract("越谷市", client=FakeClient([""], stop_reasons=["refusal"]))
        with self.assertRaises(RuntimeError):
            RX.extract("越谷市", client=FakeClient([self.good(), self.good()], stop_reasons=["max_tokens", "max_tokens"]))

    def test_save_and_load_roundtrip(self):
        doc, _ = RX.extract("越谷市", client=FakeClient([self.good()]))
        with tempfile.TemporaryDirectory() as d:
            p = RX.save_rules(doc, Path(d))
            self.assertEqual(p.name, "rules_越谷市.json")
            self.assertEqual(RX.load_rules("越谷市", Path(d))["ordinance_key"], "越谷市")
            with self.assertRaises(FileNotFoundError):
                RX.load_rules("東京都", Path(d))


if __name__ == "__main__":
    unittest.main()
