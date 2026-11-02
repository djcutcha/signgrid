"""画面の JSON API の中身。台帳(ledger/)と本線(pipeline/・output/)の関数を呼んで JSON にするだけ。
ロジックはここに足さない(訂正・確定・規則・様式は元の関数が持つ)。HTTP は server.py。

画面の「黄色」= 判断できず(items に無い)、または下書きが「要実測」とした項目(evidence.needs_onsite)。
有資格者が訂正した項目は黄色でなくなる。
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from ..ledger.db import Ledger, Sign
from ..ledger.items import _ITEMS, ITEM_KEYS, RESULTS
from ..output.common import RANK_LABEL, size_text, unjudged_item_keys
from ..output.client_a_form import write_client_a
from ..output.tokyo_form import write_tokyo_form
from ..pipeline import draft as draft_mod
from ..pipeline import review as review_mod

OUTPUTS = {"tokyo": ("東京都 第2号様式", write_tokyo_form), "client-a": ("客先A型", write_client_a)}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


class ApiError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def can_draft() -> bool:
    """「下書きを作る」が使えるか。鍵(環境変数か .env)が無ければ使えない。"""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    try:
        from ..eval.claude_judge import load_dotenv
        load_dotenv()
    except Exception:
        return False
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def meta(sample: bool) -> dict:
    return {"sample": sample, "can_draft": can_draft(), "results": list(RESULTS),
            "rank_labels": {str(k): v for k, v in RANK_LABEL.items()},
            "items": [{"key": f"{g}-{n}", "group": g, "no": n, "text": t} for g, n, t in _ITEMS]}


# ---- 看板 ----
def _media_url(ledger: Ledger, p: str) -> str | None:
    """台帳の画像パス → /media/<data_dir からの相対パス>。data_dir の外にある画像は配らない。"""
    if ledger.data_dir is None:
        return None
    rel = ledger.rel_path(p)
    return None if Path(rel).is_absolute() else "/media/" + rel.replace("\\", "/")


def sign_summary(s: Sign) -> dict:
    return {"id": s.id, "site_id": s.site_id, "name": s.name, "type": s.type, "size": size_text(s),
            "is_self_use": s.is_self_use, "permit_required": s.permit_required,
            "permit_expires": s.permit_expires, "next_inspection": s.next_inspection,
            "rank": s.rank, "rank_label": RANK_LABEL.get(s.rank), "draft_rank": s.draft_rank,
            "prev_rank": s.prev_rank, "status": s.status, "confidence": s.confidence,
            "undecided": len(unjudged_item_keys(s)), "photo_count": len(s.photos),
            "last_inspected": s.last_inspected, "confirmed_by": s.confirmed_by, "confirmed_at": s.confirmed_at}


def _correction(r, labels: dict[str, str]) -> dict:
    return {"id": r["id"], "item": r["item"], "item_label": labels.get(r["item"], "看板全体のランク"),
            "old_value": r["old_value"], "new_value": r["new_value"], "rank_before": r["rank_before"],
            "rank_after": r["rank_after"], "by": r["corrected_by"], "note": r["note"], "rule_id": r["rule_id"],
            "created": r["created"]}


def sign_detail(ledger: Ledger, sign_id: str) -> dict:
    s = ledger.get_sign(sign_id)
    if s is None:
        raise ApiError(404, f"看板 {sign_id} が台帳にありません")
    rows = ledger.list_corrections(sign_id)
    corrected = {r["item"] for r in rows}
    items = []
    for g, n, t in _ITEMS:
        key = f"{g}-{n}"
        ev = s.evidence.get(key)
        value = s.items.get(key)
        yellow = key not in corrected and (value is None or bool(ev and ev.get("needs_onsite")))
        items.append({"key": key, "group": g, "no": n, "text": t, "value": value, "evidence": ev,
                      "yellow": yellow, "corrected": key in corrected})
    site = ledger.get_site(s.site_id) if s.site_id else None
    d = sign_summary(s)
    d.update({"site_name": site.name if site else None, "address": s.address, "municipality": s.municipality,
              "remark": s.remark, "items": items, "yellow_count": sum(i["yellow"] for i in items),
              "photos": [{"n": i + 1, "url": _media_url(ledger, p)} for i, p in enumerate(s.photos)],
              "corrections": [_correction(r, {i["key"]: f"{i['group']} {i['no']} {i['text']}" for i in items})
                              for r in rows],
              "editable": s.status == "draft", "result_choices": list(RESULTS)})
    return d


def correct(ledger: Ledger, sign_id: str, body: dict) -> dict:
    s = ledger.get_sign(sign_id)
    if s is None:
        raise ApiError(404, f"看板 {sign_id} が台帳にありません")
    if s.status != "draft":
        raise ApiError(409, f"看板 {sign_id} は status={s.status}。訂正できるのは下書きだけ")
    try:
        c = review_mod.apply_correction(ledger, sign_id, str(body.get("item", "")), body.get("value"),
                                        str(body.get("by", "")), body.get("note"))
    except ValueError as e:
        raise ApiError(400, str(e))
    d = sign_detail(ledger, sign_id)
    d["changed"] = c is not None
    d["rule_id"] = c.rule_id if c else None
    return d


def finalize(ledger: Ledger, sign_id: str, body: dict) -> dict:
    s = ledger.get_sign(sign_id)
    if s is None:
        raise ApiError(404, f"看板 {sign_id} が台帳にありません")
    if s.status != "draft":
        raise ApiError(409, f"看板 {sign_id} は status={s.status}。確定できるのは下書きだけ")
    try:
        review_mod.finalize(ledger, sign_id, str(body.get("by", "")))
    except ValueError as e:
        raise ApiError(400, str(e))
    return sign_detail(ledger, sign_id)


# ---- 現場・束・規則 ----
def sites(ledger: Ledger) -> list[dict]:
    out = []
    for r in ledger.conn.execute("SELECT id FROM sites ORDER BY name, id"):
        site = ledger.get_site(r["id"])
        signs = ledger.list_signs(site_id=site.id)
        out.append({"id": site.id, "name": site.name, "address": site.address, "municipality": site.municipality,
                    "sign_count": len(signs), "draft_count": sum(s.status == "draft" for s in signs),
                    "confirmed_count": sum(s.status == "confirmed" for s in signs)})
    return out


def signs(ledger: Ledger, site_id: str | None) -> list[dict]:
    if site_id is not None and ledger.get_site(site_id) is None:
        raise ApiError(404, f"現場 {site_id} が台帳にありません")
    return [sign_summary(s) for s in ledger.list_signs(site_id=site_id)]


def bundles(ledger: Ledger) -> list[dict]:
    out = []
    for b in ledger.list_bundles():
        ss = ledger.list_signs(bundle_id=b["id"])
        out.append({"id": b["id"], "file": Path(b["file"].split("://")[-1]).name or b["file"], "pages": b["pages"],
                    "status": b["status"], "site_id": b["site_id"], "error": b["error"],
                    "sign_count": len(ss), "draft_count": sum(s.status == "draft" for s in ss),
                    "kind_counts": json.loads(b["kind_counts"] or "{}")})
    return out


def draft_bundle(ledger: Ledger, bundle_id: int, judge_factory=None) -> dict:
    """束 → 下書き(pipeline.draft_bundle)。鍵が無ければ 503。judge_factory(rules) はテストで差し替える。"""
    if ledger.get_bundle(bundle_id) is None:
        raise ApiError(404, f"束 {bundle_id} が台帳にありません")
    if judge_factory is None and not can_draft():
        raise ApiError(503, "ANTHROPIC_API_KEY がないので、下書きは作れません(環境変数か .env)")
    try:
        judge = (judge_factory or (lambda rules: draft_mod.default_judge(rules)))(ledger.recent_rules())
        results = draft_mod.draft_bundle(ledger, bundle_id, judge)
    except (RuntimeError, OSError, LookupError, ValueError) as e:
        raise ApiError(503, f"下書きを作れませんでした: {e}")
    return {"results": [{"sign_id": r.sign_id, "status": r.status, "rank": r.rank, "undecided": r.undecided,
                         "error": r.error} for r in results]}


def rules(ledger: Ledger) -> list[dict]:
    return [{"id": r["id"], "text": r["text"], "source": r["source"], "created": r["created"],
             "conflicts_with": json.loads(r["conflicts_with"] or "[]")} for r in ledger.list_rules()]


# ---- 様式の出力 ----
def export(ledger: Ledger, site_id: str, kind: str) -> tuple[bytes, str]:
    """(xlsx のバイト列, ファイル名)。出力は常に下書き(有資格者の確認・署名が要る)。"""
    if kind not in OUTPUTS:
        raise ApiError(404, f"様式は {', '.join(OUTPUTS)} のどれか: {kind!r}")
    write = OUTPUTS[kind][1]
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "out.xlsx"
        try:
            write(ledger, site_id, out)
        except LookupError as e:
            raise ApiError(404, str(e))
        except ValueError as e:
            raise ApiError(400, str(e))
        data = out.read_bytes()
    return data, f"{kind}_{re.sub(r'[^A-Za-z0-9_.-]', '_', site_id)}.xlsx"
