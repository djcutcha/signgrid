"""寸法役 DimEstimator: 看板の写真(最大2枚)から、参照物を手がかりに幅・高さを範囲つきで推定する。

2枚は1回の呼び出しでまとめて推定する(写真ごとに別々に推定して範囲を交差させる方式は、範囲に正解が入る数が
29/40 → 16/40 に落ちたので採らない。2枚は互いに独立でない。docs/EVAL.md 2026-11-04)。各写真で板面と参照物の枠
(0〜1 に正規化した [左, 上, 右, 下])も返させ、画面で写真に点線で重ねる(枠がずれていれば推定も外れている、の目印)。

送る物は 写真(長辺 1600px に縮小した JPEG)、対象の名前(どの看板の寸法か)、参照物辞書 だけ。
鍵は環境変数 ANTHROPIC_API_KEY(.env も読む)。作法は eval/claude_judge.py と同じ(クライアント・縮小・JSON の再試行1回)。
種類(kind)を渡すと指示を足す: wall(壁面)は取り付く壁面の枠 wall_boxes と壁面の幅・高さ(壁面率の概算用)、
unknown(分からない)は種類の候補 kind_candidates を返させる(人が確定する)。応答の JSON は足すだけで、従来の形のままでも読める。
judge_from_range は推定の範囲の両端で法規役を呼び、許可の要否が両端で同じならその値、違えば「要実測」。
"""
from __future__ import annotations

import io
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import anthropic

from ..eval.claude_judge import _extract_json, cost_usd, make_client, prepare_image
from ..legal import judge as legal
from . import reference_objects as ref

DEFAULT_MODEL = "claude-sonnet-5-5"
LONG_EDGE = 1600
MAX_PHOTOS = 2
MAX_TOKENS = 6000
CONFIDENCES = ("high", "mid", "low")
KINDS = ("wall", "projecting", "freestanding", "rooftop")   # 壁面 / 袖(突出し)/ 自立 / 屋上
KIND_UNKNOWN = "unknown"

# 種類ごとに user の文へ足す指示(system は変えない)
KIND_HINTS = {
    "wall": ("この看板は壁面看板です。測る看板に加えて、看板が取り付いている壁面(建物の 1 面。地面から軒まで、窓・ドアなどの開口部を含む)も測ってください。"
             "JSON に次を足す: \"wall_boxes\": [写真ごとの壁面の枠 [左, 上, 右, 下](0〜1)か null], "
             "\"wall\": {\"w_mm\": 壁面の幅, \"h_mm\": 壁面の高さ(地面から軒まで), \"w_range\": [最小, 最大], \"h_range\": [最小, 最大]}。"
             "壁面が写真に収まっていなければ wall は null。"),
    "projecting": "この看板は袖看板(壁から直角に突き出す看板)です。板面だけを測り、壁に留める金具・腕木は含めないでください。",
    "freestanding": "この看板は自立看板(地面に立つ広告塔・広告板)です。板面だけを測り、支柱・脚は含めないでください。",
    "rooftop": "この看板は屋上看板(建物の屋上に載る看板)です。板面だけを測り、架台・鉄骨は含めないでください。",
    KIND_UNKNOWN: ("看板の種類も見てください。JSON に \"kind_candidates\": [{\"kind\": \"wall\"(壁面)| \"projecting\"(袖・突出し)| "
                   "\"freestanding\"(自立)| \"rooftop\"(屋上), \"why\": 写真のどこからそう見たかの短い説明}] を、確からしい順に 1〜3 件足してください。"),
}

SYSTEM = """あなたは屋外広告物(看板)の寸法を、写真から見積もる補助者です。巻尺で測れない代わりに、写真に写る「大きさの分かる物(参照物)」を物差しにして、指定された看板の幅 W と高さ H を mm で推定し、範囲(最小〜最大)も出します。

手順:
1. まず写真の中の参照物を列挙する。下の辞書にある物を優先し、辞書の寸法を「仮定」として使う。写真に高さ制限などの表示文字(2.2M など)があれば、それも参照物として使ってよい(その場合は read_from_text を true にし、how に「文字から読んだ」と書く)。
2. 対象の範囲を明言する。指定された看板の板面(表示面)だけを測る。ポール・支柱・トラバー・脚・取付金具を含めない。同じ写真に別の看板が写っていても、指定された看板だけを測る。範囲の取り方は notes に書く。
3. 参照物と看板の見かけの長さ(画像上の画素)の比率から W と H を出す。目地・金網の線・段の数など、数えられる物は数える。見上げ・斜めで縦横が縮んで見える時は、その分を補正し、範囲を広げる。
4. 辞書の寸法の揺れ(±)と、測り方の誤差を、範囲 w_range / h_range に反映する。範囲は「実際の寸法がほぼ確実に入る幅」にする(広すぎも狭すぎもしない)。
5. 参照物が写っていない、または弱い(個体差が大きい)時は、confidence を low にして範囲を広くする。当て推量で狭い範囲を出さない。
6. 数値はすべて mm の整数。w_mm は w_range の中、h_mm は h_range の中にあること。
7. 測った板面と、物差しにした参照物の位置を、写真ごとに枠 [左, 上, 右, 下] で返す。写真の左上が 0、右下が 1 の比率(小数2桁)。写真の順に並べ、その写真に写っていない物は null にする。

参照物の辞書(公開情報の一般寸法):
{dictionary}

返答は次の形の JSON オブジェクト1つだけ(前後に文章を付けない):
{{"w_mm": 整数, "h_mm": 整数, "w_range": [最小, 最大], "h_range": [最小, 最大],
 "sign_boxes": [写真ごとの板面の枠 [左, 上, 右, 下](0〜1)か null],
 "references": [{{"name": 参照物の名前, "assumed_mm": 仮定した寸法の整数, "how": どこの何を物差しにしたかの短い説明, "boxes": [写真ごとの参照物の枠か null]}}],
 "read_from_text": 写真の文字から寸法を読んだなら true、そうでなければ false,
 "confidence": "high" か "mid" か "low",
 "notes": 対象の範囲の取り方・補正・不確かな点を短く}}"""


class DimEstimateError(Exception):
    """写真が無い・画像が壊れている・応答が2回続けて不正、など。"""


@dataclass
class DimEstimate:
    w_mm: int
    h_mm: int
    w_range: tuple[int, int]
    h_range: tuple[int, int]
    references: list[dict] = field(default_factory=list)
    read_from_text: bool = False
    confidence: str = "low"
    notes: str = ""
    model: str = ""
    usage: dict = field(default_factory=dict)
    photos: list = field(default_factory=list)  # 写真ごとの枠 [{photo(0始まり), sign_box, references:[{name, box}], wall_box(壁面のみ)}]
    wall: dict | None = None                     # 壁面のみ: {w_mm, h_mm, w_range, h_range, area_m2, area_range}。無ければ None
    kind_candidates: list = field(default_factory=list)  # 種類が分からない時: [{kind, why}]
    kind: str | None = None                      # 推定に渡した種類(None= 指定なし)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["w_range"], d["h_range"] = list(self.w_range), list(self.h_range)
        d["area_m2"] = round(self.w_mm * self.h_mm / 1e6, 2)
        d["w_pct"] = _pct(self.w_mm, self.w_range)
        d["h_pct"] = _pct(self.h_mm, self.h_range)
        return d


def _pct(v: int, rng: tuple[int, int]) -> int:
    """範囲の半幅が値の何 % か(表示用の ±%)。"""
    return int((rng[1] - rng[0]) / 2 / v * 100 + 0.5) if v else 0


def _int(v, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v != v or not 0 < v < 100000:
        raise ValueError(f"{where} は 0 より大きい数(mm)でなければならない: {v!r}")
    return round(v)


def _range(v, where: str) -> tuple[int, int]:
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise ValueError(f"{where} は [最小, 最大] の2要素でなければならない: {v!r}")
    a, b = _int(v[0], where), _int(v[1], where)
    return (a, b) if a <= b else (b, a)


def parse_box(v) -> list | None:
    """枠 [左, 上, 右, 下](0〜1)を正す。壊れていれば None(推定そのものは捨てない)。少しのはみ出しは 0〜1 に切る。"""
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        return None
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and x == x for x in v):
        return None
    if any(x < -0.05 or x > 1.05 for x in v):
        return None
    x0, y0, x1, y1 = (min(1.0, max(0.0, float(x))) for x in v)
    x0, x1 = sorted((x0, x1))
    y0, y1 = sorted((y0, y1))
    if x1 - x0 < 0.005 or y1 - y0 < 0.005:
        return None
    return [round(x0, 3), round(y0, 3), round(x1, 3), round(y1, 3)]


def _nth(v, i: int):
    return v[i] if isinstance(v, list) and i < len(v) else None


def photo_boxes(obj: dict, refs_raw: list, n_photos: int) -> list[dict]:
    """応答の sign_boxes・references[].boxes(・wall_boxes)を写真ごとに組み直す。枠が1つも無い写真も行は作る(画面で写真を出すため)。
    wall_boxes が応答にある時だけ、各行に wall_box を足す(従来の応答では行の形は変わらない)。"""
    out = []
    for i in range(n_photos):
        refs = [{"name": str(r.get("name", "")), "box": parse_box(_nth(r.get("boxes"), i))} for r in refs_raw]
        row = {"photo": i, "sign_box": parse_box(_nth(obj.get("sign_boxes"), i)), "references": [r for r in refs if r["box"]]}
        if "wall_boxes" in obj:
            row["wall_box"] = parse_box(_nth(obj.get("wall_boxes"), i))
        out.append(row)
    return out


def parse_wall(v) -> dict | None:
    """壁面の寸法 {w_mm, h_mm, w_range, h_range}。壊れていれば None(看板の推定は捨てない)。"""
    if not isinstance(v, dict):
        return None
    try:
        w, h = _int(v.get("w_mm"), "wall.w_mm"), _int(v.get("h_mm"), "wall.h_mm")
        wr = _range(v.get("w_range", [w, w]), "wall.w_range")
        hr = _range(v.get("h_range", [h, h]), "wall.h_range")
    except ValueError:
        return None
    wr, hr = (min(wr[0], w), max(wr[1], w)), (min(hr[0], h), max(hr[1], h))
    return {"w_mm": w, "h_mm": h, "w_range": list(wr), "h_range": list(hr), "area_m2": round(w * h / 1e6, 2),
            "area_range": [round(wr[0] * hr[0] / 1e6, 2), round(wr[1] * hr[1] / 1e6, 2)]}


def parse_candidates(v) -> list[dict]:
    """種類の候補。知らない種類・形の崩れた要素は捨てる(重複も)。"""
    out: list[dict] = []
    for c in v if isinstance(v, list) else []:
        if isinstance(c, dict) and c.get("kind") in KINDS and all(o["kind"] != c["kind"] for o in out):
            out.append({"kind": c["kind"], "why": str(c.get("why") or "")[:200]})
    return out[:3]


def parse_estimate(text: str, n_photos: int = 1) -> DimEstimate:
    """モデル応答 → DimEstimate。不正なら ValueError。値が範囲の外なら範囲を値まで広げる。"""
    try:
        obj = json.loads(_extract_json(text))
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON が壊れている: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("トップレベルがオブジェクトでない")
    for k in ("w_mm", "h_mm", "w_range", "h_range"):
        if k not in obj:
            raise ValueError(f"{k} がない")
    w, h = _int(obj["w_mm"], "w_mm"), _int(obj["h_mm"], "h_mm")
    wr, hr = _range(obj["w_range"], "w_range"), _range(obj["h_range"], "h_range")
    wr, hr = (min(wr[0], w), max(wr[1], w)), (min(hr[0], h), max(hr[1], h))
    conf = obj.get("confidence", "low")
    if conf not in CONFIDENCES:
        raise ValueError(f"confidence は {CONFIDENCES} のどれか: {conf!r}")
    refs = obj.get("references") or []
    if not isinstance(refs, list) or not all(isinstance(r, dict) for r in refs):
        raise ValueError("references がオブジェクトのリストでない")
    refs = [{"name": str(r.get("name", "")), "assumed_mm": r.get("assumed_mm"), "how": str(r.get("how", ""))} for r in refs]
    rft = obj.get("read_from_text", False)
    if not isinstance(rft, bool):
        raise ValueError("read_from_text が真偽値でない")
    notes = obj.get("notes") or ""
    if not isinstance(notes, str):
        raise ValueError("notes が文字列でない")
    return DimEstimate(w, h, wr, hr, refs, rft, conf, notes, photos=photo_boxes(obj, obj.get("references") or [], n_photos),
                       wall=parse_wall(obj.get("wall")), kind_candidates=parse_candidates(obj.get("kind_candidates")))


class DimEstimator:
    def __init__(self, model: str = DEFAULT_MODEL, client=None):
        self.model, self._client = model, client

    @property
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def build_messages(self, photos: list, target_name: str | None, kind: str | None = None) -> list[dict]:
        if kind is not None and kind not in KINDS + (KIND_UNKNOWN,):
            raise DimEstimateError(f"種類は {KINDS + (KIND_UNKNOWN,)} のどれか: {kind!r}")
        if not photos:
            raise DimEstimateError("写真がありません")
        content: list[dict] = []
        for p in photos[:MAX_PHOTOS]:
            src = io.BytesIO(p) if isinstance(p, (bytes, bytearray)) else p
            try:
                data = prepare_image(src, LONG_EDGE)
            except (OSError, ValueError) as e:
                raise DimEstimateError(f"画像を開けません: {e}") from e
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        n = min(len(photos), MAX_PHOTOS)
        lines = [f"写真は {n} 枚です(同じ看板を別の角度から撮ったものです)。"]
        if target_name:
            lines.append(f"寸法を知りたい看板: {target_name}")
        else:
            lines.append("寸法を知りたい看板: 写真の中で一番大きく写る看板(板面)")
        if kind:
            lines.append(KIND_HINTS[kind])
        lines.append("JSON だけを返してください。")
        content.append({"type": "text", "text": "\n".join(lines)})
        return [{"role": "user", "content": content}]

    def estimate(self, photos: list, target_name: str | None = None, kind: str | None = None) -> DimEstimate:
        messages = self.build_messages(photos, target_name, kind)
        system = SYSTEM.format(dictionary=ref.prompt_text())
        plain_in = out_tok = attempts = 0
        log: list[str] = []

        def usage() -> dict:
            return {"input_tokens": plain_in, "output_tokens": out_tok, "attempts": attempts,
                    "cost_usd": cost_usd(self.model, plain_in, out_tok)}

        for _ in range(2):  # 1回目 + JSON 不正のときの再試行1回
            attempts += 1
            try:
                resp = self.client.messages.create(model=self.model, max_tokens=MAX_TOKENS, system=system, messages=messages)
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
                raise
            except anthropic.APIError as e:
                raise DimEstimateError(f"API エラー: {type(e).__name__}") from e
            plain_in += resp.usage.input_tokens
            out_tok += resp.usage.output_tokens
            text = "".join(b.text for b in resp.content if b.type == "text")
            if resp.stop_reason == "refusal":
                raise DimEstimateError("モデルが応答を拒否しました")
            try:
                est = parse_estimate(text, min(len(photos), MAX_PHOTOS))
            except ValueError as e:
                log.append(str(e))
                messages = messages + [
                    {"role": "assistant", "content": text or "(空)"},
                    {"role": "user", "content": f"JSON が不正でした({e})。指定の形式の JSON オブジェクトだけを返し直してください。"},
                ]
                continue
            est.model, est.usage, est.kind = self.model, usage(), kind
            return est
        err = DimEstimateError("応答が2回続けて不正でした: " + " / ".join(log))
        err.usage = usage()
        raise err


# ---------- 範囲 → 法規役 ----------

def judge_from_range(estimate: DimEstimate, address: str | None, judge=legal.judge,
                     height_m: float | None = None, **extra) -> dict:
    """範囲の両端(W_low×H_low と W_high×H_high)で法規役(自家用)を呼ぶ。height_m(全高、地上から上端)が分かれば両端とも同じ値を渡す
    (高さの基準がある条例= 埼玉県系の許可・有資格者 は全高が無いと要実測のまま)。許可の要否が両端で同じならその値、違えば「要実測」。"""
    area_low = round(estimate.w_range[0] * estimate.h_range[0] / 1e6, 2)
    area_high = round(estimate.w_range[1] * estimate.h_range[1] / 1e6, 2)
    area_mid = round(estimate.w_mm * estimate.h_mm / 1e6, 2)
    return judge_from_areas(area_low, area_high, area_mid, address, judge=judge, height_m=height_m, **extra)


def judge_from_areas(area_low: float, area_high: float, area_mid: float, address: str | None, judge=legal.judge,
                     height_m: float | None = None, **extra) -> dict:
    """面積の範囲の両端(area_low・area_high ㎡)で法規役(自家用)を呼び、両端で結論が同じ項目はその値、違う項目は「要実測」。
    推定カード(judge_from_range)とオーナー画面の「調べる」(推定の値のまま調べた時)が同じ原則で判定するための共通部。
    extra(kind・wall_area_m2・出幅 等。None の物は渡さない)は両端とも同じ値で法規役へ渡す。種類ごとの項目(kind_checks)も
    両端で結論が分かれた項目は要実測(壁面率は看板の面積で変わる)。"""
    extra = {k: v for k, v in extra.items() if v is not None}
    lo = judge(address, use="self", area_m2=area_low, height_m=height_m, **extra)
    hi = judge(address, use="self", area_m2=area_high, height_m=height_m, **extra)

    def same(a: str, b: str) -> str:
        return a if a == b else legal.NEEDS_MEASURE

    permit = same(lo.permit_required, hi.permit_required)
    cites: list[dict] = []
    for c in lo.citations + hi.citations:
        if c not in cites:
            cites.append(c)
    notes = list(dict.fromkeys(lo.notes + hi.notes))
    if lo.permit_required != hi.permit_required:
        notes.append(f"推定の範囲の両端で結論が分かれる(小さい側 {area_low} ㎡: 許可{lo.permit_required}、"
                     f"大きい側 {area_high} ㎡: 許可{hi.permit_required})。実測が要る")
    duties = {}
    for k, e in lo.duties.items():
        h = hi.duties.get(k)
        if h is not None and h["value"] == e["value"]:
            duties[k] = e
        else:  # 範囲の両端で結論が分かれる -> 要実測(根拠の条文は小さい側のものを残す)
            duties[k] = {**e, "value": legal.NEEDS_MEASURE, "reason": "推定の範囲の両端で結論が分かれる",
                         "text": "要実測(推定の範囲の両端で結論が分かれる)"}
    def merge_kind(attr: str) -> list[dict]:
        out = []
        hi_rows = {r["item"]: r for r in getattr(hi, attr, None) or []}
        for row in getattr(lo, attr, None) or []:
            h = hi_rows.get(row["item"])
            if h is None or h["verdict"] == row["verdict"]:
                out.append({**row, "value_high": h["value"] if h else row["value"]})
            else:
                out.append({**row, "verdict": legal.NEEDS_MEASURE, "value_high": h["value"],
                            "note": f"推定の範囲の両端で結論が分かれる(小さい側 {row['verdict']}・大きい側 {h['verdict']})"})
        return out
    return {"kind_category": getattr(lo, "kind_category", None), "kind_checks": merge_kind("kind_checks"),
            "kind_permit_checks": merge_kind("kind_permit_checks"),
            "road_occupancy": getattr(hi, "road_occupancy", None),
            "permit": permit, "area_low": area_low, "area_high": area_high, "area_mid": area_mid, "height_m": height_m,
            "inspection_required": same(lo.inspection_required, hi.inspection_required),
            "maintenance_duty": same(lo.maintenance_duty, hi.maintenance_duty),
            "inspection_duty": same(lo.inspection_duty, hi.inspection_duty),
            "inspection_interval_years": lo.inspection_interval_years if lo.inspection_interval_years == hi.inspection_interval_years else None,
            "qualified_inspector": same(lo.qualified_inspector, hi.qualified_inspector),
            "report_duty": same(lo.report_duty, hi.report_duty), "duties": duties,
            "is_self_use_exempt": lo.is_self_use_exempt if lo.is_self_use_exempt == hi.is_self_use_exempt else None,
            "permit_term_years": hi.permit_term_years, "ordinance_key": lo.ordinance_key or hi.ordinance_key,
            "confidence": getattr(lo, "confidence", None),
            "municipality": lo.municipality or hi.municipality, "citations": cites, "notes": notes,
            "low": {"area_m2": area_low, "permit": lo.permit_required},
            "high": {"area_m2": area_high, "permit": hi.permit_required}}
