"""評価指標。母数と除外数を必ず返す。

定義(詳細は docs/EVAL.md):
- 評価母数 n      = 正解ランクがあり、かつ予測がある看板の数
- 完全一致率      = rank_pred == rank_true
- ±1以内          = |rank_pred - rank_true| <= 1
- 要対応           = ランク 3 または 4
- 見逃し率         = 要対応の看板のうち、予測が要対応でなかった割合(= 1 - recall)
- 誤警報率         = 要対応でない看板のうち、予測が要対応だった割合
- 混同行列         = 行=正解 1〜4、列=予測 1〜4
- 項目一致率       = 正解の項目判定(対象外・空・複数印を除く)のうち、予測が一致した割合。
                    予測が値を返さなかった項目(判断できず)は不一致に数える率と、答えた項目だけの率を併記し、
                    判断できずの件数は項目単位・看板単位で別に数える
- 要対応の4区分   = 要対応(正解 3・4)を、正解の項目判定で次に分ける。
                    a 内訳なし(項目判定が無い)/ b 内訳はあるが 3 以上の項目なし /
                    c 3 以上の項目があり、写真で判定できる項目を 1 つ以上含む /
                    d 3 以上の項目が全て写真では判定できない項目(項目名に 球切れ・点灯・内部・電気 を含む)。
                    「見逃し(写真可)」= c だけを母数にした見逃し率。a・b・d は写真で勝負できるとは言えない
予測が None(判定できず)の看板は、完全一致・±1 では不正解、要対応なら見逃し、
要対応でないなら誤警報にはならない(警報を出していないため)、として数える。
"""
from __future__ import annotations

from typing import Iterable, Sequence

ACTION = (3, 4)
NOT_PHOTO_WORDS = ("球切れ", "点灯", "内部", "電気")  # 項目名にこれを含む項目は、昼の写真では判定できないとみなす


def _rate(num: int, den: int) -> float | None:
    return num / den if den else None


def action_class(rank_true: int, item_judgments_true: dict[str, int] | None) -> str | None:
    """要対応(正解 3・4)の区分 "a"〜"d"。要対応でなければ None。定義はモジュール冒頭。"""
    if rank_true not in ACTION:
        return None
    if not item_judgments_true:
        return "a"
    high = [k for k, v in item_judgments_true.items() if v >= 3]
    if not high:
        return "b"
    if all(any(w in k for w in NOT_PHOTO_WORDS) for k in high):
        return "d"
    return "c"


def action_breakdown(rows: Iterable[tuple[int, dict[str, int] | None, int | None]]) -> dict:
    """rows: (正解ランク, 正解の項目判定, 予測ランク) の列。区分ごとの基数と見逃し数、(c) の見逃し率。"""
    cls = {k: 0 for k in "abcd"}
    missed = {k: 0 for k in "abcd"}
    for t, ij, p in rows:
        c = action_class(t, ij)
        if c is None:
            continue
        cls[c] += 1
        missed[c] += p not in ACTION
    return {"counts": cls, "missed": missed, "photo_miss_rate": _rate(missed["c"], cls["c"])}


def confusion_matrix(true: Sequence[int], pred: Sequence[int | None]) -> list[list[int]]:
    """行=正解(1〜4)、列=予測(1〜4)。予測 None は数えない(no_answer として別途集計)。"""
    m = [[0] * 4 for _ in range(4)]
    for t, p in zip(true, pred):
        if p in (1, 2, 3, 4):
            m[t - 1][p - 1] += 1
    return m


def rank_metrics(true: Sequence[int], pred: Sequence[int | None]) -> dict:
    """true/pred はそろった同じ長さの列(true は 1〜4 のみ)。"""
    n = len(true)
    exact = sum(1 for t, p in zip(true, pred) if p == t)
    within1 = sum(1 for t, p in zip(true, pred) if p is not None and abs(p - t) <= 1)
    pos = [(t, p) for t, p in zip(true, pred) if t in ACTION]
    neg = [(t, p) for t, p in zip(true, pred) if t not in ACTION]
    missed = sum(1 for _, p in pos if p not in ACTION)
    false_alarm = sum(1 for _, p in neg if p in ACTION)
    predicted_pos = sum(1 for p in pred if p in ACTION)
    answered = [(t, p) for t, p in zip(true, pred) if p is not None]
    exact_answered = sum(1 for t, p in answered if p == t)
    return {
        "n": n,
        "no_answer": sum(1 for p in pred if p is None),
        "exact_answered_only_rate": _rate(exact_answered, len(answered)),
        "exact": exact,
        "exact_rate": _rate(exact, n),
        "within1": within1,
        "within1_rate": _rate(within1, n),
        "action_needed_true": len(pos),
        "action_needed_missed": missed,
        "miss_rate": _rate(missed, len(pos)),
        "not_action_true": len(neg),
        "false_alarm": false_alarm,
        "false_alarm_rate": _rate(false_alarm, len(neg)),
        "action_precision": _rate(len(pos) - missed, predicted_pos),
        "confusion": confusion_matrix(true, pred),
    }


def item_metrics(pairs: Iterable[tuple[dict[str, int] | None, dict[str, int] | None]]) -> dict:
    """pairs: (item_judgments_true, item_judgments_pred) の列。true が None の看板は対象外。

    「判断できず」= 正解のある項目に、予測が値を返さなかった(null または項目の欠落)。
    項目単位(cells_unanswered)と看板単位(signs_with_any_unanswered、予測が項目判定を返さなかった看板は
    signs_without_item_pred)で別枠に数える。
    一致率は2通り: 判断できずを不一致に数える率(item_agreement_rate)と、答えた項目だけの率
    (item_agreement_answered_only_rate)。
    """
    signs_with_truth = signs_with_pred = cells = correct = unanswered = signs_unans = 0
    answered_correct = 0
    for t, p in pairs:
        if not t:
            continue
        signs_with_truth += 1
        if p is None:
            cells += len(t)
            unanswered += len(t)
            signs_unans += 1
            continue
        signs_with_pred += 1
        any_un = False
        for k, v in t.items():
            cells += 1
            pv = p.get(k)
            if pv is None:
                unanswered += 1
                any_un = True
            elif pv == v:
                correct += 1
                answered_correct += 1
        signs_unans += any_un
    answered = cells - unanswered
    return {
        "signs_with_item_truth": signs_with_truth,
        "signs_with_item_pred": signs_with_pred,
        "signs_without_item_pred": signs_with_truth - signs_with_pred,
        "signs_with_any_unanswered": signs_unans,
        "cells": cells,
        "cells_correct": correct,
        "cells_unanswered": unanswered,
        "cells_unanswered_rate": _rate(unanswered, cells),
        "item_agreement_rate": _rate(correct, cells),
        "item_agreement_answered_only_rate": _rate(answered_correct, answered),
    }


def compute(pairs: Sequence[tuple]) -> dict:
    """pairs: (EvalItem, Prediction) の列。正解ランクが None の看板は評価母数から除外し、件数を返す。"""
    evaluable = [(it, pr) for it, pr in pairs if it.rank_true is not None]
    res = rank_metrics([it.rank_true for it, _ in evaluable],
                       [pr.rank_pred for _, pr in evaluable])
    res["action_breakdown"] = action_breakdown(
        (it.rank_true, it.item_judgments_true, pr.rank_pred) for it, pr in evaluable)
    res["n_input"] = len(pairs)
    res["n_excluded_no_truth"] = len(pairs) - len(evaluable)
    res["items"] = item_metrics((it.item_judgments_true, pr.item_judgments_pred)
                                for it, pr in evaluable)
    return res
