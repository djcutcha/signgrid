"""手書きの「看板保守点検表」1枚の画像を読み、台帳の項目キーへ対応させた dict にする。

  transcribe_paper(image_path, judge_client=None) -> {"name", "size", "items", "remark", "rank"} | None

読み方と JSON の形は tools/field_transcribe.py の PROMPT を最小化したもの(ツール本体は呼ばない)。
Anthropic の呼び方は eval/claude_judge.py と同じ。失敗(鍵なし・API・JSON 不正)は None。
"""
from __future__ import annotations

import base64
import json
import unicodedata
from pathlib import Path

from kanban_karte.eval.claude_judge import DEFAULT_MODEL, _extract_json, make_client

MAX_TOKENS = 4000  # モデルの思考トークンも含むので余裕を取る(1500 では JSON が途中で切れた 2026-11-08)
MEDIA_TYPES = {".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png"}
GRADE = {"D": "良好", "C": "経過観察", "B": "要改善", "A": "即時対応"}
SIGN_MARK = {"◎": "D", "○": "C", "〇": "C", "△": "B", "×": "A", "✕": "A"}
RANK = {"良好": 1, "経過観察": 2, "要改善": 3, "即時対応": 4}

# 紙の行(点検内容 → 無ければ部位)の語 → 台帳の項目キー。上から順に最初に当たった物。
ROW_MAP = [
    ("取合", ["取付部-1", "取付部-2"]),
    ("照明", ["照明装置-1"]),
    ("不点灯", ["照明装置-1"]),
    ("フィルム", ["表示部-4"]),
    ("シート", ["表示部-4"]),
    ("塗装", ["表示部-4"]),
    ("板面", ["表示部-1"]),
    ("割れ", ["表示部-1"]),
    ("表示面", ["表示部-1"]),
    ("錆", ["基礎部・上部構造-3"]),
    ("腐食", ["基礎部・上部構造-3"]),
]

PROMPT = """これは「看板保守点検表」(手書きの点検票)1枚の画像です。内容を次の JSON だけで返してください(説明文は不要)。

{
  "sign_name": 看板名(見出しの番号を除いた名前。店名・会社名は転記しない。読めなければ null),
  "size": {"w_mm": 整数 or null, "h_mm": 整数 or null, "d_mm": 整数 or null},
  "rows": [{"part": 部位名, "check": 点検内容, "judgment": "D" | "C" | "B" | "A" | "対象外" | "空"}],
  "remarks": 備考欄の文字(読めた分だけ。無ければ null)
}

決まり:
- 凡例は D=◎ 経過良好 / C=○ 劣化あり・経過観察 / B=△ 劣化進行・要改善 / A=× 危険・即時改善。判定欄の D/C/B/A をそのまま読む。◎○△× で書かれていれば D/C/B/A に直す。
- 判定欄が斜線で消されている行は "対象外"、何も書かれていない行は "空"。
- part は表の左の部位名だけ、check は右の点検内容の印刷文言どおり(無ければ "")。
- 表の行を上から順に、すべて rows に入れる(判定が空の行も入れる)。
- 設置場所の行や備考のスケッチから寸法(W×H×D、mm)を読んで size に入れる。無い値は null。
JSON オブジェクトだけを返してください。"""


def _norm(s) -> str:
    return unicodedata.normalize("NFKC", str(s or ""))


def _keys_for(part, check) -> list[str]:
    for text in (_norm(check), _norm(part)):
        for word, keys in ROW_MAP:
            if word in text:
                return keys
    return []


def _size_text(size) -> str | None:
    if not isinstance(size, dict):
        return None
    parts = [f"{k.upper()}{size[f'{k}_mm']}" for k in ("w", "h", "d") if isinstance(size.get(f"{k}_mm"), (int, float))]
    return (" × ".join(parts) + " mm") if parts else None


def to_result(d: dict) -> dict:
    items: dict[str, str] = {}
    for r in d.get("rows") or []:
        if not isinstance(r, dict):
            continue
        j = _norm(r.get("judgment")).strip()
        j = SIGN_MARK.get(j, j).upper()
        if j not in GRADE:  # 対象外・空・読めない行は入れない
            continue
        for key in _keys_for(r.get("part"), r.get("check")):
            # 同じ項目に複数行が当たったら悪い方
            if key not in items or RANK[GRADE[j]] > RANK[items[key]]:
                items[key] = GRADE[j]
    return {
        "name": (d.get("sign_name") or None),
        "size": _size_text(d.get("size")),
        "items": items,
        "remark": (d.get("remarks") or None),
        "rank": max((RANK[v] for v in items.values()), default=None),
    }


def transcribe_paper(image_path, judge_client=None) -> dict | None:
    try:
        p = Path(image_path)
        media = MEDIA_TYPES.get(p.suffix.lower())
        if media is None:
            return None
        client = judge_client
        if client is None:
            try:
                client = make_client()
            except Exception:  # noqa: BLE001  鍵が無い
                return None
        data = base64.standard_b64encode(p.read_bytes()).decode("ascii")
        content = [{"type": "image", "source": {"type": "base64", "media_type": media, "data": data}},
                   {"type": "text", "text": PROMPT}]
        resp = client.messages.create(model=DEFAULT_MODEL, max_tokens=MAX_TOKENS,
                                      messages=[{"role": "user", "content": content}])
        text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text")
        d = json.loads(_extract_json(text))
        if not isinstance(d, dict):
            return None
        return to_result(d)
    except Exception:  # noqa: BLE001  API・画像・JSON の失敗は None
        return None
