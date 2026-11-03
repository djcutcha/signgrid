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
from ..gov.upload import UploadError, upload as gov_upload_photos
from ..gov.match import WARN_DAYS, load_candidates, match_candidates
from ..dim.estimator import DimEstimateError, DimEstimator, judge_from_range
from ..legal.locate import judge_at, place_dict
from ..output.common import RANK_LABEL, size_text, unjudged_item_keys
from ..output.client_a_form import write_client_a
from ..output.tokyo_form import write_tokyo_form
from ..pipeline import draft as draft_mod
from ..pipeline import clarify as clarify_mod
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


def size_info(s: Sign) -> dict:
    """寸法の出どころ(カルテのバッジ)。source: measured 実測 / estimated 推定(AI)/ drawing 図面 / legacy 幅×高さはあるが出どころ未記録 / none 未入力。"""
    has = bool(s.size_w and s.size_h)
    source = s.size_source if has and s.size_source else ("legacy" if has else "none")
    return {"source": source, "at": s.size_at, "by": s.size_by, "gl_mm": s.gl_mm, "gl_edge": s.gl_edge,
            "mount": s.mount, "post": s.post}


def sign_summary(s: Sign, n_figures: int = 0) -> dict:
    return {"id": s.id, "site_id": s.site_id, "name": s.name, "type": s.type, "size": size_text(s), "size_info": size_info(s), "height_m": s.height_m,
            "is_self_use": s.is_self_use, "permit_required": s.permit_required,
            "permit_expires": s.permit_expires, "next_inspection": s.next_inspection,
            "rank": s.rank, "rank_label": RANK_LABEL.get(s.rank), "draft_rank": s.draft_rank,
            "prev_rank": s.prev_rank, "status": s.status, "confidence": s.confidence,
            "undecided": len(unjudged_item_keys(s)), "photo_count": len(s.photos) - n_figures,
            "last_inspected": s.last_inspected, "confirmed_by": s.confirmed_by, "confirmed_at": s.confirmed_at}


GUIDE_BY = "line_guide"  # ガイド付き点検(LINE)の回答の by。有資格者の訂正と区別する


def _correction(r, labels: dict[str, str]) -> dict:
    return {"id": r["id"], "item": r["item"], "item_label": labels.get(r["item"], "看板全体のランク"),
            "old_value": r["old_value"], "new_value": r["new_value"], "rank_before": r["rank_before"],
            "rank_after": r["rank_after"], "by": r["corrected_by"], "note": r["note"], "rule_id": r["rule_id"],
            "created": r["created"]}


LEGAL_KEYS = ("permit_required", "inspection_required", "permit_term_years", "is_self_use_exempt", "municipality",
              "ordinance_key", "citations", "notes", "confidence",
              "maintenance_duty", "inspection_duty", "inspection_interval_years", "qualified_inspector", "report_duty", "duties")


def sign_area_m2(s: Sign) -> float | None:
    """size(mm)から表示面積 m2。幅・高さのどちらかが無ければ None(法規役が「要実測」にする)。"""
    if not s.size_w or not s.size_h:
        return None
    return round(s.size_w * s.size_h / 1_000_000, 2)


def legal_judgement(address: str | None, municipality: str | None, use: str, area_m2: float | None,
                    kind: str | None = None, placer=None, height_m: float | None = None) -> dict:
    """法規役(規則ファイルを読むだけの純粋関数)の結果を画面用の dict に。例外は握って {"error": ...}。
    placer(legal.locate.make_placer)があれば住所から用途地域を引いて判定に渡す。結果に place を付ける。"""
    try:
        r, placed = judge_at(address, use=use, area_m2=area_m2, height_m=height_m, kind=kind, placer=placer)
    except Exception as e:  # 規則ファイルが無い・住所が引けない等。画面は落とさない
        return {"error": f"{type(e).__name__}: {e}"}
    d = r.to_dict()
    out = {k: d[k] for k in LEGAL_KEYS}
    out["use"], out["area_m2"], out["place"] = use, area_m2, place_dict(placed, placer)
    return out


def sign_legal(s: Sign, site, placer=None) -> dict:
    """看板の住所(無ければ現場の住所)・自家用か・寸法から、法規の判定と根拠条文。"""
    address = s.address or (site.address if site else None) or s.municipality or (site.municipality if site else None)
    use = "general" if s.is_self_use is False else "self"
    return legal_judgement(address, None, use, sign_area_m2(s), s.type, placer, s.height_m)


def sign_detail(ledger: Ledger, sign_id: str, placer=None) -> dict:
    s = ledger.get_sign(sign_id)
    if s is None:
        raise ApiError(404, f"看板 {sign_id} が台帳にありません")
    rows = ledger.list_corrections(sign_id)
    corrected = {r["item"] for r in rows if r["corrected_by"] != GUIDE_BY}  # 現場の回答(ガイド)は有資格者の訂正ではない
    guided = {r["item"] for r in rows if r["corrected_by"] == GUIDE_BY}
    items = []
    for g, n, t in _ITEMS:
        key = f"{g}-{n}"
        ev = s.evidence.get(key)
        value = s.items.get(key)
        guide = (ev or {}).get("guide")  # 現場の回答: a/b/c/unknown/na。AI が下書きし直した項目や、有資格者が直した項目では出さない
        if guide in ("a", "b", "c") and (key in corrected or key not in guided):
            guide = None
        yellow = key not in corrected and guide != "na" and (value is None or bool(ev and ev.get("needs_onsite")))
        items.append({"key": key, "group": g, "no": n, "text": t, "value": value, "evidence": ev,
                      "yellow": yellow, "corrected": key in corrected, "guide": guide if key not in corrected else None})
    site = ledger.get_site(s.site_id) if s.site_id else None
    fig, meas = ledger.figure_paths(), ledger.measure_paths()
    d = sign_summary(s, sum(1 for p in s.photos if p in fig or p in meas))
    d.update({"site_name": site.name if site else None, "address": s.address, "municipality": s.municipality,
              "remark": s.remark, "draft_by": s.draft_by, "items": items, "yellow_count": sum(i["yellow"] for i in items),
              "photos": [{"n": i + 1, "url": _media_url(ledger, p), "figure": p in fig, "measure": p in meas}
                         for i, p in enumerate(s.photos)],
              "corrections": [_correction(r, {i["key"]: f"{i['group']} {i['no']} {i['text']}" for i in items})
                              for r in rows],
              "legal": sign_legal(s, site, placer), "editable": s.status == "draft", "result_choices": list(RESULTS)})
    return d


def default_clarifier(sample: bool):
    """聞き返し役。見本データ・鍵なしは固定の聞き返し文(API を呼ばない)、鍵があれば Sonnet 5.5。"""
    if sample or not can_draft():
        return clarify_mod.FixedClarifier()
    return clarify_mod.ClaudeClarifier()


def correct(ledger: Ledger, sign_id: str, body: dict, placer=None, clarifier=None) -> dict:
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
    d = sign_detail(ledger, sign_id, placer)
    d["changed"] = c is not None
    d["rule_id"] = c.rule_id if c else None
    # 所見が短い・条件が無いときだけ、1問聞き返す(答えなくても規則は所見のまま候補に積んである)
    d["clarify"] = None
    if c and c.rule_id and clarifier is not None and clarify_mod.needs_question(body.get("note")):
        r = ledger.get_rule(c.rule_id)
        if r["status"] == "candidate":
            q, who = clarify_mod.ask_question(clarifier, clarify_mod.context_of(ledger, sign_id, c, body.get("note")))
            d["clarify"] = {"rule_id": c.rule_id, "question": q, "asked_by": who, "item": c.item}
    return d


def rule_answer(ledger: Ledger, rule_id: int, body: dict, clarifier=None) -> dict:
    """聞き返しの答え → 候補の規則の文と構造を直す。空・「分からない」は何も変えない。"""
    try:
        clarify_mod.apply_answer(ledger, rule_id, str(body.get("question", "")), str(body.get("answer", "")),
                                 str(body.get("by", "")), clarifier or clarify_mod.FixedClarifier())
    except LookupError as e:
        raise ApiError(404, str(e))
    except ValueError as e:
        raise ApiError(400 if "要ります" in str(e) else 409, str(e))
    return _rule_json(ledger, rule_id)


def finalize(ledger: Ledger, sign_id: str, body: dict, placer=None) -> dict:
    s = ledger.get_sign(sign_id)
    if s is None:
        raise ApiError(404, f"看板 {sign_id} が台帳にありません")
    if s.status != "draft":
        raise ApiError(409, f"看板 {sign_id} は status={s.status}。確定できるのは下書きだけ")
    try:
        review_mod.finalize(ledger, sign_id, str(body.get("by", "")))
    except ValueError as e:
        raise ApiError(400, str(e))
    return sign_detail(ledger, sign_id, placer)


def set_height(ledger: Ledger, sign_id: str, body: dict, placer=None) -> dict:
    """看板の全高(m)を台帳に書く(null で消す)。法規の判定に渡る。点検の項目ではないので status は問わない。"""
    s = ledger.get_sign(sign_id)
    if s is None:
        raise ApiError(404, f"看板 {sign_id} が台帳にありません")
    if not str(body.get("by") or "").strip():
        raise ApiError(400, "入力した人の名前を入れてください")
    v = body.get("height_m")
    if v in (None, ""):
        h = None
    else:
        try:
            h = float(v)
        except (TypeError, ValueError):
            raise ApiError(400, f"height_m は数値: {v!r}")
        if isinstance(v, bool) or not 0 < h < 100:
            raise ApiError(400, f"height_m は 0 より大きく 100 未満(m): {v!r}")
        h = round(h, 2)
    s.height_m = h
    ledger.upsert_sign(s)
    return sign_detail(ledger, sign_id, placer)


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
    hidden = ledger.figure_paths() | ledger.measure_paths()  # 図面・実測写真は「写真 n 枚」に数えない
    return [sign_summary(s, sum(1 for p in s.photos if p in hidden)) for s in ledger.list_signs(site_id=site_id)]


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
    # ガイド付き点検(LINE)で現場が答えた看板がある束は、答えを上書きせず「分からない」だけを下書きする
    only_missing = any((ledger.get_sign(sid).draft_by or "").startswith("line_guide")
                       for sid in draft_mod.ensure_signs(ledger, bundle_id))
    try:
        judge = (judge_factory or (lambda rules: draft_mod.default_judge(rules)))(ledger.recent_rules())
        results = draft_mod.draft_bundle(ledger, bundle_id, judge, only_missing=only_missing)
    except (RuntimeError, OSError, LookupError, ValueError) as e:
        raise ApiError(503, f"下書きを作れませんでした: {e}")
    return {"only_missing": only_missing,
            "results": [{"sign_id": r.sign_id, "status": r.status, "rank": r.rank, "undecided": r.undecided,
                         "error": r.error} for r in results]}


def rules(ledger: Ledger) -> list[dict]:
    """規則と承認の状態。conflicts_with は台帳の値(新しい側にだけ積む)、conflicts は両方向・却下を除いた矛盾の相手。"""
    return [{"id": r["id"], "text": r["text"], "source": r["source"], "created": r["created"],
             "conflicts_with": json.loads(r["conflicts_with"] or "[]"), "conflicts": review_mod.conflict_partners(ledger, r["id"]),
             "status": r["status"], "approved_by": r["approved_by"], "approved_at": r["approved_at"],
             "resolution": r["resolution"], "structured": json.loads(r["structured"] or "null")} for r in ledger.list_rules()]


def _rule_json(ledger: Ledger, rule_id: int) -> dict:
    return next(r for r in rules(ledger) if r["id"] == rule_id)


def _rule_op(fn, *args):
    try:
        fn(*args)
    except LookupError as e:
        raise ApiError(404, str(e))
    except ValueError as e:
        raise ApiError(400 if "要ります" in str(e) or "同じ" in str(e) else 409, str(e))


def rule_decide(ledger: Ledger, rule_id: int, action: str, body: dict) -> dict:
    """規則の承認・却下(action = approve / reject)。by は必須。承認済みの規則と矛盾する規則は承認できない(409)。"""
    fn = review_mod.approve_rule if action == "approve" else review_mod.reject_rule
    _rule_op(fn, ledger, rule_id, str(body.get("by", "")), body.get("resolution"))
    return _rule_json(ledger, rule_id)


def rule_resolve(ledger: Ledger, body: dict) -> list[dict]:
    """矛盾の組を1操作で解く: keep を承認、drop を却下、reason を resolution に。戻り値は2件の規則。"""
    try:
        keep, drop = int(body.get("keep")), int(body.get("drop"))
    except (TypeError, ValueError):
        raise ApiError(400, "keep と drop(規則の id)が要ります")
    _rule_op(review_mod.resolve_conflict, ledger, keep, drop, str(body.get("by", "")), body.get("reason"))
    return [_rule_json(ledger, keep), _rule_json(ledger, drop)]


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
        out["batches"].append({"id": b["id"], "created": b["created"], "dir": Path(b["dir"]).name, "name": b["name"] if "name" in b.keys() else None,
                               "photo_count": b["n_files"], "gps_count": b["n_gps"], "nogps_count": b["n_nogps"],
                               "candidate_count": sm["total"], "by_color": sm["by_color"]})
    return out


def gov_upload(ledger: Ledger, body: dict, store: Path, geocoder) -> dict:
    """スマホからの投入。写真と(端末側で決めた)位置を、gov.upload が束ねて保存する。"""
    try:
        return gov_upload_photos(ledger.conn, body, store, geocoder)
    except UploadError as e:
        raise ApiError(e.status, e.message)


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
def _owner_num(body: dict, k: str, required: bool = False) -> float | None:
    """オーナー画面の数値欄(面積 ㎡・全高 m)。空は None(required なら 400)、数でない・0 以下・1万以上は 400。"""
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


def owner_judge(body: dict, placer=None) -> dict:
    """住所・面積(・高さ)から自家用(use=self)の法規の判定と根拠条文。安全の判定(ランク・18項目)は出さない。"""
    address = str(body.get("address") or "").strip()
    if not address:
        raise ApiError(400, "住所を入れてください")
    area, height = _owner_num(body, "area_m2", required=True), _owner_num(body, "height_m")
    try:
        r, placed = judge_at(address, use="self", area_m2=area, height_m=height, placer=placer)
    except Exception as e:
        raise ApiError(500, f"法規の判定ができませんでした: {type(e).__name__}: {e}")
    d = r.to_dict()
    out = {k: d[k] for k in LEGAL_KEYS}
    out.update({"use": "self", "area_m2": area, "height_m": height, "address": address, "place": place_dict(placed, placer)})
    return out


MAX_ESTIMATE_PHOTOS = 2
MAX_PHOTO_BYTES = 12_000_000


def owner_estimate(body: dict, estimator_factory=None, placer=None) -> dict:
    """写真1〜2枚(base64)+住所(+看板の名前、+全高 height_m)→ 寸法の推定(範囲つき)と、範囲の両端での自家用の法規の判定。
    全高が分かれば判定に渡す(高さの基準がある条例で要実測が減る)。写真は推定のために API へ送るだけで、保存しない。
    台帳は開かない。安全の判定(ランク・18項目)は出さない。"""
    address = str(body.get("address") or "").strip()
    if not address:
        raise ApiError(400, "住所を入れてください")
    height = _owner_num(body, "height_m")
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
    placed_box = []

    def judge(addr, **kw):
        r, placed = judge_at(addr, placer=placer, **kw)
        placed_box.append(placed)
        return r

    try:
        j = judge_from_range(est, address, judge=judge, height_m=height)
    except Exception as e:
        raise ApiError(500, f"法規の判定ができませんでした: {type(e).__name__}: {e}")
    return {"estimate": est.to_dict(), "judgement": j, "address": address, "height_m": height,
            "place": place_dict(placed_box[-1] if placed_box else None, placer)}


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
