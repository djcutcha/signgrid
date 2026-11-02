"""macOS Vision OCR(vision_ocr.swift をビルドした実行ファイル)の呼び出しとキャッシュ。

ビルド: docs/OCR.md。実行ファイルは data/private/vision_ocr(未追跡)。
OCR は帯(画像の一部)だけを読む。結果は data/private/ocr_cache/<key>.json に保存する(未追跡)。
"""
from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_EXE = REPO_ROOT / "data" / "private" / "vision_ocr"
DEFAULT_CACHE = REPO_ROOT / "data" / "private" / "ocr_cache"
BATCH = 40
MIN_EDGE = 1600  # 帯の長辺がこれより小さいときは拡大して読ませる(Vision は小さい文字に弱い)


@dataclass
class Line:
    text: str
    conf: float
    x: float  # 0〜1(帯の左上原点)
    y: float
    w: float
    h: float


def crop_band(img: Image.Image, y0: float, y1: float, x0: float = 0.0, x1: float = 1.0) -> Image.Image:
    """画像の相対位置(0〜1)で帯を切り出す。小さければ拡大する。"""
    W, H = img.size
    band = img.crop((round(W * x0), round(H * y0), round(W * x1), round(H * y1))).convert("RGB")
    long = max(band.size)
    if long < MIN_EDGE:
        s = MIN_EDGE / long
        band = band.resize((round(band.width * s), round(band.height * s)), Image.LANCZOS)
    return band


class VisionOcr:
    def __init__(self, exe: Path | None = None, cache_dir: Path | None = None):
        self.exe = Path(exe or DEFAULT_EXE)
        self.cache_dir = Path(cache_dir or DEFAULT_CACHE)
        if not self.exe.exists():
            raise FileNotFoundError(f"{self.exe} がありません。docs/OCR.md の手順でビルドしてください")

    @staticmethod
    def cache_key(*parts: object) -> str:
        return hashlib.sha1("|".join(map(str, parts)).encode()).hexdigest()[:20]

    def _path(self, key: str) -> Path:
        return self.cache_dir / key[:2] / f"{key}.json"

    def read_cached(self, key: str) -> list[Line] | None:
        p = self._path(key)
        if not p.exists():
            return None
        return [Line(**d) for d in json.loads(p.read_text(encoding="utf-8"))]

    def read_many(self, jobs: list[tuple[str, Image.Image]]) -> dict[str, list[Line]]:
        """jobs: [(キャッシュキー, 画像)]。キャッシュに無いものだけ OCR する。"""
        out: dict[str, list[Line]] = {}
        todo = []
        for key, img in jobs:
            c = self.read_cached(key)
            if c is not None:
                out[key] = c
            else:
                todo.append((key, img))
        for i in range(0, len(todo), BATCH):
            chunk = todo[i:i + BATCH]
            with tempfile.TemporaryDirectory() as d:
                paths = []
                for k, (key, img) in enumerate(chunk):
                    p = Path(d) / f"{k:04d}.png"
                    img.save(p)
                    paths.append(p)
                r = subprocess.run([str(self.exe), *map(str, paths)], capture_output=True, text=True)
                res = {}
                for ln in r.stdout.splitlines():
                    o = json.loads(ln)
                    res[o["path"]] = o["lines"]
            for p, (key, _img) in zip(paths, chunk):
                lines = [Line(l["t"], l["c"], l["x"], l["y"], l["w"], l["h"]) for l in res.get(str(p), [])]
                cp = self._path(key)
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_text(json.dumps([l.__dict__ for l in lines], ensure_ascii=False), encoding="utf-8")
                out[key] = lines
        return out

    def read(self, key: str, img: Image.Image) -> list[Line]:
        return self.read_many([(key, img)])[key]
