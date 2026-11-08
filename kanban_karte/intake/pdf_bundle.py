"""入口2: 紙の束(スキャンした PDF 1本)を台帳に取り込む。

流れ:
  1. register_pages: PDF の各ページの画像を取り出し(埋め込み画像があればそのまま、無ければ 150dpi で描画)、
     pages に登録する
  2. classify_pages: PageClassifier でページ種別を付ける
  3. draft_signs: 完成報告書ページの写真セルを切り出し、看板の下書き(signs)を作る。
     ページ→看板の束ね方は SignGrouper で差し替えられる(今は 1ページ(面)=1基の仮束ね)

AI は確定しない。ここで作る看板は下書きで、rank や items は空のまま(有資格者が確定する)。
"""
from __future__ import annotations

import hashlib
import shutil
import subprocess
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ..eval import crop
from ..ledger.db import Ledger, Sign
from .classifier import HeuristicClassifier, PageClassifier, PageImage

STORE_MAX_EDGE = 3508  # 保存するページ画像の長辺の上限(A4 の 300dpi 相当)
RENDER_DPI = 150       # 埋め込み画像が無いページを描画するときの解像度


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _render_page(pdf: Path, page_no: int) -> Image.Image | None:
    """埋め込み画像が無いページを 150dpi で描画する(poppler の pdftoppm が要る)。無ければ None。"""
    exe = shutil.which("pdftoppm")
    if exe is None:
        return None
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        out = Path(d) / "p"
        r = subprocess.run([exe, "-r", str(RENDER_DPI), "-f", str(page_no), "-l", str(page_no),
                            "-singlefile", "-jpeg", str(pdf), str(out)], capture_output=True)
        f = Path(d) / "p.jpg"
        if r.returncode != 0 or not f.exists():
            return None
        with Image.open(f) as im:
            im.load()
            return im.convert("RGB")


def register_pages(ledger: Ledger, bundle_id: int, pdf: Path, pages_dir: Path,
                   max_pages: int | None = None) -> int:
    """ページ画像を取り出して pages に登録する。登録したページ数を返す。"""
    src = crop.PdfPages(pdf, str(bundle_id))
    n = len(src) if max_pages is None else min(len(src), max_pages)
    out_dir = pages_dir / f"{bundle_id:06d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    for no in range(1, n + 1):
        pg = src.reader.pages[no - 1]
        src_rot = int(pg.get("/Rotate") or 0) % 360
        got, source = src.page(no), "embedded"
        if got is None:
            img = _render_page(pdf, no)
            got, source = ((img, float(RENDER_DPI)) if img is not None else None), "rendered"
        if got is None:
            ledger.upsert_page(bundle_id, no, kind="不明", kind_basis="画像を取り出せなかった", src_rotate=src_rot)
            continue
        im, dpi = got
        if max(im.size) > STORE_MAX_EDGE:
            w0 = im.width
            im.thumbnail((STORE_MAX_EDGE, STORE_MAX_EDGE), Image.LANCZOS)
            dpi = dpi * im.width / w0
        path = out_dir / f"p-{no:03d}.jpg"
        im.save(path, quality=88)
        ledger.upsert_page(bundle_id, no, image_path=ledger.rel_path(path), image_source=source, src_rotate=src_rot,
                           width=im.width, height=im.height, dpi=dpi)
    ledger.set_bundle(bundle_id, pages=n, status="pages_registered")
    return n


def classify_pages(ledger: Ledger, bundle_id: int, classifier: PageClassifier, source_name: str) -> dict[str, int]:
    rows = ledger.list_pages(bundle_id)
    for r in rows:
        if not r["image_path"]:
            continue
        with Image.open(ledger.resolve_path(r["image_path"])) as im:
            im.load()
            img = im.convert("RGB")
        dpi = r["dpi"] or 150.0
        res = classifier.classify(PageImage(r["page_no"], len(rows), img, dpi, source_name))
        ledger.set_page_kind(bundle_id, r["page_no"], res.kind, res.basis)
    counts = ledger.kind_counts(bundle_id)
    ledger.set_bundle(bundle_id, kind_counts=counts, status="classified")
    return counts


class SignGrouper(ABC):
    """完成報告書ページの写真セルを、看板(1基)に束ねる口。"""

    def prepare(self, images: dict[int, Image.Image]) -> None:
        """束ねる前に、ページ画像({page_no: 向きを起こした画像})を渡す。画像を見ない束ね方は何もしない。"""

    @abstractmethod
    def group(self, pages: list[tuple[int, str, list[crop.Cell]]]) -> list[list[tuple[int, crop.Cell]]]:
        """pages: [(page_no, half, cells)] → 看板ごとの [(page_no, cell)] のリスト。"""


class PerPageGrouper(SignGrouper):
    """仮の束ね方: ページの1面=看板1基。同じ看板が複数ページにまたがる場合は AI の束ね役に差し替える。"""

    def group(self, pages):
        return [[(no, c) for c in cells] for no, _half, cells in pages if cells]


def draft_signs(ledger: Ledger, bundle_id: int, cells_dir: Path, grouper: SignGrouper | None = None,
                max_cells: int = 6) -> list[str]:
    """完成報告書ページから写真セルを切り出し、看板の下書き(signs)を作る。作った看板 id のリストを返す。"""
    grouper = grouper or PerPageGrouper()
    rows = [r for r in ledger.list_pages(bundle_id) if r["kind"] == "完成報告書" and r["image_path"]]
    faces: list[tuple[int, str, list[crop.Cell]]] = []
    imgs: dict[int, Image.Image] = {}
    for r in rows:
        with Image.open(ledger.resolve_path(r["image_path"])) as im:
            im.load()
            img = im.convert("RGB")
        if r["rotation"]:
            img = img.rotate(r["rotation"], expand=True)
        imgs[r["page_no"]] = img
        dpi = r["dpi"] or 150.0
        cells = crop.cut_cells(img, dpi)
        for half in sorted({c.half for c in cells}):
            faces.append((r["page_no"], half, [c for c in cells if c.half == half]))
    grouper.prepare(imgs)
    ids = []
    b = ledger.get_bundle(bundle_id)
    site = ledger.get_site(b["site_id"]) if b is not None and b["site_id"] else None   # 束の物件を看板にも付ける(取り込み画面の N4/N5 が物件で引くため)
    for k, group in enumerate(grouper.group(faces), 1):
        sid = f"b{bundle_id}-s{k:03d}"
        out = cells_dir / f"{bundle_id:06d}" / sid
        out.mkdir(parents=True, exist_ok=True)
        photos = []
        for j, (no, c) in enumerate(group[:max_cells], 1):
            dst = out / f"c{j:02d}.jpg"
            sub = imgs[no].crop((c.x0, c.y0, c.x1, c.y1))
            if max(sub.size) > crop.MAX_EDGE:
                sub.thumbnail((crop.MAX_EDGE, crop.MAX_EDGE), Image.LANCZOS)
            sub.save(dst, quality=90)
            photos.append(ledger.rel_path(dst))
        ledger.upsert_sign(Sign(id=sid, name=None, photos=photos, source_bundle=bundle_id,
                                site_id=site.id if site else None, address=site.address if site else None,
                                municipality=site.municipality if site else None,
                                remark="入口2で作った下書き(束ね・判定は未確定)"))
        ids.append(sid)
    ledger.set_bundle(bundle_id, status="signs_drafted")
    return ids


@dataclass
class IntakeResult:
    bundle_id: int
    existed: bool
    pages: int
    kind_counts: dict[str, int]
    signs: list[str]


def intake_pdf(ledger: Ledger, pdf: str | Path, data_dir: str | Path,
               classifier: PageClassifier | None = None, grouper: SignGrouper | None = None,
               max_pages: int | None = None) -> IntakeResult:
    """PDF 1本を取り込む。同じ PDF(sha256 が同じ)は取り込み直さず、既存の束を返す。"""
    pdf = Path(pdf)
    data_dir = Path(data_dir)
    ledger.data_dir = data_dir  # 台帳に入れる画像のパスは data_dir 基準の相対パス
    sha = sha256_file(pdf)
    old = ledger.find_bundle_by_sha(sha)
    if old is not None:
        return IntakeResult(old["id"], True, old["pages"], ledger.kind_counts(old["id"]),
                            [s.id for s in ledger.list_signs(bundle_id=old["id"])])
    bundle_id = ledger.add_bundle(str(pdf), sha)
    try:
        n = register_pages(ledger, bundle_id, pdf, data_dir / "pages", max_pages)
        counts = classify_pages(ledger, bundle_id, classifier or HeuristicClassifier(), pdf.name)
        signs = draft_signs(ledger, bundle_id, data_dir / "cells", grouper)
    except Exception as e:  # 壊れた PDF など。束を error にして残す
        ledger.set_bundle(bundle_id, status="error", error=f"{type(e).__name__}: {e}")
        raise
    return IntakeResult(bundle_id, False, n, counts, signs)
