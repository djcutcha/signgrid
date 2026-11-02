"""同じ送信者から WINDOW(10分)以内に続けて届いた写真・用紙・メモ・位置を1件の「現場の束」にまとめる。
位置情報メッセージだけは例外で、窓の外でも、同じ送信者の直近 LOCATION_WINDOW(60分)以内で現場の無い最新の束に付ける
(LINE の写真は位置を落とすので、写真の後に位置を送る運用がある)。無ければ従来どおり(10分窓の束、なければ新しい束)。

台帳への登録:
  bundles  1束=1行。file は "line://<userId>/<日付>/<最初のメッセージID>"(台帳に kind 列が無いので、
           入口2の PDF と区別する印として line:// を頭に付ける)。status は 'pages_registered'。
  pages    届いた画像を1枚=1ページ。種別は '不明'(写真か点検用紙かは後段の分類役が決める)。
  sites    位置(写真の EXIF か LINE の位置情報メッセージ)が取れた時だけ作る。id は line-<束id>。

画像は <inbox>/<userId>/<日付>/<メッセージID>.<拡張子> に保存(未追跡の data/private 配下)。
束の進み具合は <inbox>/<userId>/current.json(今の束)と recent.json(直近 RECENT_MAX 束)、受信の記録は <inbox>/<userId>/<日付>/messages.jsonl。
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ...ledger import Ledger, Site
from .exif import gps_from_bytes, image_format

JST = timezone(timedelta(hours=9))
WINDOW = timedelta(minutes=10)           # 写真・用紙・メモを同じ束にまとめる窓
LOCATION_WINDOW = timedelta(minutes=60)  # 位置情報メッセージを、位置の無い直近の束に付ける窓
RECENT_MAX = 20
_EXT = {"JPEG": ".jpg", "PNG": ".png", "GIF": ".gif", "WEBP": ".webp", "HEIC": ".heic", "MPO": ".jpg"}
_SAFE = re.compile(r"[^A-Za-z0-9_-]")
SEEN_MAX = 200


def safe_name(s: str) -> str:
    """ユーザーID・メッセージIDをフォルダ名・ファイル名に使える形に(パスを抜ける文字を落とす)。"""
    out = _SAFE.sub("", str(s))
    if not out:
        raise ValueError(f"使えない ID: {s!r}")
    return out


@dataclass
class SessionState:
    session_id: str          # 最初のメッセージID
    bundle_id: int
    date: str                # 束を始めた日 YYYY-MM-DD(JST)
    started: str             # ISO
    last_at: str             # ISO
    photos: int = 0
    notes: int = 0
    site_id: str | None = None
    seen: list[str] = field(default_factory=list)  # 処理済みメッセージID(webhook の再送で二重に数えない)


@dataclass
class AddResult:
    state: SessionState
    new_session: bool
    duplicate: bool = False
    path: Path | None = None
    gps: tuple[float, float] | None = None


class SessionStore:
    def __init__(self, ledger: Ledger, inbox: str | Path, window: timedelta = WINDOW):
        self.ledger = ledger
        self.inbox = Path(inbox).resolve()
        self.window = window

    # ---- 状態ファイル ----
    def _user_dir(self, user_id: str) -> Path:
        return self.inbox / safe_name(user_id)

    def _load(self, user_id: str) -> SessionState | None:
        p = self._user_dir(user_id) / "current.json"
        try:
            return SessionState(**json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    def _recent(self, user_id: str) -> list[SessionState]:
        """直近の束(新しい順ではない。今の束は必ず含む)。"""
        out: list[SessionState] = []
        try:
            for r in json.loads((self._user_dir(user_id) / "recent.json").read_text(encoding="utf-8")):
                out.append(SessionState(**r))
        except (OSError, ValueError, TypeError):
            out = []
        cur = self._load(user_id)
        if cur is not None and all(r.session_id != cur.session_id for r in out):
            out.append(cur)
        return out

    def _write_json(self, path: Path, doc) -> None:
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)

    def _save(self, user_id: str, st: SessionState, current: bool = True) -> None:
        d = self._user_dir(user_id)
        d.mkdir(parents=True, exist_ok=True)
        st.seen = st.seen[-SEEN_MAX:]
        if current:
            self._write_json(d / "current.json", st.__dict__)
        recent = [r for r in self._recent(user_id) if r.session_id != st.session_id] + [st]
        self._write_json(d / "recent.json", [r.__dict__ for r in recent[-RECENT_MAX:]])

    def _log(self, user_id: str, date: str, rec: dict) -> None:
        d = self._user_dir(user_id) / date
        d.mkdir(parents=True, exist_ok=True)
        with (d / "messages.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # ---- 束の選択 ----
    def _session_for(self, user_id: str, first_message_id: str, now: datetime) -> tuple[SessionState, bool]:
        """窓の内なら今の束、外なら新しい束を台帳に作る。(状態, 新しい束か)"""
        st = self._load(user_id)
        if st is not None and safe_name(first_message_id) in st.seen:
            return st, False  # 窓が過ぎてからの webhook 再送。新しい束を作らず、呼び出し側が重複として捨てる
        if st is not None:
            last = datetime.fromisoformat(st.last_at)
            if timedelta(0) <= now - last <= self.window:
                return st, False
        date = now.astimezone(JST).strftime("%Y-%m-%d")
        sid = safe_name(first_message_id)
        file = f"line://{safe_name(user_id)}/{date}/{sid}"
        sha = hashlib.sha256(f"line:{user_id}:{first_message_id}".encode()).hexdigest()
        existing = self.ledger.find_bundle_by_sha(sha)
        bundle_id = existing["id"] if existing else self.ledger.add_bundle(file, sha, pages=0)
        self.ledger.set_bundle(bundle_id, status="pages_registered")
        iso = now.isoformat()
        return SessionState(session_id=sid, bundle_id=bundle_id, date=date, started=iso, last_at=iso), True

    def _set_site(self, st: SessionState, now: datetime, gps: tuple[float, float] | None,
                  address: str | None = None, name: str | None = None) -> None:
        """位置が分かった時に現場を作る(束で最初の位置を採る。住所・名前は空欄だけ埋める)。"""
        if gps is None and address is None:
            return
        site = self.ledger.get_site(st.site_id) if st.site_id else None
        if site is None:
            label = now.astimezone(JST).strftime("%Y-%m-%d %H:%M")
            site = Site(id=f"line-{st.bundle_id}", name=name or f"LINE受信 {label}")
            st.site_id = site.id
        if gps is not None and site.lat is None:
            site.lat, site.lng = gps
        if address and not site.address:
            site.address = address
        self.ledger.upsert_site(site)
        self.ledger.set_bundle(st.bundle_id, site_id=site.id)

    def current(self, user_id: str) -> SessionState | None:
        return self._load(user_id)

    def touch(self, user_id: str, now: datetime) -> None:
        """今の束の最終時刻を now にする(ガイド付き点検で、ボタンだけ押している間も束を切らない)。窓の外なら何もしない。"""
        st = self._load(user_id)
        if st is not None and timedelta(0) <= now - datetime.fromisoformat(st.last_at) <= self.window:
            st.last_at = now.isoformat()
            self._save(user_id, st)

    def bind_site(self, user_id: str, bundle_id: int, site: Site) -> None:
        """束に現場(案件から引いた物)を紐づける。台帳の bundles.site_id と、その束が今の束なら状態ファイルも更新。"""
        self.ledger.upsert_site(site)
        self.ledger.set_bundle(bundle_id, site_id=site.id)
        st = self._load(user_id)
        if st is not None and st.bundle_id == bundle_id:
            st.site_id = site.id
            self._save(user_id, st)

    # ---- 受信 ----
    def add_image(self, user_id: str, message_id: str, data: bytes, now: datetime) -> AddResult:
        fmt = image_format(data)
        if fmt is None:
            raise ValueError("画像として開けない")
        st, new = self._session_for(user_id, message_id, now)
        mid = safe_name(message_id)
        if mid in st.seen:
            return AddResult(st, False, duplicate=True)
        d = self._user_dir(user_id) / st.date
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{mid}{_EXT.get(fmt, '.bin')}"
        path.write_bytes(data)
        gps = gps_from_bytes(data)
        st.photos += 1
        self.ledger.upsert_page(
            st.bundle_id, st.photos, kind="不明", kind_basis="LINE で受信。写真か点検用紙かは未判定",
            image_path=str(path), image_source="line")
        self.ledger.set_bundle(st.bundle_id, pages=st.photos)
        self._set_site(st, now, gps)
        st.seen.append(mid)
        st.last_at = now.isoformat()
        self._save(user_id, st)
        self._log(user_id, st.date, {"id": mid, "type": "image", "at": st.last_at, "path": path.name,
                                    "gps": list(gps) if gps else None, "page_no": st.photos})
        return AddResult(st, new, path=path, gps=gps)

    def add_text(self, user_id: str, message_id: str, text: str, now: datetime) -> AddResult:
        st, new = self._session_for(user_id, message_id, now)
        mid = safe_name(message_id)
        if mid in st.seen:
            return AddResult(st, False, duplicate=True)
        st.notes += 1
        st.seen.append(mid)
        st.last_at = now.isoformat()
        self._save(user_id, st)
        self._log(user_id, st.date, {"id": mid, "type": "text", "at": st.last_at, "text": text})
        return AddResult(st, new)

    def _bundle_without_site(self, user_id: str, now: datetime) -> SessionState | None:
        """同じ送信者の LOCATION_WINDOW 以内で、現場(位置)の無い最新の束。"""
        for st in sorted(self._recent(user_id), key=lambda r: r.last_at, reverse=True):
            if not timedelta(0) <= now - datetime.fromisoformat(st.last_at) <= LOCATION_WINDOW:
                continue
            row = self.ledger.get_bundle(st.bundle_id)
            if row is not None and row["site_id"] is None:
                return st
        return None

    def add_location(self, user_id: str, message_id: str, lat: float, lng: float, address: str | None,
                     title: str | None, now: datetime) -> AddResult:
        mid = safe_name(message_id)
        for r in self._recent(user_id):
            if mid in r.seen:
                return AddResult(r, False, duplicate=True)
        cand = self._bundle_without_site(user_id, now)
        if cand is not None:
            st, new = cand, False
            cur = self._load(user_id)
            is_current = cur is not None and cur.session_id == st.session_id
        else:
            st, new = self._session_for(user_id, message_id, now)
            is_current = True
        gps = (float(lat), float(lng))
        self._set_site(st, now, gps, address=address)
        st.seen.append(mid)
        if is_current:
            st.last_at = now.isoformat()
        self._save(user_id, st, current=is_current)
        self._log(user_id, st.date, {"id": mid, "type": "location", "at": st.last_at, "gps": list(gps),
                                    "address": address, "title": title})
        return AddResult(st, new, gps=gps)
