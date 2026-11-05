"""現場の部位ごとの下書き: 1つの点検箇所(支持部・取付部・表示部 …)の写真 1〜2 枚 → その箇所の項目だけを Claude が下書きする。

現場のスマホで部位ごとに撮るウィザード用。束・看板・台帳には触らない(下書きを返すだけ。台帳に入れるのは別の操作)。
全体の判定役(eval/claude_judge.ClaudeJudge。例示・規則つきで重い)とは別に、ここは短い応答(10〜20 秒)を狙う:
  - 項目はその箇所の分だけ(表示部=4、支持部=2 …)、根拠は1文、max_tokens を小さく
  - 判定は様式の区分 + 判断できず: 良好 / 経過観察 / 要改善 / 対象外 / 判断できず
AI は確定しない。有資格者が確定する。
"""
from __future__ import annotations

import io
import json
import time

import anthropic

from ..eval.claude_judge import DEFAULT_MODEL, _extract_json, cost_usd, make_client, prepare_image
from ..ledger.items import _ITEMS, RESULTS

LONG_EDGE = 1280
MAX_PHOTOS = 2
MAX_TOKENS = 1500           # 思考も含む上限。項目は最大 4 つ × 根拠 1 文
EFFORT = "low"              # 考える量を抑える(output_config.effort)。速さ優先
UNDECIDED = "判断できず"
PARTS: list[str] = list(dict.fromkeys(g for g, _n, _t in _ITEMS))    # 点検箇所(様式の並び順)

SYSTEM = """あなたは屋外広告物(看板)の安全点検を補助する点検者です。渡された写真(同じ看板の同じ点検箇所を撮ったもの)を見て、指定された点検箇所の点検項目だけを判定します。
判定は次の5つのどれかです。
- 良好: 写真で見る限り劣化や損傷が無い
- 経過観察: 軽微な劣化(小さな錆・汚れ・塗装の傷み等)があるが、直ちに補修は要らない
- 要改善: 補修や交換が必要な劣化・損傷(腐食の進行、ビス・ボルトの欠落やゆるみ、ひび、変形、水抜き孔の詰まり等)
- 対象外: その箇所自体がこの看板に無い(例: 照明の無い看板の照明装置)
- 判断できず: 写っていない・暗い・裏側や内部・締付けや寸法の実測が要るなど、写真だけでは判定できない。推測で埋めない
要改善を見落とすことが最も重い誤りです。兆候が写真に見えるときは要改善を検討します。ただし、兆候が見えないのに念のためで上げることはしません。
各項目に、どの写真の何を見て判定したかを日本語で1文(evidence)で書いてください。
出力は JSON オブジェクト1つだけ。前後に説明やコードフェンスを付けないでください。
{"items": {"<項目キー>": {"result": "良好|経過観察|要改善|対象外|判断できず", "evidence": "..."}, ...}}"""


class FieldDraftError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


def items_of(part: str) -> list[tuple[str, int, str]]:
    if part not in PARTS:
        raise FieldDraftError(400, f"part は {' / '.join(PARTS)} のどれか: {part!r}")
    return [(g, n, t) for g, n, t in _ITEMS if g == part]


def parse(text: str, items: list[tuple[str, int, str]]) -> list[dict]:
    """応答 → 項目ごとの {key, group, no, text, result, evidence}(その箇所の全項目。応答に無い・不正な値は ValueError)。"""
    try:
        obj = json.loads(_extract_json(text))
    except (ValueError, json.JSONDecodeError) as e:
        raise ValueError(f"JSON として読めない: {e}")
    got = obj.get("items") if isinstance(obj, dict) else None
    if not isinstance(got, dict):
        raise ValueError("items がオブジェクトでない")
    out = []
    for g, n, t in items:
        key = f"{g}-{n}"
        v = got.get(key)
        if not isinstance(v, dict):
            raise ValueError(f"項目 {key} が無い")
        result, ev = v.get("result"), v.get("evidence")
        if result not in (*RESULTS, UNDECIDED):
            raise ValueError(f"項目 {key} の result が不正: {result!r}")
        out.append({"key": key, "group": g, "no": n, "text": t, "result": result,
                    "evidence": ev.strip() if isinstance(ev, str) else ""})
    return out


class FieldDrafter:
    """client は anthropic.Anthropic 互換(テストは偽物)。"""

    def __init__(self, model: str = DEFAULT_MODEL, client=None):
        self.model, self._client = model, client

    @property
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def build_messages(self, part: str, photos: list[bytes], target_name: str | None = None) -> list[dict]:
        items = items_of(part)
        content: list[dict] = []
        for i, b in enumerate(photos[:MAX_PHOTOS], 1):
            try:
                data = prepare_image(io.BytesIO(b), LONG_EDGE)
            except (OSError, ValueError) as e:
                raise FieldDraftError(400, f"{i} 枚目の写真を開けません: {e}")
            content.append({"type": "text", "text": f"写真{i}"})
            content.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg", "data": data}})
        lines = [f"点検箇所: {part}(写真は {min(len(photos), MAX_PHOTOS)} 枚)"]
        if target_name:
            lines.append(f"看板: {target_name}")
        lines.append("判定する項目(キー: 点検項目):")
        lines += [f"- {g}-{n}: {t}" for g, n, t in items]
        content.append({"type": "text", "text": "\n".join(lines)})
        return [{"role": "user", "content": content}]

    def draft(self, part: str, photos: list[bytes], target_name: str | None = None) -> dict:
        """{part, items: [...], usage: {input_tokens, output_tokens, cost_usd, seconds, attempts, model}}"""
        items = items_of(part)
        if not 1 <= len(photos) <= MAX_PHOTOS:
            raise FieldDraftError(400, f"photos は 1〜{MAX_PHOTOS} 枚")
        messages = self.build_messages(part, photos, target_name)
        t0 = time.monotonic()
        in_tok = out_tok = attempts = 0
        err = ""
        for _ in range(2):   # 1回目 + JSON 不正のときの再試行1回
            attempts += 1
            try:
                resp = self.client.messages.create(model=self.model, max_tokens=MAX_TOKENS, system=SYSTEM,
                                                   messages=messages, output_config={"effort": EFFORT})
            except (anthropic.AuthenticationError, anthropic.PermissionDeniedError):
                raise
            except anthropic.APIError as e:
                raise FieldDraftError(502, f"API エラー: {type(e).__name__}")
            in_tok += resp.usage.input_tokens
            out_tok += resp.usage.output_tokens
            text = "".join(b.text for b in resp.content if b.type == "text")
            if resp.stop_reason == "refusal":
                raise FieldDraftError(502, "モデルが応答を拒否しました")
            try:
                parsed = parse(text, items)
            except ValueError as e:
                err = str(e)
                messages = messages + [
                    {"role": "assistant", "content": text or "(空)"},
                    {"role": "user", "content": f"JSON が不正でした({e})。指定の形式の JSON オブジェクトだけを返し直してください。"}]
                continue
            return {"part": part, "items": parsed,
                    "usage": {"model": self.model, "input_tokens": in_tok, "output_tokens": out_tok, "attempts": attempts,
                              "seconds": round(time.monotonic() - t0, 1), "cost_usd": cost_usd(self.model, in_tok, out_tok)}}
        raise FieldDraftError(502, f"応答を読めませんでした: {err}")
