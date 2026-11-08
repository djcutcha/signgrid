"""UI v3 の全画面を Playwright で撮り、JS エラーと 4xx/5xx の読み込みを数える(統合後の目視用)。

  python3 tools/ui_v3_shots.py --base http://127.0.0.1:8797 --out runs/ui_v3_all [--site demo-site-1]

PC 幅 1280 は Chromium、スマホ幅 390 は WebKit。各画面の png と、console のエラー・失敗した読み込みを summary.txt に書く。
"""
from __future__ import annotations
import argparse, sys
from pathlib import Path
from playwright.sync_api import sync_playwright

PAGES = ["h", "n1", "n2", "n3", "n4", "n5", "n6", "n7", "l", "k", "field/index"]
WITH_SITE = {"n2", "n3", "n4", "n5", "n6", "n7", "l"}

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--base", required=True); ap.add_argument("--out", required=True); ap.add_argument("--site", default="demo-site-1")
    a = ap.parse_args(); out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    lines, bad = [], 0
    with sync_playwright() as pw:
        for label, bt, w, h in (("pc", pw.chromium, 1280, 900), ("sp", pw.webkit, 390, 844)):
            br = bt.launch(); ctx = br.new_context(viewport={"width": w, "height": h}, device_scale_factor=1)
            for pg in PAGES:
                url = f"{a.base}/v3/{pg}.html" + (f"?site={a.site}" if pg in WITH_SITE else "")
                page = ctx.new_page(); errs, fails = [], []
                page.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
                page.on("console", lambda m, errs=errs: errs.append(m.text) if m.type == "error" else None)
                page.on("response", lambda r, fails=fails: fails.append(f"{r.status} {r.url}") if r.status >= 400 else None)
                page.goto(url, wait_until="networkidle"); page.wait_for_timeout(600)
                name = pg.replace("/", "_"); page.screenshot(path=str(out / f"{name}_{label}.png"), full_page=True)
                st = "ok" if not errs and not fails else "NG"; bad += st == "NG"
                lines.append(f"{st} {label} {url} errors={len(errs)} fails={len(fails)}" + "".join(f"\n    {x}" for x in errs + fails))
                page.close()
            br.close()
    (out / "summary.txt").write_text("\n".join(lines) + "\n", encoding="utf-8"); print("\n".join(lines)); print(f"NG pages: {bad}")
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
