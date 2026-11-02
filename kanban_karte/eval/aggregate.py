"""項目判定からランクを合成する(後処理)。

正解側のランクは、客先A・Bの様式では「項目の最悪値(数字が大きい方)」で作られている
(dataset.py の rank_true。紙のスキャン束は総合ランクだけで項目なし)。
予測側も同じ規則で、ClaudeJudge が返す項目判定(items)の最悪値をランクにする。

--rank-from:
  model  モデルが返した rank のまま
  items  項目の最悪値(null・判断できずは無視。1項目も無ければ model の rank)
  blend  items の最悪値と model の rank の大きい方(見逃し側に寄せる)
項目のない看板(紙)は、どの方式でも model のまま。
"""
from __future__ import annotations

RANK_FROM = ("model", "items", "blend")


def synth_rank(model_rank: int | None, items: dict | None, mode: str) -> int | None:
    if mode not in RANK_FROM:
        raise ValueError(f"unknown rank-from: {mode}")
    vals = [v for v in (items or {}).values() if v in (1, 2, 3, 4)]
    if mode == "model" or not vals:
        return model_rank
    worst = max(vals)
    if mode == "items" or model_rank is None:
        return worst
    return max(worst, model_rank)
