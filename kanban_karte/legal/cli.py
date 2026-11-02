"""法規役の CLI。

  python3 -m kanban_karte.legal.cli judge --address "東京都豊島区見本町1-2-3" --use self --area 8.5 --height 3.2
  python3 -m kanban_karte.legal.cli municipality --address "埼玉県越谷市南越谷1-1"
  python3 -m kanban_karte.legal.cli extract --municipality 越谷市 [--dry-run] [--model ...] [--effort ...]
  python3 -m kanban_karte.legal.cli verify [--stamp]       # rules_*.json の数値を手元の正解と照合(件数と不一致の項目名だけ)
  python3 -m kanban_karte.legal.cli table3 [--dry-run]     # 東京都 別表第三だけを再抽出(API。概算 $0.2)

extract は Anthropic API を呼ぶ(1自治体あたり概算 $0.1〜0.3)。--dry-run は count_tokens(課金なし)で入力の大きさと概算費用だけ出す。
"""
from __future__ import annotations

import argparse
import json
import sys

from . import rules_extract as RX
from . import table3_extract as T3
from . import verify as VF
from .judge import judge
from .municipality import AddressResolver, coverage_note, ordinance_key


def _num(s):
    return None if s is None or s == "" or s.lower() in ("none", "null") else float(s)


def cmd_judge(a) -> int:
    res = judge(a.address, municipality=a.municipality, use=a.use, area_m2=_num(a.area), height_m=_num(a.height),
                ground_clearance_m=_num(a.ground_clearance), place=a.place, kind=a.kind)
    d = res.to_dict()
    if a.json:
        print(json.dumps(d, ensure_ascii=False, indent=2))
        return 0
    print(f"自治体: {d['municipality']}(条例: {d['ordinance_key']})  用途: {'自家用' if d['use'] == 'self' else '一般'}")
    print(f"許可: {d['permit_required']}   自家用の許可不要基準に収まる: {d['is_self_use_exempt']}")
    print(f"許可期間: {d['permit_term_years']} 年   点検: {d['inspection_required']}   信頼度: {d['confidence']}")
    for b in d["branches"]:
        print(f"  場合分け: {b['when']} -> {b['verdict']}({b['reason']})")
    for c in d["citations"]:
        v = "" if c["quote_verified"] else " [原文と不一致]"
        print(f"  根拠[{c['field']}] {c['source']} {c['article']}: {c['quote']}{v}")
    for n in d["notes"]:
        print(f"  注: {n}")
    print(f"  ({d['status']})")
    return 0


def cmd_municipality(a) -> int:
    m = AddressResolver().resolve(a.address)
    print(json.dumps({"municipality": None if m is None else {"prefecture": m.prefecture, "city": m.city},
                      "ordinance_key": ordinance_key(m), "note": coverage_note(m)}, ensure_ascii=False))
    return 0


def cmd_extract(a) -> int:
    keys = list(RX.ORDINANCE_FILES) if a.municipality == "all" else [a.municipality]
    for k in keys:
        if k not in RX.ORDINANCE_FILES:
            print(f"未対応の自治体: {k}(対応: {list(RX.ORDINANCE_FILES)})", file=sys.stderr)
            return 2
    client = RX.make_client()
    if a.dry_run:
        tot = 0.0
        for k in keys:
            n = RX.estimate_input_tokens(client, k, a.model)
            est = RX.cost_usd(a.model, n, 12000)
            tot += est
            print(f"{k}: 入力 {n} トークン / 概算 ${est:.2f}(出力 12000 トークン仮定)")
        print(f"合計概算 ${tot:.2f}(上限 ${RX.BUDGET_USD})")
        return 0
    spent = 0.0
    for k in keys:
        doc, u = RX.extract(k, client=client, model=a.model, effort=a.effort)
        p = RX.save_rules(doc)
        spent += u["usd"]
        cc = doc["citation_check"]
        print(f"{k}: {p}  引用 {cc['verified']}/{cc['total']} 一致  ${u['usd']:.3f}  試行 {u['attempts']} 回")
        if spent > RX.BUDGET_USD:
            print(f"費用が上限 ${RX.BUDGET_USD} を超えたので中断", file=sys.stderr)
            return 3
    return 0


def cmd_verify(a) -> int:
    return VF.main(["--stamp"] if a.stamp else [])


def cmd_table3(a) -> int:
    client = RX.make_client()
    if a.dry_run:
        n = client.messages.count_tokens(model=a.model, system=T3.SYSTEM_PROMPT,
                                         messages=[{"role": "user", "content": T3.table3_text()}]).input_tokens
        print(f"東京都 別表第三: 入力 {n} トークン / 概算 ${RX.cost_usd(a.model, n, 8000):.2f}(出力 8000 トークン仮定)")
        return 0
    result, u = T3.extract_table3(client=client, model=a.model, effort=a.effort)
    p = T3.apply_table3(result)
    cc = result["citation_check"]
    print(f"{p}: 基準 {len(result['permit_standards'])} 件  引用 {cc['verified']}/{cc['total']} 一致  ${u['usd']:.3f}  試行 {u['attempts']} 回")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kanban_karte.legal.cli")
    sub = ap.add_subparsers(dest="cmd", required=True)

    j = sub.add_parser("judge", help="看板1枚の法規判定")
    j.add_argument("--address")
    j.add_argument("--municipality", help="住所の代わりに条例キー(東京都/埼玉県/越谷市/さいたま市)")
    j.add_argument("--use", choices=["self", "general"], default="self", help="self=自家用 / general=一般")
    j.add_argument("--area", help="表示面積 ㎡(不明なら省略)")
    j.add_argument("--height", help="高さ m")
    j.add_argument("--ground-clearance", dest="ground_clearance", help="地上高 m")
    j.add_argument("--place", help="設置場所の区分: 許可区域 / 禁止区域 / 用途地域名(商業地域 等)")
    j.add_argument("--kind", help="看板の種類: 自立 / 壁面 / 突出 / 屋上 / 幕 / 旗 / 掛 など")
    j.add_argument("--json", action="store_true")
    j.set_defaults(fn=cmd_judge)

    m = sub.add_parser("municipality", help="住所 -> 自治体")
    m.add_argument("--address", required=True)
    m.set_defaults(fn=cmd_municipality)

    e = sub.add_parser("extract", help="条例から規則を抽出(API)")
    e.add_argument("--municipality", required=True, help="東京都/埼玉県/越谷市/さいたま市/all")
    e.add_argument("--model", default=RX.DEFAULT_MODEL)
    e.add_argument("--effort", default=RX.DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    e.add_argument("--dry-run", action="store_true", help="API は count_tokens だけ(課金なし)")
    e.set_defaults(fn=cmd_extract)

    v = sub.add_parser("verify", help="rules_*.json の数値を手元の正解と照合")
    v.add_argument("--stamp", action="store_true", help="status を第2版(照合済み、一致 x/y)に書き換える")
    v.set_defaults(fn=cmd_verify)

    t = sub.add_parser("table3", help="東京都 別表第三だけを再抽出して rules_東京都.json に足す(API)")
    t.add_argument("--model", default=RX.DEFAULT_MODEL)
    t.add_argument("--effort", default=RX.DEFAULT_EFFORT, choices=["low", "medium", "high", "xhigh", "max"])
    t.add_argument("--dry-run", action="store_true", help="count_tokens だけ(課金なし)")
    t.set_defaults(fn=cmd_table3)

    a = ap.parse_args(argv)
    if a.cmd == "judge" and not (a.address or a.municipality):
        ap.error("judge には --address か --municipality が要る")
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
