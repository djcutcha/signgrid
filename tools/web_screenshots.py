"""画面のスクリーンショットを撮る(Playwright / Chromium)。見本データで一時の台帳を作り、サーバを立てて PC 幅 1280 とスマホ幅 390 を撮る。

  python3 tools/web_screenshots.py [--out docs/img] [--only ledger,karte,...]

撮る画面: ledger(台帳)、karte(カルテ)、karte-edit(黄色のマスを直した後)、karte-confirmed(確定後)、rules(規則と出力)。
karte-edit 以降は台帳を書き換えるので、一時の台帳でだけ行う(見本の JSON は変わらない)。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kanban_karte.web.server import SAMPLE_JSON, App, make_server, seed_sample  # noqa: E402

VIEWPORTS = {"pc": {"width": 1280, "height": 900}, "sp": {"width": 390, "height": 844}}
SIGN = "demo-001"


def shoot(page, base: str, out: Path, tag: str, hash_: str, name: str, full=True, wait: str | None = None):
    page.goto(f"{base}/#{hash_}")
    page.wait_for_selector(wait or "main h1, main .karte, main table", timeout=8000)
    page.wait_for_timeout(300)
    path = out / f"{name}-{tag}.png"
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(path), full_page=full)
    print(path)


def run(page, base, out, tag, only):
    def want(k):
        return only is None or k in only

    if want("ledger"):
        shoot(page, base, out, tag, "/", "ledger", wait="main .ledger tbody tr")
    if want("karte"):
        shoot(page, base, out, tag, f"/sign/{SIGN}", "karte", wait="main .karte")
    if want("karte-edit") or want("karte-confirmed"):
        page.goto(f"{base}/#/sign/{SIGN}")
        page.wait_for_selector("main .karte")
        page.fill("#by", "見本 太郎")
        page.click(".item.yellow >> nth=0")
        page.click(".item.open .seg button:has-text('良好')")
        page.wait_for_selector(".history li")
        page.wait_for_timeout(300)
        if want("karte-edit"):
            page.evaluate("window.scrollTo(0, 0)")
            page.screenshot(path=str(out / f"karte-edit-{tag}.png"), full_page=True)
            print(out / f"karte-edit-{tag}.png")
    if want("karte-confirmed"):
        page.click("button:has-text('確定して署名')")
        page.click("button:has-text('確定する')")
        page.wait_for_selector(".banner.confirmed")
        page.wait_for_timeout(300)
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(out / f"karte-confirmed-{tag}.png"), full_page=True)
        print(out / f"karte-confirmed-{tag}.png")
    if want("ledger-after"):
        shoot(page, base, out, tag, "/", "ledger-after", wait="main .ledger tbody tr")
    if want("rules"):
        # 同じ所見で別の区分の訂正を2件(規則の矛盾の印を見せる)
        for v in ("要改善", "経過観察"):
            page.request.post(f"{base}/api/signs/demo-002/items", data={"item": "取付部-1", "value": v, "by": "見本 花子", "note": "根元に錆"})
        shoot(page, base, out, tag, "/rules", "rules", wait="main .rules")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/img")
    ap.add_argument("--only", default=None)
    a = ap.parse_args(argv)
    only = set(a.only.split(",")) if a.only else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for tag, vp in VIEWPORTS.items():
            with tempfile.TemporaryDirectory() as td:  # 画面幅ごとに新しい台帳(確定が次の撮影に残らないように)
                db = Path(td) / "l.sqlite"
                seed_sample(db)
                srv = make_server(App(db, SAMPLE_JSON.parent, sample=True), "127.0.0.1", 0)
                base = f"http://127.0.0.1:{srv.server_address[1]}"
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                ctx = browser.new_context(viewport=vp, device_scale_factor=2 if tag == "sp" else 1, locale="ja-JP",
                                          has_touch=tag == "sp", is_mobile=tag == "sp")
                page = ctx.new_page()
                try:
                    run(page, base, out, tag, only)
                finally:
                    ctx.close()
                    srv.shutdown()
                    srv.server_close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
