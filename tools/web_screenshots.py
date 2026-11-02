"""画面のスクリーンショットを撮る(Playwright / Chromium)。見本データで一時の台帳を作り、サーバを立てて PC 幅 1280 とスマホ幅 390 を撮る。

  python3 tools/web_screenshots.py [--out docs/img] [--only ledger,karte,...]

撮る画面: ledger(台帳)、karte(カルテ)、karte-legal(カルテの法規カード)、karte-edit(黄色のマスを直した後)、karte-confirmed(確定後)、
rules(規則と出力)、intake(受付)、gov(行政。gov.make_sample の見本の束を入れる)、owner(オーナー。見本の寸法で「調べる」まで)、owner-saitama(同、埼玉県の住所・全高 5m)、
owner-estimate(オーナーの「寸法を推定」後。推定役は固定値の見本で、API は呼ばない)。
karte-edit 以降は台帳を書き換えるので、一時の台帳でだけ行う(見本の JSON は変わらない)。
guide = ガイド付き点検(docs/LINE.md)で作った看板のカルテ(guide-karte-pc/sp)。模擬 webhook で見本の台本を一周する。`--only guide` のときだけ撮る。
workflow = 一周のデモ(docs/WORKFLOW.md): 下書き→訂正→聞き返し→規則候補→矛盾→承認→次の下書きで効く、の7枚(workflow-1-draft …)。
台帳を大きく書き換えるので、他の場面と混ぜず `--only workflow` のときだけ撮る。判定役は固定値の見本(承認済みの規則に従う偽物)で、API は呼ばない。
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kanban_karte.dim.estimator import DimEstimate  # noqa: E402
from kanban_karte.eval.judge import Judge, Prediction  # noqa: E402
from kanban_karte.pipeline.draft import FORM_ITEMS  # noqa: E402
from kanban_karte.gov import make_sample as gov_sample  # noqa: E402
from kanban_karte.gov.batch_intake import intake as gov_intake  # noqa: E402
from kanban_karte.intake.line.webhook import sign as web_sign  # noqa: E402
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


RESULT_NUM = {"良好": 1, "経過観察": 2, "要改善": 3}
KEY_TO_FORM = {v: k for k, v in FORM_ITEMS.items()}


class SampleJudge(Judge):
    """一周のデモ用の固定値の判定役(API は呼ばない)。取付部-1 は経過観察と下書きし、承認済みの規則に
    「<項目> …「赤錆」…は<区分>」があれば、その区分に従う。実際の判定ではない。"""
    name = "sample"

    def __init__(self, rules):
        self.rules = rules

    def judge(self, item):
        key, result, ev = "取付部-1", "経過観察", "写真2で支柱の根元に赤錆。部分的に見える。"
        for r in self.rules:
            if r.split(" ", 1)[0] == key and "「赤錆」" in r:
                result = next(v for v in ("要改善", "経過観察", "良好") if f"は{v}(下書きは" in r)
                ev = f"写真2で支柱の根元に赤錆。承認済みの規則に従い{result}とした。"
        name = KEY_TO_FORM[key]
        return Prediction(RESULT_NUM[result], {name: RESULT_NUM[result]}, 0.6,
                          raw={"reasons": ["見本の下書き(固定値)。"], "item_notes": {name: {"evidence": ev, "needs_onsite": False}}},
                          usage={"cost_usd": 0.0})


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


def run(page, base, out, tag, only, db=None):
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
    if only is not None and "workflow" in only:
        workflow(page, base, out, tag, db)
    if only is not None and "guide" in only:
        guide_karte(page, base, out, tag, db)
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


def guide_karte(page, base, out, tag, db):
    """ガイド付き点検(LINE)で作った看板のカルテ。模擬 webhook で data/sample/guide_plan.json を一周し、「現場の回答(ガイド)」のバッジを撮る。
    写真は見本の画像(無地)。LINE にも API にも繋がない。"""
    import tempfile
    from kanban_karte.intake.line.cli import SIM_SECRET
    from kanban_karte.intake.line.simulate import DirContentClient, build_guide_deliveries
    from kanban_karte.intake.line.webhook import LineApp
    from kanban_karte.ledger import Ledger

    plan_file = SAMPLE_JSON.parent / "guide_plan.json"
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        plan = build_guide_deliveries(plan_file, td / "content", run_id="shot")
        app = LineApp(SIM_SECRET, DirContentClient(td / "content"), lambda: Ledger(db), td / "inbox")
        for body in plan.deliveries:
            code, res = app.handle(body, web_sign(SIM_SECRET, body))
            assert code == 200 and not res.errors, res.errors if res else code
        with Ledger(db) as lg:
            sg = next(s for s in lg.list_signs() if (s.draft_by or "").startswith("line_guide"))
            sg.photos = [f"photos/{Path(p).name}" for p in plan_photo_names(plan_file)]  # 受信箱は一時なので、見本の画像に差し替えて表示
            lg.upsert_sign(sg)
    shoot(page, base, out, tag, f"/sign/{sg.id}", "guide-karte", wait="main .karte")


def plan_photo_names(plan_file):
    import json
    doc = json.loads(Path(plan_file).read_text(encoding="utf-8"))
    names = []
    for sg in doc["signs"]:
        names.append(sg["overview"])
        for p in sg["parts"]:
            names += p.get("photos", [])
    return names


def workflow(page, base, out, tag, db):
    """写真→下書き→訂正→聞き返し→規則候補→矛盾→承認→次の下書きで効く。--sample の固定データで一周する。"""
    from kanban_karte.ledger import Ledger, Sign

    def snap(name, full=True):
        page.wait_for_timeout(300)
        page.evaluate("window.scrollTo(0, 0)")
        path = out / f"workflow-{name}-{tag}.png"
        page.screenshot(path=str(path), full_page=full)
        print(path)

    def correct(sign, value, note, answer):
        page.goto(f"{base}/#/sign/{sign}")
        page.wait_for_selector("main .karte")
        page.fill("#by", "見本 太郎")
        page.click('.item[data-key="取付部-1"] .item-head')
        page.fill("#note", note)
        page.click(f".item.open .seg button:has-text('{value}')")
        page.wait_for_selector(".clarify")
        return answer

    with Ledger(db) as lg:  # 次の下書きの材料: 同じ写真で、まだ下書きのない看板
        b = lg.add_bundle("/scan/見本_次の現場.pdf", "sample-sha-next", 3)
        lg.set_bundle(b, status="classified", site_id="demo-site-1")
        lg.upsert_sign(Sign(id="demo-next", site_id="demo-site-1", name="袖看板(次の現場)", type="袖", size_w=900, size_h=600,
                            photos=lg.get_sign("demo-001").photos[:3], source_bundle=b))

    page.goto(f"{base}/#/sign/demo-001")                                    # 1 写真→下書き
    page.wait_for_selector("main .karte")
    snap("1-draft")
    correct("demo-001", "要改善", "赤錆", None)                              # 2 訂正→聞き返しの1問
    snap("2-clarify")
    page.fill("#clarify-answer", "全周に回っている。写真3で見える")           # 3 答える→規則候補
    page.click("button:has-text('答える')")
    page.wait_for_selector(".notice:has-text('規則候補')")
    snap("3-candidate")
    correct("demo-002", "経過観察", "赤錆", None)                            # 別の看板で、同じ所見に別の区分
    page.fill("#clarify-answer", "全周に回っている")
    page.click("button:has-text('答える')")
    page.wait_for_selector(".notice:has-text('規則候補')")
    page.goto(f"{base}/#/rules")                                            # 4 矛盾を知らせる
    page.wait_for_selector(".conflict-pair")
    snap("4-conflict")
    page.request.post(f"{base}/api/bundles/{b}/draft", data={})              # 5 承認前: 候補は下書きに効かない
    page.goto(f"{base}/#/sign/demo-next")
    page.wait_for_selector("main .karte")
    snap("5-not-yet")
    page.goto(f"{base}/#/rules")                                            # 6 どちらを残すか→承認
    page.wait_for_selector(".conflict-pair")
    page.fill(".conflict-pair .reason", "全周に回っている赤錆は、支柱の断面が減るので要改善")
    page.fill("#rule-by", "見本 太郎")
    page.click(".conflict-pair .rule >> nth=0 >> button:has-text('こちらを残す')")
    page.wait_for_selector(".rule.st-approved")
    snap("6-approved")
    page.request.post(f"{base}/api/bundles/{b}/draft", data={})              # 7 承認済みの規則が次の下書きに効く
    page.goto(f"{base}/#/sign/demo-next")
    page.wait_for_selector("main .karte")
    page.wait_for_timeout(300)
    snap("7-effect")


def field(page, base: str, out: Path, tag: str, db: Path) -> None:
    """一周の台帳(data/private/field_site.sqlite)の画面: 台帳一覧・カルテ1基(黄色あり)・様式出力。
    顧客の写真が写るので、出力先は docs/img ではなく未追跡の runs/ にする。"""
    from kanban_karte.ledger import Ledger
    from kanban_karte.output.common import unjudged_item_keys
    with Ledger(db) as lg:
        signs = [x for x in lg.list_signs() if x.status == "draft"]
    pick = max(signs, key=lambda x: (0 < len(unjudged_item_keys(x)) < 18, len(x.photos), x.id))
    shoot(page, base, out, tag, "/", "field-ledger", wait="main .ledger tbody tr")
    shoot(page, base, out, tag, f"/sign/{pick.id}", "field-karte", wait="main .karte")
    shoot(page, base, out, tag, "/rules", "field-output", wait="main .rules .out-site")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/img")
    ap.add_argument("--only", default=None)
    ap.add_argument("--field-db", default="data/private/field_site.sqlite", help="--only field のときの台帳")
    ap.add_argument("--field-data", default="data/private/field_intake", help="--only field の画像置き場")
    a = ap.parse_args(argv)
    only = set(a.only.split(",")) if a.only else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if only == {"field"}:
        return shoot_field(a, out)
    from playwright.sync_api import sync_playwright

    web_api.can_draft = lambda: True  # 見本の推定役を使うので、鍵が無くてもボタンを有効にして撮る

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for tag, vp in VIEWPORTS.items():
            with tempfile.TemporaryDirectory() as td:  # 画面幅ごとに新しい台帳(確定が次の撮影に残らないように)
                db = Path(td) / "l.sqlite"
                seed_sample(db)
                seed_extra(db, Path(td))
                srv = make_server(App(db, SAMPLE_JSON.parent, sample=True, estimator_factory=SampleEstimator,
                                      judge_factory=SampleJudge), "127.0.0.1", 0)
                base = f"http://127.0.0.1:{srv.server_address[1]}"
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                ctx = browser.new_context(viewport=vp, device_scale_factor=2 if tag == "sp" else 1, locale="ja-JP",
                                          has_touch=tag == "sp", is_mobile=tag == "sp")
                page = ctx.new_page()
                try:
                    run(page, base, out, tag, only, db)
                finally:
                    ctx.close()
                    srv.shutdown()
                    srv.server_close()
        browser.close()
    return 0


def shoot_field(a, out: Path) -> int:
    import shutil
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for tag, vp in VIEWPORTS.items():
            with tempfile.TemporaryDirectory() as td:  # 台帳は複製(画面の操作で元を変えない)
                db = Path(td) / "l.sqlite"
                shutil.copyfile(a.field_db, db)
                srv = make_server(App(db, Path(a.field_data)), "127.0.0.1", 0)
                base = f"http://127.0.0.1:{srv.server_address[1]}"
                threading.Thread(target=srv.serve_forever, daemon=True).start()
                ctx = browser.new_context(viewport=vp, device_scale_factor=2 if tag == "sp" else 1, locale="ja-JP",
                                          has_touch=tag == "sp", is_mobile=tag == "sp")
                page = ctx.new_page()
                try:
                    field(page, base, out, tag, db)
                finally:
                    ctx.close()
                    srv.shutdown()
                    srv.server_close()
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
