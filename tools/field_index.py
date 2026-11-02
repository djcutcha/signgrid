"""写真フォルダと点検票の転記を突き合わせ、看板の索引(index.tsv)と正解表(items.tsv)を作る(API なし)。

  python3 tools/field_index.py            # 置き場は sources.toml の [eval.dirs].line_paper(絶対パス)

入力  : <置き場>/raw/<フォルダ>/*.jpg(通常看板= 数字、その他看板= 「他N」)と
        <置き場>/transcribed/pages.jsonl(tools/field_transcribe.py の転記)
出力  : <置き場>/index.tsv   看板 1 基= フォルダ 1 つ + 点検票 1〜2 頁。対応しない物は status で区別する
        <置き場>/items.tsv   正解表(eval/dataset.py の load_line_paper が読む)
        <置き場>/index_summary.json
status: matched(フォルダと頁が対応)/ folder_only(フォルダだけ)/ pages_only(頁だけ)/
        merged_folder(1 フォルダに 2 基が混ざる名前。両方の頁に対応づけ、写真は付けない= 評価から除外)/
        variant_folder(枝番つきのフォルダ。同じ番号のフォルダが別にあるので写真は付けない)
総合ランク(overall_rank_normalized)は様式に総合欄が無いので、判定のあるセルの最悪値からの派生値(xlsx_karte と同じ扱い)。
D→1, C→2, B→3, A→4。紙の写真(白地に罫線と文字)は判定役の入力から外す(paper_files に残す)。
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from kanban_karte.eval import dataset  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent))
import field_transcribe as ft  # noqa: E402

SITE = "site1"
LETTER_RANK = {"D": 1, "C": 2, "B": 3, "A": 4}
CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳㉑㉒㉓㉔㉕㉖㉗㉘㉙㉚"
ITEM_COLS = ["site", "sign_name", "photo_files", "item_judgments", "overall_rank_normalized", "sign_rank_raw",
             "sign_rank_normalized", "inspection_date", "photo_items_confidence", "overall_rank_raw",
             "key", "folder", "pages", "sheet_sign_name", "size", "paper_files", "note"]
INDEX_COLS = ["key", "status", "folder", "n_photos", "n_paper", "pages", "note"]

# 紙の写真の画素規則(縮小 320px): 地が白く(min チャンネル > 200 の割合が高い)、色味がほぼ無く、文字・罫線の暗い画素がある
PAPER_WHITE, PAPER_CHROMA, PAPER_DARK = 0.50, 0.08, (0.02, 0.35)


def label(kind: str, no: int) -> str:
    return f"看板 {CIRCLED[no - 1]}" if kind == "通常" and 1 <= no <= len(CIRCLED) else (
        f"看板 {no}" if kind == "通常" else f"その他 {no}")


def parse_folder(name: str) -> tuple[str, list[int], str]:
    """(kind, 番号のリスト, 種別) 種別= single / merged / variant。読めない名前は ("?", [], "single")。"""
    s = unicodedata.normalize("NFKC", name).strip()
    if re.fullmatch(r"\d+", s):
        return "通常", [int(s)], "single"
    m = re.fullmatch(r"他(\d+)", s)
    if m:
        return "他", [int(m.group(1))], "single"
    m = re.fullmatch(r"他(\d+)\s+(\d+)", s)
    if m:
        return "他", [int(m.group(1)), int(m.group(2))], "merged"
    m = re.fullmatch(r"他(\d+)\s*-\s*\d+", s)
    if m:
        return "他", [int(m.group(1))], "variant"
    return "?", [], "single"


def shot_order(p: Path) -> tuple[int, str]:
    m = re.search(r"_(\d+)\.jpe?g$", p.name, re.I)
    return (int(m.group(1)) if m else 10**9, p.name)


def is_paper_photo(path: Path) -> bool:
    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((320, 320))
    a = np.asarray(im).astype(np.int16)
    mx, mn = a.max(2), a.min(2)
    white = float((mn > 200).mean())
    chroma = float(((mx - mn) > 40).mean())
    dark = float((mx < 110).mean())
    return white >= PAPER_WHITE and chroma <= PAPER_CHROMA and PAPER_DARK[0] <= dark <= PAPER_DARK[1]


def page_key(rec: dict) -> tuple[str, int] | None:
    n = rec.get("item_no")
    if isinstance(n, str):
        m = re.search(r"(\d+)", unicodedata.normalize("NFKC", n))
        n = int(m.group(1)) if m else None
        if n is not None and rec.get("sheet_kind") == "通常":
            return "通常", n
        return ("他", n) if n is not None else None
    if isinstance(n, int):
        return ("通常" if rec.get("sheet_kind") == "通常" else "他", n)
    return None


def group_pages(recs: dict) -> tuple[dict[tuple[str, int], list[dict]], list[dict]]:
    """転記の頁を看板ごとにまとめる。写真のみの頁は直前の点検票の頁に付ける。番号が読めない頁は unnumbered。"""
    by_key: dict[tuple[str, int], list[dict]] = {}
    unnumbered: list[dict] = []
    last: tuple[str, int] | None = None
    last_pdf = None
    for k in sorted(recs, key=lambda k: (list(ft.PDFS).index(k[0]), k[1])):
        r = recs[k]
        if r.get("sheet_kind") == "写真のみ":
            if last is not None and k[0] == last_pdf:
                by_key[last][-1].setdefault("photo_pages", []).append(k[1])
            else:
                unnumbered.append({"pdf": k[0], "page": k[1], "why": "写真のみの頁(直前の点検票なし)"})
            continue
        key = page_key(r)
        if key is None:
            unnumbered.append({"pdf": k[0], "page": k[1], "why": "看板番号が読めない"})
            last = None
            continue
        by_key.setdefault(key, []).append({"pdf": k[0], "page": k[1], "rec": r})
        last, last_pdf = key, k[0]
    return by_key, unnumbered


def cells_of(pages: list[dict]) -> dict[str, str]:
    """{ "部位 点検内容": D/C/B/A/対象外 }。点検内容の無い行(空の「その他」行など)と空欄は入れない。同名は (2) を付ける。"""
    out: dict[str, str] = {}
    for p in pages:
        for r in p["rec"].get("rows") or []:
            if not r["check"] and r["judgment"] in ("空", "対象外"):
                continue
            base = f"{r['part']} {r['check']}".strip()
            k, n = base, 1
            while k in out:
                n += 1
                k = f"{base} ({n})"
            out[k] = r["judgment"] if r["judgment"] != "空" else ""
    return out


def size_of(pages: list[dict]) -> dict:
    for p in pages:
        s = p["rec"].get("size") or {}
        if any(s.get(k) for k in ("w_mm", "h_mm", "d_mm", "gl_mm")):
            return {k: s.get(k) for k in ("w_mm", "h_mm", "d_mm", "gl_mm")}
    return {}


def build(base: Path) -> dict:
    raw, tdir = base / "raw", base / "transcribed"
    recs = ft.latest(ft.read_jsonl(tdir / "pages.jsonl"))
    by_key, unnumbered = group_pages(recs)
    folders: dict[tuple[str, int], list[tuple[str, str]]] = {}  # key -> [(フォルダ名, 種別)]
    merged: list[tuple[str, tuple[str, int], tuple[str, int]]] = []
    odd: list[str] = []
    for d in sorted((p for p in raw.iterdir() if p.is_dir()), key=lambda p: p.name):
        kind, nums, how = parse_folder(d.name)
        if kind == "?":
            odd.append(d.name)
        elif how == "merged":
            merged.append((d.name, (kind, nums[0]), (kind, nums[1])))
        else:
            folders.setdefault((kind, nums[0]), []).append((d.name, how))
    merged_keys = {k for _, a, b in merged for k in (a, b)}

    index, items = [], []
    stats = {"matched": 0, "folder_only": 0, "pages_only": 0, "merged_folder_signs": 0, "variant_folder": 0,
             "paper_photos": 0, "paper_photo_signs": 0}
    keys = sorted(set(folders) | set(by_key) | merged_keys, key=lambda k: (k[0] != "通常", k[1]))
    merged_name = {k: n for n, a, b in merged for k in (a, b)}
    for key in keys:
        kind = "通常" if key[0] == "通常" else "その他"
        k_s = f"{key[0]}{key[1]}"
        pages = by_key.get(key, [])
        fl = folders.get(key, [])
        main = [f for f, how in fl if how == "single"]
        var = [f for f, how in fl if how == "variant"]
        photos: list[Path] = []
        paper: list[Path] = []
        note = ""
        if key in merged_keys:
            status, folder = "merged_folder", merged_name[key]
            note = "1フォルダに2基が混ざる名前。分けられないので写真は付けない(評価から除外)"
            stats["merged_folder_signs"] += 1
        elif main and pages:
            status, folder = "matched", main[0]
        elif main:
            status, folder = "folder_only", main[0]
        elif pages and var:
            status, folder = "variant_folder", var[0]
            note = "枝番つきのフォルダだけ(同じ番号の通常のフォルダが無い)。写真は付けない"
        elif pages:
            status, folder = "pages_only", ""
        else:  # 枝番フォルダだけで頁も無い
            status, folder = "folder_only", var[0] if var else ""
        if var and status == "matched":
            note = f"枝番つきフォルダ {len(var)} 件は写真を付けていない"
            stats["variant_folder"] += len(var)
        if status in ("matched", "folder_only"):
            files = sorted((raw / folder).glob("*.jpg"), key=shot_order)
            for f in files:
                (paper if is_paper_photo(f) else photos).append(f)
            stats["paper_photos"] += len(paper)
            stats["paper_photo_signs"] += 1 if paper else 0
        if status == "matched":
            stats["matched"] += 1
        elif status in ("folder_only", "pages_only"):
            stats[status] += 1
        pg_s = ";".join(f"{p['pdf']}:{p['page']}" + "".join(f"+{q}" for q in p.get("photo_pages", [])) for p in pages)
        index.append({"key": k_s, "status": status, "folder": folder, "n_photos": len(photos), "n_paper": len(paper),
                      "pages": pg_s, "note": note})
        if not pages:
            continue  # 正解表は点検票のある看板だけ
        cells = cells_of(pages)
        letters = [v for v in cells.values() if v in LETTER_RANK]
        worst = max(letters, key=lambda v: LETTER_RANK[v]) if letters else ""
        rel = [str(p.relative_to(base)) for p in photos]
        items.append({
            "site": SITE, "sign_name": label(kind, key[1]), "photo_files": json.dumps(rel, ensure_ascii=False),
            "item_judgments": json.dumps(cells, ensure_ascii=False),
            "overall_rank_normalized": LETTER_RANK.get(worst, ""), "sign_rank_raw": worst,
            "sign_rank_normalized": LETTER_RANK.get(worst, ""),
            "inspection_date": next((p["rec"].get("inspection_date") for p in pages if p["rec"].get("inspection_date")), ""),
            "photo_items_confidence": "", "overall_rank_raw": "", "key": k_s, "folder": folder, "pages": pg_s,
            "sheet_sign_name": next((p["rec"].get("sign_name") for p in pages if p["rec"].get("sign_name")), ""),
            "size": json.dumps(size_of(pages)), "paper_files": json.dumps([str(p.relative_to(base)) for p in paper], ensure_ascii=False),
            "note": note,
        })
    _write(base / "index.tsv", INDEX_COLS, index)
    _write(base / "items.tsv", ITEM_COLS, items)
    summary = {**stats, "signs_in_index": len(index), "items": len(items),
               "items_with_photos": sum(1 for i in items if json.loads(i["photo_files"])),
               "items_with_rank": sum(1 for i in items if i["overall_rank_normalized"] != ""),
               "rank_dist": {str(r): sum(1 for i in items if i["overall_rank_normalized"] == r) for r in (1, 2, 3, 4)},
               "unnumbered_pages": unnumbered, "odd_folders": odd,
               "merged_folders": [n for n, _, _ in merged],
               "folders": sum(1 for _ in raw.iterdir() if _.is_dir()), "pages_transcribed": len(recs),
               "extra_pdfs_recorded_only": sorted(p.name for p in raw.glob("*.pdf") if p.name not in ft.PDFS.values()
                                                  and p.name.endswith(".pdf") and "点検票" not in p.name)}
    (base / "index_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    return summary


def _write(path: Path, cols: list[str], rows: list[dict]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, cols, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sources-config")
    a = ap.parse_args()
    raw, _ = ft.paths(a.sources_config)
    s = build(raw.parent)
    print(json.dumps(s, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
