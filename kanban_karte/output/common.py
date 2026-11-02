"""様式出力の共通部品。台帳の読み出し、写真の縮小と配置、「未判定」の赤字、下書きの透かし。

方針:
- データが無い項目は空欄にして、赤字で「未判定」と出す(黙って「良好」にしない)。
- 日付は YYYY-MM-DD。
- 有資格者が確認・署名するまでは下書き。バナー・印刷ヘッダ・斜めの透かし画像の3重で示す。
"""
from __future__ import annotations

import io
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
from pathlib import Path

from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.properties import PageSetupProperties
from PIL import Image as PILImage
from PIL import ImageDraw, ImageFont, ImageOps

from ..ledger.db import Ledger, Sign, Site
from ..ledger.items import ITEM_KEYS, _ITEMS

UNJUDGED = "未判定"
DRAFT_BANNER = "下書き(有資格者が確認・署名するまでは、正式な報告書ではありません)"
PHOTO_LONG_SIDE = 640  # 埋め込む写真の長辺(px)
MAX_PHOTOS = 6
RANK_LABEL = {1: "良好", 2: "経過観察", 3: "要改善", 4: "即時対応"}

FONT = "游ゴシック"
RED = "C00000"
F_BASE = Font(name=FONT, size=10)
F_SMALL = Font(name=FONT, size=8, color="595959")
F_BOLD = Font(name=FONT, size=10, bold=True)
F_TITLE = Font(name=FONT, size=16, bold=True)
F_UNJUDGED = Font(name=FONT, size=10, bold=True, color=RED)
F_BANNER = Font(name=FONT, size=11, bold=True, color=RED)
FILL_BANNER = PatternFill("solid", fgColor="FDE9E7")
FILL_INPUT = PatternFill("solid", fgColor="FFF8DC")  # 人が書き込む欄
FILL_HEAD = PatternFill("solid", fgColor="E7E6E6")
_THIN = Side(style="thin", color="404040")
BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
LEFT = Alignment(horizontal="left", vertical="center", wrap_text=True)

_ZEN_DIGITS = "０１２３４５６７８９"


def zen(n: int) -> str:
    return "".join(_ZEN_DIGITS[int(c)] for c in str(n))


# 18項目: (キー, 点検箇所, 全角番号付きの項目名) を items.py の正本から作る
ITEM_ROWS: list[tuple[str, str, str]] = [(f"{g}-{n}", g, f"{zen(n)} {t}") for g, n, t in _ITEMS]
assert [k for k, _, _ in ITEM_ROWS] == ITEM_KEYS


# ---------------------------------------------------------------- 台帳の読み出し
@dataclass
class SiteData:
    site: Site
    signs: list[Sign]
    ledger: Ledger


def load_site(ledger: Ledger, site_id: str) -> SiteData:
    site = ledger.get_site(site_id)
    if site is None:
        raise LookupError(f"現場 {site_id!r} が台帳にありません")
    signs = ledger.list_signs(site_id=site_id)
    if not signs:
        raise ValueError(f"現場 {site_id!r} に看板が登録されていません")
    return SiteData(site, signs, ledger)


def fmt_date(v) -> str | None:
    """日付を YYYY-MM-DD に。空は None。"""
    if v in (None, ""):
        return None
    if isinstance(v, (date, datetime)):
        return v.strftime("%Y-%m-%d")
    s = str(v).strip()
    m = re.match(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", s)
    return f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}" if m else s


def size_text(s: Sign) -> str | None:
    if s.size_w is None and s.size_h is None:
        return None
    return f"W{s.size_w if s.size_w is not None else '?'} × H{s.size_h if s.size_h is not None else '?'} mm"


def sheet_name(prefix: str, ident: str) -> str:
    """Excel のシート名(31字以内、禁止文字なし)。"""
    clean = re.sub(r"[\\/*?:\[\]]", "_", f"{prefix}_{ident}")
    return clean[:31]


def unique_sheet_names(prefix: str, ids: list[str]) -> list[str]:
    seen, out = set(), []
    for i in ids:
        base = sheet_name(prefix, i)
        name, k = base, 2
        while name in seen:
            suffix = f"~{k}"
            name, k = base[: 31 - len(suffix)] + suffix, k + 1
        seen.add(name)
        out.append(name)
    return out


# ---------------------------------------------------------------- セルの書き込み
def put(ws, rng: str, value=None, *, font=F_BASE, fill=None, align=LEFT, border=True, fmt=None):
    """範囲(結合あり)に値と書式を入れる。結合範囲の全セルに罫線をつける(Excel で枠が欠けないように)。"""
    cells = ws[rng]
    if not isinstance(cells, tuple):  # 単一セル
        cells = ((cells,),)
    elif cells and not isinstance(cells[0], tuple):  # 1 行だけの範囲
        cells = (cells,)
    if ":" in rng:
        ws.merge_cells(rng)
    first = cells[0][0]
    first.value = value
    for row in cells:
        for c in row:
            c.font, c.alignment = font, align
            if border:
                c.border = BORDER
            if fill is not None:
                c.fill = fill
            if fmt:
                c.number_format = fmt
    return first


def unjudged(ws, rng: str, *, align=CENTER):
    return put(ws, rng, UNJUDGED, font=F_UNJUDGED, align=align)


def banner(ws, rng: str):
    put(ws, rng, DRAFT_BANNER, font=F_BANNER, fill=FILL_BANNER, align=CENTER, border=False)


def page_setup(ws, *, landscape: bool = False, header: bool = True):
    ws.page_setup.paperSize = 9  # A4
    ws.page_setup.orientation = "landscape" if landscape else "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_margins.left = ws.page_margins.right = 0.5
    ws.page_margins.top = ws.page_margins.bottom = 0.6
    ws.sheet_view.showGridLines = False
    if header:
        ws.oddHeader.center.text = "下書き(有資格者が確認・署名するまで)"
        ws.oddHeader.center.size = 9
        ws.oddHeader.center.color = RED


def col_px(width_chars: float) -> int:
    return int(width_chars * 7 + 5)


# ---------------------------------------------------------------- 写真
def prepare_photo(path: Path) -> tuple[io.BytesIO, int, int] | None:
    """写真を長辺 PHOTO_LONG_SIDE px 以内に縮小した JPEG にする(拡大はしない)。開けなければ None。"""
    try:
        with PILImage.open(path) as im:
            im = ImageOps.exif_transpose(im)
            im.thumbnail((PHOTO_LONG_SIDE, PHOTO_LONG_SIDE), PILImage.LANCZOS)
            if im.mode != "RGB":
                bg = PILImage.new("RGB", im.size, "white")
                rgba = im.convert("RGBA")
                bg.paste(rgba, mask=rgba.split()[3])
                im = bg
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            buf.seek(0)
            return buf, im.size[0], im.size[1]
    except (OSError, ValueError):
        return None


def _place(ws, img: XLImage, col0: int, row0: int, dx: int, dy: int, w: int, h: int):
    """画像を (col0,row0) セルの左上から (dx,dy) px ずらして、w×h px で置く(0 始まり)。"""
    emu = 9525
    img.width, img.height = w, h
    marker = AnchorMarker(col=col0, colOff=dx * emu, row=row0, rowOff=dy * emu)
    img.anchor = OneCellAnchor(_from=marker, ext=XDRPositiveSize2D(w * emu, h * emu))
    ws.add_image(img)


def photo_grid(ws, data: SiteData, sign: Sign, *, first_row: int, ncols: int = 6, slot_cols: int = 3,
               col_width: float = 16, box_h_px: int = 250, label: str = "写真") -> list[str]:
    """first_row から、2 列×3 段(最大 MAX_PHOTOS 枚)の写真枠を作って写真を埋め込む。
    返り値は注意文(省略した枚数、開けなかったファイル)。ncols 列が col_width の等幅であること。"""
    notes: list[str] = []
    paths = list(sign.photos)
    if len(paths) > MAX_PHOTOS:
        notes.append(f"写真は {len(paths)} 枚あり、先頭の {MAX_PHOTOS} 枚だけ載せています")
    paths = paths[:MAX_PHOTOS]
    box_w = col_px(col_width) * slot_cols - 12
    r = first_row
    for band in range(3):
        cap_r, pic_r = r, r + 1
        ws.row_dimensions[pic_r].height = box_h_px * 0.75
        for side in range(2):
            idx = band * 2 + side
            c0 = 1 + side * slot_cols
            c1 = c0 + slot_cols - 1
            span = lambda row: f"{get_column_letter(c0)}{row}:{get_column_letter(c1)}{row}"  # noqa: E731
            put(ws, span(cap_r), f"{label} {idx + 1}", font=F_SMALL, fill=FILL_HEAD, align=CENTER)
            frame = put(ws, f"{get_column_letter(c0)}{pic_r}:{get_column_letter(c1)}{pic_r}", None, align=CENTER)
            if idx >= len(paths):
                frame.value = "写真なし"
                frame.font = F_SMALL
                continue
            prep = prepare_photo(data.ledger.resolve_path(paths[idx]))
            if prep is None:
                frame.value = "写真ファイルを開けません"
                frame.font = F_UNJUDGED
                notes.append(f"写真 {idx + 1} を開けませんでした: {paths[idx]}")
                continue
            buf, w, h = prep
            k = min(box_w / w, (box_h_px - 8) / h)
            dw, dh = max(1, int(w * k)), max(1, int(h * k))
            _place(ws, XLImage(buf), c0 - 1, pic_r - 1, (box_w + 12 - dw) // 2, (box_h_px - dh) // 2, dw, dh)
        r += 2
    if not sign.photos:
        notes.append("写真が未登録です")
    return notes


# ---------------------------------------------------------------- 透かし
_FONT_CANDIDATES = [
    os.environ.get("KANBAN_KARTE_FONT", ""),
    "/System/Library/Fonts/ヒラギノ角ゴシック W6.ttc",
    "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/meiryo.ttc",
    "C:/Windows/Fonts/YuGothB.ttc",
]


@lru_cache(maxsize=1)
def _watermark_bytes() -> bytes | None:
    """「下書き」を斜めに薄く描いた透過 PNG。日本語フォントが見つからなければ None(バナーと印刷ヘッダだけになる)。"""
    font = None
    for p in _FONT_CANDIDATES:
        if p and Path(p).exists():
            try:
                font = ImageFont.truetype(p, 220)
                break
            except OSError:
                continue
    if font is None:
        return None
    layer = PILImage.new("RGBA", (760, 300), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((380, 150), "下書き", font=font, fill=(192, 0, 0, 46), anchor="mm")
    layer = layer.rotate(25, expand=True, resample=PILImage.BICUBIC)
    buf = io.BytesIO()
    layer.save(buf, "PNG")
    return buf.getvalue()


def add_watermark(ws, col0: int, row0: int, dx: int = 0, dy: int = 0) -> bool:
    """斜めの「下書き」透かしを置く。置けなければ False。形式は PNG(写真は JPEG なので区別できる)。"""
    data = _watermark_bytes()
    if data is None:
        return False
    with PILImage.open(io.BytesIO(data)) as im:
        w, h = im.size
    _place(ws, XLImage(io.BytesIO(data)), col0, row0, dx, dy, int(w * 0.8), int(h * 0.8))
    return True


# ---------------------------------------------------------------- 評価
def rank_text(rank: int | None) -> str | None:
    return None if rank is None else RANK_LABEL[rank]


def unjudged_item_keys(sign: Sign) -> list[str]:
    return [k for k in ITEM_KEYS if k not in sign.items]
