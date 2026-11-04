"""寸法役 DimEstimator: 看板の写真(最大2枚)から、参照物を手がかりに幅・高さを範囲つきで推定する。

写真は1枚ずつ推定する(2枚なら2回呼ぶ)。各写真で板面と参照物の枠(0〜1 に正規化した [左, 上, 右, 下])も返させ、
画面で写真に点線で重ねる(枠が看板と違えば推定も外れている= 要実測、の目印)。2枚の結果は軸ごとに、範囲が重なれば交差、
重ならなければ和集合にして信頼度 low・要実測(combine)。

送る物は 写真(長辺 1600px に縮小した JPEG)、対象の名前(どの看板の寸法か)、参照物辞書 だけ。
鍵は環境変数 ANTHROPIC_API_KEY(.env も読む)。作法は eval/claude_judge.py と同じ(クライアント・縮小・JSON の再試行1回)。
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

SYSTEM = """あなたは屋外広告物(看板)の寸法を、写真から見積もる補助者です。巻尺で測れない代わりに、写真に写る「大きさの分かる物(参照物)」を物差しにして、指定された看板の幅 W と高さ H を mm で推定し、範囲(最小〜最大)も出します。

手順:
1. まず写真の中の参照物を列挙する。下の辞書にある物を優先し、辞書の寸法を「仮定」として使う。写真に高さ制限などの表示文字(2.2M など)があれば、それも参照物として使ってよい(その場合は read_from_text を true にし、how に「文字から読んだ」と書く)。
2. 対象の範囲を明言する。指定された看板の板面(表示面)だけを測る。ポール・支柱・トラバー・脚・取付金具を含めない。同じ写真に別の看板が写っていても、指定された看板だけを測る。範囲の取り方は notes に書く。
3. 参照物と看板の見かけの長さ(画像上の画素)の比率から W と H を出す。目地・金網の線・段の数など、数えられる物は数える。見上げ・斜めで縦横が縮んで見える時は、その分を補正し、範囲を広げる。
4. 辞書の寸法の揺れ(±)と、測り方の誤差を、範囲 w_range / h_range に反映する。範囲は「実際の寸法がほぼ確実に入る幅」にする(広すぎも狭すぎもしない)。
5. 参照物が写っていない、または弱い(個体差が大きい)時は、confidence を low にして範囲を広くする。当て推量で狭い範囲を出さない。
6. 数値はすべて mm の整数。w_mm は w_range の中、h_mm は h_range の中にあること。
7. 測った板面と、物差しにした参照物の写真の中の位置を、枠 [左, 上, 右, 下] で返す。写真の左上が 0、右下が 1 の比率(小数2桁)。写っていない物には枠を付けない(null)。

参照物の辞書(公開情報の一般寸法):
{dictionary}

返答は次の形の JSON オブジェクト1つだけ(前後に文章を付けない):
{{"w_mm": 整数, "h_mm": 整数, "w_range": [最小, 最大], "h_range": [最小, 最大],
 "sign_box": 測った板面の枠 [左, 上, 右, 下](0〜1),
 "references": [{{"name": 参照物の名前, "assumed_mm": 仮定した寸法の整数, "how": どこの何を物差しにしたかの短い説明, "box": 参照物の枠 [左, 上, 右, 下] か null}}],
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
    sign_box: list | None = None     # 板面の枠 [左, 上, 右, 下](0〜1)。1枚の推定の時
    photos: list = field(default_factory=list)  # 写真ごとの結果 [{photo, w_mm, h_mm, w_range, h_range, confidence, sign_box, references}]
    combined: str = "single"         # single 1枚 / intersect 2枚の範囲が重なった / union 重ならない軸があった(要実測)

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


def parse_estimate(text: str) -> DimEstimate:
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
    refs = [{"name": str(r.get("name", "")), "assumed_mm": r.get("assumed_mm"), "how": str(r.get("how", "")),
             "box": parse_box(r.get("box"))} for r in refs]
    rft = obj.get("read_from_text", False)
    if not isinstance(rft, bool):
        raise ValueError("read_from_text が真偽値でない")
    notes = obj.get("notes") or ""
    if not isinstance(notes, str):
        raise ValueError("notes が文字列でない")
    return DimEstimate(w, h, wr, hr, refs, rft, conf, notes, sign_box=parse_box(obj.get("sign_box")))


def _photo_entry(i: int, e: DimEstimate) -> dict:
    return {"photo": i, "w_mm": e.w_mm, "h_mm": e.h_mm, "w_range": list(e.w_range), "h_range": list(e.h_range),
            "confidence": e.confidence, "sign_box": e.sign_box, "references": e.references}


def combine(a: DimEstimate, b: DimEstimate) -> DimEstimate:
    """2枚の写真の推定を合わせる。軸(幅・高さ)ごとに、範囲が重なれば交差(値は両方の平均を交差の中に収める)、
    重ならなければ和集合(値は平均)。重ならない軸が1つでもあれば combined=union・信頼度 low(画面と法規は要実測へ)。
    重なった時の信頼度は低い方(2枚が合っても、物差しの仮定は同じ辞書なので上げない)。"""
    order = {c: i for i, c in enumerate(CONFIDENCES)}
    out, disagree = {}, []
    for ax, label in (("w", "幅"), ("h", "高さ")):
        (a0, a1), (b0, b1) = getattr(a, f"{ax}_range"), getattr(b, f"{ax}_range")
        v = round((getattr(a, f"{ax}_mm") + getattr(b, f"{ax}_mm")) / 2)
        lo, hi = max(a0, b0), min(a1, b1)
        if lo <= hi:
            out[ax] = (min(hi, max(lo, v)), (lo, hi))
        else:
            out[ax] = (v, (min(a0, b0), max(a1, b1)))
            disagree.append(label)
    conf = "low" if disagree else max(a.confidence, b.confidence, key=lambda c: order[c])
    refs, seen = [], set()
    for r in a.references + b.references:
        if r["name"] not in seen:
            seen.add(r["name"])
            refs.append(r)
    notes = [f"写真1: {a.notes}" if a.notes else "", f"写真2: {b.notes}" if b.notes else ""]
    if disagree:
        notes.append(f"2枚の写真で{'・'.join(disagree)}の推定が重ならない(範囲は両方を含む広さにした)。実測が要る")
    else:
        notes.append("2枚の写真の範囲が重なる部分に絞った")
    return DimEstimate(out["w"][0], out["h"][0], out["w"][1], out["h"][1], refs, a.read_from_text or b.read_from_text, conf,
                       " / ".join(n for n in notes if n), photos=[_photo_entry(0, a), _photo_entry(1, b)],
                       combined="union" if disagree else "intersect")


class DimEstimator:
    def __init__(self, model: str = DEFAULT_MODEL, client=None):
        self.model, self._client = model, client

    @property
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def build_messages(self, photos: list, target_name: str | None) -> list[dict]:
        """1回の呼び出しの中身(写真は1枚ずつ送る。estimate が写真ごとに呼ぶ)。"""
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
        lines = ["写真は 1 枚です。" if n == 1 else f"写真は {n} 枚です(同じ看板を別の角度から撮ったものです)。"]
        if target_name:
            lines.append(f"寸法を知りたい看板: {target_name}")
        else:
            lines.append("寸法を知りたい看板: 写真の中で一番大きく写る看板(板面)")
        lines.append("JSON だけを返してください。")
        content.append({"type": "text", "text": "\n".join(lines)})
        return [{"role": "user", "content": content}]

    def estimate(self, photos: list, target_name: str | None = None) -> DimEstimate:
        """写真1枚なら1回、2枚なら写真ごとに1回ずつ推定して combine。2枚のうち片方だけ失敗した時は残りの1枚の結果を返す
        (notes に書く)。費用(usage)は全呼び出しの合計。"""
        if not photos:
            raise DimEstimateError("写真がありません")
        photos = list(photos)[:MAX_PHOTOS]
        if len(photos) == 1:
            e = self._estimate_one(photos, target_name)
            e.photos = [_photo_entry(0, e)]
            return e
        got, errs = [], []
        for i, p in enumerate(photos):
            try:
                got.append((i, self._estimate_one([p], target_name)))
            except DimEstimateError as ex:
                errs.append((i, ex))
        total = {"input_tokens": 0, "output_tokens": 0, "attempts": 0, "cost_usd": 0.0}
        for u in [e.usage for _, e in got] + [getattr(ex, "usage", {}) for _, ex in errs]:
            for k in total:
                total[k] += (u or {}).get(k) or 0
        total["cost_usd"] = round(total["cost_usd"], 6)
        if not got:
            err = DimEstimateError(" / ".join(f"写真{i + 1}: {ex}" for i, ex in errs))
            err.usage = total
            raise err
        if len(got) == 1:
            i, e = got[0]
            e.photos = [_photo_entry(i, e)]
            e.notes = " / ".join(x for x in (e.notes, f"写真{errs[0][0] + 1}は推定できなかった({errs[0][1]})。写真{i + 1}だけで推定") if x)
        else:
            e = combine(got[0][1], got[1][1])
        e.model, e.usage = self.model, total
        return e

    def _estimate_one(self, photos: list, target_name: str | None) -> DimEstimate:
        messages = self.build_messages(photos, target_name)
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
                est = parse_estimate(text)
            except ValueError as e:
                log.append(str(e))
                messages = messages + [
                    {"role": "assistant", "content": text or "(空)"},
                    {"role": "user", "content": f"JSON が不正でした({e})。指定の形式の JSON オブジェクトだけを返し直してください。"},
                ]
                continue
            est.model, est.usage = self.model, usage()
            return est
        err = DimEstimateError("応答が2回続けて不正でした: " + " / ".join(log))
        err.usage = usage()
        raise err


# ---------- 範囲 → 法規役 ----------

def judge_from_range(estimate: DimEstimate, address: str | None, judge=legal.judge,
                     height_m: float | None = None) -> dict:
    """範囲の両端(W_low×H_low と W_high×H_high)で法規役(自家用)を呼ぶ。height_m(全高、地上から上端)が分かれば両端とも同じ値を渡す
    (高さの基準がある条例= 埼玉県系の許可・有資格者 は全高が無いと要実測のまま)。許可の要否が両端で同じならその値、違えば「要実測」。"""
    area_low = round(estimate.w_range[0] * estimate.h_range[0] / 1e6, 2)
    area_high = round(estimate.w_range[1] * estimate.h_range[1] / 1e6, 2)
    area_mid = round(estimate.w_mm * estimate.h_mm / 1e6, 2)
    return judge_from_areas(area_low, area_high, area_mid, address, judge=judge, height_m=height_m)


def judge_from_areas(area_low: float, area_high: float, area_mid: float, address: str | None, judge=legal.judge,
                     height_m: float | None = None) -> dict:
    """面積の範囲の両端(area_low・area_high ㎡)で法規役(自家用)を呼び、両端で結論が同じ項目はその値、違う項目は「要実測」。
    推定カード(judge_from_range)とオーナー画面の「調べる」(推定の値のまま調べた時)が同じ原則で判定するための共通部。"""
    lo = judge(address, use="self", area_m2=area_low, height_m=height_m)
    hi = judge(address, use="self", area_m2=area_high, height_m=height_m)

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
    return {"permit": permit, "area_low": area_low, "area_high": area_high, "area_mid": area_mid, "height_m": height_m,
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
