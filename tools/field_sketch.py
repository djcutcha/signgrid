"""点検票の「清書の表 → 次頁の手描きスケッチ」の対を作り、スケッチの寸法・取付方法を読む(紙→台帳)。

  python3 tools/field_sketch.py --pairs              # 対応付けだけ(API なし。件数を出す)
  python3 tools/field_sketch.py --run                # スケッチ頁を Sonnet 5.5 で読む(続きから再開できる)
  python3 tools/field_sketch.py --run --limit 3      # 先頭 3 頁だけ(試し)
  python3 tools/field_sketch.py --summary            # 取れた基数・表の size との一致率

対応付けの規則: 同じ PDF で「看板番号のある頁 n」の直後に続く「看板番号のない頁 n+1(…n+k)」を、その看板のスケッチとする。
通常・その他1・その他2 とも同じ規則。直前に番号つきの頁が無い頁(先頭、頁が抜けた直後)は unpaired に残し、付けない。

出力は正解データの置き場(sources.toml の [eval.dirs].line_paper)の transcribed/sketches.jsonl(リポジトリには入れない):
  1 行 1 頁。{pdf, page, sign_key("通常3"/"他12"), w_mm, h_mm, d_mm, gl_mm, gl_edge(bottom/top/unknown),
              post("□50" 等), mount(バンセン/ビス/吊り/接着/不明), display_text, printed, is_sketch, confidence, raw_notes,
              table_size, size_check{w,h,gl: match/differ/none}, usage}
食い違いは両方残す(table_size と w_mm/h_mm/gl_mm)。API 費用の上限は BUDGET_USD。
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import field_transcribe as ft  # noqa: E402

from kanban_karte.eval.claude_judge import _extract_json, cost_usd, make_client  # noqa: E402

MODEL = ft.SONNET
MAX_TOKENS = 2500
WORKERS = 4
BUDGET_USD = 1.5
GL_EDGES = ("bottom", "top", "unknown")
MOUNTS = ("バンセン", "ビス", "吊り", "接着", "不明")
MATCH_TOL = 0.03  # 表の size とスケッチの寸法は、この相対差以内なら一致

PROMPT = """これは「看板保守点検表」に続く頁のスキャンです。看板の形・寸法・取付方法を職人が手で描いたスケッチ(印刷した案を貼った頁もある)が載っています。読めた内容を次の JSON だけで返してください(説明文は不要)。

{
  "is_sketch": 看板の形や寸法が描かれた頁なら true。表だけの頁・白紙・写真だけの頁なら false,
  "w_mm": 看板の幅(整数 mm。無ければ null),
  "h_mm": 看板の高さ(整数 mm。無ければ null),
  "d_mm": 看板の厚み(奥行。整数 mm。無ければ null),
  "gl_mm": GL(地面)からの高さ(整数 mm。無ければ null),
  "gl_edge": gl_mm が「GL から看板のどの縁までか」。下の縁までなら "bottom"、上の縁までなら "top"、寸法線の端がどこか読み取れなければ "unknown"(gl_mm が null のときも "unknown"),
  "post": 支柱の寸法(例 "□50"、"φ89"。無ければ null),
  "mount": 取付方法。"バンセン" | "ビス" | "吊り" | "接着" | "不明",
  "display_text": 看板に表示されている文字(読めた分。無ければ null),
  "printed": 貼られているのが印刷した案(図面・デザイン)なら true、手描きなら false,
  "confidence": 寸法を読み取った全体の自信 0〜1,
  "raw_notes": 寸法線や書き込みのうち、上のどれにも入れられなかった物(読めたまま。無ければ null)
}

決まり:
- 寸法の単位は mm。m や cm で書かれていれば mm に直す。W は横、H は縦。複数の看板・部材が描かれているときは、この頁の中心の看板の寸法を取る。
- w_mm・h_mm は看板本体(表示面の箱・パネル)の外寸。支柱や基礎を含む全体の寸法ではない。支柱を含む全体の高さ(GL から看板の最上端まで)が書かれていれば、それは h_mm でなく gl_mm に入れて gl_edge を "top" にする。看板の下端から GL までなら gl_mm に入れて "bottom"。
- 数字を推測で補わない。読めない値は null。
- 業界用語(バンセン・GL・支柱・吊り 等)は言い換えず、書かれたまま raw_notes に残す。
JSON オブジェクトだけを返してください。"""


# ---------- 対応付け(API なし) ----------

def pair_pages(recs: dict[tuple[str, int], dict]) -> tuple[dict[tuple[str, int], str], list[dict]]:
    """({(pdf, page): sign_key}, unpaired)。看板番号のある頁 n の直後の、番号のない頁 n+1…n+k がその看板のスケッチ。
    頁が抜けている(転記が無い)所を飛び越えては付けない。"""
    from field_index import page_key  # 遅延(field_index が field_sketch を読むので循環を避ける)

    paired: dict[tuple[str, int], str] = {}
    unpaired: list[dict] = []
    for pdf in ft.PDFS:
        cur: str | None = None
        last = 0
        for page in sorted(p for (f, p) in recs if f == pdf):
            rec = recs[(pdf, page)]
            key = page_key(rec) if rec.get("item_no") not in (None, "") else None
            if key is not None:
                cur, last = f"{key[0]}{key[1]}", page
            elif cur is not None and page == last + 1:
                paired[(pdf, page)] = cur
                last = page
            else:
                unpaired.append({"pdf": pdf, "page": page, "why": "直前に看板番号のある頁がない"})
                cur = None
    return paired, unpaired


def table_size(recs: dict, paired_key: str) -> dict:
    """その看板の表の頁に書かれた size(W×H×D、GL)。無ければ {}。"""
    from field_index import group_pages, size_of

    by_key, _ = group_pages({k: v for k, v in recs.items() if v.get("item_no") not in (None, "")})
    for key, pages in by_key.items():
        if f"{key[0]}{key[1]}" == paired_key:
            return size_of(pages)
    return {}


def _close(a, b) -> str:
    if not a or not b:
        return "none"
    return "match" if abs(a - b) <= MATCH_TOL * max(a, b) else "differ"


def size_check(sk: dict, tbl: dict) -> dict:
    return {"w": _close(sk.get("w_mm"), tbl.get("w_mm")), "h": _close(sk.get("h_mm"), tbl.get("h_mm")),
            "gl": _close(sk.get("gl_mm"), tbl.get("gl_mm"))}


# ---------- 読み取り ----------

def _int(v):
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def parse_sketch(text: str) -> dict:
    d = json.loads(_extract_json(text))
    if not isinstance(d, dict):
        raise ValueError("オブジェクトでない")
    out = {k: _int(d.get(k)) for k in ("w_mm", "h_mm", "d_mm", "gl_mm")}
    edge = str(d.get("gl_edge") or "unknown").strip().lower()
    out["gl_edge"] = edge if edge in GL_EDGES else "unknown"
    if out["gl_mm"] is None:
        out["gl_edge"] = "unknown"
    mount = str(d.get("mount") or "不明").strip()
    out["mount"] = mount if mount in MOUNTS else "不明"
    out["post"] = (str(d["post"]).strip() or None) if d.get("post") else None
    out["display_text"] = (str(d["display_text"]).strip() or None) if d.get("display_text") else None
    out["printed"] = bool(d.get("printed"))
    out["is_sketch"] = bool(d.get("is_sketch", True))
    try:
        out["confidence"] = max(0.0, min(1.0, float(d.get("confidence"))))
    except (TypeError, ValueError):
        out["confidence"] = None
    out["raw_notes"] = (str(d["raw_notes"]).strip() or None) if d.get("raw_notes") else None
    return out


def read_page(client, pdf: Path, pdf_key: str, page: int, sign_key: str, tbl: dict) -> dict:
    t0 = time.time()
    rec = {"pdf": pdf_key, "page": page, "sign_key": sign_key, "model": MODEL, "table_size": tbl}
    try:
        img = ft.page_jpeg(pdf, page)
        content = [{"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": img}},
                   {"type": "text", "text": PROMPT}]
        in_tok = out_tok = 0
        for _ in range(2):
            resp = client.messages.create(model=MODEL, max_tokens=MAX_TOKENS, messages=[{"role": "user", "content": content}])
            in_tok += resp.usage.input_tokens
            out_tok += resp.usage.output_tokens
            text = "".join(b.text for b in resp.content if b.type == "text")
            try:
                rec.update(parse_sketch(text))
                rec.pop("error", None)
                break
            except (ValueError, json.JSONDecodeError) as e:
                rec["error"] = f"parse: {e}"
        else:
            rec["raw_text"] = text
        rec["usage"] = {"input_tokens": in_tok, "output_tokens": out_tok, "cost_usd": cost_usd(MODEL, in_tok, out_tok)}
        if "error" not in rec:
            rec["size_check"] = size_check(rec, tbl)
    except Exception as e:  # noqa: BLE001  API/画像の失敗はその頁の error に残し、続行する
        rec["error"] = f"{type(e).__name__}: {e}"
    rec["seconds"] = round(time.time() - t0, 2)
    return rec


def run(raw: Path, tdir: Path, limit: int | None = None) -> dict:
    recs = ft.latest(ft.read_jsonl(tdir / "pages.jsonl"))
    paired, unpaired = pair_pages(recs)
    dest = tdir / "sketches.jsonl"
    done = {(r["pdf"], r["page"]) for r in ft.read_jsonl(dest) if "error" not in r}
    spent = sum((r.get("usage") or {}).get("cost_usd") or 0 for r in ft.read_jsonl(dest))
    todo = [k for k in sorted(paired, key=lambda k: (list(ft.PDFS).index(k[0]), k[1])) if k not in done][:limit]
    tbl_cache = {sk: table_size(recs, sk) for sk in set(paired.values())}
    client = make_client()
    t0, n_err, skipped = time.time(), 0, 0
    with ThreadPoolExecutor(max_workers=WORKERS) as ex, open(dest, "a", encoding="utf-8") as f:
        # 予算を超えないよう WORKERS 頁ずつ流す
        for i in range(0, len(todo), WORKERS):
            if spent >= BUDGET_USD:
                skipped = len(todo) - i
                break
            batch = todo[i:i + WORKERS]
            for rec in ex.map(lambda k: read_page(client, raw / ft.PDFS[k[0]], k[0], k[1], paired[k], tbl_cache[paired[k]]), batch):
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                f.flush()
                n_err += 1 if "error" in rec else 0
                spent += (rec.get("usage") or {}).get("cost_usd") or 0
    return {"paired_pages": len(paired), "unpaired_pages": len(unpaired), "read_now": len(todo) - skipped,
            "errors_now": n_err, "skipped_by_budget": skipped, "seconds": round(time.time() - t0, 1),
            "cost_usd_cumulative": round(spent, 3)}


# ---------- 看板ごとの合成 ----------

def latest_sketches(rows: list[dict]) -> dict[tuple[str, int], dict]:
    d: dict[tuple[str, int], dict] = {}
    for r in rows:
        k = (r["pdf"], r["page"])
        if "error" not in r or k not in d:
            d[k] = r
    return {k: v for k, v in d.items() if "error" not in v}


def merge_sign(pages: list[dict]) -> dict:
    """同じ看板のスケッチ頁(複数あり得る)を 1 つにする。寸法・取付方法は、自信の高い頁の読めた値を優先(頁ごとに null でない最初の値)。
    スケッチでない頁(is_sketch が偽)は寸法に使わない。"""
    use = sorted((p for p in pages if p.get("is_sketch", True)), key=lambda p: -(p.get("confidence") or 0))
    out: dict = {"pages": [p["page"] for p in sorted(pages, key=lambda p: p["page"])]}
    for k in ("w_mm", "h_mm", "d_mm", "post", "display_text"):
        out[k] = next((p[k] for p in use if p.get(k)), None)
    gl = next((p for p in use if p.get("gl_mm")), None)
    out["gl_mm"], out["gl_edge"] = (gl["gl_mm"], gl.get("gl_edge") or "unknown") if gl else (None, "unknown")
    out["mount"] = next((p["mount"] for p in use if p.get("mount") not in (None, "不明")), "不明")
    out["printed"] = any(p.get("printed") for p in use)
    out["confidence"] = max((p.get("confidence") or 0 for p in use), default=None)
    return out


def by_sign(rows: list[dict]) -> dict[str, dict]:
    groups: dict[str, list[dict]] = {}
    for r in latest_sketches(rows).values():
        groups.setdefault(r["sign_key"], []).append(r)
    return {k: merge_sign(v) for k, v in groups.items()}


def summary(tdir: Path) -> dict:
    rows = latest_sketches(ft.read_jsonl(tdir / "sketches.jsonl"))
    signs = by_sign(list(rows.values()))
    chk = [r["size_check"] for r in rows.values() if r.get("size_check") and r.get("is_sketch", True)]
    def rate(f):
        both = [c[f] for c in chk if c[f] != "none"]
        return {"compared": len(both), "match": sum(1 for x in both if x == "match")}
    return {"pages_read": len(rows), "pages_sketch": sum(1 for r in rows.values() if r.get("is_sketch", True)),
            "signs": len(signs),
            "signs_w_h": sum(1 for s in signs.values() if s["w_mm"] and s["h_mm"]),
            "signs_gl": sum(1 for s in signs.values() if s["gl_mm"]),
            "gl_edge": {e: sum(1 for s in signs.values() if s["gl_mm"] and s["gl_edge"] == e) for e in GL_EDGES},
            "mount": {m: sum(1 for s in signs.values() if s["mount"] == m) for m in MOUNTS},
            "printed_signs": sum(1 for s in signs.values() if s["printed"]),
            "size_vs_table": {"w": rate("w"), "h": rate("h"), "gl": rate("gl")},
            "cost_usd": round(sum((r.get("usage") or {}).get("cost_usd") or 0 for r in ft.read_jsonl(tdir / "sketches.jsonl")), 3)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pairs", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--summary", action="store_true")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--sources-config")
    a = ap.parse_args()
    raw, tdir = ft.paths(a.sources_config)
    if a.pairs:
        recs = ft.latest(ft.read_jsonl(tdir / "pages.jsonl"))
        paired, unpaired = pair_pages(recs)
        no_item = sum(1 for r in recs.values() if r.get("item_no") in (None, ""))
        print(json.dumps({"pages_without_item_no": no_item, "paired": len(paired), "unpaired": unpaired,
                          "signs": len(set(paired.values())),
                          "per_pdf": {p: sum(1 for k in paired if k[0] == p) for p in ft.PDFS}}, ensure_ascii=False, indent=1))
    if a.run:
        print(json.dumps(run(raw, tdir, a.limit), ensure_ascii=False))
    if a.summary:
        print(json.dumps(summary(tdir), ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
