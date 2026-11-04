"""素材 B: LINE ガイドの HTML の .phone 9 個を要素スクリーンショット(2 倍)で line_01.png〜line_09.png にする。
見出し(h3)と注記(.cap)は社内向けの文なので画像からは隠し、見出しだけ index.json に残す。"""
import json
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

SRC = "/Users/cutchamm/Projects/signgrid/runs/screen_flow/v3/LINEガイド_直した流れ_2026-11-04.html"
OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/video_v2/assets/line_v3")
OUT.mkdir(parents=True, exist_ok=True)
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1500, "height": 1000}, device_scale_factor=2).new_page()
    pg.goto("file://" + SRC)
    pg.add_style_tag(content=".phone h3,.phone .cap{display:none !important}")
    ph = pg.locator(".phone")
    n = ph.count()
    idx = []
    for i in range(n):
        el = ph.nth(i)
        title = pg.evaluate("e => e.querySelector('h3') && e.querySelector('h3').textContent", el.element_handle())
        name = f"line_{i + 1:02d}.png"
        el.screenshot(path=str(OUT / name))
        idx.append({"file": name, "title": title})
    (OUT / "index.json").write_text(json.dumps(idx, ensure_ascii=False, indent=1))
    print(n, "phones", [x["title"] for x in idx])
    b.close()
