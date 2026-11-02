"""近接パス: 第1パスが 1・2 とした看板を、元解像度の拡大(2×2 分割+全景)で見直す。

第1パスの写真は長辺 1280px に縮小されている。小さな錆・ひび・退色が縮小で消えている、という仮説を測る。

  python3 -m kanban_karte.eval.closeup --first-pass runs/X.json --select-out runs/targets.tsv \\
      --sources-config .../sources.toml                      # 本丸+対照の看板を選ぶ(API なし)
  python3 -m kanban_karte.eval.closeup --first-pass runs/X.json --closeup-targets runs/targets.tsv \\
      --closeup-out runs/closeup.json --sources-config .../sources.toml --dry-run   # 送る量だけ
  python3 -m kanban_karte.eval.closeup ... (--dry-run を外すと API を呼ぶ)

入力: 第1パスの結果 JSON(run_eval の出力)と、対象の sign_id 一覧(1 行 1 件。`sign_id<TAB>群` でもよい。# 始まりは無視)。
1 基ごとに、写真を最大 2 枚(第1パスの reasons が「写真N」と挙げた番号を優先、足りなければ先頭から)選び、
各写真を元解像度から 2×2 に分けたタイル 4 枚(各タイル長辺 ≦1280px)+全景 1 枚にして送る(最大 10 枚)。
第1パスの下書き(ランク・項目判定・理由)を見せ、項目ごとに見直させる。見えない物は見えないと答えさせる。
総合ランクは、項目のある様式では項目の最悪値(aggregate.synth_rank の items と同じ規則)、項目のない様式
(紙のスキャン束)ではモデルが返した総合ランク。正解・現場名・店名は送らない。
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import random
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

import anthropic
from PIL import Image, ImageOps

from . import dataset, metrics
from .claude_judge import (MAX_TOKENS, MAX_WORKERS, RANK_NAME, SYSTEM_HEAD, JPEG_QUALITY, MAX_LONG_EDGE,
                           _as_rank, _extract_json, cost_usd, make_client)

DEFAULT_MODEL = "claude-opus-5-5"
MAX_CLOSEUP_PHOTOS = 2
FIRST_PASS_MAX_PHOTOS = 6   # 第1パスが番号を振った写真の範囲(photo_paths の先頭 6 枚)
TILE_NAMES = ("左上", "右上", "左下", "右下")
GROUP_TARGET, GROUP_CONTROL = "本丸", "対照"

SYSTEM = SYSTEM_HEAD + """

今回は近接の見直しです。別の点検者が、縮小した写真から付けた下書きの判定が渡されます。同じ写真を元の解像度から 2×2 に分けた拡大(タイル)と全景で見せます。
- 拡大でなければ見えなかった、錆・ひび・剥がれ・退色・変形・ボルトの緩み・固定の浮きが新たに見えるか、項目ごとに見直してください。
- 見えない物は見えないと答えます。写真に見えないものを「念のため」で上げることはしません(要対応でない看板を要対応とする誤りも測っています)。下書きが妥当なら、下書きと同じ値を返します。
- 値を上げる項目は、根拠の写真番号とタイル位置(左上・右上・左下・右下・全景)を書いてください。
- 項目の値は、拡大を見たうえでのあなたの判定(1〜4)です。拡大でも判断できない項目は null にします。
- 看板全体のランク rank は、項目ごとの判定のうち最も悪い値にします(項目一覧がないときは看板全体の判定)。

出力は JSON オブジェクト1つだけにしてください。前後に説明文やコードフェンスを付けないでください。形式は次のとおりです。
{"rank": 1〜4の整数, "items": {"項目名": {"rank": 1〜4の整数 または null, "evidence": "何が見えたか(1文。見えなければ「拡大でも見られない」)", "photo": 写真番号の整数 または null, "tile": "左上|右上|左下|右下|全景 または null"}, ...}, "summary": "近接で分かったことを40字以内で"}
項目一覧がないときは items を {} にし、根拠は summary に書いてください。"""


# ---------- 写真と画像 ----------

def cited_photos(reasons: list[str] | None, n_photos: int) -> list[int]:
    """第1パスの reasons が「写真N」「写真2・3」と挙げた番号(1 始まり)を、初出の順に重複なしで返す。"""
    seen: list[int] = []
    for text in reasons or []:
        for m in re.finditer(r"写真((?:\s*\d+\s*[・,、]?)+)", text):
            for d in re.findall(r"\d+", m.group(1)):
                n = int(d)
                if 1 <= n <= n_photos and n not in seen:
                    seen.append(n)
    return seen


def choose_photos(reasons: list[str] | None, n_photos: int, k: int = MAX_CLOSEUP_PHOTOS) -> list[int]:
    """近接に使う写真番号(1 始まり、最大 k 枚)。挙げられた番号を優先し、足りなければ先頭から補う。"""
    n_photos = min(n_photos, FIRST_PASS_MAX_PHOTOS)
    pick = cited_photos(reasons, n_photos)[:k]
    for i in range(1, n_photos + 1):
        if len(pick) >= k:
            break
        if i not in pick:
            pick.append(i)
    return pick


def open_oriented(path: str) -> Image.Image:
    im = ImageOps.exif_transpose(Image.open(path))
    if im.mode != "RGB":
        rgba = im.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[3])
        im = bg
    return im


def split_2x2(im: Image.Image, long_edge: int = MAX_LONG_EDGE) -> list[tuple[str, Image.Image]]:
    """元解像度の画像を 2×2 に分け(左上・右上・左下・右下)、各タイルの長辺を long_edge 以下にする。
    その後ろに全景(長辺 long_edge 以下に縮小)を足す。計 5 枚。拡大はしない。"""
    w, h = im.size
    xs, ys = (0, w // 2, w), (0, h // 2, h)
    out = []
    for r in range(2):
        for c in range(2):
            t = im.crop((xs[c], ys[r], xs[c + 1], ys[r + 1]))
            t.thumbnail((long_edge, long_edge), Image.LANCZOS)
            out.append((TILE_NAMES[r * 2 + c], t))
    whole = im.copy()
    whole.thumbnail((long_edge, long_edge), Image.LANCZOS)
    out.append(("全景", whole))
    return out


def _jpeg_b64(im: Image.Image) -> str:
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=JPEG_QUALITY)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def photo_blocks(photo_no: int, path: str) -> list[dict]:
    """写真 1 枚 → ラベル文+画像のブロック(全景 → タイル 4 枚の順)。"""
    parts = split_2x2(open_oriented(path))
    parts = parts[-1:] + parts[:-1]
    blocks: list[dict] = []
    for name, im in parts:
        label = f"写真{photo_no} 全景" if name == "全景" else f"写真{photo_no} 拡大タイル {name}(元画像の{name}の 1/4)"
        blocks.append({"type": "text", "text": label})
        blocks.append({"type": "image", "source": {
            "type": "base64", "media_type": "image/jpeg", "data": _jpeg_b64(im)}})
    return blocks


def estimate_photo_tokens(path: str) -> int:
    """送る 5 枚の画像トークン概算(幅x高さ/750)。タイルは元の半分の寸法。"""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        w, h = im.size
    tiles = [(w // 2, h // 2), (w - w // 2, h // 2), (w // 2, h - h // 2), (w - w // 2, h - h // 2), (w, h)]
    total = 0
    for tw, th in tiles:
        s = min(1.0, MAX_LONG_EDGE / max(tw, th))
        total += min(4784, -(-max(1, round(tw * s)) * max(1, round(th * s)) // 750))
    return total


# ---------- プロンプトと応答 ----------

def draft_message(item: dataset.EvalItem, rec: dict, photo_nos: list[int]) -> str:
    L = [f"拡大で見せた写真は、第1パスの写真番号 {'・'.join(map(str, photo_nos))} です(各写真: 全景 1 枚+タイル 4 枚)。"]
    if item.meta.get("sign_name"):
        L.append(f"看板名: {item.meta['sign_name']}")
    form_items = item.meta.get("form_items") or []
    if form_items:
        L.append("様式の項目名一覧です。items のキーは、この名前をそのまま使ってください:")
        L += [f"- {k}" for k in form_items]
    else:
        L.append("項目一覧はありません。items は空のオブジェクト {} にしてください。")
    L += ["", f"【第1パスの下書き】看板全体のランク: {rec['rank_pred']}({RANK_NAME[rec['rank_pred']]})"]
    for k, v in (rec.get("item_judgments_pred") or {}).items():
        L.append(f"- {k}: {v}")
    if rec.get("reasons"):
        L.append("下書きの理由: " + " / ".join(rec["reasons"]))
    L += ["", "拡大を見て、項目ごとに見直してください。JSON だけを返してください。"]
    return "\n".join(L)


def parse_closeup(text: str, form_items: list[str] | None = None) -> dict:
    """応答 → {"rank", "items": {項目: {"rank","evidence","photo","tile"}}, "summary"}。不正なら ValueError。
    items の値は整数/null でもよい。form_items があればその外のキーは捨てる(無ければ items は空)。"""
    try:
        obj = json.loads(_extract_json(text))
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON が壊れている: {e}") from e
    if not isinstance(obj, dict) or "rank" not in obj:
        raise ValueError("rank がない")
    rank = _as_rank(obj["rank"], "rank")
    raw_items = obj.get("items")
    if raw_items is None:
        raw_items = {}
    if not isinstance(raw_items, dict):
        raise ValueError("items がオブジェクトでない")
    allowed = set(form_items or [])
    items: dict[str, dict] = {}
    for k, v in raw_items.items():
        ev = photo = tile = None
        if isinstance(v, dict):
            ev, photo, tile, v = v.get("evidence"), v.get("photo"), v.get("tile"), v.get("rank")
            if ev is not None and not isinstance(ev, str):
                raise ValueError(f"items[{k!r}].evidence が文字列でない")
            if isinstance(photo, bool) or not (photo is None or isinstance(photo, int)):
                raise ValueError(f"items[{k!r}].photo が整数でない")
            if tile is not None and not isinstance(tile, str):
                raise ValueError(f"items[{k!r}].tile が文字列でない")
        if v is not None:
            v = _as_rank(v, f"items[{k!r}]")
        if k in allowed:
            items[k] = {"rank": v, "evidence": ev, "photo": photo, "tile": tile}
    summary = obj.get("summary")
    if summary is not None and not isinstance(summary, str):
        raise ValueError("summary が文字列でない")
    return {"rank": rank, "items": items, "summary": summary or ""}


def compose(first_items: dict | None, parsed: dict, has_form: bool) -> tuple[int, dict | None]:
    """近接パスの項目値(第1パスの値に、見直しで答えた項目を上書き)と総合ランク。
    項目がある様式 → 項目の最悪値。項目が 1 つも無い/項目なしの様式 → モデルが返した総合ランク。"""
    merged = dict(first_items or {}) if has_form else None
    if has_form:
        for k, v in parsed["items"].items():
            if v["rank"] is not None:
                merged[k] = v["rank"]
    vals = [v for v in (merged or {}).values() if v in (1, 2, 3, 4)]
    return (max(vals) if vals else parsed["rank"]), merged


# ---------- 実行 ----------

class CloseupJudge:
    def __init__(self, model: str = DEFAULT_MODEL, workers: int = MAX_WORKERS, effort: str | None = None,
                 client=None):
        self.model = model
        self.workers = max(1, min(workers, MAX_WORKERS))
        self.effort = effort
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def build_messages(self, item: dataset.EvalItem, rec: dict, photo_nos: list[int]) -> list[dict]:
        content: list[dict] = []
        for n in photo_nos:
            content += photo_blocks(n, item.photo_paths[n - 1])
        content.append({"type": "text", "text": draft_message(item, rec, photo_nos)})
        return [{"role": "user", "content": content}]

    def run_one(self, item: dataset.EvalItem, rec: dict, group: str | None = None) -> dict:
        out = {"sign_id": item.sign_id, "source": item.source, "group": group, "split": item.split,
               "rank_true": item.rank_true, "rank_first": rec["rank_pred"],
               "items_first": rec.get("item_judgments_pred")}
        photo_nos = choose_photos(rec.get("reasons"), len(item.photo_paths))
        out["photos_used"] = photo_nos
        if not photo_nos:
            return {**out, "rank_closeup": None, "error": "no_photos"}
        try:
            messages = self.build_messages(item, rec, photo_nos)
        except (OSError, ValueError) as e:
            return {**out, "rank_closeup": None, "error": f"image: {e}"}
        form_items = item.meta.get("form_items") or []
        kw = {"output_config": {"effort": self.effort}} if self.effort else {}
        plain_in = out_tok = attempts = 0
        err = "parse"

        def usage() -> dict:
            return {"input_tokens": plain_in, "output_tokens": out_tok, "attempts": attempts,
                    "cost_usd": cost_usd(self.model, plain_in, out_tok)}

        for _ in range(2):  # 1回目 + JSON 不正のときの再試行1回
            attempts += 1
            try:
                resp = self.client.messages.create(model=self.model, max_tokens=MAX_TOKENS, system=SYSTEM,
                                                   messages=messages, **kw)
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
                raise
            except anthropic.APIStatusError as e:
                err = f"{type(e).__name__} {e.status_code}"
                break
            except (anthropic.APIConnectionError, anthropic.BadRequestError) as e:
                err = type(e).__name__
                break
            plain_in += resp.usage.input_tokens
            out_tok += resp.usage.output_tokens
            text = "".join(b.text for b in resp.content if b.type == "text")
            if resp.stop_reason == "refusal":
                err = "refusal"
                break
            try:
                parsed = parse_closeup(text, form_items)
            except ValueError as e:
                err = f"parse: {e}"
                messages = messages + [
                    {"role": "assistant", "content": text or "(空)"},
                    {"role": "user", "content": f"JSON が不正でした({e})。指定の形式の JSON オブジェクトだけを返し直してください。"}]
                continue
            rank, merged = compose(rec.get("item_judgments_pred"), parsed, bool(form_items))
            return {**out, "rank_closeup": rank, "rank_model": parsed["rank"], "items_closeup": merged,
                    "closeup": parsed, "usage": usage()}
        return {**out, "rank_closeup": None, "error": err, "usage": usage()}

    def run_many(self, jobs: list[tuple[dataset.EvalItem, dict, str | None]]) -> list[dict]:
        if self.workers == 1 or len(jobs) <= 1:
            return [self.run_one(*j) for j in jobs]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            return list(ex.map(lambda j: self.run_one(*j), jobs))


# ---------- 対象の選び方 ----------

def select_targets(records: list[dict], items_by_id: dict, n_control_by_source: dict | None = None,
                   seed: int = 1) -> list[tuple[str, str]]:
    """[(sign_id, 群)]。本丸= 要対応の区分 c(metrics.action_class)で第1パスが 1・2 と見逃した看板。
    対照= 正解 1・2 を第1パスが正しく 1・2 と言った看板から、本丸と同じ様式の基数(n_control_by_source で
    上書き可)を seed ごとの決定論のランダム抽出(Random(f"{seed}:{source}")、sign_id 順の母集団)。
    split=eval で写真のある看板だけ。"""
    def usable(r):
        it = items_by_id.get(r["sign_id"])
        return it is not None and it.split == "eval" and it.photo_paths and r.get("rank_pred") in (1, 2, 3, 4)
    recs = [r for r in records if usable(r)]
    target = sorted((r for r in recs if r["rank_pred"] in (1, 2) and r["rank_true"] in (3, 4)
                     and metrics.action_class(r["rank_true"], items_by_id[r["sign_id"]].item_judgments_true) == "c"),
                    key=lambda r: r["sign_id"])
    want = n_control_by_source or {}
    if not want:
        for r in target:
            want[r["source"]] = want.get(r["source"], 0) + 1
    out = [(r["sign_id"], GROUP_TARGET) for r in target]
    for s in sorted(want):
        pool = sorted((r for r in recs if r["source"] == s and r["rank_true"] in (1, 2) and r["rank_pred"] in (1, 2)),
                      key=lambda r: r["sign_id"])
        pick = random.Random(f"{seed}:{s}").sample(pool, min(want[s], len(pool)))
        out += [(r["sign_id"], GROUP_CONTROL) for r in sorted(pick, key=lambda r: r["sign_id"])]
    return out


def read_targets(path: str) -> list[tuple[str, str | None]]:
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            sid, _, grp = line.partition("\t")
            out.append((sid.strip(), grp.strip() or None))
    return out


# ---------- 集計 ----------

def summarize(records: list[dict]) -> dict:
    """群ごとの集計。本丸: 救えた= 近接の予測が 3 以上、動かなかった= それ以外(失敗を含む)。
    対照: 誤警報= 近接の予測が 3 以上。"""
    def cost(rs):
        cs = [r["usage"]["cost_usd"] for r in rs if r.get("usage")]
        return None if any(c is None for c in cs) else sum(cs)
    tg = [r for r in records if r.get("group") == GROUP_TARGET]
    ct = [r for r in records if r.get("group") == GROUP_CONTROL]
    ok = lambda r: r.get("rank_closeup") in (3, 4)
    allu = [r["usage"] for r in records if r.get("usage")]
    return {"target": len(tg), "target_rescued": sum(ok(r) for r in tg),
            "target_unmoved": sum(not ok(r) for r in tg),
            "target_rescued_to_correct_class": sum(ok(r) and r["rank_true"] in (3, 4) for r in tg),
            "control": len(ct), "control_false_alarm": sum(ok(r) for r in ct),
            "failed": sum(1 for r in records if r.get("rank_closeup") is None),
            "input_tokens": sum(u["input_tokens"] for u in allu), "output_tokens": sum(u["output_tokens"] for u in allu),
            "cost_usd": cost(records)}


def _cut(s: str, n: int = 40) -> str:
    s = (s or "").replace("\n", " ").replace("|", "/")
    return s if len(s) <= n else s[:n - 1] + "…"


def to_markdown(r: dict) -> str:
    s = r["summary"]
    c = s["cost_usd"]
    L = [f"# 近接パス {r['created']}", "",
         f"- モデル: {r['model']}、第1パス: {r['first_pass']}、対象 {len(r['records'])} 基(本丸 {s['target']}+対照 {s['control']})",
         "", "| 項目 | 値 |", "|---|---|",
         f"| 本丸で救えた基数(近接の予測が 3 以上) | {s['target_rescued']} / {s['target']} |",
         f"| 本丸で動かなかった基数 | {s['target_unmoved']} / {s['target']} |",
         f"| 対照で誤警報になった基数(近接の予測が 3 以上) | {s['control_false_alarm']} / {s['control']} |",
         f"| 失敗(JSON 不正など。動かなかった側に数える) | {s['failed']} |",
         f"| 入力トークン / 出力トークン | {s['input_tokens']:,} / {s['output_tokens']:,} |",
         f"| 費用(価格表による推定) | " + ("不明" if c is None else f"${c:.2f}") + " |", "",
         "| sign_id | 群 | 正解 | 第1パス | 近接 | 根拠(40字) |", "|---|---|---|---|---|---|"]
    for x in r["records"]:
        why = (x.get("closeup") or {}).get("summary") or x.get("error") or ""
        L.append(f"| {x['sign_id']} | {x.get('group') or '-'} | {x['rank_true']} | {x['rank_first']} | "
                 f"{x['rank_closeup'] if x.get('rank_closeup') is not None else '失敗'} | {_cut(why)} |")
    return "\n".join(L)


def log_row(r: dict, note: str = "") -> str:
    """docs/EVAL.md の「日ごとの測定記録」に追記する 1 行(列は run_eval.log_row と同じ。母数に使えない列は "-")。"""
    s = r["summary"]
    cond = (f"近接パス(元解像度 2×2 分割+全景、写真2枚まで) / {len(r['records'])}基=本丸{s['target']}+対照{s['control']} / "
            f"本丸で救えた{s['target_rescued']}/{s['target']}・対照で誤警報{s['control_false_alarm']}/{s['control']} / "
            f"第1パスは{r['first_pass']}")
    if note:
        cond += f" / {note}"
    c = s["cost_usd"]
    cells = [r["created"][:10], cond, r["model"], r.get("effort") or "API 既定", f"{len(r['records'])}基",
             "-", "-", "-", "-", "-", "-", f"看板{s['failed']}/項目-", f"{s['input_tokens']:,}",
             "不明" if c is None else f"${c:.2f}"]
    return "| " + " | ".join(x.replace("|", "/") for x in cells) + " |"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--first-pass", required=True, help="第1パスの結果 JSON(run_eval の出力。records に reasons が要る)")
    ap.add_argument("--sources-config", default=None, help="sources.toml のパス")
    ap.add_argument("--select-out", default=None, metavar="TSV",
                    help="本丸+対照の看板を選んで書く(sign_id<TAB>群)。API は呼ばない")
    ap.add_argument("--control-seed", type=int, default=1)
    ap.add_argument("--closeup-targets", default=None, metavar="IDS", help="対象の sign_id 一覧")
    ap.add_argument("--closeup-out", default=None, metavar="JSON", help="結果 JSON のパス")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--effort", choices=["low", "medium", "high", "xhigh", "max"], default=None)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--dry-run", action="store_true", help="API を呼ばず、送る写真枚数と推定トークンだけ出す")
    ap.add_argument("--append-log", action="store_true", help="結果の 1 行を docs/EVAL.md の測定記録に追記する")
    ap.add_argument("--log-note", default="")
    a = ap.parse_args(argv)
    if not (a.select_out or a.closeup_targets):
        ap.error("--select-out か --closeup-targets が要ります")
    if a.closeup_targets and not a.closeup_out and not a.dry_run:
        ap.error("--closeup-targets には --closeup-out が要ります")

    fp = json.loads(Path(a.first_pass).read_text(encoding="utf-8"))
    recs = fp["records"]
    items = {i.sign_id: i for i in dataset.load_all(a.sources_config, dataset.RANKED_SOURCES)}

    if a.select_out:
        sel = select_targets(recs, items, seed=a.control_seed)
        Path(a.select_out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.select_out).write_text("# sign_id\t群\n" + "".join(f"{s}\t{g}\n" for s, g in sel), encoding="utf-8")
        n_t = sum(g == GROUP_TARGET for _, g in sel)
        print(f"{a.select_out}: 本丸 {n_t} 基 + 対照 {len(sel) - n_t} 基(seed={a.control_seed})")
        if not a.closeup_targets:
            return 0

    by_id = {r["sign_id"]: r for r in recs}
    jobs = []
    for sid, grp in read_targets(a.closeup_targets):
        if sid not in items or sid not in by_id or by_id[sid].get("rank_pred") is None:
            print(f"対象外(データ/第1パスに無い): {sid}", file=sys.stderr)
            continue
        if items[sid].split != "eval":
            print(f"例示用の看板は対象にしません: {sid}", file=sys.stderr)
            continue
        jobs.append((items[sid], by_id[sid], grp))
    judge = CloseupJudge(a.model, a.workers, a.effort)

    if a.dry_run:
        img = txt = 0
        for it, rec, _ in jobs:
            for n in choose_photos(rec.get("reasons"), len(it.photo_paths)):
                img += estimate_photo_tokens(it.photo_paths[n - 1])
            txt += len(SYSTEM) + len(draft_message(it, rec, [1]))
        est_in = img + txt
        est = cost_usd(a.model, est_in, 800 * len(jobs))
        print(f"dry-run(API なし): {len(jobs)} 基、推定入力 {est_in:,} トークン(画像 {img:,} + 文 {txt:,})、"
              f"出力は 1 基 800 トークンと仮定して約 " + ("不明" if est is None else f"${est:.2f}"))
        return 0

    try:
        results = judge.run_many(jobs)
    except RuntimeError as e:
        print(f"エラー: {e}", file=sys.stderr)
        return 2
    r = {"created": datetime.now().isoformat(timespec="seconds"), "model": a.model, "effort": a.effort,
         "first_pass": Path(a.first_pass).name, "records": results}
    r["summary"] = summarize(results)
    out = Path(a.closeup_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(r, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_markdown(r)
    out.with_suffix(".md").write_text(md, encoding="utf-8")
    print(md)
    if a.append_log:
        from .run_eval import EVAL_MD, append_log
        append_log(log_row(r, a.log_note))
        print(f"\n記録を追記しました: {EVAL_MD}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
