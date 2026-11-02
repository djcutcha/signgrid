"""候補看板を許可台帳(デモでは自社の施工台帳 signs)と突き合わせる。

- 候補看板の重心と台帳の看板(signs.lat/lng)の距離が 30m 以内で最近傍の1基に一致させる。無ければ「台帳に無い」。
- 一致したら signs の法規の列(permit_required / permit_expires)から許可の状態と残り日数を出す。
- 判定の優先: 期限が入っていれば期限で判定 → 無ければ permit_required で判定。
状態(status)と地図の色:
  unregistered       台帳に無い=無許可の疑い(要確認)        赤
  expired            台帳の許可期限が切れている                赤
  expiring           期限まで warn_days(既定 90)日以内        橙
  permit_incomplete  台帳に有るが許可の要否/期限が未記入       橙
  permitted          許可あり(期限は warn_days より先)        緑
  not_required       台帳で許可不要                            緑
  no_position        位置なし(GPS の無い写真)                 灰
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..ledger.db import Sign
from .batch_intake import ensure_gov_schema, haversine_m

MATCH_RADIUS_M = 30.0
WARN_DAYS = 90

STATUS_COLOR = {
    "unregistered": "red", "expired": "red",
    "expiring": "orange", "permit_incomplete": "orange",
    "permitted": "green", "not_required": "green",
    "no_position": "gray",
}
STATUS_LABEL = {
    "unregistered": "台帳に無い(無許可の疑い・要確認)",
    "expired": "許可期限切れ",
    "expiring": "期限が近い",
    "permit_incomplete": "台帳に有るが許可の要否・期限が未記入",
    "permitted": "許可あり",
    "not_required": "許可不要(台帳)",
    "no_position": "位置なし",
}


@dataclass
class CandidateRow:
    id: int | None
    lat: float | None
    lng: float | None
    photos: list[str] = field(default_factory=list)   # 相対パス
    first_taken: str | None = None
    last_taken: str | None = None
    pref: str | None = None
    city: str | None = None
    geocoded: bool = False

    @property
    def place(self) -> str | None:
        return f"{self.pref} {self.city}" if self.pref and self.city else None


@dataclass
class MatchResult:
    cand: CandidateRow
    status: str
    sign: Sign | None = None
    distance_m: float | None = None
    days_left: int | None = None        # 負なら期限超過の日数
    permit_expires: str | None = None
    renew_from: str | None = None       # 更新手続きの開始の目安(期限の warn_days 日前。設定値で、条例の定めではない)
    note: str = ""

    @property
    def color(self) -> str:
        return STATUS_COLOR[self.status]

    @property
    def label(self) -> str:
        return STATUS_LABEL[self.status]


def nearest_sign(lat: float, lng: float, signs: list[Sign], radius_m: float = MATCH_RADIUS_M):
    """(一番近い看板, 距離m)。radius_m 以内に無ければ (None, 最近傍までの距離 or None)。位置の無い看板は対象外。"""
    best, best_d = None, None
    for s in signs:
        if s.lat is None or s.lng is None:
            continue
        d = haversine_m(lat, lng, s.lat, s.lng)
        if best_d is None or d < best_d:
            best, best_d = s, d
    if best is not None and best_d <= radius_m:
        return best, best_d
    return None, best_d


def permit_state(sign: Sign, today: date, warn_days: int = WARN_DAYS) -> tuple[str, int | None, str | None, str]:
    """台帳の看板1基の許可の状態。戻り値は (status, 残り日数, 更新手続き開始の目安日, 注記)。"""
    exp = sign.permit_expires
    if exp:
        try:
            d = date.fromisoformat(exp)
        except ValueError:
            return "permit_incomplete", None, None, f"許可期限の書式が不正: {exp!r}"
        left = (d - today).days
        renew = (d - timedelta(days=warn_days)).isoformat()
        if left < 0:
            return "expired", left, renew, f"{-left}日前に期限切れ"
        if left <= warn_days:
            return "expiring", left, renew, f"残り{left}日"
        return "permitted", left, renew, f"残り{left}日"
    if sign.permit_required is False:
        return "not_required", None, None, "台帳で許可不要"
    if sign.permit_required is True:
        return "permit_incomplete", None, None, "許可が要るが期限が台帳に無い"
    return "permit_incomplete", None, None, "許可の要否が台帳に未記入"


def match_candidates(cands: list[CandidateRow], signs: list[Sign], today: date, *,
                     radius_m: float = MATCH_RADIUS_M, warn_days: int = WARN_DAYS) -> list[MatchResult]:
    out = []
    for c in cands:
        if c.lat is None or c.lng is None:
            out.append(MatchResult(c, "no_position", note="GPS が無い写真(位置で台帳と突き合わせられない)"))
            continue
        sign, d = nearest_sign(c.lat, c.lng, signs, radius_m)
        if sign is None:
            note = "台帳の看板が近くに無い" if d is None else f"最寄りの台帳の看板まで {d:.0f}m(基準 {radius_m:.0f}m)"
            out.append(MatchResult(c, "unregistered", distance_m=d, note=note))
            continue
        status, left, renew, note = permit_state(sign, today, warn_days)
        out.append(MatchResult(c, status, sign=sign, distance_m=d, days_left=left,
                               permit_expires=sign.permit_expires, renew_from=renew, note=note))
    return out


def load_candidates(conn: sqlite3.Connection, batch_id: int | None = None) -> list[CandidateRow]:
    """gov_ の表から候補看板(位置あり)と、位置なしの写真(1枚=1行)を読む。batch_id があればその回の分だけ。"""
    ensure_gov_schema(conn)
    where, args = ("", ())
    if batch_id is not None:
        where, args = " AND batch_id=?", (batch_id,)
    photos: dict[int, list[str]] = {}
    for cid, rel in conn.execute("SELECT candidate_id, rel FROM gov_photos WHERE candidate_id IS NOT NULL" + where +
                                 " ORDER BY taken_at, rel", args):
        photos.setdefault(cid, []).append(rel)
    rows = [CandidateRow(r[0], r[1], r[2], photos.get(r[0], []), r[3], r[4], r[5], r[6], bool(r[7]))
            for r in conn.execute("SELECT id, lat, lng, first_taken, last_taken, pref, city, geocoded "
                                  "FROM gov_candidates WHERE 1=1" + where + " ORDER BY id", args)]
    for r in conn.execute("SELECT rel, taken_at FROM gov_photos WHERE candidate_id IS NULL" + where +
                          " ORDER BY taken_at, rel", args):
        rows.append(CandidateRow(None, None, None, [r[0]], r[1], r[1]))
    return rows
