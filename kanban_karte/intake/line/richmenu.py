"""リッチメニュー(トーク画面の下の 2×2 の4区画): 写真を送る / 現在地を送る / 今日の案件 / ガイド付き点検。

  python3 -m kanban_karte.intake.line.cli richmenu --dry-run   JSON を出し、画像を書くだけ(LINE に送らない)
  python3 -m kanban_karte.intake.line.cli richmenu --apply     作成 → 画像アップロード → 既定に設定(LINE_CHANNEL_ACCESS_TOKEN が要る)

「今日の案件」は postback(act=today)で、サーバが案件一覧(か現場の選択)を返す。
「ガイド付き点検」は postback(act=guide&start=1)で、ガイド付き点検(intake/line/guide.py)を始める。有資格者の今の使い方(写真を送る)は変えない。
"""
from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ...output.common import _FONT_CANDIDATES
from .client import call
from .reply import LOCATION_URI

WIDTH, HEIGHT = 2500, 1686  # LINE のリッチメニューの大きいサイズ(2500x1686)。2×2 で1区画 1250x843
CELL_W, CELL_H = WIDTH // 2, HEIGHT // 2
LABELS = ("写真を送る", "現在地を送る", "今日の案件", "ガイド付き点検")
CREATE_URL = "https://api.line.me/v2/bot/richmenu"
UPLOAD_URL = "https://api-data.line.me/v2/bot/richmenu/{id}/content"
DEFAULT_URL = "https://api.line.me/v2/bot/user/all/richmenu/{id}"
_FILLS = ((231, 242, 233), (231, 238, 247), (247, 241, 226), (242, 232, 244))  # 無地の淡い4色(区画の見分け用)


def menu_json() -> dict:
    actions = (
        {"type": "camera", "label": LABELS[0]},
        {"type": "uri", "label": LABELS[1], "uri": LOCATION_URI},
        {"type": "postback", "label": LABELS[2], "data": "act=today", "displayText": LABELS[2]},
        {"type": "postback", "label": LABELS[3], "data": "act=guide&start=1", "displayText": LABELS[3]},
    )
    return {
        "size": {"width": WIDTH, "height": HEIGHT},
        "selected": True,
        "name": "SignGrid main",
        "chatBarText": "メニュー",
        "areas": [{"bounds": {"x": (i % 2) * CELL_W, "y": (i // 2) * CELL_H, "width": CELL_W, "height": CELL_H},
                   "action": a} for i, a in enumerate(actions)],
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
    """2500x1686 の PNG。無地の4区画(2×2)に文字だけ。"""
    im = Image.new("RGB", (WIDTH, HEIGHT), "white")
    d = ImageDraw.Draw(im)
    font = _font(120)  # 7文字(ガイド付き点検)が区画幅(1250px)に余白つきで収まる大きさ
    for area, label, fill in zip(menu_json()["areas"], LABELS, _FILLS):
        b = area["bounds"]
        d.rectangle([b["x"], b["y"], b["x"] + b["width"] - 1, b["y"] + b["height"] - 1], fill=fill,
                    outline=(160, 160, 160), width=4)
        d.text((b["x"] + b["width"] / 2, b["y"] + b["height"] / 2), label, font=font, fill=(40, 40, 40), anchor="mm")
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
