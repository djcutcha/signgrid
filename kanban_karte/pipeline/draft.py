"""束(bundle)→ 看板ごとの下書き。ClaudeJudge に写真を見せ、signs に下書きを書く。

- 様式は xlsx_center の18項目系(東京都18項目と恒等)。判定役が返す項目名は「④点検箇所-番号 原文」で、
  台帳のキー(点検箇所-番号)に戻して items に入れる。
- items の値は台帳の区分(良好 / 経過観察 / 要改善)。判定役の 1 → 良好、2 → 経過観察、3・4 → 要改善。
  「判断できず」(null・要実測)は、items にキーを入れない(未記入)。理由は evidence に残る。
  看板全体の rank(1〜4)は判定役の値のまま draft_rank と rank に入る(4 は項目では表せない)。
- 写真が無い看板は status=needs_photos(rank なし)。確定済みの看板は上書きしない。
- 束に看板がまだ無い(LINE の束)ときは、写真全部で1基の仮の看板を作る。本当の束ね(複数基に分ける)は
  有資格者が確認画面でやる前提。
- 写真番号(evidence の photos)は看板の photos の並びの 1 始まり(判定役に送った順)。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..eval.dataset import EvalItem
from ..ledger.db import Ledger, Sign
from ..ledger.items import _ITEMS, ITEM_KEYS

FORM_SOURCE = "xlsx_center"
# 判定役に渡す項目名(客先Aの様式と同じ「④点検箇所-番号 原文」)→ 台帳のキー
FORM_ITEMS: dict[str, str] = {f"④{g}-{n} {t}": f"{g}-{n}" for g, n, t in _ITEMS}
assert list(FORM_ITEMS.values()) == ITEM_KEYS

RANK_TO_RESULT = {1: "良好", 2: "経過観察", 3: "要改善", 4: "要改善"}
SOURCE_PAGE_KINDS = ("写真", "不明")  # 入口1(LINE)の写真は未分類の「不明」で入る


@dataclass
class DraftResult:
    sign_id: str
    status: str            # draft / needs_photos / confirmed(確定済みで触らない)/ error(判定に失敗。看板は変えない)
    rank: int | None = None
    undecided: int = 0     # 判断できなかった項目数(18 − items の数)
    cost_usd: float | None = None
    error: str | None = None


def ensure_signs(ledger: Ledger, bundle_id: int) -> list[str]:
    """束の看板の id。まだ無ければ(LINE の束)、写真全部で1基の仮の看板を作る。"""
    b = ledger.get_bundle(bundle_id)
    if b is None:
        raise LookupError(f"束 {bundle_id} が台帳にありません")
    signs = ledger.list_signs(bundle_id=bundle_id)
    if signs:
        return [s.id for s in signs]
    photos = [ledger.rel_path(r["image_path"]) for r in ledger.list_pages(bundle_id)
              if r["image_path"] and r["kind"] in SOURCE_PAGE_KINDS]
    site = ledger.get_site(b["site_id"]) if b["site_id"] else None
    sid = f"b{bundle_id}-s001"
    ledger.upsert_sign(Sign(
        id=sid, site_id=site.id if site else None, address=site.address if site else None,
        municipality=site.municipality if site else None, photos=photos, source_bundle=bundle_id,
        remark="束の写真全部を1基とした仮の束ね(複数基なら有資格者が分ける)"))
    return [sid]


def _photo_numbers(text: str | None) -> list[int]:
    return sorted({int(n) for n in re.findall(r"写真\s*(\d+)", text or "")})


def _remark(reasons: list[str]) -> str | None:
    """reasons の先頭2つを1〜2文に。"""
    sents = [r.strip().rstrip("。") + "。" for r in reasons[:2] if r and r.strip()]
    return "".join(sents) or None


def _evidence(raw) -> dict[str, dict]:
    notes = (raw.get("item_notes") or {}) if isinstance(raw, dict) else {}
    return {FORM_ITEMS[name]: {"photos": _photo_numbers(n.get("evidence")), "text": n.get("evidence"),
                               "needs_onsite": bool(n.get("needs_onsite"))}
            for name, n in notes.items() if name in FORM_ITEMS}


def eval_item_for(ledger: Ledger, sign: Sign) -> EvalItem:
    return EvalItem(source=FORM_SOURCE, sign_id=sign.id,
                    photo_paths=[str(ledger.resolve_path(p)) for p in sign.photos],
                    rank_true=None, item_judgments_true=None, split="eval",
                    meta={"form_items": list(FORM_ITEMS), "sign_name": sign.name})


def apply_prediction(sign: Sign, pred) -> None:
    """判定役の結果を看板の下書きにする(sign を書き換える。台帳には書かない)。"""
    sign.items = {FORM_ITEMS[k]: RANK_TO_RESULT[v] for k, v in (pred.item_judgments_pred or {}).items()
                  if k in FORM_ITEMS}
    sign.rank = sign.draft_rank = pred.rank_pred
    sign.confidence = pred.confidence
    sign.evidence = _evidence(pred.raw)
    reasons = pred.raw.get("reasons", []) if isinstance(pred.raw, dict) else []
    sign.remark = _remark(reasons)
    sign.status = "draft"
    sign.confirmed_at = sign.confirmed_by = None


def draft_bundle(ledger: Ledger, bundle_id: int, judge) -> list[DraftResult]:
    """束の看板すべてに下書きを書く。judge は Judge(ClaudeJudge か偽の判定役)。"""
    ids = ensure_signs(ledger, bundle_id)
    results: list[DraftResult] = []
    todo: list[Sign] = []
    for sid in ids:
        s = ledger.get_sign(sid)
        if s.status == "confirmed":
            results.append(DraftResult(sid, "confirmed", s.rank))
        elif not any(ledger.resolve_path(p).exists() for p in s.photos):
            s.status, s.rank, s.draft_rank, s.items, s.evidence, s.confidence = "needs_photos", None, None, {}, {}, None
            ledger.upsert_sign(s)
            results.append(DraftResult(sid, "needs_photos"))
        else:
            todo.append(s)
    preds = judge.judge_many([eval_item_for(ledger, s) for s in todo]) if todo else []
    for s, pred in zip(todo, preds):
        cost = (pred.usage or {}).get("cost_usd")
        if pred.rank_pred is None:
            err = str((pred.raw or {}).get("error") or "判定できなかった(応答が不正・API エラー)")
            results.append(DraftResult(s.id, "error", cost_usd=cost, error=err))
            continue
        apply_prediction(s, pred)
        ledger.upsert_sign(s)
        results.append(DraftResult(s.id, "draft", s.rank, len(ITEM_KEYS) - len(s.items), cost))
    if not any(r.status == "error" for r in results):
        ledger.set_bundle(bundle_id, status="signs_drafted")
    return results


def default_judge(rules: list[str] | None = None, sources_config=None, examples: int = 6, **opts):
    """本番の判定役: ClaudeJudge(例示 examples、xlsx_center の例示用の側から。現場の重複なし)。
    rules= 台帳の最新の規則(ledger.recent_rules())を例示の後ろに入れる。"""
    from ..eval import dataset
    from ..eval.claude_judge import ClaudeJudge
    judge = ClaudeJudge(examples=examples, rules=rules, **opts)
    judge.set_example_pool(dataset.load_all(sources_config, sources=(FORM_SOURCE,)))
    return judge
