"""市民からの報告の入口(/report/、ログイン不要)と、/gov/ の地図の「市民からの報告」の層の API。

  GET  /report/ /report/status?code=...   画面(report.html。状態の確認も同じ画面)
  GET  /api/report/meta                   呼び名・気になる所の選択肢
  POST /api/report                        {photo: base64, lat?, lng?, address?, note?, signs?: [...]} → {code, status, text, date}
  GET  /api/report/status?code=...        {code, status, text, date}(写真・一言・位置は返さない)
  GET  /api/gov/citizen                   {total, by_status, pins: [{lat, lng(小数 3 桁), status, date}], no_position}

悪用の最低限: 送信は 1 IP あたり 1 時間 20 件(メモリ上。再起動で戻る)、写真は 8 MB まで・JPEG/PNG/WebP だけ、
EXIF の位置は使わずブラウザの位置を使う、写真は保存し直して EXIF を捨てる。顔やナンバーの自動処理はしない(写真を公開しないため)。
台帳 DB は照合のために読むだけ(書かない)。
"""
from __future__ import annotations

import base64
import threading
import time
from collections import defaultdict, deque
from pathlib import Path

from .. import citizen as C
from ..citizen.store import DEFAULT_DB, SIGNS, STATUSES, ReportError, Store

STATIC = Path(__file__).with_name("static")
RATE_LIMIT, RATE_WINDOW_S = 20, 3600


class RateLimit:
    def __init__(self, limit: int = RATE_LIMIT, window: float = RATE_WINDOW_S, clock=time.monotonic):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            q = self._hits[key]
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return False
            q.append(now)
            return True


def wants(path: str) -> bool:
    return path in ("/report", "/report/", "/report/status", "/api/report", "/api/report/meta", "/api/report/status", "/api/gov/citizen")


def store(app) -> Store:
    """App に 1 つだけ持たせる。見本(--sample)は台帳の隣に置く(data/private を汚さない)。テストは app._citizen を差し込める。"""
    s = getattr(app, "_citizen", None)
    if s is None:
        s = Store(app.db.parent / "citizen_reports.sqlite") if app.sample else Store(DEFAULT_DB)
        app._citizen = s
    return s


def limiter(app) -> RateLimit:
    r = getattr(app, "_citizen_rate", None)
    if r is None:
        r = app._citizen_rate = RateLimit()
    return r


def client_ip(h) -> str:
    """送り元。cloudflared / tailscale serve の後ろでは接続元が 127.0.0.1 になるので、前段の見出しを先に見る。"""
    for k in ("CF-Connecting-IP", "X-Forwarded-For"):
        v = h.headers.get(k)
        if v:
            return v.split(",")[0].strip()
    return h.client_address[0]


def ledger_signs(app) -> list:
    with app.ledger() as lg:
        return lg.list_signs()


def summary_for_map(app) -> dict | None:
    """/gov/ の地図の層。報告が 1 件も無ければ None(層を出さない)。"""
    st = store(app)
    if not st.path.exists():
        return None
    st.rematch(ledger_signs(app))
    sm = st.summary()
    return sm if sm["total"] else None


def serve(h, app, method: str, path: str, q: dict, body: dict | None) -> None:
    if path == "/report":
        return h._send(308, b"", "text/plain", {"Location": "/report/"})
    if path in ("/report/", "/report/status"):
        return h._file(STATIC / "report.html", "text/html; charset=utf-8")
    st = store(app)
    if method == "GET" and path == "/api/report/meta":
        return h._json({"brand": C.BRAND, "brand_sub": C.BRAND_SUB, "signs": list(SIGNS), "statuses": list(STATUSES)})
    if method == "GET" and path == "/api/report/status":
        r = st.by_code((q.get("code") or [""])[0])
        if r is None:
            return h._error(404, "その受付番号は見つかりません。番号を確かめてください。")
        st.rematch(ledger_signs(app))
        return h._json(st.by_code(r.code).public())
    if method == "GET" and path == "/api/gov/citizen":
        st.rematch(ledger_signs(app))
        return h._json(st.summary())
    if method == "POST" and path == "/api/report":
        if not limiter(app).allow(client_ip(h)):
            return h._error(429, "短い時間に送られた数が多すぎます。時間をおいてから送ってください。")
        raw = body.get("photo")
        if not isinstance(raw, str) or not raw:
            return h._error(400, "写真を付けてください")
        try:
            photo = base64.b64decode(raw.split(",", 1)[-1], validate=True)
        except ValueError:
            return h._error(400, "写真が base64 ではありません")
        try:
            r = st.add(photo, lat=body.get("lat"), lng=body.get("lng"), address=body.get("address") or "",
                       note=body.get("note") or "", signs=body.get("signs") or [], ledger_signs=ledger_signs(app))
        except ReportError as e:
            return h._error(400, str(e))
        return h._json(r.public())
    return h._error(405 if path.startswith("/api/report") else 404, "この経路は使えません")
