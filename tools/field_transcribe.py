"""客先の 2026年 1店舗(LINE 写真+点検票スキャン)の点検票を、手書きの判定欄まで転記する(紙→正解)。

  python3 tools/field_transcribe.py --run                 # 3 本の PDF 全頁を Sonnet 5.5 で転記(続きから再開できる)
  python3 tools/field_transcribe.py --run --limit 3       # 先頭 3 頁だけ(試し)
  python3 tools/field_transcribe.py --verify              # 15 頁(通常 5・その他 10)を Opus 5.5 でも転記し、判定欄を照合

出力は正解データの置き場(sources.toml の [eval.dirs].line_paper)の transcribed/ に書く(リポジトリには入れない):
  pages.jsonl        Sonnet の転記(1 行 1 頁)
  pages_opus.jsonl   照合用の Opus の転記(15 頁)
  verify.json        照合結果(セル一致率・item_no 一致率・不一致セルの両方の値・費用)

1 頁の JSON: {page, sheet_kind(通常/その他/写真のみ/不明), item_no(整数 or "他N"), sign_name, inspection_date,
             size{w_mm,h_mm,d_mm,gl_mm}, rows:[{part, check, judgment(D/C/B/A/対象外/空), confidence}], remarks}
凡例は paper_scan と同じ D=◎ 経過良好 / C=○ / B=△ / A=×(正規化は D→1, C→2, B→3, A→4)。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import random
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image  # noqa: E402

from kanban_karte.eval import dataset  # noqa: E402
from kanban_karte.eval.claude_judge import _extract_json, cost_usd, make_client  # noqa: E402
from kanban_karte.eval.crop import PdfPages  # noqa: E402

PDFS = {"通常": "通常点検票.pdf", "その他1": "その他１点検票.pdf", "その他2": "その他２点検票.pdf"}
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-5-5"
LONG_EDGE = 2000
MAX_TOKENS = 5000
WORKERS = 4
VERIFY_N = {"通常": 5, "その他": 10}
JUDGMENTS = ("D", "C", "B", "A", "対象外", "空")
SIGN_MARK = {"◎": "D", "○": "C", "〇": "C", "△": "B", "×": "A", "✕": "A"}

PROMPT = """これは「看板保守点検表」(手書きの点検票)1頁のスキャンです。頁の内容を次の JSON だけで返してください(説明文は不要)。

{
  "sheet_kind": "通常" | "その他" | "写真のみ" | "不明",
  "item_no": 看板番号。丸数字(①〜㉓)は整数(① → 1)、「他N」「その他N」は文字列 "他N"。読めなければ null,
  "sign_name": 看板名(見出しの番号を除いた名前。店名・会社名の欄は転記しない。読めなければ null),
  "inspection_date": 点検日(見えたまま。例 "令和8年8月31日"。無ければ null),
  "size": {"w_mm": 整数 or null, "h_mm": 整数 or null, "d_mm": 整数 or null, "gl_mm": 整数 or null},
  "rows": [{"part": 部位名, "check": 点検内容, "judgment": "D" | "C" | "B" | "A" | "対象外" | "空", "confidence": 0〜1}],
  "remarks": 備考欄の文字(スケッチの寸法・メモ。読めた分だけ。無ければ null)
}

決まり:
- sheet_kind: アンカーボルト・鉄骨・板金・ロゴ・照明などの表がある様式は "通常"。躯体種別・取付方法・面板種別などのチェック枠がある様式は "その他"。看板の印刷写真や見出しだけで点検表の表が無い頁は "写真のみ"(rows は空)。
- 凡例は D=◎ 経過良好 / C=○ 劣化あり・経過観察 / B=△ 劣化進行・要改善 / A=× 危険・即時改善。判定欄に書かれた D/C/B/A をそのまま読む。◎○△× で書かれていれば D/C/B/A に直す。
- 判定欄が斜線(/ や \\)で消されている行は "対象外"、何も書かれていない行は "空"。読み取れないが何か書いてある行は "空" にして confidence を 0.3 以下にする。
- part は表の左の「部位」の名前だけ(番号・括弧書きの材質・数量は除く。例 "アンカーボルト"、"鉄骨"、"板金"、"ロゴ"、"照明"、"躯体種別"、"取付方法"、"面板種別"、"表示面"、"設置面"、"その他")。check は右の「点検内容」の印刷文言どおり(例 "錆・腐食はないか")。点検内容が印刷されていない行(部位だけの行)は check を "" にする。
- 表の行を上から順に、すべて rows に入れる(判定が空の行も入れる)。部位が複数行にまたがる表では、2 行目以降も part を同じ名前で繰り返す。
- 判定が部位全体に1つだけ大きく書かれ、点検内容が複数行ある場合は、判定が書かれた行に付け、他の行は "空" にする。
- 設置場所の行や備考のスケッチから寸法(W×H×D、GL までの高さ。単位 mm)を読んで size に入れる。無い値は null。
- confidence は、その行の判定を読み取った自信(はっきり読めたら 0.9 以上)。
JSON オブジェクトだけを返してください。"""


def paths(cfg_path: str | None = None) -> tuple[Path, Path]:
    """(PDF のある raw/, 出力の transcribed/)。置き場は sources.toml の [eval.dirs].line_paper(絶対パス)。"""
    cfg = dataset.load_config(cfg_path)
    base = Path(cfg["nas_root"]) / cfg["dirs"]["line_paper"]
    return base / "raw", base / "transcribed"


def page_jpeg(pdf: Path, page: int) -> str:
    got = PdfPages(pdf).page(page)
    if got is None:
        raise ValueError("画像を取り出せない")
    im = got[0].convert("RGB")
    im.thumbnail((LONG_EDGE, LONG_EDGE), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=88)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def parse_page(text: str) -> dict:
    d = json.loads(_extract_json(text))
    if not isinstance(d, dict):
        raise ValueError("オブジェクトでない")
    d.setdefault("rows", [])
    rows = []
    for r in d["rows"] or []:
        j = str(r.get("judgment") or "空").strip()
        j = SIGN_MARK.get(j, j).upper() if j not in ("対象外", "空") else j
        if j not in JUDGMENTS:
            j = "空"
        try:
            conf = float(r.get("confidence"))
        except (TypeError, ValueError):
            conf = None
        rows.append({"part": str(r.get("part") or "").strip(), "check": str(r.get("check") or "").strip(),
                     "judgment": j, "confidence": conf})
    d["rows"] = rows
    if d.get("sheet_kind") not in ("通常", "その他", "写真のみ", "不明"):
        d["sheet_kind"] = "不明"
    n = d.get("item_no")
    if isinstance(n, str) and re.fullmatch(r"\d+", n.strip()):
        d["item_no"] = int(n)
    return d


def transcribe_page(client, model: str, pdf: Path, pdf_key: str, page: int) -> dict:
    t0 = time.time()
    rec = {"pdf": pdf_key, "page": page, "model": model}
    try:
        img = page_jpeg(pdf, page)
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
                   {"type": "text", "text": PROMPT}]
        in_tok = out_tok = 0
        for attempt in range(2):
            resp = client.messages.create(model=model, max_tokens=MAX_TOKENS,
                                          messages=[{"role": "user", "content": content}])
            in_tok += resp.usage.input_tokens
            out_tok += resp.usage.output_tokens
            text = "".join(b.text for b in resp.content if b.type == "text")
            try:
                rec.update(parse_page(text))
                break
            except (ValueError, json.JSONDecodeError) as e:
                rec["error"] = f"parse: {e}"
        else:
            rec["raw_text"] = text
        if "rows" in rec and rec.get("error") and rec.get("sheet_kind"):
            rec.pop("error")
        rec["usage"] = {"input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost_usd(model, in_tok, out_tok)}
    except Exception as e:  # noqa: BLE001  API/画像の失敗はその頁の error に残し、続行する
        rec["error"] = f"{type(e).__name__}: {e}"
    rec["seconds"] = round(time.time() - t0, 2)
    return rec


def all_pages(raw: Path) -> list[tuple[str, int]]:
    import pypdf
    return [(k, p) for k, name in PDFS.items() for p in range(1, len(pypdf.PdfReader(str(raw / name)).pages) + 1)]


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


def run(raw: Path, out: Path, model: str, targets: list[tuple[str, int]], out_name: str, workers: int = WORKERS) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    dest = out / out_name
    done = {(r["pdf"], r["page"]) for r in read_jsonl(dest) if "error" not in r}
    todo = [t for t in targets if t not in done]
    client = make_client()
    t0 = time.time()
    n_err = 0
    with ThreadPoolExecutor(max_workers=workers) as ex, open(dest, "a", encoding="utf-8") as f:
        for rec in ex.map(lambda t: transcribe_page(client, model, raw / PDFS[t[0]], t[0], t[1]), todo):
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            n_err += 1 if "error" in rec else 0
    recs = latest(read_jsonl(dest))
    cost = sum((r.get("usage") or {}).get("cost_usd") or 0 for r in read_jsonl(dest))
    return {"model": model, "pages_total": len(targets), "pages_done_now": len(todo), "errors_now": n_err,
            "seconds": round(time.time() - t0, 1), "cost_usd_cumulative": round(cost, 3), "pages_ok": len(recs)}


def latest(recs: list[dict]) -> dict[tuple[str, int], dict]:
    """同じ頁が複数行あるとき(エラー後の再実行)は、エラーでない最後の行。"""
    d: dict[tuple[str, int], dict] = {}
    for r in recs:
        k = (r["pdf"], r["page"])
        if "error" not in r or k not in d:
            d[k] = r
    return {k: v for k, v in d.items() if "error" not in v}


# ---------- 照合 ----------

def _norm(s: str) -> str:
    s = re.sub(r"[((].*?[))]", "", s or "")
    return re.sub(r"[\s\d.．、。・:：]+", "", s)


def cell_map(rec: dict) -> dict[str, str]:
    """{ "部位|点検内容#n": 判定 }。同じ部位・点検内容が複数あれば出現順に #n を付ける。"""
    seen: dict[str, int] = {}
    out = {}
    for r in rec.get("rows") or []:
        k = f"{_norm(r['part'])}|{_norm(r['check'])}"
        seen[k] = seen.get(k, 0) + 1
        out[f"{k}#{seen[k]}"] = r["judgment"]
    return out


def pick_verify(recs: dict, seed: int = 0) -> list[tuple[str, int]]:
    """Sonnet が「通常」「その他」と読んだ頁から層別(通常 5・その他 10)。写真のみ・不明は選ばない。"""
    rng = random.Random(f"verify:{seed}")
    out = []
    for kind, n in VERIFY_N.items():
        pool = sorted(k for k, r in recs.items() if r.get("sheet_kind") == kind)
        out += sorted(rng.sample(pool, min(n, len(pool))))
    return out


def compare(a: dict, b: dict) -> dict:
    ca, cb = cell_map(a), cell_map(b)
    keys = sorted(set(ca) | set(cb))
    diffs = [{"cell": k, "sonnet": ca.get(k, "(なし)"), "opus": cb.get(k, "(なし)")}
             for k in keys if ca.get(k, "(なし)") != cb.get(k, "(なし)")]
    letters = ("D", "C", "B", "A")
    marked = {k for k in keys if ca.get(k) in letters or cb.get(k) in letters}  # どちらかが D/C/B/A のセル
    marked_diff = [d for d in diffs if d["cell"] in marked]
    return {"cells": len(keys), "cells_match": len(keys) - len(diffs), "marked_cells": len(marked),
            "marked_match": len(marked) - len(marked_diff), "diffs": diffs,
            "item_no_match": a.get("item_no") == b.get("item_no"), "kind_match": a.get("sheet_kind") == b.get("sheet_kind")}


def verify(raw: Path, out: Path, seed: int = 0) -> dict:
    recs = latest(read_jsonl(out / "pages.jsonl"))
    pages = pick_verify(recs, seed)
    stat = run(raw, out, OPUS, pages, "pages_opus.jsonl")
    opus = latest(read_jsonl(out / "pages_opus.jsonl"))
    per, tot = [], {"cells": 0, "cells_match": 0, "marked_cells": 0, "marked_match": 0, "item_no": 0, "kind": 0, "n": 0}
    for k in pages:
        if k not in opus:
            continue
        c = compare(recs[k], opus[k])
        per.append({"pdf": k[0], "page": k[1], "sonnet_item_no": recs[k].get("item_no"),
                    "opus_item_no": opus[k].get("item_no"), **c})
        tot["n"] += 1
        tot["item_no"] += c["item_no_match"]
        tot["kind"] += c["kind_match"]
        for f in ("cells", "cells_match", "marked_cells", "marked_match"):
            tot[f] += c[f]
    cost_opus = sum((r.get("usage") or {}).get("cost_usd") or 0 for r in read_jsonl(out / "pages_opus.jsonl"))
    res = {"pages": tot["n"], "strata": VERIFY_N, "seed": seed, "totals": tot,
           "cell_match_rate": tot["cells_match"] / tot["cells"] if tot["cells"] else None,
           "marked_cell_match_rate": tot["marked_match"] / tot["marked_cells"] if tot["marked_cells"] else None,
           "item_no_match_rate": tot["item_no"] / tot["n"] if tot["n"] else None,
           "opus_run": stat, "opus_cost_usd": round(cost_opus, 3), "per_page": per}
    (out / "verify.json").write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    return res


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", action="store_true", help="全頁を Sonnet で転記")
    ap.add_argument("--verify", action="store_true", help="15 頁を Opus でも転記して照合")
    ap.add_argument("--limit", type=int, help="先頭からこの頁数だけ(試し)")
    ap.add_argument("--sources-config")
    a = ap.parse_args()
    raw, out = paths(a.sources_config)
    if a.run:
        pages = all_pages(raw)[: a.limit]
        print(json.dumps(run(raw, out, SONNET, pages, "pages.jsonl"), ensure_ascii=False))
    if a.verify:
        r = verify(raw, out)
        print(json.dumps({k: r[k] for k in r if k != "per_page"}, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
