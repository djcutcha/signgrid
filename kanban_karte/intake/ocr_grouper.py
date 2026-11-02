"""看板への束ね v2: 完成報告書の各面の見出し(アイテムNo・写真No・品名)を OCR で読み、
隣り合う面で見出しが矛盾しなければ同じ看板(複数ページにまたがる看板の続き)として束ねる。

- 見出しの読み取り(parse_header)と束ね(group_keys)は OCR の行だけを受ける純関数。OCR なしで試せる。
- 調査票形式(店舗名・調査日・調査対象の見出し)は 1面=1基。
- 見出しが読めない面は、読めた項目が1つも無ければ前の面とは束ねない(誤って混ぜるより分ける)。
AI は確定しない。ここで束ねた結果は看板の下書きで、有資格者が確定する。
"""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass

from PIL import Image

from ..eval import crop
from ..ocr.vision import Line, VisionOcr, crop_band
from .ocr_classifier import CHOSA, TOP_BAND, hits, norm
from .pdf_bundle import SignGrouper

HEAD_X = 0.30            # 見出し欄(左上の表)の右端(面の幅に対する割合)
LABEL_Y = (0.38, 0.52)   # ラベル行(申請No・アイテムNo・写真No)の y(帯の高さに対する割合)
VALUE_Y = (0.42, 0.60)   # 番号の値
NAME_Y = (0.54, 0.70)    # 品名
NAME_SIMILAR = 0.6
SEQ_X, SEQ_Y = 0.75, 0.5  # 「P.n」の位置(帯の右上)


@dataclass
class HeaderKey:
    item: str | None = None
    photo: str | None = None
    name: str | None = None
    seq: int | None = None  # 右上の「P.n」(その看板の何面目か)
    survey: bool = False  # 調査票形式(1面=1基)


def _label_kind(t: str) -> str | None:
    if "NO" not in t:
        return None
    if "写" in t or "真" in t:
        return "photo"
    if "テム" in t or "イテ" in t or "ITEM" in t or t.startswith("ア") or t.startswith("Y") or t.startswith("7"):
        return "item"
    if "請" in t or "中" in t or "申" in t or "錆" in t:
        return "app"
    return None


def parse_header(lines: list[Line]) -> HeaderKey:
    """面の上部帯の OCR 行 → 見出しの鍵。"""
    text = norm(" ".join(l.text for l in lines))
    if hits(text, CHOSA) >= 2 and "アイテム" not in text:
        return HeaderKey(survey=True)
    left = [l for l in lines if l.x + l.w / 2 < HEAD_X]
    seq = None
    for l in lines:
        m = re.fullmatch(r"P[.,]?([0-9LI])", norm(l.text))
        if m and l.x > SEQ_X and l.y < SEQ_Y:
            seq = 1 if m.group(1) in "LI" else int(m.group(1))
    labels = []
    for l in left:
        if LABEL_Y[0] <= l.y + l.h / 2 <= LABEL_Y[1]:
            k = _label_kind(norm(l.text))
            if k:
                labels.append((k, l.x + l.w / 2))
    key = HeaderKey(seq=seq)
    for l in left:
        t = norm(l.text)
        cy = l.y + l.h / 2
        if re.fullmatch(r"\d{1,3}", t) and VALUE_Y[0] <= cy <= VALUE_Y[1] and labels:
            kind = min(labels, key=lambda lb: abs(lb[1] - (l.x + l.w / 2)))[0]
            if kind == "item" and key.item is None:
                key.item = t
            elif kind == "photo" and key.photo is None:
                key.photo = t
        elif NAME_Y[0] <= cy <= NAME_Y[1] and len(t) >= 3 and "NO" not in t and key.name is None:
            key.name = t
    return key


def same_sign(a: HeaderKey, b: HeaderKey) -> bool:
    """b は a と同じ看板の続きか。P.n が 1 ずつ増えれば続き、増えない(P.1 に戻る等)なら新しい看板。
    P.n が読めないときは、共通に読めた項目が1つ以上あり、すべて一致するとき True。
    (束によっては P.n が 3,2,1 と降順の看板もあるが、昇順の看板と同居するので見分けられず、対応していない)"""
    if a.survey or b.survey:
        return False
    if a.seq is not None and b.seq is not None:
        if b.seq == a.seq + 1:
            return True
        if b.seq <= a.seq:
            return False  # 進まない・戻る=新しい看板
    common = 0
    for fa, fb in ((a.item, b.item), (a.photo, b.photo)):
        if fa is not None and fb is not None:
            if fa != fb:
                return False
            common += 1
    if a.name is not None and b.name is not None:
        if difflib.SequenceMatcher(None, a.name, b.name).ratio() < NAME_SIMILAR:
            return False
        common += 1
    return common > 0


def group_keys(keys: list[HeaderKey]) -> list[list[int]]:
    """面の並び(読む順)の鍵 → 看板ごとの面の番号のリスト。直前の面と同じ看板なら続ける。"""
    groups: list[list[int]] = []
    for i, k in enumerate(keys):
        if groups and same_sign(keys[groups[-1][-1]], k):
            groups[-1].append(i)
        else:
            groups.append([i])
    return groups


FACE_X = {"-": (0.0, 1.0), "L": (0.0, 0.5), "R": (0.5, 1.0)}


class OcrSignGrouper(SignGrouper):
    name = "ocr-header"

    def __init__(self, ocr: VisionOcr | None = None):
        self.ocr = ocr or VisionOcr()
        self.images: dict[int, Image.Image] = {}

    def prepare(self, images: dict[int, Image.Image]) -> None:
        self.images = images

    def face_band(self, page_no: int, half: str) -> tuple[str, Image.Image]:
        import hashlib
        x0, x1 = FACE_X.get(half, (0.0, 1.0))
        b = crop_band(self.images[page_no], 0.0, TOP_BAND, x0, x1)
        return hashlib.sha1(b.tobytes()).hexdigest()[:20] + f"-face{TOP_BAND}", b

    def group(self, pages: list[tuple[int, str, list[crop.Cell]]]):
        faces = [(no, half, cells) for no, half, cells in pages if cells]
        bands = [self.face_band(no, half) for no, half, _ in faces]
        read = self.ocr.read_many(bands)
        keys = [parse_header(read[k]) for k, _ in bands]
        return [[(faces[i][0], c) for i in g for c in faces[i][2]] for g in group_keys(keys)]
