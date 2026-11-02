"""gov の見本データを作る: GPS 入りの無地 JPEG 10枚 + 架空の施工台帳(JSON)。

  python3 -m kanban_karte.gov.make_sample                      # data/sample/gov/ に写真と台帳 JSON を書く
  python3 -m kanban_karte.gov.make_sample --db /path/x.sqlite  # さらに台帳 JSON を SQLite に読み込む

座標は越谷市役所・越谷レイクタウン駅・埼玉県庁・足立区役所など公共の場所の周辺(架空の看板を置くための目安で、実在の看板ではない)。
店名はすべて架空。期限の日付は基準日 2026-11-02 で赤/橙/緑/灰が全部出るように決めてある。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image

OUT = Path(__file__).resolve().parents[2] / "data" / "sample" / "gov"
SAMPLE_TODAY = "2026-11-02"

# (ファイル名, 緯度, 経度, 撮影時刻)。緯度経度が None なら GPS なし
PHOTOS = [
    # A: 越谷市役所の前。同じ看板を3枚(重心から台帳の看板まで約10m)
    ("a1.jpg", 35.891000, 139.790800, "2026-11-02 10:02:10"),
    ("a2.jpg", 35.891030, 139.790830, "2026-11-02 10:02:50"),
    ("a3.jpg", 35.891010, 139.790790, "2026-11-02 10:03:30"),
    # C: 市役所から東へ約250m。台帳に無い看板(2枚)
    ("c1.jpg", 35.891100, 139.793600, "2026-11-02 10:12:00"),
    ("c2.jpg", 35.891120, 139.793630, "2026-11-02 10:12:45"),
    # B: 越谷レイクタウン駅の近く(期限まで74日)
    ("b1.jpg", 35.876800, 139.822500, "2026-11-02 10:45:00"),
    ("b2.jpg", 35.876830, 139.822530, "2026-11-02 10:46:10"),
    # D: 埼玉県庁の近く(台帳の許可は期限切れ)
    ("d1.jpg", 35.856900, 139.648900, "2026-11-02 11:30:00"),
    # E: 足立区役所の近く(台帳に有るが許可の要否が未記入)
    ("e1.jpg", 35.775200, 139.804900, "2026-11-02 13:05:00"),
    # F: GPS の無い写真
    ("f1.jpg", None, None, "2026-11-02 14:00:00"),
]

LEDGER = {
    "_comment": "gov 見本の施工台帳。店名・看板・日付はすべて架空。位置は公共の場所の周辺の目安。",
    "sites": [],
    "signs": [
        {"id": "gov-001", "name": "見本商店 ポール看板", "type": "自立", "address": "埼玉県越谷市(市役所前の目安)",
         "lat": 35.891090, "lng": 139.790850, "municipality": "埼玉県越谷市",
         "is_self_use": True, "permit_required": True, "permit_expires": "2027-09-30"},
        {"id": "gov-002", "name": "見本クリニック 壁面看板", "type": "壁面", "address": "埼玉県越谷市(レイクタウン駅近くの目安)",
         "lat": 35.876850, "lng": 139.822560, "municipality": "埼玉県越谷市",
         "is_self_use": True, "permit_required": True, "permit_expires": "2027-01-15"},
        {"id": "gov-003", "name": "見本不動産 野立て看板", "type": "野立て", "address": "埼玉県さいたま市(県庁近くの目安)",
         "lat": 35.856950, "lng": 139.648950, "municipality": "埼玉県さいたま市",
         "is_self_use": False, "permit_required": True, "permit_expires": "2026-10-15"},
        {"id": "gov-004", "name": "見本食堂 袖看板", "type": "袖", "address": "東京都足立区(区役所近くの目安)",
         "lat": 35.775250, "lng": 139.804950, "municipality": "東京都足立区",
         "permit_required": None},
        {"id": "gov-005", "name": "見本書店 自立看板", "type": "自立", "address": "埼玉県越谷市(巡回で写っていない看板)",
         "lat": 35.887600, "lng": 139.790500, "municipality": "埼玉県越谷市",
         "is_self_use": True, "permit_required": False},
    ],
}


def _gps_ifd(lat: float, lng: float) -> dict:
    def dms(v: float):
        v = abs(v)
        d = int(v)
        m = int((v - d) * 60)
        s = round(((v - d) * 60 - m) * 60, 4)
        return (float(d), float(m), float(s))
    return {1: "N" if lat >= 0 else "S", 2: dms(lat), 3: "E" if lng >= 0 else "W", 4: dms(lng)}


def write_jpeg(path: Path, lat: float | None, lng: float | None, taken: str | None, *, color=(214, 218, 222)) -> None:
    """無地の小さな JPEG を、EXIF(GPS・撮影時刻)つきで書く。lat/lng が None なら GPS 無し。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    im = Image.new("RGB", (64, 48), color)
    ex = Image.Exif()
    if taken:
        ex.get_ifd(0x8769)[0x9003] = taken.replace("-", ":", 2)
        ex[0x0132] = taken.replace("-", ":", 2)
    if lat is not None and lng is not None:
        ex.get_ifd(0x8825).update(_gps_ifd(lat, lng))
    im.save(path, "JPEG", exif=ex, quality=70)


def build(out: Path = OUT) -> None:
    for name, lat, lng, t in PHOTOS:
        write_jpeg(out / "photos" / name, lat, lng, t)
    (out / "ledger_gov.json").write_text(json.dumps(LEDGER, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--db", help="台帳 JSON を読み込む SQLite")
    a = ap.parse_args(argv)
    build(Path(a.out))
    print(f"写真 {len(PHOTOS)} 枚と ledger_gov.json を {a.out} に書いた")
    if a.db:
        from ..ledger import Ledger
        with Ledger(a.db) as lg:
            lg.import_json(Path(a.out) / "ledger_gov.json")
        print(f"台帳を {a.db} に読み込んだ")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
