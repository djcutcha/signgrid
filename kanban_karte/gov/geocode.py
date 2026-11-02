"""緯度経度 → 市区町村。国土地理院の逆ジオコーダ(住所検索 API の一種)を使う。

  https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress?lat=..&lon=..
  応答: {"results": {"muniCd": "11222", "lv01Nm": "越ヶ谷"}}(日本の外や海上は {})

- muniCd → 自治体名は data/gsi/muni.csv(国土地理院の市区町村コード表の複製。出典は data/gsi/README.md)。
- 呼び出しはキャッシュ(JSON ファイル。緯度経度を小数4桁=約10mで丸めた値がキー)し、実際の通信は1秒に1回以下。
  通信エラーはキャッシュしない(次回やり直せる)。
- 対象自治体は 東京都・埼玉県(越谷市・さいたま市は埼玉県の中)。対象外でも自治体名は返すが in_scope=False。
"""
from __future__ import annotations

import csv
import json
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ENDPOINT = "https://mreversegeocoder.gsi.go.jp/reverse-geocoder/LonLatToAddress"
MUNI_CSV = Path(__file__).resolve().parents[2] / "data" / "gsi" / "muni.csv"
MIN_INTERVAL_SEC = 1.0
USER_AGENT = "kanban-karte-gov/0.1 (research prototype)"

# 対象自治体の範囲(都道府県で見る。越谷市・さいたま市は埼玉県の中なので、この2県で足りる)
SCOPE_PREFECTURES = ("東京都", "埼玉県")


@dataclass(frozen=True)
class Place:
    muni_cd: str
    pref: str
    city: str          # 例: 越谷市 / さいたま市 浦和区 / 新宿区
    in_scope: bool

    @property
    def label(self) -> str:
        return f"{self.pref} {self.city}"


@lru_cache(maxsize=None)
def load_muni_table(path: Path = MUNI_CSV) -> dict[str, tuple[str, str]]:
    """muniCd(5桁) -> (都道府県名, 市区町村名)。"""
    with open(path, encoding="utf-8", newline="") as f:
        return {r["muniCd"]: (r["prefName"], r["muniName"]) for r in csv.DictReader(f)}


def place_from_code(muni_cd: str) -> Place | None:
    muni_cd = str(muni_cd).zfill(5)
    hit = load_muni_table().get(muni_cd)
    if hit is None:
        return None
    pref, city = hit
    return Place(muni_cd, pref, city, pref in SCOPE_PREFECTURES)


def _http_fetch(lat: float, lng: float) -> dict:
    q = urllib.parse.urlencode({"lat": f"{lat:.6f}", "lon": f"{lng:.6f}"})
    req = urllib.request.Request(f"{ENDPOINT}?{q}", headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


class Geocoder:
    """逆ジオコーダ。fetch / sleep / clock は試験で差し替えられる。"""

    def __init__(self, cache_path: str | Path | None = None, fetch=_http_fetch, *,
                 min_interval: float = MIN_INTERVAL_SEC, sleep=time.sleep, clock=time.monotonic):
        self.cache_path = Path(cache_path) if cache_path else None
        self._fetch, self._sleep, self._clock = fetch, sleep, clock
        self.min_interval = min_interval
        self._last_call: float | None = None
        self.calls = 0
        self._cache: dict[str, dict | None] = {}
        if self.cache_path and self.cache_path.exists():
            self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))

    @staticmethod
    def key(lat: float, lng: float) -> str:
        return f"{lat:.4f},{lng:.4f}"

    def _save(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=1, sort_keys=True),
                                       encoding="utf-8")

    def lookup(self, lat: float, lng: float) -> tuple[bool, Place | None]:
        """(答えを得たか, 自治体)。(True, None) は「答えは来たが日本の市区町村ではない(海上・国外)」。
        (False, None) は通信エラー等で答えが得られなかった(キャッシュしない・やり直せる)。"""
        k = self.key(lat, lng)
        if k in self._cache:
            v = self._cache[k]
            return True, (place_from_code(v["muniCd"]) if v else None)
        if self._last_call is not None:
            wait = self.min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()
        self.calls += 1
        try:
            data = self._fetch(lat, lng)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return False, None
        res = (data or {}).get("results") or {}
        cd = res.get("muniCd")
        self._cache[k] = {"muniCd": str(cd).zfill(5), "lv01Nm": res.get("lv01Nm")} if cd else None
        self._save()
        return True, (place_from_code(cd) if cd else None)


def geocode_candidates(conn: sqlite3.Connection, geocoder: Geocoder, *, only_pending: bool = True) -> tuple[int, int]:
    """gov_candidates の自治体を埋める。戻り値は (答えを得た数, 得られなかった数)。"""
    q = "SELECT id, lat, lng FROM gov_candidates"
    if only_pending:
        q += " WHERE geocoded = 0"
    ok = ng = 0
    for r in conn.execute(q + " ORDER BY id").fetchall():
        got, place = geocoder.lookup(r[1], r[2])
        if not got:
            ng += 1
            continue
        ok += 1
        conn.execute("UPDATE gov_candidates SET geocoded=1, muni_cd=?, pref=?, city=? WHERE id=?",
                     (place.muni_cd if place else None, place.pref if place else None,
                      place.city if place else None, r[0]))
    conn.commit()
    return ok, ng
