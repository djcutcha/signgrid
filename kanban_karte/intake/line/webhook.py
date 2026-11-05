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
import os
import threading
from urllib.parse import parse_qs
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Callable

from ...ledger import Ledger, Site
from ...legal.municipality import AddressResolver
from . import reply as reply_text
from .address import AddressSearch
from .client import LineClient
from .guide import START_TEXT, Guide, Say, site_choices as guide_site_choices
from .guide_ledger import LedgerSink
from .jobs import Job, find_job, jobs_near, load_jobs
from .session import JST, SessionStore, safe_name

log = logging.getLogger("kanban_karte.line")
MAX_BODY = 2 * 1024 * 1024
WEBHOOK_PATH = "/line/webhook"


def auto_draft_enabled() -> bool:
    """環境変数 LINE_GUIDE_AUTO_DRAFT=1(既定は OFF)で、下書きの鍵(web.api.can_draft)もある時だけ自動で下書きする。"""
    if os.environ.get("LINE_GUIDE_AUTO_DRAFT") != "1":
        return False
    from ...web import api
    return api.can_draft()


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
    guide_say: Say | None = None  # ガイド付き点検の進行中は、受け取りの返事の代わりにこれを返す


@dataclass
class Delivery:
    """1回の webhook 配信の結果(テストと模擬で見る用)。"""
    events: int = 0
    saved: list[Path] = field(default_factory=list)
    replies: list[tuple[str, str]] = field(default_factory=list)  # (replyToken, 文面)
    errors: list[str] = field(default_factory=list)


def _say_extra(say: Say) -> dict:
    extra: dict = {"quick_replies": say.quick}
    if say.more:
        extra["more"] = say.more  # 長いまとめの 2 通目以降(同じ返信に続けて載せる)
    return extra


class LineApp:
    def __init__(self, secret: str, client: LineClient, ledger_factory: Callable[[], Ledger],
                 inbox: str | Path, now: Callable[[], datetime] | None = None,
                 jobs: Callable[[], list[Job]] | None = None, address_search: AddressSearch | None = None,
                 auto_draft: Callable[[int], None] | None = None):
        self.secret = secret
        self.client = client
        self.ledger_factory = ledger_factory
        self.inbox = Path(inbox)
        self.now = now or (lambda: datetime.now(JST))
        self.jobs = jobs or load_jobs  # 今日の案件。実運用は SignJOBZ の点検指示書に差し替える
        self.address_search = address_search  # None なら座標は引かない
        self._lock = threading.Lock()  # 束の判定と台帳書き込みは1本ずつ
        self.auto_draft = auto_draft or self._draft_in_background  # 束 → 写真からの下書き。テストで差し替える
        self.guide_sink = LedgerSink(now=self.now, auto_draft=auto_draft_enabled)  # ガイド付き点検の答えを台帳へ(台帳は配信ごとに差し替える)
        self.guide = Guide(self.inbox, self.guide_sink, site_choices=self._guide_site_choices)

    def _draft_in_background(self, bundle_id: int) -> None:
        """ガイド終了で AI 判定の項目がある束を、webhook の応答を止めずに下書きする(Web の「下書きを作る」と同じ web.api.draft_bundle)。
        結果は台帳に入るだけで、LINE へは通知しない。"""
        threading.Thread(target=self._draft_bundle, args=(bundle_id,), name=f"line-draft-{bundle_id}", daemon=True).start()

    def _draft_bundle(self, bundle_id: int) -> None:
        from ...web import api
        try:
            with self.ledger_factory() as ledger:
                api.draft_bundle(ledger, bundle_id)
        except Exception:
            log.exception("auto draft failed: bundle %s", bundle_id)

    def _guide_site_choices(self) -> list[dict]:
        return guide_site_choices(self._today_jobs(), reply_text.site_choices([], None)[-1])

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
            self.guide_sink.ledger = ledger
            for ev in events:
                try:
                    self._one(ev, store, tally, follows, out)
                except Exception as e:  # 1件の失敗で配信全体(LINE の再送)を巻き込まない
                    log.exception("event failed")
                    out.errors.append(f"{type(e).__name__}: {e}")
        for bundle_id in self.guide_sink.draft_bundles:  # 台帳を閉じたあと(書き込みが済んでから)走らせる
            self.auto_draft(bundle_id)
        self.guide_sink.draft_bundles.clear()
        for token, text in follows:
            self._reply(token, text, out)
        for t in tally.values():
            if not t.touched or not t.reply_token:
                continue
            if t.guide_say is not None:
                text = t.guide_say.text
                if t.failed:
                    text = f"{t.failed} 枚は取得できませんでした。もう一度送ってください\n{text}"
                self._reply(t.reply_token, text, out, bundle_id=t.bundle_id, **_say_extra(t.guide_say))
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
        if mtype == "text" and self._guide_text(user_id, mid, str(msg.get("text", "")), store, t, now):
            return
        guiding = self.guide.active(user_id)
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
            if guiding and not res.duplicate and res.path is not None:
                t.guide_say = self.guide.photo(user_id, res.state.bundle_id, res.state.photos, res.path, now)
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
        if guiding and mtype == "location" and res.state.site_id:
            t.guide_say = self.guide.location_given(user_id, res.state.site_id) or t.guide_say

    # ---- ガイド付き点検 ----
    def _guide_start(self, user_id: str, store: SessionStore, now: datetime) -> Say:
        """始める(進行中なら今の問いに戻る)。直近の束に現場が分かっていれば、現場を尋ねない。"""
        st = store.current(user_id)
        recent = st is not None and timedelta(0) <= now - datetime.fromisoformat(st.last_at) <= store.window
        return self.guide.start(user_id, now, st.site_id if recent and st.site_id else None)

    def _guide_text(self, user_id: str, mid: str, text: str, store: SessionStore, t: _UserTally, now: datetime) -> bool:
        """「点検開始」と、進行中のテキスト(命令・看板の名前・メモ)を受ける。ガイドが受けたら True。"""
        if text.strip() == START_TEXT:
            if self.guide.active(user_id):
                store.touch(user_id, now)
            t.guide_say, t.touched = self._guide_start(user_id, store, now), True
            return True
        got = self.guide.text(user_id, text, mid)
        if got is None:
            return False
        kind, say = got
        if kind == "dup":
            return True
        store.touch(user_id, now)
        if kind == "memo":
            store.add_text(user_id, mid, text, now)
        t.guide_say, t.touched = say, True
        return True

    # ---- 現場の選択 ----
    def _today_jobs(self) -> list[Job]:
        return jobs_near(self.jobs(), self.now().astimezone(JST).date())

    def _postback(self, ev: dict, user_id: str, store: SessionStore, out: Delivery) -> None:
        token = ev.get("replyToken")
        q = {k: v[0] for k, v in parse_qs((ev.get("postback") or {}).get("data", "")).items()}
        act = q.get("act")
        if act == "guide" and token:
            return self._guide_postback(token, q, user_id, store, out)
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

    def _guide_postback(self, token: str, q: dict, user_id: str, store: SessionStore, out: Delivery) -> None:
        now = self.now()
        if self.guide.active(user_id):
            store.touch(user_id, now)
        if "start" in q:
            say = self._guide_start(user_id, store, now)
        elif "site" in q:
            job = find_job(self.jobs(), q["site"])
            if job is None:
                say = Say(reply_text.job_not_found(), self._guide_site_choices())
            else:
                site = self._job_site(job, store.ledger)
                store.ledger.upsert_site(site)
                say = self.guide.choose_site(user_id, site.id, job.site_name)
        else:
            say = self.guide.postback(user_id, q)
        if say is not None:
            self._reply(token, say.text, out, **_say_extra(say))

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
