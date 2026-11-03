"""提出動画の画面の場面を、Playwright の録画で素材化する(見本データだけ。API は呼ばない)。

  python3 tools/video_capture.py [--out runs/video_capture] [--only 01,02,...] [--port 8795]

場面(クリップ名= NN_<場面>_<pc|sp>.mp4。PC= 1920×1080、SP= 780×1688(390×844 を device_scale_factor=2 で)、H.264 / 30fps / 音なし):
  01 一手で始まる(受付→台帳→カルテ→様式出力)  02 受け継ぐ一周(WORKFLOW の7場面)  03 重なる負担(規則と出力)
  04 行政(撮る→送る→候補→地図、SP)  05 看板の持ち主(SP)  06 寸法の実測(カルテの実測バッジと寸法枠)
見本の台帳(meta.sample=true)を一時の場所に作ってサーバを立てる。data/private は開かない。
先頭と末尾に 1 秒の静止を入れる。スクロールは page.mouse.wheel で 1 秒 300px。
web_screenshots.py の見本の推定役・判定役・投入を使う。判定役は固定値の見本(承認済みの規則に従う偽物)。
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import web_screenshots as ws  # noqa: E402
from kanban_karte.web import api as web_api  # noqa: E402
from kanban_karte.web.server import SAMPLE_JSON, App, make_server, seed_sample  # noqa: E402

FFMPEG = "/opt/homebrew/bin/ffmpeg"
PC = {"width": 1920, "height": 1080}
SP = {"width": 390, "height": 844}
PX_PER_SEC = 300


class Rec:
    """1クリップ分の操作の道具。mark() の時点から後を動画にする(読み込み中の空白を切る)。"""

    def __init__(self, page, base: str, vp: dict):
        self.page, self.base, self.vp = page, base, vp
        self.t0 = time.time()
        self.start = 0.0
        self.shots = None  # SP(2倍画素)では mark() 以後の待ちの間に Shots で撮る

    def mark(self):
        self.start = time.time() - self.t0
        if self.shots:
            self.shots.snap()

    def hold(self, sec: float):
        self.hold_until(time.time() + sec)

    def hold_until(self, end: float):
        """end まで待つ。2倍画素の撮影中は待ちの間に撮り続ける(撮影が遅れた分は次の待ちから削って 300px/秒を保つ)。"""
        if not self.shots or not self.start:
            self.page.wait_for_timeout(max(0, int((end - time.time()) * 1000)))
            return
        while time.time() < end:
            self.shots.snap()
            self.page.wait_for_timeout(max(0, int(min(end - time.time(), 0.05) * 1000)))

    def scroll(self, px: int):
        """1秒 300px で px だけ下へ(負なら上へ)。100ms ごとに 30px。"""
        p = self.page
        p.mouse.move(self.vp["width"] // 2, self.vp["height"] // 2)
        step = 30 if px > 0 else -30
        t = time.time()
        for _ in range(abs(px) // 30):
            if self.shots:  # 連写(SP)では wheel の滑らか移動が固定ヘッダを1フレーム遅らせるので、即時に動かす
                p.evaluate("s => window.scrollBy({top: s, behavior: 'instant'})", step)
            else:
                p.mouse.wheel(0, step)
            t += 0.1
            self.hold_until(t)

    def scroll_for(self, sec: float):
        """ページの下端まで、または sec 秒のうち早い方まで、ゆっくり下へ。残りは静止。"""
        p = self.page
        room = p.evaluate("document.documentElement.scrollHeight - innerHeight - scrollY")
        px = min(int(room), int(sec * PX_PER_SEC)) // 30 * 30
        self.scroll(px)
        self.hold(max(0.0, sec - px / PX_PER_SEC))

    def top(self):
        self.page.evaluate("window.scrollTo(0, 0)")

    def go(self, hash_: str, wait: str, entry: str = ""):
        self.page.goto(f"{self.base}{entry}/#{hash_}")
        self.page.wait_for_selector(wait, timeout=10000)
        self.page.wait_for_timeout(300)


def to_mp4(webm: Path, mp4: Path, ss: float) -> float:
    subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-ss", f"{ss:.2f}", "-i", str(webm), "-r", "30", "-c:v", "libx264",
                    "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(mp4)], check=True)
    return probe_duration(mp4)


VIDEO_NOTE_JS = """
(() => {
  const css = document.createElement('style');
  css.textContent = '#sample-badge{display:none !important}'
    + ' #kk-video-note{position:fixed;right:6px;bottom:4px;font:10px/1.2 "Hiragino Sans","Hiragino Kaku Gothic ProN",sans-serif;'
    + 'color:#666;background:rgba(255,255,255,.75);padding:1px 4px;border-radius:3px;z-index:9999;pointer-events:none}';
  const add = () => {
    document.head.appendChild(css);
    if (window.top !== window) return;  // 注記は親の画面だけ(地図の iframe にも付くと右下で2重になる)
    const d = document.createElement('div'); d.id = 'kk-video-note';
    d.textContent = 'データは生成したサンプルデータです。'; document.body.appendChild(d);
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', add); else add();
})();
"""
# 動画の画面では「見本データ」の帯を出さず、右下に極小の注記だけ出す(Cutcha 2026-11-03 16:10「見本データ ではなくて、
# データは生成したサンプルデータです。のように極小で出す」)。帯は見本の台帳で出る正しい表示なので、隠すのはこの録画だけ。


class Shots:
    """device_scale_factor=2 の実画素(780×1688)を page.screenshot の連続撮りで取る。
    Playwright の録画は 2倍にしても CSS サイズのまま左上に描くだけ、CDP の screencast も CSS サイズのフレームしか出さないため。
    Rec の待ち(hold / scroll)の間に撮り、時刻つきで保存して ffmpeg の concat で可変フレームレートの動画に戻す(スクロールは約 8〜10 枚/秒)。"""

    def __init__(self, page, dirpath: Path):
        self.page, self.dir, self.frames, self.end = page, dirpath, [], 0.0  # frames= (時刻, ファイル)

    def snap(self):
        f = self.dir / f"f{len(self.frames):05d}.jpg"
        # 描画を2フレーム待ってから撮る(固定ヘッダ(position:sticky)が1フレーム遅れて上に灰色の帯が写るのを防ぐ)
        self.page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        self.page.screenshot(path=str(f), type="jpeg", quality=92)
        self.frames.append((time.time(), f))

    def to_mp4(self, mp4: Path) -> float:
        fr = self.frames
        lst = self.dir / "list.txt"
        with lst.open("w") as w:
            for (t, f), nxt in zip(fr, [x[0] for x in fr[1:]] + [max(self.end, fr[-1][0] + 0.1)]):
                w.write(f"file '{f}'\nduration {max(nxt - t, 0.001):.4f}\n")
            w.write(f"file '{fr[-1][1]}'\n")
        subprocess.run([FFMPEG, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", "fps=30",
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-an", "-movflags", "+faststart", str(mp4)], check=True)
        return probe_duration(mp4)


def probe_duration(mp4: Path) -> float:
    r = subprocess.run([FFMPEG, "-i", str(mp4)], capture_output=True, text=True).stderr
    h, m, s = r.split("Duration: ")[1].split(",")[0].split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def clip(browser, name: str, vp: dict, body, out: Path, port: int, pre=None) -> tuple[str, float]:
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        db = td / "l.sqlite"
        seed_sample(db)
        ws.seed_extra(db, td)
        srv = make_server(App(db, SAMPLE_JSON.parent, sample=True, estimator_factory=ws.SampleEstimator,
                              judge_factory=ws.SampleJudge), "127.0.0.1", port)
        base = f"http://127.0.0.1:{port}"
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        ctx_args = dict(viewport=vp, locale="ja-JP", accept_downloads=True)
        try:
            state = {}
            if pre:  # 録画しない準備(台帳の下ごしらえ)
                c0 = browser.new_context(**ctx_args)
                state = pre(c0.new_page(), base, db, td) or {}
                c0.close()
            hi = vp is SP  # SP は 2倍の画素(CSS の見え方は 390×844 のまま)
            if hi:
                ctx = browser.new_context(**ctx_args, device_scale_factor=2)
            else:
                ctx = browser.new_context(**ctx_args, record_video_dir=str(td / "v"), record_video_size=vp)
            page = ctx.new_page()
            page.add_init_script(VIDEO_NOTE_JS)  # 動画用: 「見本データ」帯を隠し、極小の注記を右下に(Cutcha 2026-11-03 16:10)
            rec = Rec(page, base, vp)
            if hi:
                (td / "f").mkdir()
                rec.shots = Shots(page, td / "f")
            try:
                body(rec, db, state)
                if rec.shots:
                    rec.shots.end = time.time()
            finally:
                video = page.video
                ctx.close()
            mp4 = out / f"{name}.mp4"
            dur = rec.shots.to_mp4(mp4) if rec.shots else to_mp4(Path(video.path()), mp4, rec.start)
        finally:
            srv.shutdown()
            srv.server_close()
    print(f"{mp4}  {dur:.1f}s")
    return name, dur


# ---- 場面 ----

def s01(r: Rec, db, st):
    """受付 → 台帳 → カルテ → 様式出力。各 3.5〜5 秒(全体 20〜23 秒)。"""
    r.go("/intake", "main .intake tbody tr")
    r.mark(); r.hold(0.7)
    r.scroll_for(3)
    r.go("/", "main .ledger tbody tr"); r.hold(0.5); r.scroll_for(3)
    r.go(f"/sign/{ws.SIGN}", "main .karte"); r.hold(0.5); r.scroll_for(4.5)
    r.go("/rules", "main .rules .out-site"); r.hold(0.7)
    r.page.click(".out-site .btn >> nth=0"); r.hold(2)
    r.page.click(".out-site .btn >> nth=1"); r.hold(2)
    r.hold(0.7)


def s02(r: Rec, db, st):
    """受け継ぐ一周: 下書き→訂正→聞き返し→規則候補→矛盾→承認→次の下書きに効く。"""
    from kanban_karte.ledger import Ledger, Sign
    p = r.page
    with Ledger(db) as lg:  # 次の下書きの材料
        b = lg.add_bundle("/scan/見本_次の現場.pdf", "sample-sha-next", 3)
        lg.set_bundle(b, status="classified", site_id="demo-site-1")
        lg.upsert_sign(Sign(id="demo-next", site_id="demo-site-1", name="袖看板(次の現場)", type="袖", size_w=900, size_h=600,
                            photos=lg.get_sign("demo-001").photos[:3], source_bundle=b))

    def correct(sign, value, note):
        r.go(f"/sign/{sign}", "main .karte")
        p.fill("#by", "見本 太郎")
        p.click('.item[data-key="取付部-1"] .item-head')
        p.fill("#note", note)
        p.click(f".item.open .seg button:has-text('{value}')")
        p.wait_for_selector(".clarify")

    r.go("/sign/demo-001", "main .karte")
    p.click('.item[data-key="取付部-1"] .item-head')           # 1 下書きと根拠
    r.mark(); r.hold(2)
    p.click('.item[data-key="取付部-1"] .item-head')
    correct("demo-001", "要改善", "赤錆"); r.hold(2)           # 2 訂正→聞き返し
    p.fill("#clarify-answer", "全周に回っている。写真3で見える")
    p.click("button:has-text('答える')")
    p.wait_for_selector(".notice:has-text('規則候補')"); r.hold(2)  # 3 規則の候補
    correct("demo-002", "経過観察", "赤錆")
    p.fill("#clarify-answer", "全周に回っている")
    p.click("button:has-text('答える')")
    p.wait_for_selector(".notice:has-text('規則候補')")
    r.go("/rules", ".conflict-pair"); r.hold(2.5)               # 4 矛盾の検出
    p.request.post(f"{r.base}/api/bundles/{b}/draft", data={})
    p.fill(".conflict-pair .reason", "全周に回っている赤錆は、支柱の断面が減るので要改善")
    p.fill("#rule-by", "見本 太郎")
    p.click(".conflict-pair .rule >> nth=0 >> button:has-text('こちらを残す')")
    p.wait_for_selector(".rule.st-approved"); r.hold(2)         # 5 承認
    p.request.post(f"{r.base}/api/bundles/{b}/draft", data={})
    r.go("/sign/demo-next", "main .karte")
    p.click('.item[data-key="取付部-1"] .item-head'); r.hold(3)  # 6 次の下書きに効く
    r.hold(1)


def s03(r: Rec, db, st):
    """規則と出力: 客先型と東京都様式の2種類の出力が並ぶ。"""
    for v in ("要改善", "経過観察"):
        r.page.request.post(f"{r.base}/api/signs/demo-002/items", data={"item": "取付部-1", "value": v, "by": "見本 花子", "note": "根元に錆"})
    r.go("/rules", "main .rules .out-site")
    r.mark(); r.hold(1)
    r.page.click(".out-site .btn >> nth=1"); r.hold(2)
    r.page.click(".out-site .btn >> nth=0"); r.hold(2.5)
    r.hold(1)


def s04(r: Rec, db, st):
    """行政(SP): ① 撮る → ② 送る → 候補のカード → 地図(赤・橙・緑・灰)。"""
    p = r.page
    p.context.grant_permissions(["geolocation"])
    p.context.set_geolocation({"latitude": 35.8910, "longitude": 139.7908})
    p.goto(f"{r.base}/gov/#/gov?today=2026-11-02")
    p.wait_for_selector("#gov-photo", state="attached")
    p.wait_for_timeout(500)
    photos = sorted(str(x) for x in (Path(db).parent / "gov" / "photos").glob("*.jpg"))
    p.locator("#gov-capture").scroll_into_view_if_needed()
    r.mark(); r.hold(1)
    p.set_input_files("#gov-photo", photos)
    p.wait_for_function("document.querySelector('.gov-sel').textContent.startsWith('%d 枚')" % len(photos))
    r.hold(2)
    p.click(".gov-capture button:has-text('送る')")
    p.wait_for_selector(".gov-done", timeout=20000)
    p.locator(".gov-done").scroll_into_view_if_needed(); r.hold(2)
    # 件数タイル(#gov-results)の上端を固定ヘッダの直下に揃える位置までゆっくり下へ(「登録 N / 時刻」の行がヘッダに半分隠れないように)
    delta = p.evaluate("Math.round(document.getElementById('gov-results').getBoundingClientRect().top - document.querySelector('.topbar').offsetHeight - 8)")
    r.scroll(delta // 30 * 30)
    p.frame_locator(".gov-map iframe").locator(".leaflet-tile-loaded").first.wait_for(timeout=15000)
    r.hold(5)
    r.hold(1)


def s05(r: Rec, db, st):
    """看板の持ち主(SP): 許可の要否(結論)を先に映し、3秒止めてから義務の詳細へゆっくり下る。"""
    p = r.page
    r.go("/owner", "main .owner", entry="/owner")
    p.set_input_files("#owner-photo", str(SAMPLE_JSON.parent / "photos" / "plain_a.png"))
    p.fill("#owner-address", "東京都豊島区見本町1-2-3")
    p.fill("#owner-w", "1.95"); p.fill("#owner-h", "0.6")
    r.mark(); r.hold(1)
    p.click("button:has-text('調べる')")
    p.wait_for_selector(".owner-result .cites li")
    # 結果の見出し(直下に 自家用・許可 の行)が、固定ヘッダ(約 40px)の下の、画面の上に来る位置へ
    p.evaluate("window.scrollTo(0, document.querySelector('.owner-result').getBoundingClientRect().top + scrollY - 52)")
    r.hold(3)
    r.scroll_for(4)
    r.hold(1)


def pre06(page, base, db, td):
    ws.guide_karte(page, base, td, "pc", db)
    from kanban_karte.ledger import Ledger
    with Ledger(db) as lg:
        sid = next(s.id for s in lg.list_signs() if (s.draft_by or "").startswith("line_guide"))
    return {"sign": sid}


def s06(r: Rec, db, st):
    """寸法の実測の結果: カルテの実測バッジと寸法枠。"""
    r.go(f"/sign/{st['sign']}", "main .size-card")
    r.page.locator(".size-card").scroll_into_view_if_needed()
    r.mark(); r.hold(3)
    r.hold(2)


SCENES = [
    ("01", "demo-start", "pc", s01, None), ("02", "inherit-loop", "pc", s02, None), ("03", "burden-output", "pc", s03, None),
    ("04", "gov", "sp", s04, None), ("05", "owner", "sp", s05, None), ("06", "measure", "pc", s06, pre06),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="runs/video_capture")
    ap.add_argument("--only", default=None, help="01,02 のように番号で絞る")
    ap.add_argument("--port", type=int, default=8795)
    a = ap.parse_args(argv)
    only = set(a.only.split(",")) if a.only else None
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    web_api.can_draft = lambda: True
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for num, scene, dev, body, pre in SCENES:
            if only and num not in only:
                continue
            clip(browser, f"{num}_{scene}_{dev}", PC if dev == "pc" else SP, body, out, a.port, pre)
        browser.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
