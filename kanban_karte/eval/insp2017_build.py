"""2017 年の点検報告書と写真を、アノテーションの列(/expert/ の点検記録)が読む items.tsv にする(依頼 14 B)。

  python3 -m kanban_karte.eval.insp2017_build [--root ~/Projects/fkogei-estimate-data/eval/insp2017]

入力(読むだけ):
  reports/*.pdf   「サイン保守・点検報告書」。サマリーの頁に看板ごとの総合評価(A/B/C/該当なし)、
                  その後に「<看板名> 点検結果詳細」の頁が看板ごとに続き、その頁の写真がその看板の写真
  photos/<営業所>/*.JPG   2017-08 の写真(撮影時刻なし)。どの看板か分からないので、番号順に 6 枚ずつの束にする
出力(root の下に書く): signs/<報告書番号>/<看板番号>/ に報告書の写真、items.tsv(eval/nas と同じ列)。
同じ中身の報告書(md5)は 1 本だけ使う。総合評価は A=1 B=2 C=3 に直して sign_rank_normalized に置く(画面には出さない)。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path.home() / "Projects" / "fkogei-estimate-data" / "eval" / "insp2017"
GRADE = {"A": 1, "B": 2, "C": 3}
BUNDLE = 6
MIN_SIDE = 200     # これより小さい画像(アイコン・縮小の見出し)は入れない
COLS = ["source_file", "sheet", "client", "site", "inspection_date", "sign_name", "overall_rank_raw", "overall_rank_normalized",
        "item_judgments", "remark", "photo_files", "photo_items_confidence", "sign_no", "sign_rank_raw", "sign_rank_normalized",
        "adopted_mtime", "dup_count", "extra"]


def _text(pdf: Path, page: int) -> str:
    return subprocess.run(["pdftotext", "-layout", "-f", str(page), "-l", str(page), str(pdf), "-"],
                          capture_output=True, text=True, check=False).stdout


def _pages(pdf: Path) -> int:
    m = re.search(r"Pages:\s+(\d+)", subprocess.run(["pdfinfo", str(pdf)], capture_output=True, text=True, check=False).stdout)
    return int(m.group(1)) if m else 0


def parse_report(pdf: Path) -> dict:
    """{site, date, signs: [{name, grade, pages: [始, 終]}]}。看板の並びは詳細の頁の順、評価はサマリーの同じ順の値。"""
    n = _pages(pdf)
    texts = {p: _text(pdf, p) for p in range(1, n + 1)}
    head = texts.get(1, "")
    plain = subprocess.run(["pdftotext", "-l", "1", str(pdf), "-"], capture_output=True, text=True, check=False).stdout
    site = re.search(r"物件名\s*\n+\s*(\S[^\n]*)", plain)
    date = re.search(r"(\d{4})年\s*(\d+)月\s*(\d+)日", head)
    starts = []
    for p in range(1, n + 1):
        m = re.search(r"^\s*(\S.*?)\s*点検結果詳細", texts[p], re.M)
        if m:
            starts.append((p, m.group(1).strip()))
    grades = []   # サマリーの頁はつなげて読む(看板の見出しが頁の末尾で、評価が次の頁に来ることがある)
    lines = "\n".join(texts[p] for p in range(1, (starts[0][0] if starts else n + 1))).splitlines()
    for i, ln in enumerate(lines):
        if "総合評価" in ln and "点検項目" in ln:
            grades.append(next((x.strip() for x in lines[i + 1:] if x.strip() in ("A", "B", "C", "D", "該当なし")), ""))
    signs = []
    for k, (p, name) in enumerate(starts):
        end = starts[k + 1][0] - 1 if k + 1 < len(starts) else n
        signs.append({"name": name, "grade": grades[k] if k < len(grades) else "", "pages": [p, end]})
    return {"site": site.group(1).strip() if site else pdf.stem,
            "date": f"{date.group(1)}-{int(date.group(2)):02d}-{int(date.group(3)):02d}" if date else "", "signs": signs}


def _extract(pdf: Path, pages: list[int], out: Path) -> list[Path]:
    """その頁の埋め込み画像を out に出す。同じ中身・小さい画像は捨てる。"""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    subprocess.run(["pdfimages", "-all", "-f", str(pages[0]), "-l", str(pages[1]), str(pdf), str(out / "i")],
                   capture_output=True, check=False)
    from PIL import Image
    keep, seen = [], set()
    for f in sorted(out.iterdir()):
        try:
            with Image.open(f) as im:
                small = min(im.size) < MIN_SIDE
                if f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
                    im.convert("RGB").save(f.with_suffix(".jpg"), quality=88)
        except OSError:
            small = True
        if f.suffix.lower() not in (".jpg", ".jpeg", ".png"):
            f.unlink()
            f = f.with_suffix(".jpg")
            if not f.exists():
                continue
        h = hashlib.sha1(f.read_bytes()).hexdigest()
        if small or h in seen:
            f.unlink()
            continue
        seen.add(h)
        keep.append(f)
    return keep


def _summary_images(pdf: Path, rep: dict, out: Path) -> list[Path]:
    """サマリーの頁(2 頁目〜最初の詳細の頁の前)の写真を、出てくる順に 1 看板 1 枚で返す。重複は消さない(順が看板と対応するため)。"""
    first = rep["signs"][0]["pages"][0] if rep["signs"] else 0
    if first <= 2:
        return []
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    subprocess.run(["pdfimages", "-j", "-f", "2", "-l", str(first - 1), str(pdf), str(out / "s")], capture_output=True, check=False)
    return sorted(f for f in out.iterdir() if f.suffix.lower() == ".jpg")


def build(root: Path = ROOT) -> dict:
    rows, used, counts = [], set(), {"reports": 0, "report_signs": 0, "report_photos": 0, "bundles": 0, "bundle_photos": 0}
    for r, pdf in enumerate(sorted((root / "reports").glob("*.pdf"))):
        md5 = hashlib.md5(pdf.read_bytes()).hexdigest()
        if md5 in used:
            continue
        used.add(md5)
        rep = parse_report(pdf)
        counts["reports"] += 1
        summary = _summary_images(pdf, rep, root / "signs" / f"r{r:02d}" / "_summary")
        for k, s in enumerate(rep["signs"], 1):
            files = _extract(pdf, s["pages"], root / "signs" / f"r{r:02d}" / f"{k:02d}")
            if not files and k - 1 < len(summary):   # 詳細の頁に写真が無い看板は、サマリーの頁の同じ順の 1 枚
                files = [summary[k - 1]]
            g = GRADE.get(s["grade"], "")
            rows.append({"source_file": f"reports/{pdf.name}", "site": rep["site"], "inspection_date": rep["date"], "sign_name": s["name"],
                         "overall_rank_raw": s["grade"], "overall_rank_normalized": g, "sign_rank_raw": s["grade"], "sign_rank_normalized": g,
                         "photo_files": json.dumps([str(f.relative_to(root)) for f in files], ensure_ascii=False), "sign_no": str(k),
                         "extra": json.dumps({"pages": s["pages"]})})
            counts["report_signs"] += 1
            counts["report_photos"] += len(files)
    for d in sorted(x for x in (root / "photos").iterdir() if x.is_dir()) if (root / "photos").is_dir() else []:
        files = sorted(f for f in d.iterdir() if f.suffix.lower() in (".jpg", ".jpeg"))
        for k in range(0, len(files), BUNDLE):
            chunk = files[k:k + BUNDLE]
            rows.append({"source_file": f"photos/{d.name}", "site": d.name, "sign_name": "写真の束(営業所ごと・対応なし)", "sign_no": str(k // BUNDLE + 1),
                         "photo_files": json.dumps([str(f.relative_to(root)) for f in chunk], ensure_ascii=False)})
            counts["bundles"] += 1
            counts["bundle_photos"] += len(chunk)
    with open(root / "items.tsv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLS, delimiter="\t", extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({c: row.get(c, "") for c in COLS})
    return counts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.eval.insp2017_build", description="2017 年の点検報告書から items.tsv を作る")
    ap.add_argument("--root", default=str(ROOT))
    a = ap.parse_args(argv)
    print(json.dumps(build(Path(a.root)), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
