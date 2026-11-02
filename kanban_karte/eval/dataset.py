"""正解データの読み込み。看板1基=1レコード(EvalItem)の統一形式にする。

正解データの実体はリポジトリに無い。参照先のパスは data/private/sources.toml(.gitignore 済み)に書く。
このコードはパスを持たない。

共通軸: 1=良好, 2=経過観察, 3=要改善, 4=即時対応/危険。「要対応」= 3 または 4。
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import crop

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SOURCES = REPO_ROOT / "data" / "private" / "sources.toml"
SPLIT_SALT = "kanban-karte-split-v1"
EVAL_PERCENT = 70  # 70% を評価用、30% を例示用(few-shot 等に使ってよい側)

# 評価指標の対象になる(rank を持ちうる)ソースと、対象外ソース
# source キー(顧客名は出さない):
#   paper_scan      紙のスキャン束
#   xlsx_center     客先A: センター単位の安全点検報告書(18項目)
#   xlsx_karte      客先B: 看板カルテ様式(5項目x4段階)
#   xlsx_sketch     客先C: 判定なし(写真と項目のみ。評価対象外)
#   xlsx_compliance 客先D: 条例の適合/不適合(安全ランクではない。法規判定の別枠)
RANKED_SOURCES = ("paper_scan", "xlsx_center", "xlsx_karte")
ALL_SOURCES = RANKED_SOURCES + ("xlsx_sketch", "xlsx_compliance")

_LABEL_TO_RANK = {"良好": 1, "経過観察": 2, "要改善": 3, "即時修理": 4, "即時対応": 4, "危険": 4}
_LETTER_TO_RANK = {"A": 1, "B": 2, "C": 3, "D": 4}  # 客先A様式(A が最良)


@dataclass
class EvalItem:
    source: str
    sign_id: str
    photo_paths: list[str]
    rank_true: int | None
    item_judgments_true: dict[str, int] | None
    split: str  # "eval" | "example"
    meta: dict = field(default_factory=dict)


def load_config(path: str | os.PathLike | None = None) -> dict:
    p = Path(path or os.environ.get("KANBAN_KARTE_SOURCES") or DEFAULT_SOURCES)
    if not p.exists():
        raise FileNotFoundError(
            f"{p} がありません。data/private/sources.example.toml をコピーして実パスを書いてください"
        )
    with open(p, "rb") as f:
        return tomllib.load(f)["eval"]


def assign_split(group_key: str) -> str:
    """group_key の SHA-256 で決定論的に 70/30 に分ける(同じ group_key は必ず同じ側)。"""
    h = hashlib.sha256(f"{SPLIT_SALT}:{group_key}".encode("utf-8")).digest()
    return "eval" if int.from_bytes(h[:8], "big") % 100 < EVAL_PERCENT else "example"


def _read_tsv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def _rank(v: str) -> int | None:
    v = (v or "").strip()
    return int(v) if v in ("1", "2", "3", "4") else None


def _parse_item_judgments(raw: str) -> tuple[dict[str, int], int]:
    """item_judgments JSON を {項目: 1〜4} にする。

    採るのは 良好/経過観察/要改善/即時修理 と A〜D(客先A様式の②)の単一値だけ。
    対象外・空欄・複数印・形式不明は除く。戻り値の2つめは「複数印または形式不明で落とした数」。
    """
    out: dict[str, int] = {}
    dropped = 0
    try:
        d = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}, 0
    for k, v in d.items():
        v = (v or "").strip()
        if v in ("", "対象外"):
            continue
        if v in _LABEL_TO_RANK:
            out[k] = _LABEL_TO_RANK[v]
        elif v in _LETTER_TO_RANK:
            out[k] = _LETTER_TO_RANK[v]
        else:
            dropped += 1  # 複数印("良好/経過観察")、"看板A\n照明B" など
    return out, dropped


def _scan_pages(page_spec: str) -> list[int]:
    nums: list[int] = []
    for tok in page_spec.split(","):
        m = re.match(r"\s*(\d+)", tok)
        if m and int(m.group(1)) not in nums:
            nums.append(int(m.group(1)))
    return nums


CROP_VERSION = "v5"  # crop.py の検出ロジックや入力画像を変えたら上げる(古いキャッシュを使わない)
CROP_MAX_CELLS = 12


def _page_source(scan_root: Path, pdf_root: Path | None, bundle_id: str) -> crop.PageSource:
    """元 PDF があればその埋め込み画像(約300dpi)、無ければ 90dpi のページ画像。"""
    if pdf_root is not None and (pdf_root / f"{bundle_id}.pdf").exists():
        return crop.PdfPages(pdf_root / f"{bundle_id}.pdf", bundle_id)
    return crop.JpegPages(scan_root / "pages" / bundle_id)


def _item_crops(scan_root: Path, pdf_root: Path | None, cache_root: Path, rots, item_group: str,
                bundle_id: str, spec: str) -> list[str]:
    """アイテムのページから写真セルを切り出し(キャッシュあり)、パスのリストを返す。"""
    out_dir = cache_root / CROP_VERSION / item_group
    done = out_dir / ".done"
    if done.exists():
        return sorted(str(p) for p in out_dir.glob("c*.jpg"))
    if out_dir.exists():
        for f in out_dir.iterdir():
            f.unlink()
    paths = crop.crop_item(_page_source(scan_root, pdf_root, bundle_id), spec, out_dir,
                           max_cells=CROP_MAX_CELLS, rots=rots)
    out_dir.mkdir(parents=True, exist_ok=True)
    done.write_text(str(len(paths)))
    return paths


def load_paper_scan(scan_root: Path, cache_root: Path | None = None,
                    pdf_root: Path | None = None) -> list[EvalItem]:
    """紙のスキャン束。入力は完成報告書ページから切り出した「写真セル」だけ。

    ページ画像そのものは評価欄(正解)・総評・キャプションが写るので使わない(docs/EVAL.md)。
    評価欄のない様式(調査票様式。正解ランクも付かない)は切り出さず、写真なしにする。
    """
    cache_root = cache_root or (REPO_ROOT / "data" / "private" / "crops" / "paper_scan")
    rots = crop.load_rotations(scan_root / "page_index.tsv")
    items = []
    for r in _read_tsv(scan_root / "ground_truth_items.tsv"):
        rank = _rank(r["rank_normalized"])
        sid = r["item_group"]
        paths = []
        if rank is not None and r["fmt"] == "header-strip":
            paths = _item_crops(scan_root, pdf_root, cache_root, rots, sid, r["id"], r["pages"])
        items.append(EvalItem(
            source="paper_scan", sign_id=sid, photo_paths=paths,
            rank_true=rank, item_judgments_true=None,
            split=assign_split(f"paper_scan|{r['id']}"),  # 束(同じ現場の報告書)単位
            meta={"bundle_id": r["id"], "group_key": f"paper_scan|{r['id']}",
                  "fmt": r["fmt"], "rank_letter": r["rank_letter"],
                  "confidence": r["confidence"], "inspection_date": r["inspection_date"],
                  "photos_missing": 0, "crop_cells": len(paths)},
        ))
    return items


def _load_nas(source: str, base: Path, *, rank_col: str) -> list[EvalItem]:
    """base = その様式のディレクトリ(名前は sources.toml の [eval.dirs] で与える)。"""
    rows = _read_tsv(base / "items.tsv")
    items = []
    for i, r in enumerate(rows):
        files = json.loads(r["photo_files"]) if r["photo_files"] else []
        paths, missing = [], 0
        for rel in files:
            p = base / rel
            if p.exists():
                paths.append(str(p))
            else:
                missing += 1
        ij, dropped = _parse_item_judgments(r["item_judgments"])
        try:
            form_items = list(json.loads(r["item_judgments"]).keys()) if r["item_judgments"] else []
        except json.JSONDecodeError:
            form_items = []
        # sign_id: 同じ看板が年違いで複数回出るため、行番号で一意にする
        sid = f"{source}:{i:04d}"
        # 分割キーは現場単位(センター/会館)。同じ現場の看板・同じ看板の年違いが必ず同じ側に落ちる
        group = f"{source}|{r['site']}"
        rank = _rank(r[rank_col]) if rank_col else None
        items.append(EvalItem(
            source=source, sign_id=sid, photo_paths=paths, rank_true=rank,
            item_judgments_true=ij if (rank is not None and ij) else None,
            split=assign_split(group),
            meta={"group_key": group, "site": r["site"], "sign_name": r["sign_name"],
                  "form_items": form_items,  # 様式が持つ全項目名(対象外・未記入も含む)
                  "inspection_date": r["inspection_date"],
                  "overall_rank_normalized": _rank(r["overall_rank_normalized"]),
                  "sign_rank_raw": r["sign_rank_raw"],
                  "item_judgments_dropped_multi": dropped,
                  "photos_missing": missing,
                  "photo_items_confidence": r["photo_items_confidence"]},
        ))
    return items


def load_xlsx_center(base: Path) -> list[EvalItem]:
    # 看板別(腐食/破損/固定の最悪値)を正解にする。センター総合評価は meta に残す
    return _load_nas("xlsx_center", base, rank_col="sign_rank_normalized")


def load_xlsx_karte(base: Path) -> list[EvalItem]:
    # 看板単位 = 項目評価の最悪値(派生値)
    return _load_nas("xlsx_karte", base, rank_col="sign_rank_normalized")


def load_xlsx_sketch(base: Path) -> list[EvalItem]:
    # 判定欄が全て空。評価対象外(写真は入力例にだけ使える)
    return _load_nas("xlsx_sketch", base, rank_col="")


def load_xlsx_compliance(base: Path) -> list[EvalItem]:
    # 条例適合/不適合は安全ランクではない。法規判定の正解として別枠。rank は付けない
    items = _load_nas("xlsx_compliance", base, rank_col="")
    for it, r in zip(items, _read_tsv(base / "items.tsv")):
        it.meta["compliance"] = r["sign_rank_raw"] or r["overall_rank_raw"]  # 適合/不適合/空
    return items


def load_all(config_path: str | os.PathLike | None = None,
             sources: tuple[str, ...] = ALL_SOURCES) -> list[EvalItem]:
    cfg = load_config(config_path)
    scan_root, nas_root = Path(cfg["scan_root"]), Path(cfg["nas_root"])
    dirs = cfg.get("dirs", {})  # source キー -> nas_root 配下のディレクトリ名

    def nas(key):
        if key not in dirs:
            raise KeyError(f"sources.toml の [eval.dirs] に {key} がありません")
        return nas_root / dirs[key]

    loaders = {
        "paper_scan": lambda: load_paper_scan(
            scan_root, Path(cfg["crop_cache"]) if cfg.get("crop_cache") else None,
            Path(cfg["pdf_root"]) if cfg.get("pdf_root") else None),
        "xlsx_center": lambda: load_xlsx_center(nas("xlsx_center")),
        "xlsx_karte": lambda: load_xlsx_karte(nas("xlsx_karte")),
        "xlsx_sketch": lambda: load_xlsx_sketch(nas("xlsx_sketch")),
        "xlsx_compliance": lambda: load_xlsx_compliance(nas("xlsx_compliance")),
    }
    out: list[EvalItem] = []
    for s in sources:
        out.extend(loaders[s]())
    return out


def summarize(items: list[EvalItem]) -> dict:
    """ソース別の件数: 総数 / rank あり / rank なし(除外) / 分割後(rank ありのうち eval・example)。"""
    res: dict[str, dict] = {}
    for it in items:
        d = res.setdefault(it.source, {"total": 0, "with_rank": 0, "no_rank_excluded": 0,
                                       "eval": 0, "example": 0, "photos_missing": 0, "no_photos": 0})
        d["total"] += 1
        d["photos_missing"] += it.meta.get("photos_missing", 0)
        if it.rank_true is None:
            d["no_rank_excluded"] += 1
        else:
            d["with_rank"] += 1
            d["no_photos"] += 0 if it.photo_paths else 1  # ランクありで入力の写真がない(AI 評価から除外)
            d[it.split] += 1
    return res
