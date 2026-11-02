"""住所 → 緯度経度。国土地理院の住所検索 API(無料・鍵不要)。

  https://msearch.gsi.go.jp/address-search/AddressSearch?q=住所
  応答: [{"geometry": {"coordinates": [経度, 緯度]}, "properties": {"title": "..."}}, ...](該当なしは [])

- 結果は JSON ファイルにキャッシュ(「見つからなかった」も覚える)。通信エラーはキャッシュしない。
- 実際の通信は 1 秒に 1 回以下(gov/geocode.py と同じ作り)。fetch / sleep / clock は試験で差し替える。
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ENDPOINT = "https://msearch.gsi.go.jp/address-search/AddressSearch"
MIN_INTERVAL_SEC = 1.0
USER_AGENT = "kanban-karte-line/0.1 (research prototype)"


def _http_fetch(address: str) -> list:
    req = urllib.request.Request(f"{ENDPOINT}?{urllib.parse.urlencode({'q': address})}",
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


class AddressSearch:
    def __init__(self, cache_path: str | Path | None = None, fetch=_http_fetch, *,
                 min_interval: float = MIN_INTERVAL_SEC, sleep=time.sleep, clock=time.monotonic):
        self.cache_path = Path(cache_path) if cache_path else None
        self._fetch, self._sleep, self._clock = fetch, sleep, clock
        self.min_interval = min_interval
        self._last_call: float | None = None
        self.calls = 0
        self._cache: dict[str, list[float] | None] = {}
        if self.cache_path and self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self._cache = {}

    def _save(self) -> None:
        if self.cache_path:
            self.cache_path.parent.mkdir(parents=True, exist_ok=True)
            self.cache_path.write_text(json.dumps(self._cache, ensure_ascii=False, indent=1, sort_keys=True),
                                       encoding="utf-8")

    def lookup(self, address: str) -> tuple[float, float] | None:
        """(緯度, 経度)。見つからない・通信できない時は None(推測で埋めない)。"""
        key = address.strip()
        if not key:
            return None
        if key in self._cache:
            v = self._cache[key]
            return (v[0], v[1]) if v else None
        if self._last_call is not None:
            wait = self.min_interval - (self._clock() - self._last_call)
            if wait > 0:
                self._sleep(wait)
        self._last_call = self._clock()
        self.calls += 1
        try:
            data = self._fetch(key)
            first = data[0]["geometry"]["coordinates"] if data else None
        except (urllib.error.URLError, OSError, ValueError, TimeoutError, KeyError, IndexError, TypeError):
            return None
        self._cache[key] = [float(first[1]), float(first[0])] if first else None
        self._save()
        v = self._cache[key]
        return (v[0], v[1]) if v else None
