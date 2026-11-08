"""看板管理台帳の画面(標準ライブラリの http.server だけ)。

  python3 -m kanban_karte.web                                  見本データ(data/sample/)で起動
  python3 -m kanban_karte.web --db <sqlite> --data-dir <dir>   自分の台帳で起動(--port 既定 8790)
  python3 -m kanban_karte.web --db <新しいsqlite> --sample     見本データを入れた台帳を作って起動

台帳は --db(環境変数 KANBAN_KARTE_DB)。無ければ見本データを一時の台帳に入れる(再起動で消える)。
127.0.0.1 だけで待ち受ける。POST は Content-Type: application/json だけ受ける。
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit, parse_qs

from ..gov.geocode import Geocoder
from ..ledger.db import Ledger
from ..legal.locate import SamplePlacer, make_placer
from ..pipeline.intake_run import IntakeJobs
from . import api, citizen, expert, field_submit

REPO_ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).with_name("static")
SAMPLE_JSON = REPO_ROOT / "data" / "sample" / "ledger.json"
MAX_BODY = 30_000_000
MAX_UPLOAD_BODY = 60_000_000    # 行政のスマホ投入は 1回 30枚(縮小済みでも 1枚 0.5〜1MB)
MAX_INTAKE_BODY = 150_000_000   # 画面の取り込み(写真フォルダ・PDF。base64 で 4/3 倍)
MEDIA_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp",
               ".gif": "image/gif"}

SIGN = re.compile(r"^/api/signs/([^/]+)$")
SIGN_ACT = re.compile(r"^/api/signs/([^/]+)/(items|finalize|reopen)$")
SIGN_HEIGHT = re.compile(r"^/api/signs/([^/]+)/height$")
RULE_ANSWER = re.compile(r"^/api/rules/(\d+)/answer$")
RULE_ACT = re.compile(r"^/api/rules/(\d+)/(approve|reject)$")
BUNDLE_DRAFT = re.compile(r"^/api/bundles/(\d+)/draft$")
INTAKE_PROGRESS = re.compile(r"^/api/intake/(\d+)/progress$")
SITE_PATCH = re.compile(r"^/api/sites/([^/]+)$")
OUTPUT = re.compile(r"^/api/sites/([^/]+)/output/([a-z-]+)\.xlsx$")


class App:
    """サーバの設定。リクエストごとに台帳を開く(SQLite の接続はスレッドを渡さない)。"""

    def __init__(self, db: str | Path, data_dir: str | Path, sample: bool = False, judge_factory=None,
                 estimator_factory=None, placer=None, clarifier=None, geocoder=None, gov_store=None,
                 intake_parts=None, field_drafter_factory=None):
        # data_dir は絶対パスで持つ(--data-dir が相対でも、台帳の絶対パスの写真と突き合わせられる。Ledger も同じ)
        self.db, self.data_dir, self.sample, self.judge_factory = Path(db), Path(data_dir).resolve(), sample, judge_factory
        self.estimator_factory = estimator_factory
        self.field_drafter_factory = field_drafter_factory   # 部位の下書き役。None なら鍵があれば Sonnet(テストは偽物)
        self.clarifier = clarifier  # 聞き返し役。None なら見本・鍵なしは固定文、鍵があれば Sonnet(リクエストごとに作る)
        # 用途地域を引く部品。起動時に1回だけ作って共有する。見本データは通信も GIS 読み込みもしない(固定の商業地域)
        self.placer = SamplePlacer() if sample and placer is None else placer
        # 行政のスマホ投入: 逆ジオコーダと写真の置き場。見本は同梱の geocache だけで引く(通信しない)
        self.gov_store = Path(gov_store) if gov_store else self.db.parent / "gov_uploads"
        self.geocoder = geocoder if geocoder is not None else self._default_geocoder()
        # 画面の取り込み(/api/intake/)の裏の実行。intake_parts(log) -> (仕分け役, 束ね役) はテストで差し替える
        self.intake_jobs = IntakeJobs(self.ledger, self.data_dir, intake_parts,
                                      draft=lambda lg, bid: api.draft_bundle(lg, bid, self.judge_factory))
        self.verbose = False

    def _default_geocoder(self) -> Geocoder:
        if self.sample:
            def offline(lat, lng):
                raise OSError("見本は通信しない")
            return Geocoder(SAMPLE_JSON.parent / "gov" / "geocache.json", fetch=offline)
        return Geocoder(str(self.db) + ".geocache.json")   # gov.cli の既定と同じキャッシュ

    def ledger(self) -> Ledger:
        return Ledger(self.db, data_dir=self.data_dir)

    def media_path(self, rel: str) -> Path | None:
        """/media/<rel> → data_dir の下の画像。下に収まらない・画像でない・無いものは None。"""
        root = self.data_dir.resolve()
        p = (root / rel).resolve()
        if not p.is_relative_to(root) or p.suffix.lower() not in MEDIA_TYPES or not p.is_file():
            return None
        return p


JST = timezone(timedelta(hours=9))


def make_handler(app: App):
    class Handler(BaseHTTPRequestHandler):
        server_version = "kanban-karte"

        def log_message(self, fmt, *args):
            if app.verbose:
                sys.stderr.write("%s %s %s\n" % (datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S"), self.command, self.path))

        # ---- 応答 ----
        def _send(self, status: int, body: bytes, ctype: str, headers: dict | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            self._send(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8",
                       {"Cache-Control": "no-store"})

        def _error(self, status: int, message: str) -> None:
            self._json({"error": message}, status)

        def _file(self, p: Path, ctype: str, cache: str = "no-cache") -> None:
            self._send(200, p.read_bytes(), ctype, {"Cache-Control": cache})

        # ---- 振り分け ----
        def do_HEAD(self):
            self.do_GET()

        def do_GET(self):
            url = urlsplit(self.path)
            path, q = unquote(url.path), parse_qs(url.query)
            try:
                if expert.wants(path):   # 有資格者のアノテーション画面(expert.py)
                    return expert.serve(self, app, "GET", path, q, None)
                if citizen.wants(path):  # 市民からの報告(citizen.py)。ログイン不要
                    return citizen.serve(self, app, "GET", path, q, None)
                if path == "/":  # 入口は UI v3 のホーム(2026-11-08)。旧い台帳画面は /index.html に残す
                    return self._send(308, b"", "text/plain", {"Location": "/v3/" + (f"?{url.query}" if url.query else "")})
                if path == "/index.html":
                    return self._file(STATIC / "index.html", "text/html; charset=utf-8")
                if path in ("/gov", "/owner"):  # 末尾スラッシュ無しは入口へ転送(クエリは引き継ぐ)
                    return self._send(308, b"", "text/plain", {"Location": path + "/" + (f"?{url.query}" if url.query else "")})
                if path in ("/gov/", "/owner/"):
                    return self._file(STATIC / (path.strip("/") + ".html"), "text/html; charset=utf-8")
                if path in ("/v3", "/field", "/field/"):  # 社内 UI v3 の入口(/field/ は現場ウィザード)
                    return self._send(308, b"", "text/plain", {"Location": "/v3/" + ("field/" if path.startswith("/field") else "")
                                                               + (f"?{url.query}" if url.query else "")})
                if path.startswith("/v3/"):  # static/v3 をそのまま。末尾スラッシュは H ホーム / 現場ウィザードの入口
                    rel = path[len("/v3/"):]
                    if rel in ("", "field/"):
                        rel += "index.html" if rel else "h.html"
                    p = (STATIC / "v3" / rel).resolve()
                    if not p.is_relative_to((STATIC / "v3").resolve()) or not p.is_file():
                        return self._error(404, "見つかりません")
                    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    return self._file(p, ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
                if path.startswith("/static/"):
                    p = (STATIC / path[len("/static/"):]).resolve()
                    if not p.is_relative_to(STATIC.resolve()) or not p.is_file():
                        return self._error(404, "見つかりません")
                    ctype = mimetypes.guess_type(p.name)[0] or "application/octet-stream"
                    return self._file(p, ctype + ("; charset=utf-8" if ctype.startswith("text/") or ctype.endswith("javascript") else ""))
                if path.startswith("/media/"):
                    p = app.media_path(path[len("/media/"):])
                    if p is None:
                        return self._error(404, "画像が見つかりません")
                    return self._file(p, MEDIA_TYPES[p.suffix.lower()], "max-age=300")
                if not path.startswith("/api/"):
                    return self._error(404, "見つかりません")
                if path == "/api/owner/kinds":  # 種類の選択肢と撮り方(台帳を開かない)
                    return self._json(api.owner_kinds())
                if path == "/api/field/parts":  # 部位の選択肢(台帳を開かない)
                    return self._json(api.field_parts())
                if path == "/api/field/locate":  # 位置 → 町名までの住所と自治体(台帳を開かない)
                    return self._json(api.field_locate((q.get("lat") or [None])[0], (q.get("lng") or [None])[0], app.geocoder))
                with app.ledger() as ledger:
                    if path == "/api/meta":
                        return self._json(api.meta(app.sample))
                    if path == "/api/sites":
                        return self._json(api.sites(ledger))
                    if path == "/api/signs":
                        return self._json(api.signs(ledger, (q.get("site") or [None])[0]))
                    if path == "/api/bundles":
                        return self._json(api.bundles(ledger))
                    if path == "/api/gov/batches":
                        return self._json(api.gov_batches(ledger, (q.get("today") or [None])[0]))
                    if path in ("/api/gov/candidates", "/api/gov/map.html"):
                        try:
                            batch = int((q.get("batch") or [""])[0])
                        except ValueError:
                            return self._error(400, "batch=<一括登録の id> を付けてください")
                        today = (q.get("today") or [None])[0]
                        if path == "/api/gov/map.html":
                            return self._send(200, api.gov_map_html(ledger, batch, today, citizen.summary_for_map(app)), "text/html; charset=utf-8",
                                              {"Cache-Control": "no-store"})
                        return self._json(api.gov_candidates(ledger, batch, today))
                    if path == "/api/rules":
                        return self._json(api.rules(ledger))
                    m = INTAKE_PROGRESS.match(path)
                    if m:
                        return self._json(api.intake_progress(ledger, int(m[1]), app.intake_jobs))
                    m = SIGN.match(path)
                    if m:
                        return self._json(api.sign_detail(ledger, m[1], app.placer))
                    m = OUTPUT.match(path)
                    if m:
                        data, name = api.export(ledger, m[1], m[2])
                        return self._send(200, data, api.XLSX, {
                            "Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name)}",
                            "Cache-Control": "no-store"})
                self._error(404, "見つかりません")
            except api.ApiError as e:
                self._error(e.status, e.message)

        def do_PUT(self):
            self.do_POST()

        def do_PATCH(self):
            self.do_POST()

        def do_POST(self):
            path = unquote(urlsplit(self.path).path)
            if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
                return self._error(415, "Content-Type は application/json")
            try:
                n = int(self.headers.get("Content-Length") or 0)
                limit = MAX_INTAKE_BODY if path == "/api/intake/upload" else MAX_UPLOAD_BODY if path == "/api/gov/upload" else MAX_BODY
                if n > limit:
                    return self._error(413, "本文が大きすぎます")
                body = json.loads(self.rfile.read(n) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._error(400, "本文が JSON のオブジェクトではありません")
            try:
                if expert.wants(path):
                    return expert.serve(self, app, "POST", path, {}, body)
                if citizen.wants(path):
                    return citizen.serve(self, app, "POST", path, {}, body)
                if (self.command == "PATCH") != bool(SITE_PATCH.match(path)):    # PATCH は現場の属性だけ。現場の属性は PATCH だけ
                    return self._error(405, "この経路は " + ("PATCH" if SITE_PATCH.match(path) else "POST か PUT") + " で送ってください")
                if self.command != "PATCH" and (self.command == "PUT") != bool(SIGN_HEIGHT.match(path)):    # PUT は全高だけ。全高は PUT だけ
                    return self._error(405, "この経路は " + ("PUT" if SIGN_HEIGHT.match(path) else "POST") + " で送ってください")
                if path == "/api/owner/judge":  # 台帳を開かない・書かない
                    return self._json(api.owner_judge(body, app.placer))
                if path == "/api/owner/estimate":  # 台帳を開かない・書かない。写真は保存しない
                    return self._json(api.owner_estimate(body, app.estimator_factory, app.placer))
                if path == "/api/field/draft":  # 台帳を開かない・書かない。写真は保存しない
                    return self._json(api.field_draft(body, app.field_drafter_factory))
                if path == "/api/field/photo":  # 写真を 1 枚置く(台帳は開かない)。Claude API は呼ばない
                    return self._json(field_submit.field_photo(body, app.data_dir))
                with app.ledger() as ledger:
                    m = SITE_PATCH.match(path)
                    if m:
                        return self._json(api.patch_site(ledger, m[1], body))
                    if path == "/api/field/submit":  # 看板 1 基を下書き(確認待ち)として台帳に入れる。Claude API は呼ばない
                        return self._json(field_submit.field_submit(ledger, body, app.data_dir))
                    if path == "/api/intake/upload":
                        return self._json(api.intake_upload(ledger, body, app.intake_jobs, app.data_dir))
                    if path == "/api/gov/upload":
                        return self._json(api.gov_upload(ledger, body, app.gov_store, app.geocoder))
                    m = SIGN_HEIGHT.match(path)
                    if m:
                        return self._json(api.set_height(ledger, m[1], body, app.placer))
                    m = SIGN_ACT.match(path)
                    if m:
                        if m[2] == "items":
                            return self._json(api.correct(ledger, m[1], body, app.placer, app.clarifier or api.default_clarifier(app.sample)))
                        if m[2] == "reopen":
                            return self._json(api.reopen(ledger, m[1], body, app.placer))
                        return self._json(api.finalize(ledger, m[1], body, app.placer))
                    m = RULE_ANSWER.match(path)
                    if m:
                        return self._json(api.rule_answer(ledger, int(m[1]), body, app.clarifier or api.default_clarifier(app.sample)))
                    m = RULE_ACT.match(path)
                    if m:
                        return self._json(api.rule_decide(ledger, int(m[1]), m[2], body))
                    if path == "/api/rules/resolve":
                        return self._json(api.rule_resolve(ledger, body))
                    m = BUNDLE_DRAFT.match(path)
                    if m:
                        return self._json(api.draft_bundle(ledger, int(m[1]), app.judge_factory))
                self._error(404, "見つかりません")
            except field_submit.SubmitError as e:
                self._json({"error": e.message, **e.extra}, e.status)
            except api.ApiError as e:
                self._error(e.status, e.message)

    return Handler


def make_server(app: App, host: str = "127.0.0.1", port: int = 8790) -> ThreadingHTTPServer:
    return ThreadingHTTPServer((host, port), make_handler(app))


def seed_sample(db: Path, data_dir: Path = SAMPLE_JSON.parent) -> None:
    with Ledger(db, data_dir=data_dir) as lg:
        lg.import_json(SAMPLE_JSON)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.web", description="看板管理台帳の画面")
    ap.add_argument("--db", default=None, help="台帳 SQLite(既定 data/private/ledger.sqlite、環境変数 KANBAN_KARTE_DB。無ければ見本データ)")
    ap.add_argument("--data-dir", default=None, help="台帳の画像パス(相対)の基準フォルダ(環境変数 KANBAN_KARTE_DATA)")
    ap.add_argument("--sample", action="store_true", help="見本データを台帳に入れる(--db が新しい/空のときだけ)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8790)
    a = ap.parse_args(argv)
    default_db = os.environ.get("KANBAN_KARTE_DB") or str(REPO_ROOT / "data" / "private" / "ledger.sqlite")
    db = Path(a.db or default_db)
    data_dir = Path(a.data_dir or os.environ.get("KANBAN_KARTE_DATA") or REPO_ROOT / "data" / "private" / "intake")
    sample = a.sample
    if not a.db and not db.exists():
        sample = True
        db = Path(tempfile.mkdtemp(prefix="kanban_karte_sample_")) / "ledger.sqlite"
    elif not db.exists() and not sample:
        print(f"台帳が見つかりません: {db}(見本データで始めるなら --sample)", file=sys.stderr)
        return 1
    if sample:
        data_dir = SAMPLE_JSON.parent
        if not db.exists():
            seed_sample(db, data_dir)
    app = App(db, data_dir, sample=sample, placer=None if sample else make_placer())
    if not sample:
        print("用途地域: " + ("GIS を読み込んだ" if app.placer else "GIS が無いので区分なしで判定(python3 -m kanban_karte.geo.fetch_gis)"), file=sys.stderr)
    app.verbose = True
    srv = make_server(app, a.host, a.port)
    print(f"台帳 {db}{'(見本データ)' if sample else ''}\nhttp://{a.host}:{a.port}/ を開く。止めるのは Ctrl-C", file=sys.stderr)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
