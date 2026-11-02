"""判定役のインターフェース。

- ConstantJudge(2): 常に 2(経過観察)を返す。下限の目安
- OracleJudge: 正解をそのまま返す。スコアリングの検算用(一致率 100% になるはず)
- ClaudeJudge: claude_judge.py(Claude に写真を見せて判定させる)
- ReplayJudge: 以前の結果 JSON の予測を返す(API なし。反証役の比較で下書きを固定する用)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .dataset import EvalItem


@dataclass
class Prediction:
    rank_pred: int | None  # 1〜4。None=判定できず(指標では不正解・見逃し扱い)
    item_judgments_pred: dict[str, int] | None = None
    confidence: float | None = None
    raw: dict | str | None = None  # モデル応答の生データ・失敗理由(デバッグ用)
    usage: dict | None = None  # {input_tokens, output_tokens, attempts, cost_usd}(API を使う判定役のみ)
    verified: bool = False  # 反証役(verifier.py)を通した。見落とし指摘があれば rank/項目を上げている
    verifier_notes: list[dict] | None = None  # 反証役の指摘 [{item, photo, reason}]。通して指摘なしなら []
    rank_before: int | None = None  # 反証役を通す前の rank(通したときだけ)


class Judge:
    name = "base"

    def judge(self, item: EvalItem) -> Prediction:
        raise NotImplementedError

    def judge_many(self, items: list[EvalItem]) -> list[Prediction]:
        """items と同じ順で返す。並列にできる判定役は上書きする。"""
        return [self.judge(it) for it in items]


class ConstantJudge(Judge):
    """常に同じランクを返す。項目判定は、その様式の項目一覧(様式で既知)に同じ値を入れる。"""

    def __init__(self, value: int = 2):
        self.value = value
        self.name = "constant"

    def judge(self, item: EvalItem) -> Prediction:
        items = None
        if item.item_judgments_true is not None:
            items = {k: self.value for k in item.item_judgments_true}
        return Prediction(self.value, items, confidence=0.0, raw="constant")


class OracleJudge(Judge):
    """正解をそのまま返す(検算用。評価の数字として報告してはならない)。"""

    name = "oracle"

    def judge(self, item: EvalItem) -> Prediction:
        ij = dict(item.item_judgments_true) if item.item_judgments_true is not None else None
        return Prediction(item.rank_true, ij, confidence=1.0, raw="oracle")


class ReplayJudge(Judge):
    """以前の結果 JSON(runs/…)の予測をそのまま返す(API なし)。判定役の下書きを固定して、
    後段(反証役など)だけを変えて比べるため。結果 JSON に無い看板は判定できず(None)。"""

    name = "replay"

    def __init__(self, path: str):
        import json
        r = json.loads(Path(path).read_text(encoding="utf-8"))
        self.model = r.get("model")
        self.source_run = Path(path).name
        self._recs = {x["sign_id"]: x for x in r["records"]}

    def judge(self, item: EvalItem) -> Prediction:
        x = self._recs.get(item.sign_id)
        if x is None or x.get("rank_pred") is None:
            return Prediction(None, raw={"error": "not_in_replay"})
        raw = {"reasons": x["reasons"]} if x.get("reasons") else {}
        if x.get("item_notes"):
            raw["item_notes"] = x["item_notes"]
        return Prediction(x["rank_pred"], x.get("item_judgments_pred"), x.get("confidence"),
                          raw=raw, usage=x.get("usage"))


def build_judge(name: str, **opts) -> Judge:
    if name == "constant":
        return ConstantJudge(2)
    if name == "oracle":
        return OracleJudge()
    if name == "replay":
        return ReplayJudge(**opts)
    if name == "claude":
        from .claude_judge import ClaudeJudge  # anthropic / PIL は claude のときだけ要る
        return ClaudeJudge(**opts)
    raise ValueError(f"unknown judge: {name}")


JUDGE_NAMES = ("constant", "oracle", "claude", "replay")
