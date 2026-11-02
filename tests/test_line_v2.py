"""LINE 入口 v2: 返信ログ、現場の選択(クイックリプライ/postback)、位置の付け替え、リッチメニュー、住所検索。通信はすべてモック。"""
import io
import json
import unittest
import urllib.error
from unittest import mock

from kanban_karte.intake.line import client as line_client
from tests.test_line import USER, Base, img_event, jpeg


class _Resp(io.BytesIO):
    def __init__(self, body=b"{}", status=200):
        super().__init__(body)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ReplyLogTest(Base):
    def test_ok_logged_with_bundle(self):
        self.client.contents["m1"] = jpeg()
        with self.assertLogs("kanban_karte.line", "INFO") as cm:
            self.post(self.app(), img_event("m1"))
        self.assertTrue(any("reply ok 1通 bundle=#1" in m for m in cm.output), cm.output)

    def test_failure_logged(self):
        self.client.contents["m1"] = jpeg()

        def boom(token, text, **kw):
            raise IOError("LINE 側のエラー")
        self.client.reply = boom
        with self.assertLogs("kanban_karte.line", "ERROR") as cm:
            self.post(self.app(), img_event("m1"))
        self.assertTrue(any("reply failed bundle=#1" in m and "LINE 側のエラー" in m for m in cm.output))


class HttpClientLogTest(unittest.TestCase):
    def test_success_logs_status(self):
        c = line_client.HttpLineClient("tok")
        with mock.patch("urllib.request.urlopen", return_value=_Resp()) as op, \
                self.assertLogs("kanban_karte.line", "INFO") as cm:
            c.reply("rt", "こんにちは")
        sent = json.loads(op.call_args[0][0].data)
        self.assertEqual(sent["messages"], [{"type": "text", "text": "こんにちは"}])
        self.assertTrue(any("reply -> HTTP 200" in m for m in cm.output))

    def test_http_error_logs_status_and_body(self):
        c = line_client.HttpLineClient("tok")
        err = urllib.error.HTTPError("u", 400, "Bad", {}, io.BytesIO(b'{"message":"Invalid reply token"}'))
        with mock.patch("urllib.request.urlopen", side_effect=err), \
                self.assertLogs("kanban_karte.line", "ERROR") as cm:
            with self.assertRaises(urllib.error.HTTPError):
                c.reply("rt", "x")
        self.assertTrue(any("HTTP 400" in m and "Invalid reply token" in m for m in cm.output))


if __name__ == "__main__":
    unittest.main()
