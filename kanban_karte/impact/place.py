"""住所 → 設置場所の区分(用途地域)。geo.geocode(住所→座標)と geo.zoning(座標→用途地域)をつなぐ。

得意先住所は『現地』の近似なので、座標が住所の自治体に落ちたかを逆ジオコーダ(gov.geocode)で確かめる。
別の自治体に落ちた座標(住所検索の誤ヒット)は使わない。

status:
  zone        用途地域が引けた(vocab を judge の place に渡せる。名前が語彙に無いときは vocab=None)
  outside     自治体の用途地域データはあるが、どの用途地域にも入らない(市街化調整区域など)。区分は渡さない
  no_data     その自治体の用途地域データを持っていない。区分は渡さない
  not_found   住所検索に該当なし
  error       住所検索・逆ジオコーダの通信エラー(やり直せる。キャッシュしていない)
  mismatch    座標が住所の自治体と違う自治体に落ちた
このうち not_found / error / mismatch が『座標化できなかった』。
"""
from __future__ import annotations

from dataclasses import dataclass

from ..geo import geocode as GC
from ..geo.zoning import ZoningIndex
from ..gov import geocode as GG
from ..legal.municipality import Municipality

GEOCODE_FAILED = ("not_found", "error", "mismatch")


@dataclass(frozen=True)
class Placed:
    status: str
    vocab: str | None = None        # judge(place=...) に渡す語(ZONE_VOCAB)
    name: str | None = None         # 用途地域の原データの名前
    precision: str | None = None    # 住所検索の粒度 lot / chome / area


class ZonePlacer:
    def __init__(self, geocoder: GC.AddressGeocoder, reverse: GG.Geocoder, index: ZoningIndex):
        self.geocoder, self.reverse, self.index = geocoder, reverse, index
        self._memo: dict[tuple[str, str, str | None], Placed] = {}

    def place(self, address: str, muni: Municipality | None) -> Placed:
        key = (GC.norm_address(address), muni.prefecture if muni else "", muni.city if muni else None)
        if key not in self._memo:
            r = self._place(address, muni)
            if r.status != "error":          # 通信エラーは覚えない(同じ実行内でもやり直せる)
                self._memo[key] = r
            return r
        return self._memo[key]

    def _place(self, address: str, muni: Municipality | None) -> Placed:
        got, loc = self.geocoder.lookup(address)
        if not got:
            return Placed("error")
        if loc is None:
            return Placed("not_found")
        got, place = self.reverse.lookup(loc.lat, loc.lng)
        if not got:
            return Placed("error", precision=loc.precision)
        if place is None or (muni is not None and not _same_muni(muni, place)):
            return Placed("mismatch", precision=loc.precision)
        z = self.index.lookup(loc.lat, loc.lng, place.muni_cd)
        return Placed(z.status, z.vocab, z.name, loc.precision)


def _same_muni(muni: Municipality, place: GG.Place) -> bool:
    """住所から引いた自治体と、座標の自治体が同じか。さいたま市は『さいたま市 浦和区』の市の部分で見る。"""
    if place.pref != muni.prefecture:
        return False
    return muni.city is None or place.city.split()[0] == muni.city
