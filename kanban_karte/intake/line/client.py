"""LINE Messaging API の呼び出し。画像の取得と返信だけ。

環境変数 LINE_CHANNEL_ACCESS_TOKEN を使う。テストと模擬 webhook では LineClient の別実装に差し替える。
通信の結果は logger "kanban_karte.line" に出す(失敗時は HTTP ステータスと応答本文)。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Protocol

CONTENT_URL = "https://api-data.line.me/v2/bot/message/{id}/content"
REPLY_URL = "https://api.line.me/v2/bot/message/reply"
TIMEOUT = 20

log = logging.getLogger("kanban_karte.line")


class LineClient(Protocol):
    def get_content(self, message_id: str) -> bytes: ...
    def reply(self, reply_token: str, text: str) -> None: ...


def call(token: str, url: str, data: bytes | None = None, content_type: str = "application/json",
         what: str = "") -> bytes:
    """認証つきで1回呼ぶ。POST(data あり)は成功時にも HTTP ステータスを1行、失敗時は本文つきでログに出す。"""
    req = urllib.request.Request(url, data=data, headers={"Authorization": f"Bearer {token}"})
    if data is not None:
        req.add_header("Content-Type", content_type)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            body = r.read()
            if data is not None:
                log.info("LINE API %s -> HTTP %s", what or url, r.status)
            return body
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:500]
        log.error("LINE API %s -> HTTP %s %s", what or url, e.code, detail)
        raise
    except Exception as e:
        log.error("LINE API %s -> %s: %s", what or url, type(e).__name__, e)
        raise


class HttpLineClient:
    def __init__(self, access_token: str):
        if not access_token:
            raise ValueError("LINE_CHANNEL_ACCESS_TOKEN が空")
        self.token = access_token

    def get_content(self, message_id: str) -> bytes:
        return call(self.token, CONTENT_URL.format(id=message_id), what=f"content {message_id}")

    def reply(self, reply_token: str, text: str, quick_replies: list[dict] | None = None,
              button: tuple[str, str] | None = None, more: list[str] | None = None) -> None:
        """text を1通返す(more があれば続けて載せる。1 回の返信は 5 通まで)。quick_replies は quickReply.items、button は (ラベル, URI) で URI ボタン付きにする。"""
        if button is not None:
            label, uri = button
            msg = {"type": "template", "altText": text,
                   "template": {"type": "buttons", "text": text,
                                "actions": [{"type": "uri", "label": label, "uri": uri}]}}
        else:
            msg = {"type": "text", "text": text}
            if quick_replies:
                msg["quickReply"] = {"items": quick_replies}
        msgs = [msg] + [{"type": "text", "text": t} for t in (more or [])]
        if quick_replies and more:  # クイックリプライは最後の通に付ける
            msgs[0].pop("quickReply", None)
            msgs[-1]["quickReply"] = {"items": quick_replies}
        body = {"replyToken": reply_token, "messages": msgs[:5]}
        call(self.token, REPLY_URL, json.dumps(body, ensure_ascii=False).encode("utf-8"), what="reply")
