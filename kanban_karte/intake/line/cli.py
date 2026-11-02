"""python3 -m kanban_karte.intake.line.cli {serve,simulate,richmenu}

serve     webhook サーバを起動(LINE_CHANNEL_SECRET と LINE_CHANNEL_ACCESS_TOKEN が要る)
simulate  模擬 webhook を送る。--url なしなら同じプロセスにサーバを立てて一通り流す(LINE も鍵も不要)
richmenu  リッチメニューの JSON と画像を作る(--dry-run)。--apply で LINE に作成・画像アップロード・既定に設定
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
from pathlib import Path

from ...ledger import Ledger
from .address import AddressSearch
from .client import HttpLineClient
from .jobs import load_jobs
from .richmenu import HEIGHT, WIDTH, apply as apply_richmenu, menu_json, render_image
from .simulate import DirContentClient, build_deliveries, list_images, post, webhook_url
from .webhook import LineApp, make_server

REPO_ROOT = Path(__file__).resolve().parents[3]
PRIVATE = REPO_ROOT / "data" / "private"
SIM_SECRET = "simulate-secret"


def _paths(a, sim: bool = False):
    """--db / --inbox / 環境変数 の順。無指定なら serve は本番の置き場、simulate は line_sim/ の別置き場(本番の台帳を汚さない)。"""
    base = PRIVATE / "line_sim" if sim else PRIVATE
    db, inbox = a.db, a.inbox
    if not sim:
        db = db or os.environ.get("KANBAN_KARTE_DB")
        inbox = inbox or os.environ.get("KANBAN_KARTE_LINE_INBOX")
    return Path(db or base / "ledger.sqlite"), Path(inbox or base / "line_inbox")


def cmd_serve(a) -> int:
    secret = os.environ.get("LINE_CHANNEL_SECRET", "")
    if not secret:
        print("LINE_CHANNEL_SECRET が未設定(docs/LINE.md 参照)")
        return 2
    if a.sim_content_dir:
        client = DirContentClient(a.sim_content_dir, echo=True)
    else:
        token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
        if not token:
            print("LINE_CHANNEL_ACCESS_TOKEN が未設定(docs/LINE.md 参照)")
            return 2
        client = HttpLineClient(token)
    db, inbox = _paths(a)
    jobs_path = a.jobs
    app = LineApp(secret, client, lambda: Ledger(db), inbox, jobs=lambda: load_jobs(jobs_path),
                  address_search=AddressSearch(inbox / "address_cache.json"))
    srv = make_server(app, a.host, a.port)
    print(f"待ち受け http://{a.host}:{srv.server_address[1]}/line/webhook  台帳={db}  受信箱={inbox}", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_simulate(a) -> int:
    images = list_images(a.images) if a.images else []
    location = tuple(float(x) for x in a.location.split(",")) if a.location else None
    if not images and not a.text and not location:
        print("送る物が無い(--images / --text / --location)")
        return 2
    content_dir = Path(a.content_dir)
    plan = build_deliveries(images, content_dir, one_by_one=a.one_by_one, text=a.text, location=location)
    if a.url:
        secret = os.environ.get("LINE_CHANNEL_SECRET", SIM_SECRET)
        url = webhook_url(a.url)
        for body in plan.deliveries:
            print("HTTP", post(url, secret, body))
        return 0
    db, inbox = _paths(a, sim=True)
    client = DirContentClient(content_dir, echo=True)
    app = LineApp(SIM_SECRET, client, lambda: Ledger(db), inbox)
    srv = make_server(app, "127.0.0.1", 0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/line/webhook"
    try:
        for body in plan.deliveries:
            print("HTTP", post(url, SIM_SECRET, body))
    finally:
        srv.shutdown()
        srv.server_close()
    with Ledger(db) as ledger:
        for b in ledger.list_bundles():
            if b["file"].startswith("line://"):
                print(f"束 {b['id']}: {b['file']} ページ{b['pages']} status={b['status']}")
    print(f"台帳={db} 受信箱={inbox}")
    return 0


def cmd_richmenu(a) -> int:
    image = render_image(a.image_out)
    print(json.dumps(menu_json(), ensure_ascii=False, indent=2))
    print(f"画像={image} ({WIDTH}x{HEIGHT})", flush=True)
    if not a.apply:
        print("--dry-run: LINE には送っていない(--apply で作成・画像アップロード・既定に設定)")
        return 0
    token = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN", "")
    if not token:
        print("LINE_CHANNEL_ACCESS_TOKEN が未設定(docs/LINE.md 参照)")
        return 2
    print("richMenuId =", apply_richmenu(token, image))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.intake.line.cli", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="台帳(serve の既定 data/private/ledger.sqlite、simulate は line_sim/ 下)")
    ap.add_argument("--inbox", default=None, help="受信した画像の置き場(serve の既定 data/private/line_inbox)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve", help="webhook サーバを起動")
    s.add_argument("--host", default="127.0.0.1")
    s.add_argument("--port", type=int, default=8787)
    s.add_argument("--jobs", default=None, help="今日の案件の JSON(既定 data/sample/jobs.json。形は jobs.py)")
    s.add_argument("--sim-content-dir", default=None,
                   help="画像をここから読み、返信は画面に出すだけ(simulate --url の相手用。LINE に繋がない)")
    s.set_defaults(fn=cmd_serve)
    m = sub.add_parser("simulate", help="模擬 webhook を送る")
    m.add_argument("--images", help="送る画像のフォルダ")
    m.add_argument("--text", help="一緒に送るメモ")
    m.add_argument("--location", help="一緒に送る位置 '緯度,経度'")
    m.add_argument("--one-by-one", action="store_true", help="1イベントずつ別の配信にする(既定はまとめて1配信)")
    m.add_argument("--url", help="送り先(省略時は同じプロセスに仮のサーバを立てる)")
    m.add_argument("--content-dir", default=str(PRIVATE / "line_sim_content"), help="模擬の画像の置き場")
    m.set_defaults(fn=cmd_simulate)
    r = sub.add_parser("richmenu", help="リッチメニューの JSON と画像を作る / LINE に設定する")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true", help="JSON を出し画像を書くだけ(LINE に送らない)")
    g.add_argument("--apply", action="store_true", help="LINE に作成・画像アップロード・既定に設定")
    r.add_argument("--image-out", default=str(PRIVATE / "richmenu.png"), help="画像の書き出し先")
    r.set_defaults(fn=cmd_richmenu)
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
