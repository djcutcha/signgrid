"""runs/page_dump.json に decide() を当てて、混同行列と誤判定の例を見る(規則の調整用)。

  python3 -m tools.rule_tune [--show 正解種別>予測種別 [件数]]
束の id のハッシュで調整用(偶数側)と確認用(奇数側)に分け、両方の再現率を出す。
"""
import json
import sys
from collections import Counter

from kanban_karte.eval.page_kind_eval import confusion, render_markdown
from kanban_karte.intake.ocr_classifier import decide

d = json.load(open("runs/page_dump.json", encoding="utf-8"))
for r in d:
    r["pred"], r["why"] = decide(r["text"], r, r["page"])
tune = [r for r in d if int(r["id"]) % 2 == 0]
held = [r for r in d if int(r["id"]) % 2 == 1]
for name, rs in (("調整用", tune), ("確認用", held), ("全体", d)):
    pairs = [(r["kind"], r["pred"]) for r in rs]
    m = confusion(pairs)
    ok = sum(a == b for a, b in pairs)
    print(f"{name}: {ok / len(pairs):.1%} ({len(pairs)}p) ",
          " ".join(f"{k}再現{m[k][k] / sum(m[k].values()):.0%}" for k in ("完成報告書", "指示書", "点検表", "危険予知", "地図", "写真", "その他", "不明") if k in m))
print(render_markdown("tune", [(r["kind"], r["pred"]) for r in d]))
if "--show" in sys.argv:
    i = sys.argv.index("--show")
    t, p = sys.argv[i + 1].split(">")
    n = int(sys.argv[i + 2]) if len(sys.argv) > i + 2 else 12
    for r in [r for r in d if r["kind"] == t and r["pred"] == p][:n]:
        print(r["id"], r["page"], f"cells={int(r['n_cells'])} sat={r['sat']:.2f}", "|", r["why"], "|", r["text"][:110])
