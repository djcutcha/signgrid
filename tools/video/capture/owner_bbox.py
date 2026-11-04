"""素材 A: 稼働中の /owner/(SP)で「写真 → 点線の枠 → 寸法の範囲 → 許可の判定カード」を録り、05_owner_bbox_sp.mp4 にする。
  python3 tools/video/capture/owner_bbox.py [--base http://127.0.0.1:8790] [--out runs/video_v2/assets/05_owner_bbox_sp.mp4]
推定 API は呼ばない(保存済みの応答 estimate_pc.json を page.route で返す)。SP 390x844 を device_scale_factor=2 で 780x1688、
Shots(連続撮り)で 2 倍画素を取り、H.264 30fps 音なしの mp4 にする。右下に極小の注記。"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))
import video_capture as vc  # noqa: E402
from playwright.sync_api import sync_playwright  # noqa: E402

FLOW = Path("/Users/cutchamm/Projects/kk-wt/ui-mock/runs/screen_flow_b")
PHOTO = str(FLOW / "photos" / "street_dusk.jpg")
EST = (FLOW / "owner" / "estimate_pc.json").read_text()
NOTE_JS = vc.VIDEO_NOTE_JS.replace("データは生成したサンプルデータです。", "写真は生成したサンプルです。")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8790")
    ap.add_argument("--out", default=str(ROOT / "runs/video_v2/assets/05_owner_bbox_sp.mp4"))
    a = ap.parse_args()
    out = Path(a.out)
    fdir = ROOT / "runs/video_v4/gen/owner_frames"
    fdir.mkdir(parents=True, exist_ok=True)
    for f in fdir.glob("*"):
        f.unlink()
    with sync_playwright() as p:
        b = p.chromium.launch()
        ctx = b.new_context(viewport=vc.SP, device_scale_factor=2, is_mobile=True, has_touch=True, locale="ja-JP")
        pg = ctx.new_page()
        # 応答はクロージャで渡す(既定値つきの第 2 引数にすると Playwright が request を上書きする)
        body = EST
        pg.route("**/api/owner/estimate", lambda route: route.fulfill(status=200, content_type="application/json", body=body))
        pg.add_init_script(NOTE_JS)
        pg.goto(f"{a.base}/owner/#/owner")
        pg.wait_for_selector("#owner-photo", state="attached")
        pg.add_style_tag(content=".topbar{position:static !important}")
        rec = vc.Rec(pg, a.base, vc.SP)
        rec.shots = vc.Shots(pg, fdir)
        rec.mark(); rec.hold(1.0)
        pg.set_input_files("#owner-photo", PHOTO)
        pg.wait_for_selector(".owner-preview img"); rec.hold(1.2)
        rec.scroll(60)
        for sel, val in (("#owner-address", "東京都杉並区阿佐谷南1丁目"), ("#owner-target", "左側の壁面の横長の看板(ラーメン屋)"), ("#owner-height", "4")):
            pg.fill(sel, val); rec.hold(0.6)
        pg.locator('button:text-is("寸法を推定")').scroll_into_view_if_needed(); rec.hold(0.6)
        pg.click('button:text-is("寸法を推定")')
        pg.wait_for_selector("#owner-est-out .est-size", timeout=20000); rec.hold(0.6)
        pg.locator("#owner-est-out").scroll_into_view_if_needed(); rec.hold(2.0)  # 点線の枠の写真で止める
        rec.scroll(150); rec.hold(1.5)
        pg.click('button:text-is("調べる")')
        pg.wait_for_selector("#owner-out > *", timeout=20000); rec.hold(0.6)
        pg.locator("#owner-out").scroll_into_view_if_needed(); rec.hold(3.0)
        rec.shots.end = time.time()
        ctx.close(); b.close()
    out.parent.mkdir(parents=True, exist_ok=True)
    print(out, f"{rec.shots.to_mp4(out):.1f}s")


if __name__ == "__main__":
    main()
