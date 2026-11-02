"""リッチメニュー(トーク画面の下の3区画): 写真を送る / 現在地を送る / 今日の案件。

  python3 -m kanban_karte.intake.line.cli richmenu --dry-run   JSON を出し、画像を書くだけ(LINE に送らない)
  python3 -m kanban_karte.intake.line.cli richmenu --apply     作成 → 画像アップロード → 既定に設定(LINE_CHANNEL_ACCESS_TOKEN が要る)

「今日の案件」は postback(act=today)で、サーバが案件一覧(か現場の選択)を返す。
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ...output.common import _FONT_CANDIDATES
from .client import call
from .reply import LOCATION_URI

WIDTH, HEIGHT = 2500, 843
LABELS = ("写真を送る", "現在地を送る", "今日の案件")
CREATE_URL = "https://api.line.me/v2/bot/richmenu"
UPLOAD_URL = "https://api-data.line.me/v2/bot/richmenu/{id}/content"
DEFAULT_URL = "https://api.line.me/v2/bot/user/all/richmenu/{id}"
_FILLS = ((231, 242, 233), (231, 238, 247), (247, 241, 226))  # 無地の淡い3色(区画の見分け用)


def menu_json() -> dict:
    w = WIDTH // 3
    xs = (0, w, 2 * w)
    widths = (w, w, WIDTH - 2 * w)
    actions = (
        {"type": "camera", "label": LABELS[0]},
        {"type": "uri", "label": LABELS[1], "uri": LOCATION_URI},
        {"type": "postback", "label": LABELS[2], "data": "act=today", "displayText": LABELS[2]},
    )
    return {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,
        "name": "SignGrid main",
        "chatBarText": "メニュー",
        "areas": [{"bounds": {"x": x, "y": 0, "width": ww, "height": HEIGHT}, "action": a}
                  for x, ww, a in zip(xs, widths, actions)],
    }


def _font(size: int) -> ImageFont.FreeTypeFont:
    for p in _FONT_CANDIDATES:
        if p and Path(p).exists():
            try:
                return ImageFont.truetype(p, size)
            except OSError:
                continue
    raise RuntimeError("日本語フォントが見つからない(環境変数 KANBAN_KARTE_FONT にフォントのパスを指定)")


def render_image(path: str | Path) -> Path:
    """2500x843 の PNG。無地の3区画に文字だけ。"""
    im = Image.new("RGB", (WIDTH, HEIGHT), "white")
    d = ImageDraw.Draw(im)
    font = _font(110)  # 6文字が区画幅(833px)に余白つきで収まる大きさ
    for area, label, fill in zip(menu_json()["areas"], LABELS, _FILLS):
        b = area["bounds"]
        d.rectangle([b["x"], 0, b["x"] + b["width"] - 1, HEIGHT - 1], fill=fill, outline=(160, 160, 160), width=4)
        d.text((b["x"] + b["width"] / 2, HEIGHT / 2), label, font=font, fill=(40, 40, 40), anchor="mm")
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path, "PNG", optimize=True)
    return path


def apply(token: str, image_path: str | Path) -> str:
    """メニューを作って画像を載せ、全ユーザーの既定にする。richMenuId を返す。"""
    body = call(token, CREATE_URL, json.dumps(menu_json(), ensure_ascii=False).encode("utf-8"),
                what="richmenu create")
    menu_id = json.loads(body)["richMenuId"]
    call(token, UPLOAD_URL.format(id=menu_id), Path(image_path).read_bytes(), "image/png",
         what=f"richmenu image {menu_id}")
    call(token, DEFAULT_URL.format(id=menu_id), b"", what=f"richmenu default {menu_id}")
    return menu_id
