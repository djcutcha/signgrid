"""画面からの取り込み: 写真フォルダ・点検表の PDF・紙1枚の写真 → 束 → 仕分け → 看板の枠 → 下書き(裏の1スレッド)。

  store_upload   送られた物を検査して data_dir に置き、束(bundles)を作る(同じ内容は既存の束を返す)
  IntakeJobs     束ごとの実行(スレッド)と、画面に見せる短いログ(メモリ上。再起動で消える)
  progress       仕分け / 転記 / 下書き / 反証役 の done/total。台帳に記録が無い段は None(作り物の数は出さない)

流れは本線の関数をそのまま呼ぶ: intake.pdf_bundle(register_pages → classify_pages → draft_signs)→ pipeline.draft(draft_bundle)。
紙1枚の写真は PDF と同じ道: 1ページの束として登録する(中身の転記はまだ。仕分けまで)。
AI は確定しない。ここで作る看板は下書きで、確定は有資格者。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
import threading
from collections import deque
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

from ..eval import crop
from ..intake import pdf_bundle
from ..intake.classifier import Classification, HeuristicClassifier, PageClassifier, PageImage
from ..ledger.db import Ledger

MAX_FILES = 100
MAX_IMAGE_BYTES = 25_000_000    # 紙1枚のスマホ写真・スキャン JPEG は 10MB 超がある
MAX_PDF_BYTES = 80_000_000
LOG_KEEP = 200                  # メモリに残す行数(画面へは最後の数行)
IMAGE_FORMATS = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
A4_LONG_IN = 11.69              # 画像の dpi が分からない時、長辺を A4 の長辺とみなして dpi を決める(分類の寸法特徴用)
STAGES = (("sort", "仕分け"), ("transcribe", "転記"), ("draft", "下書き"), ("verify", "反証役"))


class IntakeError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


@dataclass
class Upload:
    bundle_id: int
    existed: bool
    kind: str          # pdf / images
    n_files: int


def _safe_name(name: str, fallback: str) -> str:
    n = re.sub(r"[^\w.\-]+", "_", Path(str(name or "")).name, flags=re.UNICODE).strip("._")
    return n[:80] or fallback


def parse_files(body: dict) -> list[dict]:
    """body.files = [{name, data(base64)}] を検査して [{name, bytes, sha256, is_pdf, fmt}] にする。
    PDF は1本だけ。写真は何枚でも(JPEG / PNG / WEBP)。PDF と写真の混在は断る(1回の取り込み=1つの束)。"""
    raw = body.get("files")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_FILES:
        raise IntakeError(400, f"files は 1〜{MAX_FILES} 件の配列({{name, data(base64)}})")
    out = []
    for i, x in enumerate(raw, 1):
        if not isinstance(x, dict) or not isinstance(x.get("data"), str):
            raise IntakeError(400, f"{i} 件目: data は base64 の文字列")
        try:
            b = base64.b64decode(x["data"].split(",", 1)[-1], validate=True)
        except (ValueError, binascii.Error):
            raise IntakeError(400, f"{i} 件目: data が base64 ではありません")
        name = _safe_name(x.get("name"), f"file{i:03d}")
        if not b:
            raise IntakeError(400, f"{i} 件目: 中身が空です")
        if b[:5] == b"%PDF-":
            if len(b) > MAX_PDF_BYTES:
                raise IntakeError(400, f"{i} 件目: PDF が大きすぎます({MAX_PDF_BYTES // 1_000_000}MB まで)")
            out.append({"name": name, "bytes": b, "is_pdf": True, "fmt": "PDF", "sha256": hashlib.sha256(b).hexdigest()})
            continue
        if len(b) > MAX_IMAGE_BYTES:
            raise IntakeError(400, f"{i} 件目: 写真が大きすぎます({MAX_IMAGE_BYTES // 1_000_000}MB まで)")
        try:
            with Image.open(io.BytesIO(b)) as im:
                im.verify()
                fmt = im.format
        except (UnidentifiedImageError, OSError, SyntaxError):
            raise IntakeError(400, f"{i} 件目: 画像でも PDF でもありません")
        if fmt not in IMAGE_FORMATS:
            raise IntakeError(400, f"{i} 件目: 写真は JPEG / PNG / WEBP だけ({fmt})。HEIC は JPEG にして送ってください")
        out.append({"name": name, "bytes": b, "is_pdf": False, "fmt": fmt, "sha256": hashlib.sha256(b).hexdigest()})
    if any(f["is_pdf"] for f in out):
        if len(out) > 1:
            raise IntakeError(400, "PDF は1本だけ送れます(写真と混ぜない・PDF を複数送らない)")
    return out


def store_upload(ledger: Ledger, files: list[dict], data_dir: Path, site_id: str | None = None) -> Upload:
    """検査済みの files を data_dir/uploads/<sha12>/ に置き、束を作る。同じ内容(sha256)は既存の束を返す。
    PDF 1本の sha は intake_pdf と同じ(CLI で取り込んだ同じ PDF も重複として扱う)。"""
    if site_id is not None and ledger.get_site(site_id) is None:
        raise IntakeError(404, f"現場 {site_id} が台帳にありません")
    is_pdf = files[0]["is_pdf"]
    sha = files[0]["sha256"] if is_pdf else hashlib.sha256(("images:" + ",".join(f["sha256"] for f in files)).encode()).hexdigest()
    old = ledger.find_bundle_by_sha(sha)
    if old is not None:
        return Upload(old["id"], True, "pdf" if is_pdf else "images", len(files))
    d = data_dir / "uploads" / sha[:12]
    d.mkdir(parents=True, exist_ok=True)
    paths = []
    for k, f in enumerate(files, 1):
        stem = f["name"] if is_pdf else f"{k:03d}_{f['name']}"
        p = d / (stem if is_pdf else Path(stem).stem + IMAGE_FORMATS[f["fmt"]])
        p.write_bytes(f["bytes"])
        paths.append(p)
    label = str(paths[0]) if is_pdf else f"upload://{len(files)}枚/{files[0]['name']}"
    bid = ledger.add_bundle(label, sha, pages=0)
    if site_id:
        ledger.set_bundle(bid, site_id=site_id)
    return Upload(bid, False, "pdf" if is_pdf else "images", len(files))


def upload_paths(data_dir: Path, ledger: Ledger, bundle_id: int) -> list[Path]:
    """束に置いた元ファイル(アップロード順)。"""
    b = ledger.get_bundle(bundle_id)
    d = data_dir / "uploads" / b["sha256"][:12]
    return sorted(p for p in d.iterdir() if p.is_file()) if d.is_dir() else []


def register_images(ledger: Ledger, bundle_id: int, images: list[Path], pages_dir: Path) -> int:
    """写真1枚=1ページとして pages に登録する(PDF の register_pages と同じ置き場・同じ縮小)。紙1枚の写真は1ページの束になる。"""
    out_dir = pages_dir / f"{bundle_id:06d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    n = 0
    for no, src in enumerate(images, 1):
        n = no
        try:
            with Image.open(src) as im:
                im = ImageOps.exif_transpose(im)
                if im.mode != "RGB":
                    rgba = im.convert("RGBA")
                    bg = Image.new("RGB", rgba.size, (255, 255, 255))
                    bg.paste(rgba, mask=rgba.split()[3])
                    im = bg
                if max(im.size) > pdf_bundle.STORE_MAX_EDGE:
                    im.thumbnail((pdf_bundle.STORE_MAX_EDGE, pdf_bundle.STORE_MAX_EDGE), Image.LANCZOS)
                path = out_dir / f"p-{no:03d}.jpg"
                im.save(path, quality=88)
                w, h = im.size
        except (OSError, ValueError):
            ledger.upsert_page(bundle_id, no, kind="不明", kind_basis="画像を開けなかった", image_source="upload")
            continue
        ledger.upsert_page(bundle_id, no, image_path=ledger.rel_path(path), image_source="upload",
                           width=w, height=h, dpi=max(w, h) / A4_LONG_IN)
    ledger.set_bundle(bundle_id, pages=n, status="pages_registered")
    return n


class PhotoFallbackClassifier(PageClassifier):
    """OCR が使えない環境(Vision の実行ファイルが無い)の仕分け。写真セルが並ぶページは完成報告書、それ以外は写真。
    本線の仮実装(先頭ページ=指示書)は写真フォルダの1枚目を指示書にしてしまうので、取り込み画面では使わない。"""

    name = "photo-fallback"

    def classify(self, page: PageImage) -> Classification:
        n = len(crop.cut_cells(page.image, page.dpi))
        if n >= HeuristicClassifier.MIN_CELLS:
            return Classification("完成報告書", f"{self.name}: 写真セル {n} 個")
        return Classification("写真", f"{self.name}: 写真セル {n} 個")


def default_parts(log=None) -> tuple[PageClassifier, pdf_bundle.SignGrouper]:
    """本番の仕分け役と束ね役: OCR(macOS Vision)があればそれ、無ければ PhotoFallbackClassifier と 1面=1基。"""
    try:
        from ..intake.ocr_classifier import OcrHeuristicClassifier
        from ..intake.ocr_grouper import OcrSignGrouper
        from ..ocr.vision import VisionOcr
        ocr = VisionOcr()
        return OcrHeuristicClassifier(ocr), OcrSignGrouper(ocr)
    except (FileNotFoundError, OSError, ImportError) as e:
        if log:
            log(f"OCR が使えないので簡易の仕分けにします({type(e).__name__})")
        return PhotoFallbackClassifier(), pdf_bundle.PerPageGrouper()


class IntakeJobs:
    """束ごとの取り込みの実行と、画面に見せるログ(メモリ上)。1束=1スレッド。"""

    def __init__(self, ledger_factory, data_dir: Path, parts_factory=None, draft=None):
        """ledger_factory() -> Ledger(スレッドごとに開く)/ parts_factory(log) -> (分類役, 束ね役)/
        draft(ledger, bundle_id) -> {"results": [...]}(下書き。無ければ下書きの段は飛ばす)"""
        self.ledger_factory, self.data_dir = ledger_factory, Path(data_dir)
        self.parts_factory = parts_factory or default_parts
        self.draft = draft
        self._lock = threading.Lock()
        self._jobs: dict[int, dict] = {}

    def state(self, bundle_id: int) -> dict | None:
        with self._lock:
            j = self._jobs.get(bundle_id)
            return None if j is None else {"status": j["status"], "error": j["error"], "log": list(j["log"])}

    def running(self, bundle_id: int) -> bool:
        with self._lock:
            j = self._jobs.get(bundle_id)
            return bool(j and j["status"] == "running")

    def _log(self, bundle_id: int, line: str) -> None:
        with self._lock:
            self._jobs[bundle_id]["log"].append(line)

    def start(self, bundle_id: int) -> None:
        with self._lock:
            if self._jobs.get(bundle_id, {}).get("status") == "running":
                return
            t = threading.Thread(target=self._run, args=(bundle_id,), name=f"intake-{bundle_id}", daemon=True)
            self._jobs[bundle_id] = {"status": "running", "error": None, "log": deque(maxlen=LOG_KEEP), "thread": t}
        t.start()

    def wait(self, bundle_id: int, timeout: float | None = None) -> None:
        """テスト用: 終わるまで待つ。"""
        with self._lock:
            t = self._jobs[bundle_id]["thread"]
        t.join(timeout)

    def _finish(self, bundle_id: int, status: str, error: str | None = None) -> None:
        with self._lock:
            self._jobs[bundle_id].update(status=status, error=error)

    def _run(self, bundle_id: int) -> None:
        log = lambda line: self._log(bundle_id, line)   # noqa: E731
        try:
            with self.ledger_factory() as ledger:
                self._steps(ledger, bundle_id, log)
        except Exception as e:  # 壊れた PDF・画像など。束を error にして残す
            msg = f"{type(e).__name__}: {e}"
            log(f"止まりました: {msg}")
            try:
                with self.ledger_factory() as ledger:
                    ledger.set_bundle(bundle_id, status="error", error=msg)
            except Exception:
                pass
            self._finish(bundle_id, "error", msg)

    def _steps(self, ledger: Ledger, bundle_id: int, log) -> None:
        b = ledger.get_bundle(bundle_id)
        srcs = upload_paths(self.data_dir, ledger, bundle_id)
        if not srcs:
            raise FileNotFoundError("取り込んだ元ファイルが見つかりません")
        pdf = srcs[0] if srcs[0].suffix.lower() == ".pdf" else None
        log(f"取り込み開始: {'PDF ' + pdf.name if pdf else f'写真 {len(srcs)} 枚'}")
        n = (pdf_bundle.register_pages(ledger, bundle_id, pdf, self.data_dir / "pages") if pdf
             else register_images(ledger, bundle_id, srcs, self.data_dir / "pages"))
        log(f"ページ {n} 枚を登録")
        classifier, grouper = self.parts_factory(log)
        counts = pdf_bundle.classify_pages(ledger, bundle_id, classifier, pdf.name if pdf else b["file"])
        for r in ledger.list_pages(bundle_id):
            log(f"{r['page_no']} 枚目を「{r['kind']}」と判断")
        log("仕分け完了: " + "、".join(f"{k} {v}" for k, v in counts.items()))
        signs = pdf_bundle.draft_signs(ledger, bundle_id, self.data_dir / "cells", grouper)
        log(f"看板の枠 {len(signs)} 基を作成" if signs else "完成報告書のページが無いので、写真から1基の枠を作ります")
        if self.draft is None:
            log("下書きは作りません(判定役が設定されていません)")
            return self._finish(bundle_id, "done")
        try:
            r = self.draft(ledger, bundle_id)
        except Exception as e:   # 鍵が無い・API の失敗。仕分けまでの結果は残す
            msg = getattr(e, "message", None) or f"{type(e).__name__}: {e}"
            log(f"下書きを作れませんでした: {msg}")
            ledger.set_bundle(bundle_id, error=msg)   # status は変えない(仕分けまで済んだ束のまま)
            return self._finish(bundle_id, "error", msg)
        for x in r["results"]:
            log(f"看板 {x['sign_id']} の下書き: " + (f"ランク {x['rank']}、判断できず {x['undecided']} 項目" if x["status"] == "draft"
                                                    else x["status"] + (f"({x['error']})" if x.get("error") else "")))
        errs = [x for x in r["results"] if x["status"] == "error"]
        if errs:
            return self._finish(bundle_id, "error", errs[0].get("error") or "下書きに失敗した看板があります")
        log("下書き完了")
        self._finish(bundle_id, "done")


def progress(ledger: Ledger, bundle_id: int, jobs: IntakeJobs) -> dict | None:
    """画面の進み具合。台帳に記録がある段だけ数を出し、無い段は done/total とも None。
      仕分け = 種別を付けたページ / 全ページ    下書き = 下書きが付いた看板 / 看板(看板がまだ無い間は None)
      転記・反証役 = 本線がまだ記録を持たない(None)"""
    b = ledger.get_bundle(bundle_id)
    if b is None:
        return None
    st = jobs.state(bundle_id)
    pages = ledger.list_pages(bundle_id)
    signs = ledger.list_signs(bundle_id=bundle_id)
    stages = {
        "sort": {"done": sum(1 for p in pages if p["kind_basis"] is not None), "total": len(pages)} if pages else None,
        "transcribe": None,
        "draft": ({"done": sum(s.status in ("draft", "confirmed", "needs_photos") for s in signs), "total": len(signs)}
                  if signs else None),
        "verify": None,
    }
    if st:
        status, error, log = st["status"], st["error"], st["log"][-6:]
    else:   # この起動で走らせていない束(LINE・過去の取り込み)は台帳の状態から
        status = "error" if b["status"] == "error" or b["error"] else "done" if b["status"] == "signs_drafted" else "pending"
        error, log = b["error"], []
    return {"bundle_id": bundle_id, "status": status, "error": error, "bundle_status": b["status"], "site_id": b["site_id"],
            "stages": [{"key": k, "label": label, **(stages[k] or {"done": None, "total": None})} for k, label in STAGES],
            "sign_count": len(signs), "log": log}
