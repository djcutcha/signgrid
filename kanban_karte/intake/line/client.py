"""LINE Messaging API の呼び出し。画像の取得と返信だけ。

環境変数 LINE_CHANNEL_ACCESS_TOKEN を使う。テストと模擬 webhook では LineClient の別実装に差し替える。
"""
from __future__ import annotations

import json
import urllib.request
from typing import Protocol

CONTENT_URL = "https://api-data.line.me/v2/bot/message/{id}/content"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"
TIMEOUT = 20


class LineClient(Protocol):
    def get_content(self, message_id: str) -> bytes: ...
    def reply(self, reply_token: str, text: str) -> None: ...


class HttpLineClient:
    def __init__(self, access_token: str):
        if not access_token:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN が空")
        self.token = access_token

    def _req(self, url: str, data: bytes | None = None) -> bytes:
        req = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {self.token}"})
        if data is not None:
            req.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            return r.read()

    def get_content(self, message_id: str) -> bytes:
        return self._req(CONTENT_URL.format(id=message_id))

    def reply(self, reply_token: str, text: str) -> None:
        body = {"replyToken": reply_token, "messages": [{"type": "text", "text": text}]}
        self._req(REPLY_URL, json.dumps(body, ensure_ascii=False).encode("utf-8"))
