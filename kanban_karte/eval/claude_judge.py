"""ClaudeJudge: 看板の写真を Claude に見せて、ランクと項目判定を JSON で返させる。

ゼロショット(例示なし・ルーブリックなし)。送る物は
  - 写真(最大 max_photos 枚。長辺 1280px に縮小した JPEG)
  - 看板名と、様式の項目名一覧(分かる様式のみ)
だけ。現場名・点検日・正解は送らない。

鍵は環境変数 ANTHROPIC_API_KEY(リポジトリ直下の .env も読む。.env は git に入れない)。
サンプリングパラメータ(temperature 等)は送らない: 現行のモデルは既定値以外を受け付けず、
SDK 2.x には引数自体が無い。
"""
from __future__ import annotations

import base64
import io
import json
import math
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import anthropic
from PIL import Image, ImageOps

from .dataset import REPO_ROOT, EvalItem
from .judge import Judge, Prediction

DEFAULT_MODEL = "claude-sonnet-5-5"  # 最新の Sonnet(claude-api スキル 2026-10-06 版の表)
MAX_LONG_EDGE = 1280
JPEG_QUALITY = 85
MAX_TOKENS = 6000  # 思考トークンも含む上限
MAX_WORKERS = 4
# 1M トークンあたりの USD (入力, 出力)。claude-api スキルの価格表(2026-10-06)から。
PRICES = {
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
ASSUMED_OUTPUT_TOKENS = 800  # dry-run の出力トークン仮定(1看板あたり。根拠なしの仮置き)

SYSTEM_PROMPT = """あなたは屋外広告物(看板)の安全点検を補助する点検者です。写真から看板の劣化・損傷・固定状態を観察し、次の4段階で判定します。

1 = 良好: 劣化や損傷が見られない
2 = 経過観察: 軽微な劣化があるが、直ちに補修は要らない
3 = 要改善: 補修や交換が必要な劣化・損傷がある
4 = 即時対応・危険: 落下・倒壊・感電などの恐れがあり、直ちに使用停止や補修が必要

看板全体のランク rank は、項目ごとの判定のうち最も悪い値にします。
写真から判断できない項目や、該当しない項目は null にします。推測で埋めないでください。

出力は JSON オブジェクト1つだけにしてください。前後に説明文やコードフェンスを付けないでください。形式は次のとおりです。
{"rank": 1〜4の整数, "items": {"項目名": 1〜4の整数 または null, ...}, "reasons": ["根拠を日本語で短く", ...(最大5つ)], "confidence": 0〜1の数}"""


# ---------- 画像 ----------

def resized_size(w: int, h: int, long_edge: int = MAX_LONG_EDGE) -> tuple[int, int]:
    s = min(1.0, long_edge / max(w, h))
    return max(1, round(w * s)), max(1, round(h * s))


def prepare_image(path: str, long_edge: int = MAX_LONG_EDGE) -> str:
    """画像を開き、向きを直し、長辺 long_edge に縮小した JPEG の base64 を返す。"""
    with Image.open(path) as im:
        im = ImageOps.exif_transpose(im)
        if im.mode != "RGB":  # 透過 PNG などは白背景に合成
            rgba = im.convert("RGBA")
            bg = Image.new("RGB", rgba.size, (255, 255, 255))
            bg.paste(rgba, mask=rgba.split()[3])
            im = bg
        im.thumbnail((long_edge, long_edge), Image.LANCZOS)
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=JPEG_QUALITY)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def estimate_image_tokens(path: str, long_edge: int = MAX_LONG_EDGE) -> int:
    """縮小後サイズから画像トークンを概算(幅x高さ/750、上限 4784)。実測は count_tokens で。"""
    with Image.open(path) as im:
        w, h = im.size
    w, h = resized_size(w, h, long_edge)
    return min(4784, math.ceil(w * h / 750))


# ---------- プロンプト ----------

def build_user_text(item: EvalItem, n_photos: int) -> str:
    lines = [f"写真は {n_photos} 枚です。"]
    sign_name = item.meta.get("sign_name")
    if sign_name:
        lines.append(f"看板名: {sign_name}")
    form_items = item.meta.get("form_items") or []
    if form_items:
        lines.append("様式の項目名一覧です。items のキーは、この名前をそのまま使ってください:")
        lines += [f"- {k}" for k in form_items]
    else:
        lines.append("項目一覧はありません。items は空のオブジェクト {} にしてください。")
    lines.append("JSON だけを返してください。")
    return "\n".join(lines)


# ---------- 応答のパース ----------

def _extract_json(text: str) -> str:
    t = text.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.S)
    if m:
        t = m.group(1).strip()
    a, b = t.find("{"), t.rfind("}")
    if a == -1 or b <= a:
        raise ValueError("JSON オブジェクトが見つからない")
    return t[a:b + 1]


def _as_rank(v, where: str) -> int:
    if isinstance(v, bool) or not isinstance(v, int) or v not in (1, 2, 3, 4):
        raise ValueError(f"{where} は 1〜4 の整数でなければならない: {v!r}")
    return v


def parse_response(text: str, form_items: list[str] | None = None) -> dict:
    """モデル応答 → {"rank", "items", "reasons", "confidence"}。不正なら ValueError。

    - rank: 1〜4 の整数(必須)。範囲外・文字列・小数・bool は不正
    - items: {項目名: 1〜4 または null}。null は「判断できない」で結果から除く。範囲外は不正。
      form_items があれば、その外のキーは捨てる。無ければ items は空にする
    - reasons: 文字列のリスト(省略可、最大5件に切る)
    - confidence: 0〜1 の数(省略可)。範囲外は不正
    """
    try:
        obj = json.loads(_extract_json(text))
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON が壊れている: {e}") from e
    if not isinstance(obj, dict):
        raise ValueError("トップレベルがオブジェクトでない")
    if "rank" not in obj:
        raise ValueError("rank がない")
    rank = _as_rank(obj["rank"], "rank")

    items: dict[str, int] = {}
    raw_items = obj.get("items", {})
    if raw_items is None:
        raw_items = {}
    if not isinstance(raw_items, dict):
        raise ValueError("items がオブジェクトでない")
    allowed = set(form_items or [])
    for k, v in raw_items.items():
        if v is None:
            continue
        v = _as_rank(v, f"items[{k!r}]")
        if k in allowed:
            items[k] = v

    reasons = obj.get("reasons", [])
    if reasons is None:
        reasons = []
    if not isinstance(reasons, list) or not all(isinstance(x, str) for x in reasons):
        raise ValueError("reasons が文字列のリストでない")

    conf = obj.get("confidence")
    if conf is not None:
        if isinstance(conf, bool) or not isinstance(conf, (int, float)) or not 0 <= conf <= 1:
            raise ValueError(f"confidence は 0〜1 の数でなければならない: {conf!r}")
        conf = float(conf)
    return {"rank": rank, "items": items, "reasons": reasons[:5], "confidence": conf}


# ---------- 費用 ----------

def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float | None:
    p = PRICES.get(model)
    if p is None:
        return None
    return (input_tokens * p[0] + output_tokens * p[1]) / 1_000_000


# ---------- .env / クライアント ----------

def load_dotenv(path: Path | None = None) -> None:
    """KEY=VALUE 形式の .env を読み、未設定の環境変数だけ埋める。"""
    p = path or (REPO_ROOT / ".env")
    if not p.exists():
        return
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and v and k not in os.environ:
            os.environ[k] = v


# ---------- 判定役 ----------

class ClaudeJudge(Judge):
    name = "claude"

    def __init__(self, model: str = DEFAULT_MODEL, max_photos: int = 6, workers: int = MAX_WORKERS,
                 effort: str | None = None, client=None):
        self.model = model
        self.max_photos = max_photos
        self.workers = max(1, min(workers, MAX_WORKERS))
        self.effort = effort
        self._client = client

    @property
    def client(self):
        if self._client is None:
            load_dotenv()
            if not os.environ.get("ANTHROPIC_API_KEY"):
                raise RuntimeError("ANTHROPIC_API_KEY がありません(環境変数か .env)")
            # 429 / 529 / 5xx / 接続エラーは SDK が指数バックオフで再試行する
            self._client = anthropic.Anthropic(max_retries=6, timeout=180.0)
        return self._client

    # --- 送る物の組み立て ---
    def photos_for(self, item: EvalItem) -> list[str]:
        return item.photo_paths[: self.max_photos]

    def build_messages(self, item: EvalItem) -> list[dict]:
        photos = self.photos_for(item)
        content: list[dict] = []
        for i, p in enumerate(photos, 1):
            content.append({"type": "text", "text": f"写真{i}"})
            content.append({"type": "image", "source": {
                "type": "base64", "media_type": "image/jpeg", "data": prepare_image(p)}})
        content.append({"type": "text", "text": build_user_text(item, len(photos))})
        return [{"role": "user", "content": content}]

    def estimate(self, item: EvalItem) -> dict:
        """API を呼ばずに、送る写真枚数と入力トークンを概算する(dry-run 用)。"""
        photos = self.photos_for(item)
        img = sum(estimate_image_tokens(p) for p in photos)
        text = len(SYSTEM_PROMPT) + len(build_user_text(item, len(photos))) + 8 * len(photos)
        return {"photos": len(photos), "image_tokens": img, "text_tokens": text,
                "input_tokens": img + text}

    # --- API 呼び出し ---
    def _call(self, messages: list[dict]):
        kw = {}
        if self.effort:
            kw["output_config"] = {"effort": self.effort}
        return self.client.messages.create(
            model=self.model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT, messages=messages, **kw)

    def judge(self, item: EvalItem) -> Prediction:
        if not self.photos_for(item):
            return Prediction(None, raw={"error": "no_photos"})
        form_items = item.meta.get("form_items") or []
        try:
            messages = self.build_messages(item)
        except (OSError, ValueError) as e:  # 壊れた画像など
            return Prediction(None, raw={"error": f"image: {e}"})

        in_tok = out_tok = 0
        attempts = 0
        log: list[dict] = []

        def usage() -> dict:
            return {"input_tokens": in_tok, "output_tokens": out_tok, "attempts": attempts,
                    "cost_usd": cost_usd(self.model, in_tok, out_tok)}

        for _ in range(2):  # 1回目 + JSON 不正のときの再試行1回
            attempts += 1
            try:
                resp = self._call(messages)
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
                raise  # 鍵・権限の問題は全件失敗するので止める
            except anthropic.BadRequestError as e:
                log.append({"error": f"BadRequest: {e}"})
                break
            except anthropic.APIStatusError as e:  # 429/5xx は SDK の再試行を使い切った後
                log.append({"error": f"{type(e).__name__} {e.status_code}"})
                break
            except anthropic.APIConnectionError as e:
                log.append({"error": f"{type(e).__name__}"})
                break
            in_tok += resp.usage.input_tokens
            out_tok += resp.usage.output_tokens
            text = "".join(b.text for b in resp.content if b.type == "text")
            if resp.stop_reason == "refusal":
                log.append({"error": "refusal", "stop_details": str(getattr(resp, "stop_details", None))})
                break
            try:
                p = parse_response(text, form_items)
            except ValueError as e:
                log.append({"text": text, "parse_error": str(e), "stop_reason": resp.stop_reason})
                messages = messages + [
                    {"role": "assistant", "content": text or "(空)"},
                    {"role": "user", "content": f"JSON が不正でした({e})。指定の形式の JSON オブジェクトだけを返し直してください。"},
                ]
                continue
            return Prediction(p["rank"], p["items"] if form_items else {}, p["confidence"],
                              raw={"reasons": p["reasons"], "model": self.model,
                                   "stop_reason": resp.stop_reason, "retries": attempts - 1},
                              usage=usage())
        return Prediction(None, None, None, raw={"failed": log, "model": self.model}, usage=usage())

    def judge_many(self, items: list[EvalItem]) -> list[Prediction]:
        if self.workers == 1 or len(items) <= 1:
            return [self.judge(it) for it in items]
        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            return list(ex.map(self.judge, items))
