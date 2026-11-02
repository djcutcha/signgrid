"""受け取りの返事の文面。短く。毎回の挨拶・受付番号・「少々お待ちください」は出さない。"""
from __future__ import annotations


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
