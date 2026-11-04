"""素材 C: 公開リポジトリ README の冒頭を PC 1920x1500(下へパンして README まで見せる) で撮る(repo_readme.png)。"""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright

OUT = Path(sys.argv[1] if len(sys.argv) > 1 else "runs/video_v2/assets/images/repo_readme.png")
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_context(viewport={"width": 1920, "height": 1500}, locale="ja-JP").new_page()
    r = pg.goto("https://github.com/djcutcha/signgrid", wait_until="networkidle")
    print("status", r.status if r else None, pg.title())
    pg.wait_for_selector("article.markdown-body", timeout=15000)
    pg.wait_for_timeout(800)
    pg.screenshot(path=str(OUT))
    b.close()
