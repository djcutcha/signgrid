import json
import os
import tempfile
import unittest
from types import SimpleNamespace

from PIL import Image

from kanban_karte.intake.paper_transcribe import transcribe_paper

REPLY = {
    "sign_name": "右折進入禁止",
    "size": {"w_mm": 2700, "h_mm": 450, "d_mm": 35},
    "rows": [
        {"part": "取付部", "check": "各種取合(ボルト・ビス・接着等)", "judgment": "D"},
        {"part": "表示面", "check": "板面の割れ・外れ", "judgment": "C"},
        {"part": "基礎", "check": "錆・腐食はないか", "judgment": "B"},
        {"part": "照明", "check": "照明の不点灯", "judgment": "空"},
        {"part": "表示面", "check": "塗装の剥がれ・退色", "judgment": "対象外"},
    ],
    "remarks": "下部に錆",
}


class FakeClient:
    def __init__(self, text):
        self.messages = SimpleNamespace(create=lambda **kw: SimpleNamespace(
            content=[SimpleNamespace(type="text", text=text)]))


class PaperTranscribeTest(unittest.TestCase):
    def setUp(self):
        fd, self.img = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        Image.new("RGB", (8, 8), "white").save(self.img)
        self.addCleanup(os.unlink, self.img)

    def test_shape_and_grades(self):
        r = transcribe_paper(self.img, FakeClient("```json\n" + json.dumps(REPLY) + "\n```"))
        self.assertEqual(r["name"], "右折進入禁止")
        self.assertEqual(r["size"], "W2700 × H450 × D35 mm")
        self.assertEqual(r["items"], {"取付部-1": "良好", "取付部-2": "良好", "表示部-1": "経過観察",
                                      "基礎部・上部構造-3": "要改善"})
        self.assertEqual(r["remark"], "下部に錆")
        self.assertEqual(r["rank"], 3)

    def test_bad_json_returns_none(self):
        self.assertIsNone(transcribe_paper(self.img, FakeClient("読めません")))

    def test_no_key_returns_none(self):
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            from kanban_karte.eval import claude_judge as cj
            orig = cj.load_dotenv
            cj.load_dotenv = lambda *a, **k: None
            try:
                self.assertIsNone(transcribe_paper(self.img))
            finally:
                cj.load_dotenv = orig
        finally:
            if old is not None:
                os.environ["ANTHROPIC_API_KEY"] = old


if __name__ == "__main__":
    unittest.main()
