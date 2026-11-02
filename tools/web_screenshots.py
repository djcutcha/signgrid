"""画面のスクリーンショットを撮る(Playwright / Chromium)。見本データで一時の台帳を作り、サーバを立てて PC 幅 1280 とスマホ幅 390 を撮る。

  python3 tools/web_screenshots.py [--out docs/img] [--only ledger,karte,...]

撮る画面: ledger(台帳)、karte(カルテ)、karte-legal(カルテの法規カード)、karte-edit(黄色のマスを直した後)、karte-confirmed(確定後)、
rules(規則と出力)、intake(受付)、gov(行政。gov.make_sample の見本の束を入れる)、owner(オーナー。見本の寸法で「調べる」まで)、owner-saitama(同、埼玉県の住所・全高 5m)、
owner-estimate(オーナーの「寸法を推定」後。推定役は固定値の見本で、API は呼ばない)。
karte-edit 以降は台帳を書き換えるので、一時の台帳でだけ行う(見本の JSON は変わらない)。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kanban_karte.dim.estimator import DimEstimate  # noqa: E402
from kanban_karte.gov import make_sample as gov_sample  # noqa: E402
from kanban_karte.gov.batch_intake import intake as gov_intake  # noqa: E402
from kanban_karte.web import api as web_api  # noqa: E402
from kanban_karte.web.server import SAMPLE_JSON, App, make_server, seed_sample  # noqa: E402

VIEWPORTS = {"pc": {"width": 1280, "height": 900}, "sp": {"width": 390, "height": 844}}
SIGN = "demo-001"


class SampleEstimator:
    """スクリーンショット用の固定値(API は呼ばない)。実際の推定結果ではない。"""

    def estimate(self, photos, target_name):
        return DimEstimate(3200, 900, (2800, 3600), (800, 1000),
                           [{"name": "ドア(出入口の扉)", "assumed_mm": 2000, "how": "入口の扉の高さを物差しにした"},
                            {"name": "コンクリートブロック", "assumed_mm": 190, "how": "段数を数えた"}],
                           False, "mid", "板面だけを測った(支柱・取付金具は含めない)。", "sample", {})


def shoot(page, base: str, out: Path, tag: str, hash_: str, name: str, full=True, wait: str | None = None):
    page.goto(f"{base}/#{hash_}")
    page.wait_for_selector(wait or "main h1, main .karte, main table", timeout=8000)
    page.wait_for_timeout(300)
    path = out / f"{name}-{tag}.png"
    page.evaluate("window.scrollTo(0, 0)")
    page.screenshot(path=str(path), full_page=full)
    print(path)


def seed_extra(db: Path, tmp: Path) -> None:
    """受付と行政の見本: 束を2本(PC の PDF と LINE の写真)、gov の見本の束(写真10枚・台帳5基)。"""
    from kanban_karte.ledger import Ledger

    gov_sample.build(tmp / "gov")
    with Ledger(db) as lg:
        a = lg.add_bundle("/scan/見本_点検票.pdf", "sample-sha-pc", 4)
        lg.set_bundle(a, status="classified", kind_counts={"点検票": 3, "写真": 1}, site_id="demo-site-1")
        b = lg.add_bundle("line://Usample/2026-11-02/s1", "sample-sha-line", 6)
        lg.set_bundle(b, status="pages_registered", site_id="demo-site-1")
        c = lg.add_bundle("/scan/見本_現地写真.pdf", "sample-sha-pc2", 2)
        lg.set_bundle(c, status="signs_drafted", site_id="demo-site-1")
        lg.import_json(tmp / "gov" / "ledger_gov.json")
        gov_intake(lg.conn, tmp / "gov" / "photos")


def run(page, base, out, tag, only):
    def want(k):
        return only is None or k in only

    if want("ledger"):
        shoot(page, base, out, tag, "/", "ledger", wait="main .ledger tbody tr")
    if want("karte"):
        shoot(page, base, out, tag, f"/sign/{SIGN}", "karte", wait="main .karte")
    if want("karte-legal"):
        page.goto(f"{base}/#/sign/{SIGN}")
        page.wait_for_selector("main .legal-card .cites li")
        page.wait_for_timeout(300)
        page.add_style_tag(content=".topbar{position:static !important}")  # 要素の撮影に sticky のヘッダーが写り込まないように
        path = out / f"karte-legal-{tag}.png"
        page.locator(".legal-card").screenshot(path=str(path))
        print(path)
    if want("intake"):
        shoot(page, base, out, tag, "/intake", "intake", wait="main .intake tbody tr")
    if want("gov"):
        shoot(page, base, out, tag, "/gov?today=2026-11-02", "gov", wait="main .gov-table tbody tr")
        page.wait_for_timeout(2500)  # 地図(Leaflet・地理院タイル)の読み込み待ち
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(out / f"gov-{tag}.png"), full_page=True)
    if want("owner"):
        page.goto(f"{base}/#/owner")
        page.wait_for_selector("main .owner")
        page.set_input_files("#owner-photo", str(SAMPLE_JSON.parent / "photos" / "plain_a.png"))
        page.fill("#owner-address", "東京都豊島区見本町1-2-3")
        page.fill("#owner-w", "1.95")
        page.fill("#owner-h", "0.6")
        page.click("button:has-text('調べる')")
        page.wait_for_selector(".owner-result .cites li")
        page.wait_for_timeout(600)
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(out / f"owner-{tag}.png"), full_page=True)
        print(out / f"owner-{tag}.png")
    if want("owner-saitama"):  # 許可不要の自家用でも点検の義務がある条例(埼玉県)。全高 5m で有資格者は努力義務
        page.goto(f"{base}/#/owner")
        page.wait_for_selector("main .owner")
        page.fill("#owner-address", "埼玉県さいたま市大宮区桜木町1-1-1")
        page.fill("#owner-w", "1.95")
        page.fill("#owner-h", "0.6")
        page.fill("#owner-height", "5")
        page.click("button:has-text('調べる')")
        page.wait_for_selector(".owner-result .cites li")
        page.wait_for_timeout(600)
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(out / f"owner-saitama-{tag}.png"), full_page=True)
        print(out / f"owner-saitama-{tag}.png")
    if want("owner-estimate"):
        page.goto(f"{base}/#/owner")
        page.wait_for_selector("main .owner")
        page.set_input_files("#owner-photo", str(SAMPLE_JSON.parent / "photos" / "plain_a.png"))
        page.fill("#owner-address", "東京都豊島区見本町1-2-3")
        page.click("button:has-text('寸法を推定')")
        page.wait_for_selector(".owner-estimate .cites li")
        page.wait_for_timeout(600)
        page.evaluate("window.scrollTo(0, 0)")
        page.screenshot(path=str(out / f"owner-estimate-{tag}.png"), full_page=True)
        print(out / f"owner-estimate-{tag}.png")
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

    web_api.can_draft = lambda: True  # 見本の推定役を使うので、鍵が無くてもボタンを有効にして撮る

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for tag, vp in VIEWPORTS.items():
            with tempfile.TemporaryDirectory() as td:  # 画面幅ごとに新しい台帳(確定が次の撮影に残らないように)
                db = Path(td) / "l.sqlite"
                seed_sample(db)
                seed_extra(db, Path(td))
                srv = make_server(App(db, SAMPLE_JSON.parent, sample=True, estimator_factory=SampleEstimator), "127.0.0.1", 0)
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
