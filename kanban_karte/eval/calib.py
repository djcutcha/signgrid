"""予測ランクの較正(後処理)。様式(source)ごとに「予測→正解」の写像を学習する。

学習に使えるのは例示用(split=example)の看板だけ。評価用(split=eval)の看板が1基でも入ると拒否する。
  mode   予測 r のとき、学習データで最も多かった正解(同数なら r に近い方、それも同じなら大きい方)
  shift  様式ごとに (正解の平均 - 予測の平均) を四捨五入した整数だけ予測をずらす(1〜4 に丸める)
学習データに無い予測値は、mode ではそのまま、shift は様式の ずらし量を使う。
予測が None の看板は学習にも変換にも使わない(None のまま)。
"""
from __future__ import annotations

from collections import Counter, defaultdict

CALIB = ("none", "mode", "shift")


def _clip(v: int) -> int:
    return max(1, min(4, v))


def fit(records: list[dict], kind: str, pred_key: str = "rank_pred") -> dict:
    """records: 結果 JSON の records(source, split, rank_true, rank_pred)。戻り値は JSON にできる辞書。"""
    if kind not in ("mode", "shift"):
        raise ValueError(f"unknown calib: {kind}")
    bad = [r["sign_id"] for r in records if r.get("split") != "example"]
    if bad:
        raise ValueError(f"例示用以外の看板が学習データに入っています: {bad[:3]}")
    by_src: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for r in records:
        if r.get(pred_key) in (1, 2, 3, 4) and r.get("rank_true") in (1, 2, 3, 4):
            by_src[r["source"]].append((r[pred_key], r["rank_true"]))
    out: dict = {"kind": kind, "n": {}, "map": {}, "confusion": {}}
    for s, prs in by_src.items():
        out["n"][s] = len(prs)
        conf: dict[int, Counter] = defaultdict(Counter)
        for p, t in prs:
            conf[p][t] += 1
        out["confusion"][s] = {str(p): dict(sorted(c.items())) for p, c in sorted(conf.items())}
        if kind == "mode":
            out["map"][s] = {str(p): max(c, key=lambda t: (c[t], -abs(t - p), t)) for p, c in conf.items()}
        else:
            out["map"][s] = {"delta": round(sum(t for _, t in prs) / len(prs) - sum(p for p, _ in prs) / len(prs))}
    return out


def apply(model: dict, source: str, pred: int | None) -> int | None:
    m = model["map"].get(source)
    if pred is None or m is None:
        return pred
    if model["kind"] == "mode":
        return m.get(str(pred), pred)
    return _clip(pred + m["delta"])


def train_ids(records: list[dict]) -> set[str]:
    return {r["sign_id"] for r in records}
