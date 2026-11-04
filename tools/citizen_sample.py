"""市民からの報告の見本(架空)を 5 件入れる。写真は無地の画像に番号を描いた物、位置は gov 見本の看板(公共の場所の周辺の目安)の近く。

  python3 tools/citizen_sample.py --db <citizen_reports.sqlite> [--ledger <台帳 sqlite>]

--ledger があれば台帳の看板と照らし合わせて状態を付ける(gov 見本の台帳 = gov.make_sample の ledger_gov.json を読み込んだ台帳)。
一言・気になる所・日付はすべて架空。本番の data/private には入れないこと(--db は明示して渡す)。
"""
from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kanban_karte.citizen.store import INSPECTED, Store  # noqa: E402

# (緯度, 経度, 住所, 一言, 気になる所, 日時, 状態の上書き)。位置は gov 見本の看板からの目安
SAMPLES = [
    (35.891060, 139.790870, "", "支柱の根元が茶色くなっていました", ["錆"], "2026-11-01T09:12:00", None),       # gov-001 の近く(把握済み)
    (35.891140, 139.793650, "", "最近できた看板のようです", [], "2026-11-01T17:40:00", None),                  # 台帳に無い所(未把握)
    (35.876870, 139.822540, "", "強い風の日に揺れていました", ["ぐらつき", "傾き"], "2026-10-20T08:05:00", INSPECTED),  # gov-002(点検済みの見本)
    (35.775230, 139.804930, "", "", ["剥がれ"], "2026-11-02T12:30:00", None),                                  # gov-004 の近く(把握済み)
    (None, None, "越谷市 見本町の交差点の角(架空)", "位置が取れなかったので住所で送ります", ["その他"], "2026-11-03T19:02:00", None),  # 位置なし(受付)
]


def photo(n: int) -> bytes:
    im = Image.new("RGB", (640, 480), (196 - n * 12, 208, 220))
    d = ImageDraw.Draw(im)
    d.rectangle([160, 120, 480, 300], outline=(60, 60, 60), width=6)
    d.text((200, 190), f"SAMPLE {n}", fill=(40, 40, 40))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=80)
    return buf.getvalue()


def seed(store: Store, ledger_signs: list | None = None) -> list:
    out = []
    for i, (lat, lng, addr, note, signs, at, status) in enumerate(SAMPLES, 1):
        out.append(store.add(photo(i), lat=lat, lng=lng, address=addr, note=note, signs=signs,
                             ledger_signs=ledger_signs, created_at=at, status=status))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--db", required=True)
    ap.add_argument("--ledger")
    a = ap.parse_args(argv)
    signs = None
    if a.ledger:
        from kanban_karte.ledger import Ledger
        with Ledger(a.ledger) as lg:
            signs = lg.list_signs()
    for r in seed(Store(a.db), signs):
        print(r.code, r.status, r.matched_sign_id or "-")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
