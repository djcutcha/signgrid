"""Verifier(反証役): 判定役の下書きを別の文脈で見直し、「要改善(3)以上の見落とし」だけを問う。

判定役の下書き(rank・項目判定・理由・evidence)と写真を受け取り、別の API 呼び出しで
「過去の有資格者ならここを要改善にしたはず、という項目があるか」を JSON で返させる。
  - 反証役には判定例(例示)を見せない。system には評価基準(RUBRIC_TEXT。顧客名なし)だけを置く
  - 見落とし指摘があれば rank を max(rank, 3) に上げ、該当項目を 3 にする(verified=True、verifier_notes に指摘)
  - 指摘なしなら下書きのまま(verified=True、verifier_notes=[])
  - 下書きが 3 以上(上げる余地なし)・下書きが失敗(rank None)の看板は、API を呼ばずそのまま返す
  - 反証役の呼び出しが失敗したときは下書きのまま。raw["verifier_error"] に理由を残す
費用は判定役+反証役の合計を usage に入れる(usage["verifier"] に反証役分の内訳)。
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import anthropic

from .claude_judge import (MAX_TOKENS, MAX_WORKERS, RANK_NAME, RUBRIC_TEXT, _extract_json,
                           cost_usd, make_client, prepare_image)
from .dataset import EvalItem
from .judge import Prediction

DEFAULT_VERIFIER_MODEL = "claude-sonnet-5-5"
WHOLE = "全体"  # 項目一覧のない様式で、看板全体について指摘するときの項目名

VERIFIER_HEAD = """あなたは屋外広告物(看板)の安全点検の第二の点検者(反証役)です。別の点検者が写真から付けた下書きの判定が渡されます。あなたの役目は一つだけです: 下書きが「要改善(3)以上」を見落としていないかを確かめること。
判定は 1=良好、2=経過観察、3=要改善(補修や交換が必要な劣化・損傷)、4=即時対応・危険、の4段階です。
- 下書きが 1・2 とした項目のうち、過去の有資格者ならこの写真を見て 3 以上にしたはずの項目だけを挙げてください。
- 挙げるときは、写真の番号と、その写真の何が(どの程度)問題かを具体的に書いてください。写真に見えないものを「念のため」で挙げることはしません(要対応でない看板を要対応とする誤りも測っています)。
- 下書きの 1・2 が妥当なら「なし」とします。下書きを疑うこと自体が目的ではありません。
- 下書きが既に 3 以上とした項目は対象外です。"""

VERIFIER_FORMAT = """出力は JSON オブジェクト1つだけにしてください。前後に説明文やコードフェンスを付けないでください。
見落としがあるとき: {"missed": [{"item": "項目名", "photo": 写真番号(整数), "reason": "写真の何が問題か(1〜2文)"}, ...]}
見落としが無いとき: {"missed": []}
項目名は、渡された項目一覧の名前をそのまま使ってください。項目一覧が無いときは "全体" としてください。"""

# 評価基準の最後の1文は「判定例があればその付け方を優先」: 反証役は例示を見ないので外す
_RUBRIC = RUBRIC_TEXT.rsplit("\n", 1)[0] + "\nこの基準は目安です。様式の項目名を優先してください。"

SYSTEM = VERIFIER_HEAD + "\n\n" + _RUBRIC + "\n\n" + VERIFIER_FORMAT


def draft_text(item: EvalItem, pred: Prediction, n_photos: int) -> str:
    """下書きの判定を見せる文(看板名・項目一覧・下書き)。正解や現場名は入れない。"""
    L = [f"写真は {n_photos} 枚です。"]
    if item.meta.get("sign_name"):
        L.append(f"看板名: {item.meta['sign_name']}")
    form_items = item.meta.get("form_items") or []
    if form_items:
        L.append("様式の項目名一覧:")
        L += [f"- {k}" for k in form_items]
    else:
        L.append("項目一覧はありません(項目名は \"全体\" としてください)。")
    L += ["", f"【下書きの判定】看板全体のランク: {pred.rank_pred}({RANK_NAME[pred.rank_pred]})"]
    raw = pred.raw if isinstance(pred.raw, dict) else {}
    notes = raw.get("item_notes") or {}
    for k, v in (pred.item_judgments_pred or {}).items():
        ev = (notes.get(k) or {}).get("evidence")
        L.append(f"- {k}: {v}" + (f"(根拠: {ev})" if ev else ""))
    if raw.get("reasons"):
        L.append("下書きの理由: " + " / ".join(raw["reasons"]))
    L += ["", "この下書きが要改善(3)以上を見落としている項目があれば挙げてください。JSON だけを返してください。"]
    return "\n".join(L)


def parse_verdict(text: str) -> list[dict]:
    """応答 → 見落としの指摘 [{item, photo, reason}]。「なし」だけの応答は []。不正なら ValueError。"""
    if text.strip().strip("。.「」『』\"'") == "なし":
        return []
    try:
        obj = json.loads(_extract_json(text))
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON が壊れている: {e}") from e
    if not isinstance(obj, dict) or not isinstance(obj.get("missed"), list):
        raise ValueError("missed のリストがない")
    out = []
    for m in obj["missed"]:
        if not isinstance(m, dict) or not isinstance(m.get("item"), str) or not m["item"].strip():
            raise ValueError(f"missed の要素に item(文字列)がない: {m!r}")
        photo = m.get("photo")
        if isinstance(photo, bool) or not (photo is None or isinstance(photo, int)):
            raise ValueError(f"photo は整数でなければならない: {photo!r}")
        reason = m.get("reason")
        if reason is not None and not isinstance(reason, str):
            raise ValueError("reason が文字列でない")
        out.append({"item": m["item"].strip(), "photo": photo, "reason": reason or ""})
    return out


def apply_verdict(pred: Prediction, missed: list[dict], form_items: list[str] | None) -> Prediction:
    """指摘を下書きに反映する。指摘があれば rank=max(rank,3)、項目一覧にある該当項目は max(現在値,3)。"""
    new_rank = max(pred.rank_pred, 3) if missed else pred.rank_pred
    items = dict(pred.item_judgments_pred) if pred.item_judgments_pred is not None else None
    if missed and items is not None:
        for m in missed:
            if m["item"] in (form_items or []):
                items[m["item"]] = max(items.get(m["item"], 0), 3)
    return replace(pred, rank_pred=new_rank, item_judgments_pred=items, verified=True,
                   verifier_notes=missed, rank_before=pred.rank_pred)


def _merge_usage(a: dict | None, b: dict, verifier_model: str) -> dict:
    """判定役 a と反証役 b の合算(a が無ければ b だけ)。内訳は ["verifier"] に残す。"""
    if not a:
        return {**b, "verifier": dict(b)}
    ca, cb = a.get("cost_usd"), b.get("cost_usd")
    return {
        "input_tokens": a["input_tokens"] + b["input_tokens"],
        "output_tokens": a["output_tokens"] + b["output_tokens"],
        "cache_write_tokens": a.get("cache_write_tokens", 0) + b.get("cache_write_tokens", 0),
        "cache_read_tokens": a.get("cache_read_tokens", 0) + b.get("cache_read_tokens", 0),
        "attempts": a["attempts"] + b["attempts"],
        "cost_usd": None if ca is None or cb is None else ca + cb,
        "verifier": {**b, "model": verifier_model},
    }


class Verifier:
    def __init__(self, model: str = DEFAULT_VERIFIER_MODEL, max_photos: int = 6, workers: int = MAX_WORKERS,
                 effort: str | None = None, client=None):
        self.model = model
        self.max_photos = max_photos
        self.workers = max(1, min(workers, MAX_WORKERS))
        self.effort = effort
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def build_messages(self, item: EvalItem, pred: Prediction) -> list[dict]:
        photos = item.photo_paths[: self.max_photos]
        content: list[dict] = []
        for i, p in enumerate(photos, 1):
            content.append({"type": "text", "text": f"写真{i}"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": prepare_image(p)}})
        content.append({"type": "text", "text": draft_text(item, pred, len(photos))})
        return [{"role": "user", "content": content}]

    def verify(self, item: EvalItem, pred: Prediction) -> Prediction:
        if pred.rank_pred is None or pred.rank_pred >= 3 or not item.photo_paths:
            return pred
        try:
            messages = self.build_messages(item, pred)
        except (OSError, ValueError) as e:
            return self._failed(pred, f"image: {e}")
        kw = {"output_config": {"effort": self.effort}} if self.effort else {}
        plain_in = out_tok = attempts = 0
        err = "parse"
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
                missed = parse_verdict(text)
            except ValueError as e:
                err = f"parse: {e}"
                messages = messages + [
                    {"role": "assistant", "content": text or "(空)"},
                    {"role": "user", "content": f"JSON が不正でした({e})。指定の形式の JSON オブジェクトだけを返し直してください。"}]
                continue
            out = apply_verdict(pred, missed, item.meta.get("form_items") or [])
            out.usage = _merge_usage(pred.usage, self._usage(plain_in, out_tok, attempts), self.model)
            return out
        failed = self._failed(pred, err)
        failed.usage = _merge_usage(pred.usage, self._usage(plain_in, out_tok, attempts), self.model)
        return failed

    def _usage(self, plain_in: int, out_tok: int, attempts: int) -> dict:
        return {"input_tokens": plain_in, "output_tokens": out_tok, "attempts": attempts,
                "cost_usd": cost_usd(self.model, plain_in, out_tok)}

    @staticmethod
    def _failed(pred: Prediction, err: str) -> Prediction:
        raw = dict(pred.raw) if isinstance(pred.raw, dict) else {"draft_raw": pred.raw}
        raw["verifier_error"] = err
        return replace(pred, raw=raw)

    def verify_many(self, items: list[EvalItem], preds: list[Prediction]) -> list[Prediction]:
        if self.workers == 1 or len(items) <= 1:
            return [self.verify(i, p) for i, p in zip(items, preds)]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            return list(ex.map(self.verify, items, preds))
