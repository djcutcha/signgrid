"""同じ条件で2回測った結果 JSON を突き合わせ、「同じ看板で予測が変わった基数」を数える。

  python3 tools/eval_jitter.py runs/A.json runs/B.json

出すもの(sign_id は出さず、件数だけ):
  - 予測(ランク)が変わった基数 / 共通の基数
  - 完全一致の判定(正解と一致か)が変わった基数と、その向き(A→B で当たり→外れ / 外れ→当たり)
  - 見逃し(正解 3,4 を予測 1,2)・誤警報(正解 1,2 を予測 3,4)の判定が変わった基数
"""
from __future__ import annotations

import json
import sys


def load(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return {r["sign_id"]: r for r in json.load(f)["records"]}


def is_action(rank) -> bool:
    return rank in (3, 4)  # 答えなし(None)は要対応でない側。metrics.py と同じ(見逃しに数え、誤警報には数えない)


def compare(a: dict, b: dict) -> dict:
    common = sorted(set(a) & set(b))
    out = {"common": len(common), "only_a": len(set(a) - set(b)), "only_b": len(set(b) - set(a)),
           "pred_changed": 0, "pred_changed_by": {}, "exact_changed": 0, "exact_hit_to_miss": 0,
           "exact_miss_to_hit": 0, "miss_changed": 0, "miss_a": 0, "miss_b": 0,
           "false_alarm_changed": 0, "false_alarm_a": 0, "false_alarm_b": 0,
           "exact_a": 0, "exact_b": 0}
    for sid in common:
        ra, rb = a[sid], b[sid]
        t, pa, pb = ra["rank_true"], ra["rank_pred"], rb["rank_pred"]
        if pa != pb:
            out["pred_changed"] += 1
            if pa is not None and pb is not None:
                k = f"{pa}->{pb}"
                out["pred_changed_by"][k] = out["pred_changed_by"].get(k, 0) + 1
        ea, eb = pa == t, pb == t
        out["exact_a"] += ea
        out["exact_b"] += eb
        if ea != eb:
            out["exact_changed"] += 1
            out["exact_hit_to_miss" if ea else "exact_miss_to_hit"] += 1
        if t in (3, 4):  # 見逃し: 要対応なのに 3,4 と予測しなかった
            ma, mb = not is_action(pa), not is_action(pb)
            out["miss_a"] += ma
            out["miss_b"] += mb
            out["miss_changed"] += ma != mb
        elif t in (1, 2):
            fa, fb = is_action(pa), is_action(pb)
            out["false_alarm_a"] += fa
            out["false_alarm_b"] += fb
            out["false_alarm_changed"] += fa != fb
    return out


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 2:
        print(__doc__)
        return 2
    r = compare(load(argv[0]), load(argv[1]))
    n = r["common"]
    print(f"共通 {n} 基(A だけ {r['only_a']}、B だけ {r['only_b']})")
    print(f"予測ランクが変わった: {r['pred_changed']}/{n} 基  内訳 {r['pred_changed_by']}")
    print(f"完全一致の当たり外れが変わった: {r['exact_changed']}/{n} 基"
          f"(当たり→外れ {r['exact_hit_to_miss']}、外れ→当たり {r['exact_miss_to_hit']})"
          f"  完全一致 A={r['exact_a']} B={r['exact_b']}")
    print(f"見逃しの有無が変わった: {r['miss_changed']} 基  見逃し A={r['miss_a']} B={r['miss_b']}")
    print(f"誤警報の有無が変わった: {r['false_alarm_changed']} 基  誤警報 A={r['false_alarm_a']} B={r['false_alarm_b']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
