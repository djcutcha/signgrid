"""POST /line/webhook を受ける。標準ライブラリだけ(http.server)。

環境変数:
  LINE_CHANNEL_SECRET        署名検証(X-Line-Signature = base64(HMAC-SHA256(secret, 生の本文)))
  LINE_CHANNEL_ACCESS_TOKEN  画像の取得と返信
処理するイベント: message(image / text / location)、postback(現場の選択)、follow。それ以外は無視する。
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import threading
from urllib.parse import parse_qs
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from ...ledger import Ledger, Site
from ...legal.municipality import AddressResolver
from . import reply as reply_text
from .address import AddressSearch
from .client import LineClient
from .jobs import Job, find_job, jobs_near, load_jobs
from .session import JST, SessionStore, safe_name

log = logging.getLogger("kanban_karte.line")
MAX_BODY = 2 * 1024 * 1024
WEBHOOK_PATH = "/line/webhook"


def sign(secret: str, body: bytes) -> str:
    return base64.b64encode(hmac.new(secret.encode("utf-8"), body, hashlib.sha256).digest()).decode("ascii")


def verify_signature(secret: str, body: bytes, signature: str | None) -> bool:
    if not secret or not signature:
        return False
    return hmac.compare_digest(sign(secret, body), signature.strip())


@dataclass
class _UserTally:
    reply_token: str | None = None
    state_photos: int = 0       # 束の写真の累計
    notes: int = 0
    position_only: bool = False
    failed: int = 0
    touched: bool = False       # この配信で新しく受け取った物がある(再送だけなら返事しない)
    bundle_id: int | None = None
    site_id: str | None = None  # 束の現場。None なら現場を尋ねる


@dataclass
class Delivery:
    """1回の webhook 配信の結果(テストと模擬で見る用)。"""
    events: int = 0
    saved: list[Path] = field(default_factory=list)
    replies: list[tuple[str, str]] = field(default_factory=list)  # (replyToken, 文面)
    errors: list[str] = field(default_factory=list)


class LineApp:
    def __init__(self, secret: str, client: LineClient, ledger_factory: Callable[[], Ledger],
                 inbox: str | Path, now: Callable[[], datetime] | None = None,
                 jobs: Callable[[], list[Job]] | None = None, address_search: AddressSearch | None = None):
        self.secret = secret
        self.client = client
        self.ledger_factory = ledger_factory
        self.inbox = Path(inbox)
        self.now = now or (lambda: datetime.now(JST))
        self.jobs = jobs or load_jobs  # 今日の案件。実運用は SignJOBZ の点検指示書に差し替える
        self.address_search = address_search  # None なら座標は引かない
        self._lock = threading.Lock()  # 束の判定と台帳書き込みは1本ずつ

    # ---- 入口 ----
    def handle(self, body: bytes, signature: str | None) -> tuple[int, Delivery | None]:
        """(HTTP ステータス, 結果)。署名が合わなければ 401 で何も処理しない。"""
        if not verify_signature(self.secret, body, signature):
            return 401, None
        try:
            doc = json.loads(body.decode("utf-8"))
            events = doc.get("events", [])
            if not isinstance(events, list):
                raise ValueError("events がリストでない")
        except (ValueError, UnicodeDecodeError, AttributeError):
            return 400, None
        with self._lock:
            return 200, self._process(events)

    # ---- イベント処理 ----
    def _process(self, events: list) -> Delivery:
        out = Delivery(events=len(events))
        tally: dict[str, _UserTally] = {}
        follows: list[tuple[str, str]] = []
        with self.ledger_factory() as ledger:
            store = SessionStore(ledger, self.inbox)
            for ev in events:
                try:
                    self._one(ev, store, tally, follows, out)
                except Exception as e:  # 1件の失敗で配信全体(LINE の再送)を巻き込まない
                    log.exception("event failed")
                    out.errors.append(f"{type(e).__name__}: {e}")
        for token, text in follows:
            self._reply(token, text, out)
        for t in tally.values():
            if not t.touched or not t.reply_token:
                continue
            text = reply_text.receipt(t.state_photos, has_position_only=t.position_only, notes=t.notes,
                                      failed=t.failed)
            if text and t.state_photos > 0 and t.site_id is None:
                # 位置も現場も無い束: 今日の現場を尋ねる
                self._reply(t.reply_token, f"{text}\n{reply_text.ask_site()}", out, bundle_id=t.bundle_id,
                            quick_replies=reply_text.site_choices(self._today_jobs(), t.bundle_id))
            elif text:
                self._reply(t.reply_token, text, out, bundle_id=t.bundle_id)
        return out

    def _one(self, ev: dict, store: SessionStore, tally: dict[str, _UserTally], follows: list,
             out: Delivery) -> None:
        etype = ev.get("type")
        src = ev.get("source") or {}
        user_id = src.get("userId")
        token = ev.get("replyToken")
        if etype == "postback" and user_id:
            self._postback(ev, user_id, store, out)
            return
        if etype == "follow":
            if token:
                follows.append((token, reply_text.follow()))
            return
        if etype != "message" or not user_id:
            return
        msg = ev.get("message") or {}
        mtype, mid = msg.get("type"), str(msg.get("id", ""))
        if mtype not in ("image", "text", "location") or not mid:
            return
        now = self.now()
        t = tally.setdefault(user_id, _UserTally())
        if token:
            t.reply_token = token  # 返事は配信の最後のイベントのトークンで1通
        if mtype == "image":
            try:
                data = self.client.get_content(mid)
                res = store.add_image(user_id, mid, data, now)
            except Exception as e:
                t.failed += 1
                t.touched = True
                out.errors.append(f"image {mid}: {type(e).__name__}: {e}")
                return
            if res.path is not None:
                out.saved.append(res.path)
        elif mtype == "text":
            res = store.add_text(user_id, mid, str(msg.get("text", "")), now)
            t.notes += 1
        else:
            res = store.add_location(user_id, mid, msg["latitude"], msg["longitude"], msg.get("address"),
                                     msg.get("title"), now)
        if res.duplicate:
            return  # LINE の再送。数えない・返事もしない
        t.touched = True
        t.state_photos = res.state.photos
        t.bundle_id = res.state.bundle_id
        t.site_id = res.state.site_id
        t.position_only = mtype == "location" and res.state.photos == 0

    # ---- 現場の選択 ----
    def _today_jobs(self) -> list[Job]:
        return jobs_near(self.jobs(), self.now().astimezone(JST).date())

    def _postback(self, ev: dict, user_id: str, store: SessionStore, out: Delivery) -> None:
        token = ev.get("replyToken")
        q = {k: v[0] for k, v in parse_qs((ev.get("postback") or {}).get("data", "")).items()}
        act = q.get("act")
        if not token or act not in ("site", "other", "today"):
            return
        st = store.current(user_id)
        try:
            bundle_id = int(q["b"]) if "b" in q else (st.bundle_id if st else None)
        except ValueError:
            bundle_id = None
        if bundle_id is not None and not self._owns(store, user_id, bundle_id):
            bundle_id = None
        if act == "other":
            self._reply(token, reply_text.other_prompt(), out, bundle_id=bundle_id,
                        button=("現在地を送る", reply_text.LOCATION_URI))
        elif act == "today":
            jobs = self._today_jobs()
            if bundle_id is not None and st is not None and st.bundle_id == bundle_id and st.site_id is None:
                self._reply(token, reply_text.ask_site(), out, bundle_id=bundle_id,
                            quick_replies=reply_text.site_choices(jobs, bundle_id))
            else:
                self._reply(token, reply_text.today_list(jobs), out, bundle_id=bundle_id)
        else:
            job = find_job(self.jobs(), q.get("job", ""))
            if job is None:
                self._reply(token, reply_text.job_not_found(), out, bundle_id=bundle_id)
            elif bundle_id is None:
                self._reply(token, reply_text.no_bundle(), out)
            else:
                store.bind_site(user_id, bundle_id, self._job_site(job, store.ledger))
                self._reply(token, reply_text.site_accepted(job.site_name), out, bundle_id=bundle_id)

    @staticmethod
    def _owns(store: SessionStore, user_id: str, bundle_id: int) -> bool:
        b = store.ledger.get_bundle(bundle_id)
        return b is not None and str(b["file"]).startswith(f"line://{safe_name(user_id)}/")

    def _job_site(self, job: Job, ledger: Ledger) -> Site:
        """案件の現場。同じ案件は同じ sites 行(job-<案件ID>)。座標は住所検索 API(キャッシュ済み)。"""
        site = ledger.get_site(f"job-{job.id}") or Site(id=f"job-{job.id}", name=job.site_name)
        site.name, site.address = job.site_name, job.address
        if site.lat is None and self.address_search is not None:
            pos = self.address_search.lookup(job.address)
            if pos is None:
                log.warning("address search: no position for job %s", job.id)
            else:
                site.lat, site.lng = pos
        m = AddressResolver().resolve(job.address)
        if m is not None:
            site.municipality = m.city or m.prefecture
        return site

    def _reply(self, token: str, text: str, out: Delivery, bundle_id: int | None = None, **extra) -> None:
        where = f" bundle=#{bundle_id}" if bundle_id is not None else ""
        try:
            self.client.reply(token, text, **extra)
            out.replies.append((token, text))
            log.info("reply ok 1通%s", where)
        except Exception as e:
            log.error("reply failed%s: %s: %s", where, type(e).__name__, e)
            out.errors.append(f"reply: {type(e).__name__}: {e}")


def make_handler(app: LineApp):
    class Handler(BaseHTTPRequestHandler):
        def _send(self, code: int, body: bytes = b"", ctype: str = "text/plain; charset=utf-8"):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/healthz":
                self._send(200, b"ok")
            else:
                self._send(404)

        def do_POST(self):
            if self.path != WEBHOOK_PATH:
                return self._send(404)
            try:
                n = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                return self._send(400)
            if n < 0 or n > MAX_BODY:
                return self._send(413)
            body = self.rfile.read(n)
            code, _ = app.handle(body, self.headers.get("X-Line-Signature"))
            self._send(code, b"OK" if code == 200 else b"")

        def log_message(self, fmt, *args):  # 標準の stderr 出力は使わず logging へ
            log.info("%s %s", self.address_string(), fmt % args)

    return Handler


def make_server(app: LineApp, host: str = "127.0.0.1", port: int = 8787) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(app))
