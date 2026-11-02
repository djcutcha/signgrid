"""画面の JSON API の中身。台帳(ledger/)と本線(pipeline/・output/)の関数を呼んで JSON にするだけ。
ロジックはここに足さない(訂正・確定・規則・様式は元の関数が持つ)。HTTP は server.py。

画面の「黄色」= 判断できず(items に無い)、または下書きが「要実測」とした項目(evidence.needs_onsite)。
有資格者が訂正した項目は黄色でなくなる。
"""
from __future__ import annotations

import base64
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from ..ledger.db import Ledger, Sign
from ..ledger.items import _ITEMS, ITEM_KEYS, RESULTS
from ..gov.map import COLOR_NAME, COLORS, COLOR_MEANING, summarize, write_map
from ..gov.match import WARN_DAYS, load_candidates, match_candidates
from ..dim.estimator import DimEstimateError, DimEstimator, judge_from_range
from ..legal.judge import judge as legal_judge
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
    return {"sample": sample, "can_draft": can_draft(), "can_estimate": can_draft(), "results": list(RESULTS),
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


LEGAL_KEYS = ("permit_required", "inspection_required", "permit_term_years", "is_self_use_exempt", "municipality",
              "ordinance_key", "citations", "notes", "confidence")


def sign_area_m2(s: Sign) -> float | None:
    """size(mm)から表示面積 m2。幅・高さのどちらかが無ければ None(法規役が「要実測」にする)。"""
    if not s.size_w or not s.size_h:
        return None
    return round(s.size_w * s.size_h / 1_000_000, 2)


def legal_judgement(address: str | None, municipality: str | None, use: str, area_m2: float | None,
                    kind: str | None = None) -> dict:
    """法規役(規則ファイルを読むだけの純粋関数)の結果を画面用の dict に。例外は握って {"error": ...}。"""
    try:
        r = legal_judge(address, municipality=None, use=use, area_m2=area_m2, kind=kind)
    except Exception as e:  # 規則ファイルが無い・住所が引けない等。画面は落とさない
        return {"error": f"{type(e).__name__}: {e}"}
    d = r.to_dict()
    out = {k: d[k] for k in LEGAL_KEYS}
    out["use"], out["area_m2"] = use, area_m2
    return out


def sign_legal(s: Sign, site) -> dict:
    """看板の住所(無ければ現場の住所)・自家用か・寸法から、法規の判定と根拠条文。height_m は台帳に看板の
    全高が無い(size は表示面の寸法)ので渡さない。"""
    address = s.address or (site.address if site else None) or s.municipality or (site.municipality if site else None)
    use = "general" if s.is_self_use is False else "self"
    return legal_judgement(address, None, use, sign_area_m2(s), s.type)


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
              "legal": sign_legal(s, site), "editable": s.status == "draft", "result_choices": list(RESULTS)})
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
        out.append({"id": b["id"], "source": "LINE" if b["file"].startswith("line://") else "PC",
                    "file": Path(b["file"].split("://")[-1]).name or b["file"], "pages": b["pages"],
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


# ---- 行政の一括登録(gov_ の表。無い台帳では「まだありません」) ----
def _has_gov(ledger: Ledger) -> bool:
    """読むだけで表を作らない(gov の表が無い台帳に、画面を開いただけで書き込まない)。"""
    return ledger.conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='gov_batches'").fetchone() is not None


def _today(q: str | None) -> date:
    try:
        return date.fromisoformat(q) if q else date.today()
    except ValueError:
        raise ApiError(400, f"today は YYYY-MM-DD: {q!r}")


def _gov_results(ledger: Ledger, batch_id: int, today: date):
    if not _has_gov(ledger) or ledger.conn.execute("SELECT 1 FROM gov_batches WHERE id=?", (batch_id,)).fetchone() is None:
        raise ApiError(404, f"一括登録 {batch_id} がありません")
    return match_candidates(load_candidates(ledger.conn, batch_id), ledger.list_signs(), today)


def gov_batches(ledger: Ledger, today_q: str | None = None) -> dict:
    today = _today(today_q)
    out = {"available": _has_gov(ledger), "colors": {c: {"name": COLOR_NAME[c], "meaning": COLOR_MEANING[c]} for c in COLORS},
           "batches": []}
    if not out["available"]:
        return out
    for b in ledger.conn.execute("SELECT * FROM gov_batches ORDER BY id DESC"):
        sm = summarize(_gov_results(ledger, b["id"], today))
        out["batches"].append({"id": b["id"], "created": b["created"], "dir": Path(b["dir"]).name,
                               "photo_count": b["n_files"], "gps_count": b["n_gps"], "nogps_count": b["n_nogps"],
                               "candidate_count": sm["total"], "by_color": sm["by_color"]})
    return out


def gov_candidates(ledger: Ledger, batch_id: int, today_q: str | None = None) -> dict:
    today = _today(today_q)
    rows = []
    for r in _gov_results(ledger, batch_id, today):
        c, sg = r.cand, r.sign
        rows.append({"id": c.id, "has_position": c.lat is not None, "lat": c.lat, "lng": c.lng, "photo_count": len(c.photos),
                     "place": c.place, "first_taken": c.first_taken,
                     "sign": {"id": sg.id, "site_id": sg.site_id, "name": sg.name, "type": sg.type} if sg else None,
                     "distance_m": None if r.distance_m is None else round(r.distance_m),
                     "color": r.color, "status": r.status, "label": r.label, "note": r.note,
                     "permit_expires": r.permit_expires, "days_left": r.days_left})
    order = {"red": 0, "orange": 1, "green": 2, "gray": 3}
    rows.sort(key=lambda x: (order[x["color"]], x["days_left"] if x["days_left"] is not None else 10**6))
    return {"batch": batch_id, "today": today.isoformat(), "colors": {c: COLOR_NAME[c] for c in COLORS}, "candidates": rows}


def gov_map_html(ledger: Ledger, batch_id: int, today_q: str | None = None) -> bytes:
    """write_map を一時ファイルに書いて返す(出典表示つき・地理院タイル。OSM は使わない)。"""
    today = _today(today_q)
    results = _gov_results(ledger, batch_id, today)
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "map.html"
        write_map(out, results, today=today, ledger_signs=ledger.list_signs(), warn_days=WARN_DAYS)
        return out.read_bytes()


# ---- オーナーの1枚撮り(DB に書かない) ----
def owner_judge(body: dict) -> dict:
    """住所・面積(・高さ)から自家用(use=self)の法規の判定と根拠条文。安全の判定(ランク・18項目)は出さない。"""
    address = str(body.get("address") or "").strip()
    if not address:
        raise ApiError(400, "住所を入れてください")

    def num(k: str, required: bool = False) -> float | None:
        v = body.get(k)
        if v in (None, ""):
            if required:
                raise ApiError(400, f"{k} を入れてください")
            return None
        try:
            f = float(v)
        except (TypeError, ValueError):
            raise ApiError(400, f"{k} は数値: {v!r}")
        if isinstance(v, bool) or not 0 < f < 10000:
            raise ApiError(400, f"{k} は 0 より大きい数: {v!r}")
        return f

    area, height = num("area_m2", required=True), num("height_m")
    try:
        r = legal_judge(address, use="self", area_m2=area, height_m=height)
    except Exception as e:
        raise ApiError(500, f"法規の判定ができませんでした: {type(e).__name__}: {e}")
    d = r.to_dict()
    out = {k: d[k] for k in LEGAL_KEYS}
    out.update({"use": "self", "area_m2": area, "height_m": height, "address": address})
    return out


MAX_ESTIMATE_PHOTOS = 2
MAX_PHOTO_BYTES = 12_000_000


def owner_estimate(body: dict, estimator_factory=None) -> dict:
    """写真1〜2枚(base64)+住所(+看板の名前)→ 寸法の推定(範囲つき)と、範囲の両端での自家用の法規の判定。
    写真は推定のために API へ送るだけで、保存しない。台帳は開かない。安全の判定(ランク・18項目)は出さない。"""
    address = str(body.get("address") or "").strip()
    if not address:
        raise ApiError(400, "住所を入れてください")
    raw = body.get("photos")
    if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_ESTIMATE_PHOTOS or not all(isinstance(x, str) for x in raw):
        raise ApiError(400, f"photos は base64 の文字列を 1〜{MAX_ESTIMATE_PHOTOS} 枚")
    photos = []
    for x in raw:
        try:
            b = base64.b64decode(x.split(",", 1)[-1], validate=True)
        except ValueError:
            raise ApiError(400, "photos が base64 ではありません")
        if not b or len(b) > MAX_PHOTO_BYTES:
            raise ApiError(400, "写真が空か大きすぎます")
        photos.append(b)
    name = str(body.get("target_name") or "").strip()[:80] or None
    if estimator_factory is None:
        if not can_draft():
            raise ApiError(503, "寸法の推定には ANTHROPIC_API_KEY が要ります(環境変数か .env)")
        estimator_factory = DimEstimator
    try:
        est = estimator_factory().estimate(photos, name)
    except DimEstimateError as e:
        raise ApiError(502, f"寸法を推定できませんでした: {e}")
    except Exception as e:  # 鍵の誤り・接続など
        raise ApiError(502, f"寸法を推定できませんでした: {type(e).__name__}")
    try:
        j = judge_from_range(est, address)
    except Exception as e:
        raise ApiError(500, f"法規の判定ができませんでした: {type(e).__name__}: {e}")
    return {"estimate": est.to_dict(), "judgement": j, "address": address}


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
