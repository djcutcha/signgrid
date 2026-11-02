"""安全の18項目(東京都 屋外広告物安全点検報告書 第2号様式)の項目名。

出典(2026-11-02 に原文を確認):
  東京都都市整備局「屋外広告物安全点検報告書の様式改正」
  https://www.toshiseibi.metro.tokyo.lg.jp/ryokuchi_keikan/kekan_kese/koukoku/tenkenkaisei
  様式 PDF https://www.toshiseibi.metro.tokyo.lg.jp/documents/d/toshiseibi/pdf_tenken_kaisei_2025-1
  (2026-04-01 施行。点検結果は 良好 / 経過観察 / 要改善 / 対象外 の4区分)
UI モック(vault の UIモック_v1_看板カルテ_2026-11-01.html)の項目名は、この様式を略した表記。
ここでは様式の原文どおりに持つ。キーは「点検箇所-番号」。
"""
from __future__ import annotations

# (点検箇所, 番号, 点検項目の原文)
_ITEMS: list[tuple[str, int, str]] = [
    ("基礎部・上部構造", 1, "上部構造全体の傾斜、ぐらつき"),
    ("基礎部・上部構造", 2, "基礎のクラック（ひび割れ）、支柱と根巻きとの隙間、支柱ぐらつき"),
    ("基礎部・上部構造", 3, "鉄骨のさび発生、塗装の老朽化"),
    ("支持部", 1, "鉄骨接合部（溶接部・プレート）の腐食、変形、隙間"),
    ("支持部", 2, "鉄骨接合部（ボルト、ナット、ビス）のゆるみ、欠落"),
    ("取付部", 1, "アンカーボルト・取付部プレートの腐食、変形"),
    ("取付部", 2, "溶接部の劣化、コーキングの劣化等"),
    ("取付部", 3, "取付対象部（柱・壁・スラブ）・取付部周辺の異常"),
    ("表示部", 1, "表示面板・切り文字等の腐食、破損、変形、ビス等の欠落"),
    ("表示部", 2, "側板、表示面板押さえの腐食、破損、ねじれ、変形、欠損"),
    ("表示部", 3, "広告板底部の腐食、水抜き孔の詰まり"),
    ("表示部", 4, "表示面の汚染、変色、はく離"),
    ("照明装置", 1, "照明装置の不点灯、不発光"),
    ("照明装置", 2, "照明装置の取付部の破損、変形、さび、漏水"),
    ("照明装置", 3, "周辺機器の劣化、破損"),
    ("その他", 1, "付属部材（装飾、振れ止め棒、鳥よけ、その他付属品）の腐食、破損"),
    ("その他", 2, "避雷針の腐食、損傷"),
    ("その他", 3, "その他点検した事項"),
]

ITEM_KEYS: list[str] = [f"{g}-{n}" for g, n, _ in _ITEMS]
ITEM_LABELS: dict[str, str] = {f"{g}-{n}": f"{g} {n} {t}" for g, n, t in _ITEMS}
assert len(ITEM_KEYS) == 18

# 点検結果の区分(様式の原文)。ledger の items JSON の値はこの文字列
RESULTS = ("良好", "経過観察", "要改善", "対象外")
# 評価の共通軸(1=良好 2=経過観察 3=要改善)への対応。対象外は None
RESULT_TO_RANK = {"良好": 1, "経過観察": 2, "要改善": 3, "対象外": None}


def validate_items(items: dict) -> dict[str, str]:
    """items(項目キー -> 区分)を検査して返す。未知のキー・区分は ValueError。未記入の項目は含めなくてよい。"""
    out = {}
    for k, v in items.items():
        if k not in ITEM_LABELS:
            raise ValueError(f"未知の点検項目: {k!r}")
        if v not in RESULTS:
            raise ValueError(f"点検結果は {RESULTS} のどれか: {k}={v!r}")
        out[k] = v
    return out
