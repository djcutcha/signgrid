"""紙のスキャン(完成報告書のページ画像)から「写真のセル」だけを切り出す。

完成報告書のページには、評価ランク欄・看板名の帯・総評(所見)欄・写真下のキャプション欄がある。
これらは正解そのもの(または正解を示唆する文)なので、AI への入力にしてはならない。
そこで、ページを走査して「写真が貼られた矩形」だけを検出し、その矩形を1枚ずつ別の画像として切り出す。

検出は画素の統計だけで行う(OCR・外部 API は使わない):
  1. 左右2面付け(A3 横)のページは中央で左右に分ける(L/R)
  2. 各面で「白でない画素」の行密度を出し、密度の高い連続行を写真の行とする
     (ヘッダの文字行・写真下のキャプション欄は密度が低いので入らない)
  3. 写真の行ごとに、列密度から写真のセルを分ける
  4. 小さすぎるもの・細長すぎるもの・中身がほぼ白のもの(空の枠)は捨てる
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

NONWHITE = 235       # RGB の最大値がこれ未満なら「白でない」
ROW_DENSITY = 0.40   # 面の幅に対する白でない画素の割合がこれ以上の行を写真の行とする
COL_DENSITY = 0.30
MIN_CELL = 70        # セルの最小の幅・高さ(px、90dpi 相当のページ画像で)
MIN_INK = 0.45       # セル内の白でない画素の割合(空の枠を捨てる)
MAX_ASPECT = 3.0
GAP = 8              # この px までの隙間は同じ行・列として繋ぐ
TWO_UP_RATIO = 1.2   # 幅/高さ がこれ以上なら左右2面付け


YELLOW_ROW = 0.70    # 写真の下に黄色いマーカーで強調したキャプション帯がある。その行の黄色の割合
YELLOW_MAX = 40      # 黄色帯として削る最大の高さ(px)
BOTTOM_MARGIN = 10   # キャプション欄との境目に残る線・文字を避けるため、下端を常にこれだけ削る


def _trim_caption_band(rgb: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> int:
    """セルの下端に黄色の帯(キャプションの強調)があれば、その手前までの y1 を返す。"""
    sub = rgb[y0:y1, x0:x1].astype(int)
    yellow = (sub[..., 0] > 200) & (sub[..., 1] > 180) & (sub[..., 2] < 140)
    frac = yellow.mean(axis=1)
    n = 0
    while n < min(YELLOW_MAX, len(frac) - 1) and frac[len(frac) - 1 - n] >= YELLOW_ROW:
        n += 1
    if n:
        n += 2  # 帯の縁の滲み
    return y1 - n - BOTTOM_MARGIN


@dataclass(frozen=True)
class Cell:
    half: str  # "-"(1面) / "L" / "R"
    x0: int
    y0: int
    x1: int
    y1: int  # ページ画像上の座標


def _runs(mask: np.ndarray, gap: int) -> list[tuple[int, int]]:
    """True が続く区間 [a, b) を、gap 以下の隙間は繋いで返す。"""
    idx = np.flatnonzero(mask)
    if idx.size == 0:
        return []
    out, a, prev = [], idx[0], idx[0]
    for i in idx[1:]:
        if i - prev > gap + 1:
            out.append((int(a), int(prev) + 1))
            a = i
        prev = i
    out.append((int(a), int(prev) + 1))
    return out


def find_cells(img: Image.Image, half: str | None = None) -> list[Cell]:
    """ページ画像から写真セルを探す。half を指定するとその面だけ(L/R/'-')。"""
    rgb = np.asarray(img.convert("RGB"))
    H, W = rgb.shape[:2]
    two_up = W / H >= TWO_UP_RATIO
    faces = [("L", 0, W // 2), ("R", W // 2, W)] if two_up else [("-", 0, W)]
    cells: list[Cell] = []
    for name, fx0, fx1 in faces:
        # 面の指定があっても、ページが1面なら全体を使う(L/R の表記が付いた1面ページがある)
        if half is not None and two_up and half != name:
            continue
        face = rgb[:, fx0:fx1]
        ink = face.max(axis=2) < NONWHITE
        fw = fx1 - fx0
        # 面の端の罫線・余白に引きずられないよう、左右 3% を除いて密度を測る
        m = int(fw * 0.03)
        core = ink[:, m:fw - m]
        rows = core.mean(axis=1) >= ROW_DENSITY
        for ry0, ry1 in _runs(rows, GAP):
            if ry1 - ry0 < MIN_CELL:
                continue
            cols = ink[ry0:ry1].mean(axis=0) >= COL_DENSITY
            for cx0, cx1 in _runs(cols, GAP):
                w, h = cx1 - cx0, ry1 - ry0
                if w < MIN_CELL or h < MIN_CELL or max(w, h) / min(w, h) > MAX_ASPECT:
                    continue
                if ink[ry0:ry1, cx0:cx1].mean() < MIN_INK:
                    continue
                ny1 = _trim_caption_band(face, cx0, ry0, cx1, ry1)
                if ny1 - ry0 < MIN_CELL:
                    continue
                cells.append(Cell(name, fx0 + cx0, ry0, fx0 + cx1, ny1))
    cells = _regularize(cells, H)
    # 読む順(上から下、左から右)。同じ段(y が近い)は x 順
    cells.sort(key=lambda c: (c.half != "L" and c.half != "-", round(c.y0 / 40), c.x0))
    return cells


def _groups(spans: list[tuple[int, int]]) -> list[list[int]]:
    """区間(a, b)のリストを、重なるものどうしでまとめたインデックスのグループにする。"""
    order = sorted(range(len(spans)), key=lambda i: spans[i][0])
    groups: list[list[int]] = []
    end = -1
    for i in order:
        a, b = spans[i]
        if groups and a < end:
            groups[-1].append(i)
            end = max(end, b)
        else:
            groups.append([i])
            end = b
    return groups


CAPTION_GAP = 40     # 写真の下端からキャプション欄の下端まで(px)。上へ広げるとき、これより上へは広げない
HEADER_FLOOR = 0.20  # 上端を広げるとき、ページ上端からこの割合より上へは広げない(ヘッダ帯・「全景」の見出しを避ける)


def _regularize(cells: list[Cell], page_h: int = 0) -> list[Cell]:
    """同じ段・同じ列の写真は同じ上下・左右にそろっている。白っぽい写真で検出が欠けたセルを、
    同じ段(列)の他のセルに合わせて広げる(面ごとに行う)。"""
    out = list(cells)
    for face in {c.half for c in cells}:
        idx = [i for i, c in enumerate(out) if c.half == face]
        for g in _groups([(out[i].y0, out[i].y1) for i in idx]):
            members = [idx[k] for k in g]
            y0 = min(out[i].y0 for i in members)
            y1 = max(out[i].y1 for i in members)
            for i in members:
                out[i] = Cell(out[i].half, out[i].x0, y0, out[i].x1, y1)
        # 段の高さが他の段より明らかに小さいとき(空の写真・白い空で上が欠けた段)は、
        # 下端(キャプション欄の直上)を基準に、最大の段の高さまで上へ広げる
        rows = [[idx[k] for k in g] for g in _groups([(out[i].y0, out[i].y1) for i in idx])]
        if len(rows) >= 2 and page_h:
            max_h = max(out[r[0]].y1 - out[r[0]].y0 for r in rows)
            prev_bottom = int(page_h * HEADER_FLOOR)
            for r in rows:
                c0 = out[r[0]]
                if c0.y1 - c0.y0 < 0.9 * max_h:
                    ny0 = max(prev_bottom, c0.y1 - max_h)
                    for i in r:
                        out[i] = Cell(out[i].half, out[i].x0, min(ny0, out[i].y0), out[i].x1, out[i].y1)
                prev_bottom = max(out[i].y1 for i in r) + CAPTION_GAP  # 写真の下のキャプション欄の分
        for g in _groups([(out[i].x0, out[i].x1) for i in idx]):
            members = [idx[k] for k in g]
            x0 = min(out[i].x0 for i in members)
            x1 = max(out[i].x1 for i in members)
            for i in members:
                out[i] = Cell(out[i].half, x0, out[i].y0, x1, out[i].y1)
    return out


def parse_page_spec(spec: str) -> list[tuple[int, str | None]]:
    """ground_truth_items の pages 列("11R" / "3L,3R" / "7,8")→ [(ページ番号, 面 or None)]。"""
    out: list[tuple[int, str | None]] = []
    for tok in spec.split(","):
        m = re.match(r"\s*(\d+)\s*([LR])?", tok)
        if m:
            k = (int(m.group(1)), m.group(2))
            if k not in out:
                out.append(k)
    return out


def load_rotations(page_index_tsv: Path) -> dict[tuple[str, int], int]:
    """page_index.tsv の rot 列(横向き・上下逆のページを起こす PIL の反時計回り角度)。"""
    import csv
    out: dict[tuple[str, int], int] = {}
    with open(page_index_tsv, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            if r.get("rot") not in (None, "", "0"):
                out[(r["id"], int(r["page"]))] = int(r["rot"])
    return out


DET_DPI = 90        # 検出の閾値(MIN_CELL など)は、この解像度のページ画像に合わせてある
MAX_EDGE = 1600     # 切り出した写真の長辺の上限(これを超えたら縮小して保存)


class PageSource:
    """束のページ画像の取り出し口。page(n) は (向きを起こした RGB 画像, その dpi)、無ければ None。"""

    bundle_id: str

    def page(self, n: int) -> tuple[Image.Image, float] | None:
        raise NotImplementedError


class JpegPages(PageSource):
    """90dpi のページ画像(eval の pages/<id>/p-NNN.jpg)。"""

    def __init__(self, page_dir: Path):
        self.page_dir = Path(page_dir)
        self.bundle_id = self.page_dir.name

    def page(self, n: int):
        p = self.page_dir / f"p-{n:03d}.jpg"
        if not p.exists():
            return None
        with Image.open(p) as im:
            im.load()
            return im.convert("RGB"), 90.0


class PdfPages(PageSource):
    """元の PDF。各ページはスキャン画像1枚の埋め込みなので、描き直さずにその画像をそのまま取り出す。

    ページの /Rotate(時計回り)を反映した向きにする(PDF で見た向き = 90dpi の pages/ と同じ向き)。
    """

    def __init__(self, pdf_path: Path, bundle_id: str | None = None):
        import pypdf
        self.pdf_path = Path(pdf_path)
        self.bundle_id = bundle_id or self.pdf_path.stem
        self.reader = pypdf.PdfReader(str(self.pdf_path))

    def __len__(self) -> int:
        return len(self.reader.pages)

    def page(self, n: int):
        import io
        if not 1 <= n <= len(self.reader.pages):
            return None
        pg = self.reader.pages[n - 1]
        imgs = list(pg.images)
        if not imgs:
            return None
        best = max(imgs, key=lambda i: len(i.data))  # 最大の画像(スキャン本体)
        with Image.open(io.BytesIO(best.data)) as im:
            im.load()
            im = im.convert("RGB")
        dpi = im.width / (float(pg.mediabox.width) / 72.0)
        rot = int(pg.get("/Rotate") or 0) % 360
        if rot:
            im = im.rotate(360 - rot, expand=True)  # /Rotate は時計回り、PIL は反時計回り
        return im, dpi


def open_page(path: Path, rot: int = 0) -> Image.Image:
    """90dpi のページ画像を開き、page_index の回転(PIL 反時計回り)で起こす(目視確認用)。"""
    im = JpegPages(Path(path).parent).page(int(Path(path).stem.split("-")[1]))[0]
    return im.rotate(rot, expand=True) if rot else im


def cut_cells(img: Image.Image, dpi: float, half: str | None = None) -> list[Cell]:
    """高解像度のページ画像から写真セルを探す。検出は DET_DPI に縮小した画像で行い、座標を戻す。"""
    s = DET_DPI / dpi
    det = img.resize((max(1, round(img.width * s)), max(1, round(img.height * s))), Image.LANCZOS) \
        if s < 0.99 else img
    k = img.width / det.width
    out = []
    for c in find_cells(det, half):
        out.append(Cell(c.half, max(0, round(c.x0 * k)), max(0, round(c.y0 * k)),
                        min(img.width, round(c.x1 * k)), min(img.height, round(c.y1 * k))))
    return out


def crop_item(source: PageSource, spec: str, out_dir: Path, max_cells: int = 6,
              rots: dict[tuple[str, int], int] | None = None, max_edge: int = MAX_EDGE) -> list[str]:
    """アイテムのページ指定から写真セルを順に切り出して out_dir に保存し、パスのリストを返す。

    面の指定(L/R)があればその面だけ。無ければページ全体。切り出せなければ空リスト。
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[str] = []
    for page, half in parse_page_spec(spec):
        got = source.page(page)
        if got is None:
            continue
        im, dpi = got
        rot = (rots or {}).get((source.bundle_id, page), 0)
        if rot:
            im = im.rotate(rot, expand=True)
        for c in cut_cells(im, dpi, half):
            if len(paths) >= max_cells:
                return paths
            sub = im.crop((c.x0, c.y0, c.x1, c.y1))
            if max(sub.size) > max_edge:
                sub.thumbnail((max_edge, max_edge), Image.LANCZOS)
            dst = out_dir / f"c{len(paths) + 1:02d}.jpg"
            sub.save(dst, quality=90)
            paths.append(str(dst))
    return paths
