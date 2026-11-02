"""住所 → 緯度経度。国土地理院の住所検索 API(無料・鍵不要)。

  https://msearch.gsi.go.jp/address-search/AddressSearch?q=<住所>
  応答: [{"geometry": {"coordinates": [経度, 緯度]}, "properties": {"title": "埼玉県越谷市越ヶ谷四丁目２番１号"}}, ...]
        (関連度順。見つからないときは [])

- 呼び出しはキャッシュ(JSON。キーは住所を NFKC・空白除去で正規化した文字列)し、実際の通信は1秒に1回以下。
  通信エラーはキャッシュしない(次回やり直せる)。『答えは来たが該当なし』はキャッシュする。
- 逆向き(緯度経度 → 市区町村)は gov.geocode の逆ジオコーダに任せる。`muni_of` はそれを呼ぶだけ。
  通信の間隔・User-Agent・自治体コード表は gov.geocode と共通(ここでは持たない)。
- 先頭の1件だけを採る。title が入力より粗いとき(番地まで届かず町名止まり 等)は precision で分かる。
"""
from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from ..gov import geocode as GG

ENDPOINT = "https://msearch.gsi.go.jp/address-search/AddressSearch"
DEFAULT_CACHE = Path(__file__).resolve().parents[2] / "data" / "private" / "geocode_cache.json"


@dataclass(frozen=True)
class Located:
    lat: float
    lng: float
    title: str          # API が返した正規化済みの住所
    candidates: int     # 返ってきた候補の数(2以上は曖昧)

    @property
    def precision(self) -> str:
        """title の粒度。番地・号まで = 'lot'、丁目まで = 'chome'、それより粗い = 'area'。"""
        if re.search(r"[0-9０-９]+番|[0-9０-９]+号|[0-9０-９]+[-－]", self.title):
            return "lot"
        if re.search(r"丁目|[0-9０-９]+$", self.title):
            return "chome"
        return "area"


def norm_address(address: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", address or ""))


def _http_fetch(query: str) -> list:
    q = urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(f"{ENDPOINT}?{q}", headers={"User-Agent": GG.USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


class AddressGeocoder:
    """順ジオコーダ。fetch / sleep / clock は試験で差し替えられる。"""

    def __init__(self, cache_path: str | Path | None = DEFAULT_CACHE, fetch=_http_fetch, *,
                 min_interval: float = GG.MIN_INTERVAL_SEC, sleep=time.sleep, clock=time.monotonic):
        self.cache_path = Path(cache_path) if cache_path else None
        self._fetch, self._sleep, self._clock = fetch, sleep, clock
        self.min_interval = min_interval
        self._last_call: float | None = None
        self.calls = 0
        self._cache: dict[str, dict | None] = {}
        if self.cache_path and self.cache_path.exists():
            self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))

    def _save(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=1, sort_keys=True),
                                       encoding="utf-8")

    def lookup(self, address: str) -> tuple[bool, Located | None]:
        """(答えを得たか, 位置)。(True, None) は該当なし。(False, None) は通信エラー等(キャッシュしない)。"""
        key = norm_address(address)
        if not key:
            return True, None
        if key in self._cache:
            v = self._cache[key]
            return True, (Located(**v) if v else None)
        if self._last_call is not None:
            wait = self.min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()
        self.calls += 1
        try:
            data = self._fetch(key)
        except (urllib.error.URLError, OSError, ValueError, TimeoutError):
            return False, None
        loc = None
        if isinstance(data, list) and data:
            try:
                lng, lat = data[0]["geometry"]["coordinates"][:2]
                loc = Located(float(lat), float(lng), str(data[0]["properties"].get("title", "")), len(data))
            except (KeyError, TypeError, ValueError):
                return False, None
        self._cache[key] = ({"lat": loc.lat, "lng": loc.lng, "title": loc.title, "candidates": loc.candidates}
                            if loc else None)
        self._save()
        return True, loc


def muni_of(reverse: GG.Geocoder, loc: Located) -> GG.Place | None:
    """位置 → 市区町村。gov.geocode の逆ジオコーダを呼ぶ。通信エラー・該当なしは None。"""
    got, place = reverse.lookup(loc.lat, loc.lng)
    return place if got else None
