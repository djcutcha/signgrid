"""受け取りの返事の文面。短く。毎回の挨拶・受付番号・「少々お待ちください」は出さない。"""
from __future__ import annotations

from urllib.parse import urlencode


def receipt(photos: int, has_position_only: bool = False, notes: int = 0, failed: int = 0) -> str:
    """photos: この束の写真の累計枚数。failed: 今回取れなかった枚数。"""
    if photos > 0:
        text = f"受け取りました。写真 {photos} 枚。看板カルテの下書きを作っています"
    elif has_position_only:
        text = "位置を受け取りました"
    elif notes > 0:
        text = "メモを受け取りました"
    else:
        text = ""
    if failed > 0:
        text = (text + "。" if text else "") + f"{failed} 枚は取得できませんでした。もう一度送ってください"
    return text


def follow() -> str:
    """友だち追加のときの1回だけの案内。"""
    return "現場で撮った看板の写真と、記入した点検用紙の写真を、そのまま送ってください"


# ---- 現場の選択 ----
LOCATION_URI = "https://line.me/R/nv/location"
OTHER_LABEL = "その他(現在地を送る)"
LABEL_MAX = 20  # クイックリプライ・ボタンのラベルの上限(LINE の仕様)


def ask_site() -> str:
    return "今日の現場はどれですか"


def _clip(s: str, n: int = LABEL_MAX) -> str:
    return s if len(s) <= n else s[:n - 1] + "…"


def site_choices(jobs, bundle_id: int | None) -> list[dict]:
    """クイックリプライの items。案件ごとに postback(act=site)、最後に「その他(現在地を送る)」(act=other)。"""
    b = {} if bundle_id is None else {"b": bundle_id}
    items = [{"type": "action", "action": {
        "type": "postback", "label": _clip(j.site_name), "displayText": j.site_name,
        "data": urlencode({"act": "site", "job": j.id, **b})}} for j in jobs]
    items.append({"type": "action", "action": {
        "type": "postback", "label": OTHER_LABEL, "displayText": OTHER_LABEL,
        "data": urlencode({"act": "other", **b})}})
    return items


def site_accepted(site_name: str) -> str:
    return f"{site_name}で受け付けました"


def other_prompt() -> str:
    return "下のボタンから現在地を送ってください"


def today_list(jobs) -> str:
    if not jobs:
        return "今日の案件はありません"
    return "今日の案件\n" + "\n".join(f"・{j.site_name}" for j in jobs)


def job_not_found() -> str:
    return "その案件が見つかりませんでした。もう一度選んでください"


def no_bundle() -> str:
    return "先に写真を送ってください"
