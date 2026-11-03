"""客先の 2026年 1店舗(LINE 写真+点検票スキャン)の一式を、今の SignGrid に通して「一周」し、工程ごとの時間と費用を測る。

  python3 tools/field_run.py                      # 全工程(API 可: Opus 5.5 の下書き)
  python3 tools/field_run.py --no-draft           # 登録と出力の時間だけ(API なし)
  python3 tools/field_run.py --max-signs 3        # 試し

工程: (a) 点検票 PDF 3 本を intake で束として登録(pages・種別)→ (b) 索引から看板と写真を台帳に登録
      → (b2) スケッチ頁(図面)を看板に付け、寸法・全高を台帳に入れる(items.tsv の sketch_* 列。pages.tag="図面")
      → (c) pipeline/draft.py で全基の下書き(Opus、写真は 1 基 6 枚まで、承認済み規則なし。図面は入力に入れない)→ (d) 様式 2 種を書き出す。
  python3 tools/field_run.py --no-draft --draft-keys 他2,他12   # 下書きは指定の看板だけ(画面写真用の少数)
台帳は data/private/field_site.sqlite(未追跡)、記録は runs/field_site_run.json(未追跡)。
店名・客先名は台帳にも記録にも入れない(看板名は「看板 ①」「その他 N」)。
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
import sys
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import field_legal as fl  # noqa: E402
import field_transcribe as ft  # noqa: E402

from kanban_karte.eval import dataset, metrics  # noqa: E402
from kanban_karte.eval.judge import Prediction  # noqa: E402
from kanban_karte.intake.pdf_bundle import intake_pdf  # noqa: E402
from kanban_karte.ledger.db import FIGURE_TAG, Ledger, Sign, Site  # noqa: E402
from kanban_karte.output.client_a_form import write_client_a  # noqa: E402
from kanban_karte.output.tokyo_form import write_tokyo_form  # noqa: E402
from kanban_karte.pipeline import draft as pdraft  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SITE_ID = "site1"
MAX_PHOTOS = 6
MODEL = "claude-opus-5-5"


def iso_date(s: str | None) -> str | None:
    m = re.search(r"令和\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", unicodedata.normalize("NFKC", s or ""))
    return f"{2018 + int(m.group(1))}-{int(m.group(2)):02d}-{int(m.group(3)):02d}" if m else None


class TimedJudge:
    """ClaudeJudge を包み、看板ごとの秒数と費用を残す。1 基目だけ単独(例示のキャッシュを作る)、残りは並列。"""

    def __init__(self, judge, workers: int = 4):
        self.judge, self.workers = judge, workers
        self.seconds: dict[str, float] = {}

    def _one(self, it):
        t0 = time.time()
        p = self.judge.judge(it)
        self.seconds[it.sign_id] = round(time.time() - t0, 2)
        return p

    def judge_many(self, items):
        if not items:
            return []
        first, rest = [self._one(items[0])], items[1:]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            return first + list(ex.map(self._one, rest))


def register_pdfs(ledger: Ledger, raw: Path, data_dir: Path) -> list[dict]:
    out = []
    for key, name in ft.PDFS.items():
        t0 = time.time()
        r = intake_pdf(ledger, raw / name, data_dir)
        out.append({"pdf": key, "bundle_id": r.bundle_id, "pages": r.pages, "kind_counts": r.kind_counts,
                    "existed": r.existed, "seconds": round(time.time() - t0, 2)})
    return out


def register_signs(ledger: Ledger, base: Path, address: str | None, max_signs: int | None) -> tuple[int, list[dict]]:
    """索引の看板を台帳に登録する。写真は撮影順の先頭 MAX_PHOTOS 枚(判定役に送る枚数と同じ)。bundle は写真の束 1 つ。"""
    ledger.upsert_site(Site(id=SITE_ID, name="客先の店舗(2026年)", address=address or None))
    bid = ledger.add_bundle("field-photos", hashlib.sha256(b"field-photos").hexdigest())
    ledger.set_bundle(bid, site_id=SITE_ID, status="classified")
    rows = list(csv.DictReader(open(base / "items.tsv", encoding="utf-8"), delimiter="\t"))
    rows = [r for r in rows if r["key"].startswith(("通常", "他")) and json.loads(r["photo_files"])] \
        + [r for r in rows if not json.loads(r["photo_files"])]
    rows = rows[:max_signs]
    info = []
    for r in rows:
        files = json.loads(r["photo_files"])
        dm = fl.dims(r)  # 表の W×H(無ければスケッチ)と、スケッチの GL から出した全高
        sid = f"fs-{r['key']}"
        photos = []
        for j, f in enumerate(files[:MAX_PHOTOS], 1):  # 画面が /media で配れるよう、台帳の画像置き場(data_dir)にコピーする
            dst = ledger.data_dir / "photos" / sid / f"{j:02d}.jpg"
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(base / f, dst)
            photos.append(ledger.rel_path(dst))
        ledger.upsert_sign(Sign(
            id=sid, site_id=SITE_ID, name=r["sign_name"], size_w=dm["size_w"], size_h=dm["size_h"], height_m=dm["height_m"],
            address=address or None, photos=photos, source_bundle=bid,
            last_inspected=iso_date(r["inspection_date"]),
            remark=None if files else "写真なし(1フォルダに2基が混ざる、またはフォルダが無い)"))
        info.append({"sign_id": sid, "key": r["key"], "photos_total": len(files), "photos_used": min(len(files), MAX_PHOTOS)})
    return bid, info


def register_figures(ledger: Ledger, base: Path) -> dict:
    """items.tsv の sketch_pages(「PDF名:頁」の;区切り)の頁画像を、その看板の図面として付ける。
    頁は PDF の束にすでに画像で入っている(pages.image_path)ので、pages.tag を「図面」にして、看板の画像の並びの末尾に足す。"""
    bundles = {Path(b["file"]).name: b["id"] for b in ledger.list_bundles()}
    n_pages = n_signs = 0
    for r in csv.DictReader(open(base / "items.tsv", encoding="utf-8"), delimiter="\t"):
        if not r.get("sketch_pages"):
            continue
        s = ledger.get_sign(f"fs-{r['key']}")
        if s is None:
            continue
        added = 0
        for ref in r["sketch_pages"].split(";"):
            pdf, page = ref.rsplit(":", 1)
            bid = bundles.get(ft.PDFS[pdf])
            row = next((p for p in ledger.list_pages(bid) if p["page_no"] == int(page)), None) if bid else None
            if row is None or not row["image_path"]:
                continue
            ledger.set_page_tag(bid, int(page), FIGURE_TAG)
            rel = ledger.rel_path(ledger.resolve_path(row["image_path"]))
            if rel not in s.photos:
                s.photos.append(rel)
            added += 1
        if added:
            ledger.upsert_sign(s)
            n_pages += added
            n_signs += 1
    return {"figure_pages": n_pages, "signs_with_figures": n_signs}


def draft_keys(ledger: Ledger, keys: list[str], model: str) -> list[dict]:
    """指定の看板だけ下書きを書く(bundle 全体ではなく。画面写真用の少数)。図面は判定役に渡らない(draft.eval_item_for)。"""
    judge = pdraft.default_judge(model=model, max_photos=MAX_PHOTOS)
    out = []
    for k in keys:
        s = ledger.get_sign(f"fs-{k}")
        if s is None:
            continue
        pred = judge.judge(pdraft.eval_item_for(ledger, s))
        if pred.rank_pred is None:
            out.append({"key": k, "status": "error"})
            continue
        pdraft.apply_prediction(s, pred)
        ledger.upsert_sign(s)
        out.append({"key": k, "status": "draft", "rank": s.rank, "cost_usd": (pred.usage or {}).get("cost_usd")})
    return out


def truth_compare(ledger: Ledger, base: Path, per: list[dict], model: str) -> dict:
    """下書きのランクを正解(点検票の最悪値)と同じ指標で比べる。全看板と評価側(split=eval)の 2 通り。"""
    truth = {i.sign_id: i for i in dataset.load_line_paper(base)}
    out = {}
    for name, pick in (("all", lambda it: True), ("eval_split", lambda it: it.split == "eval")):
        pairs = []
        for p in per:
            it = truth.get(f"line_paper:{p['key']}")
            if it is None or it.rank_true is None or not it.photo_paths or not pick(it):
                continue
            pairs.append((it, Prediction(p["draft_rank"], None, None)))
        out[name] = {k: v for k, v in metrics.compute(pairs).items() if k not in ("items", "action_breakdown")}
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=str(REPO / "data/private/field_site.sqlite"))
    ap.add_argument("--data-dir", default=str(REPO / "data/private/field_intake"))
    ap.add_argument("--out", default=str(REPO / "runs/field_site_run.json"))
    ap.add_argument("--outputs-dir", default=str(REPO / "runs/field_site_out"))
    ap.add_argument("--no-draft", action="store_true")
    ap.add_argument("--max-signs", type=int)
    ap.add_argument("--draft-keys", help="--no-draft のとき、この看板(例 他2,他12)だけ下書きを書く")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--sources-config")
    a = ap.parse_args()

    cfg = dataset.load_config(a.sources_config)
    raw, tdir = ft.paths(a.sources_config)
    base = raw.parent
    addr_file = REPO / "data/private/field_site_address.txt"  # 住所は未追跡のファイルにだけ(区まで)
    address = addr_file.read_text(encoding="utf-8").strip() if addr_file.exists() else (cfg.get("line_paper") or {}).get("address")
    db = Path(a.db)
    if db.exists():
        db.unlink()  # 毎回作り直す(所要時間を同じ条件で測る)
    db.parent.mkdir(parents=True, exist_ok=True)
    data_dir = Path(a.data_dir)
    ledger = Ledger(db, data_dir=data_dir)
    res: dict = {"model": a.model, "max_photos": MAX_PHOTOS, "steps": {}}

    t0 = time.time()
    res["bundles"] = register_pdfs(ledger, raw, data_dir)
    res["steps"]["register_pages_s"] = round(time.time() - t0, 1)

    t0 = time.time()
    bid, info = register_signs(ledger, base, address, a.max_signs)
    res["steps"]["register_signs_s"] = round(time.time() - t0, 1)
    res["signs_registered"] = len(info)
    res["figures"] = register_figures(ledger, base)
    if a.no_draft and a.draft_keys:
        res["draft_keys"] = draft_keys(ledger, [k.strip() for k in a.draft_keys.split(",") if k.strip()], a.model)

    per = []
    if not a.no_draft:
        judge = TimedJudge(pdraft.default_judge(model=a.model, max_photos=MAX_PHOTOS))
        t0 = time.time()
        results = pdraft.draft_bundle(ledger, bid, judge)
        res["steps"]["draft_s"] = round(time.time() - t0, 1)
        meta = {i["sign_id"]: i for i in info}
        for r in results:
            s = ledger.get_sign(r.sign_id)
            per.append({**meta[r.sign_id], "status": r.status, "draft_rank": r.rank, "undecided": r.undecided,
                        "cost_usd": r.cost_usd, "seconds": judge.seconds.get(r.sign_id), "error": r.error})
        res["per_sign"] = per
        res["draft"] = {"signs": len(per), "drafted": sum(1 for p in per if p["status"] == "draft"),
                        "needs_photos": sum(1 for p in per if p["status"] == "needs_photos"),
                        "errors": sum(1 for p in per if p["status"] == "error"),
                        "undecided_total": sum(p["undecided"] for p in per),
                        "signs_with_undecided": sum(1 for p in per if p["undecided"]),
                        "cost_usd": round(sum(p["cost_usd"] or 0 for p in per), 3)}
        res["draft_vs_truth"] = truth_compare(ledger, base, [p for p in per if p["status"] == "draft"], a.model)

    out_dir = Path(a.outputs_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    write_tokyo_form(ledger, SITE_ID, out_dir / "tokyo_form2.xlsx")
    t1 = time.time()
    write_client_a(ledger, SITE_ID, out_dir / "client_a.xlsx")
    res["steps"]["output_tokyo_s"] = round(t1 - t0, 1)
    res["steps"]["output_client_a_s"] = round(time.time() - t1, 1)

    pages = ft.read_jsonl(tdir / "pages.jsonl")
    res["transcribe"] = {"pages": len(ft.latest(pages)), "model": ft.SONNET,
                         "seconds_sum": round(sum(r.get("seconds") or 0 for r in pages), 1),
                         "cost_usd": round(sum((r.get("usage") or {}).get("cost_usd") or 0 for r in pages), 3)}
    res["cost_usd_total_pipeline"] = round(res["transcribe"]["cost_usd"] + res.get("draft", {}).get("cost_usd", 0), 3)
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in res.items() if k != "per_sign"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
