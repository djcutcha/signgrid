"""現場ウィザード(スマホ /v3/field/)の送信: 写真を 1 枚ずつ置き(field_photo)、看板 1 基を下書きとして台帳に入れる(field_submit)。
Claude API は呼ばない。下書き(区分と根拠)は端末が /api/field/draft で作った物を、そのまま入れる。

  写真   data_dir/field/<submission_id>/<slot>__<n>.<ext> に置く。同じ内容の再送は重複させず(existed)、別の内容は撮り直しとして置き換える。
  看板   signs に status=draft・draft_by='field_wizard:<送信idの先頭4桁>' で作る。rank は付けない(有資格者が確定する)。
         写真は 全景 → 正面 → 部位(様式の並び)の順で signs.photos に入れ、束(bundles。file='field://<送信id>')の pages に tag 付きで登録する。
  項目   良好・経過観察・要改善は signs.items へ。対象外は items に入れない。判断できず(AI 判定)は items に入れず
         evidence の guide='unknown' にする(LINE のガイドと同じ。束の下書きは答えのある項目を残して、これだけを写真から作る)。
         evidence = {photos:[看板の写真番号], guide:a|b|c|unknown|na, field:ai|human, text:根拠の 1 文}。
  現場   住所が同じ既存の現場があればそれ、無ければ新しく作る(id='field-<送信id先頭8桁>')。
  再送   同じ送信idの submit は、作り済みの看板を返す(existed=True)。
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import re
from datetime import datetime
from pathlib import Path

from PIL import Image, UnidentifiedImageError

from ..intake.line.measure import GL_EDGE
from ..intake.line.shoot import FRONT_TAG
from ..ledger.db import Ledger, Sign, Site
from ..ledger.items import ITEM_KEYS
from ..pipeline import field_draft as field_draft_mod

BY = "field_wizard"
OVERVIEW, FRONT = "全景", "正面"
SLOTS = (OVERVIEW, FRONT, *field_draft_mod.PARTS)
SLOT_TAG = {OVERVIEW: "overview", FRONT: FRONT_TAG}
MAX_PHOTO_BYTES = 12_000_000
MAX_PER_SLOT = field_draft_mod.MAX_PHOTOS
IMAGE_EXT = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}
KIND_TYPE = {"wall": "壁面", "projecting": "突出", "freestanding": "自立", "rooftop": "屋上"}
GUIDE = {"良好": "a", "経過観察": "b", "要改善": "c", "対象外": "na"}
UNDECIDED = field_draft_mod.UNDECIDED
SID = re.compile(r"^[0-9A-Za-z-]{8,64}$")


class SubmitError(Exception):
    def __init__(self, status: int, message: str, extra: dict | None = None):
        super().__init__(message)
        self.status, self.message, self.extra = status, message, extra or {}


def _sid(body: dict) -> str:
    sid = body.get("submission_id")
    if not isinstance(sid, str) or not SID.match(sid):
        raise SubmitError(400, "submission_id は 8〜64 文字の英数字とハイフン")
    return sid


def _slot(body: dict) -> tuple[str, int]:
    slot, n = body.get("slot"), body.get("n", 1)
    if slot not in SLOTS:
        raise SubmitError(400, f"slot は {' / '.join(SLOTS)} のどれか: {slot!r}")
    if isinstance(n, bool) or not isinstance(n, int) or not 1 <= n <= (1 if slot in (OVERVIEW, FRONT) else MAX_PER_SLOT):
        raise SubmitError(400, f"n は 1〜{1 if slot in (OVERVIEW, FRONT) else MAX_PER_SLOT}: {n!r}")
    return slot, n


def _dir(data_dir: Path, sid: str) -> Path:
    return Path(data_dir) / "field" / sid


def _file(data_dir: Path, sid: str, slot: str, n: int) -> Path | None:
    d = _dir(data_dir, sid)
    hits = sorted(d.glob(f"{slot}__{n}.*")) if d.is_dir() else []
    return hits[0] if hits else None


def field_photo(body: dict, data_dir: Path) -> dict:
    """写真 1 枚を置く。body = {submission_id, slot, n?, name?, data(base64)}。"""
    sid, (slot, n) = _sid(body), _slot(body)
    raw = body.get("data")
    if not isinstance(raw, str):
        raise SubmitError(400, "data は base64 の文字列")
    try:
        b = base64.b64decode(raw.split(",", 1)[-1], validate=True)
    except (ValueError, binascii.Error):
        raise SubmitError(400, "data が base64 ではありません")
    if not b or len(b) > MAX_PHOTO_BYTES:
        raise SubmitError(400, "写真が空か大きすぎます")
    try:
        with Image.open(io.BytesIO(b)) as im:
            im.verify()
            fmt = im.format
    except (UnidentifiedImageError, OSError, SyntaxError):
        raise SubmitError(400, "画像ではありません")
    if fmt not in IMAGE_EXT:
        raise SubmitError(400, f"写真は JPEG / PNG / WEBP だけ({fmt})")
    sha = hashlib.sha256(b).hexdigest()
    old = _file(data_dir, sid, slot, n)
    if old is not None and hashlib.sha256(old.read_bytes()).hexdigest() == sha:
        return {"submission_id": sid, "slot": slot, "n": n, "existed": True, "sha256": sha}
    d = _dir(data_dir, sid)
    d.mkdir(parents=True, exist_ok=True)
    if old is not None:
        old.unlink()                       # 撮り直し: 前の写真を置き換える
    (d / f"{slot}__{n}{IMAGE_EXT[fmt]}").write_bytes(b)
    return {"submission_id": sid, "slot": slot, "n": n, "existed": False, "sha256": sha}


def _num(v, where: str, lo: float, hi: float) -> float | None:
    if v in (None, ""):
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo < v < hi:
        raise SubmitError(400, f"{where} は {lo} より大きく {hi} 未満の数: {v!r}")
    return float(v)


def _text(v, where: str, mx: int) -> str | None:
    if v is None:
        return None
    if not isinstance(v, str):
        raise SubmitError(400, f"{where} は文字列: {v!r}")
    v = v.strip()
    return v[:mx] or None


def _find_site(ledger: Ledger, address: str) -> Site | None:
    r = ledger.conn.execute("SELECT id FROM sites WHERE address=? ORDER BY created, id LIMIT 1", (address,)).fetchone()
    return ledger.get_site(r["id"]) if r else None


def field_submit(ledger: Ledger, body: dict, data_dir: Path, now=None) -> dict:
    """看板 1 基を下書き(確認待ち)として台帳に入れる。
    body = {submission_id, kind?, name?, address, municipality?, lat?, lng?, photos:[{slot, n}], size?{w,h,depth,gl_m,by}, items:[{key,result,reason,photo_no,by}], notes?}"""
    now = now or datetime.now
    sid = _sid(body)
    sign_id = f"fw-{sid[:8]}"
    old = ledger.get_sign(sign_id)
    if old is not None:
        if not (old.draft_by or "").startswith(BY):
            raise SubmitError(409, f"看板 {sign_id} は別の物で使われています")
        return {"sign_id": sign_id, "site_id": old.site_id, "bundle_id": old.source_bundle, "status": old.status,
                "existed": True, "photos": len(old.photos), "items": len(old.items)}

    address = _text(body.get("address"), "address", 200)
    if not address:
        raise SubmitError(400, "address(住所)が要ります")
    kind = body.get("kind")
    if kind not in (None, "", "unknown", *KIND_TYPE):
        raise SubmitError(400, f"kind は {' / '.join(KIND_TYPE)} / unknown のどれか: {kind!r}")
    muni = _text(body.get("municipality"), "municipality", 40)
    lat, lng = _num(body.get("lat"), "lat", -90, 90), _num(body.get("lng"), "lng", -180, 180)

    # 写真: 端末が「送った」と言う物が全部置いてあるか
    man = body.get("photos")
    if not isinstance(man, list) or not man:
        raise SubmitError(400, "photos は [{slot, n}] の配列(1 枚以上)")
    want = []
    for x in man:
        if not isinstance(x, dict):
            raise SubmitError(400, "photos の要素は {slot, n}")
        want.append(_slot(x))
    want = sorted(set(want), key=lambda sn: (SLOTS.index(sn[0]), sn[1]))
    missing = [{"slot": s, "n": n} for s, n in want if _file(data_dir, sid, s, n) is None]
    if missing:
        label = "、".join(f"{m['slot']}" + (f" {m['n']}枚目" if m["slot"] in field_draft_mod.PARTS else "") for m in missing)
        raise SubmitError(409, f"まだ届いていない写真があります: {label}", {"missing": missing})
    paths = {sn: _file(data_dir, sid, *sn) for sn in want}
    order = {sn: i + 1 for i, sn in enumerate(want)}            # (slot, n) -> 看板の写真番号(1 始まり)

    items_in = body.get("items") or []
    if not isinstance(items_in, list):
        raise SubmitError(400, "items は配列")
    items, evidence = {}, {}
    for it in items_in:
        if not isinstance(it, dict) or it.get("key") not in ITEM_KEYS:
            raise SubmitError(400, f"items の key は点検項目のキー: {it.get('key') if isinstance(it, dict) else it!r}")
        key, res = it["key"], it.get("result")
        by = it.get("by", "ai")
        if by not in ("ai", "human"):
            raise SubmitError(400, f"by は ai / human: {by!r}")
        if res in (None, UNDECIDED, ""):
            guide = "unknown"
        elif res in GUIDE:
            guide = GUIDE[res]
        else:
            raise SubmitError(400, f"result は 良好 / 経過観察 / 要改善 / 対象外 / {UNDECIDED}: {res!r}")
        part = key.rsplit("-", 1)[0]
        pn = it.get("photo_no")
        nums = []
        if pn is not None:
            if isinstance(pn, bool) or not isinstance(pn, int) or (part, pn) not in order:
                raise SubmitError(400, f"{key} の photo_no は、{part} で送った写真の番号: {pn!r}")
            nums = [order[(part, pn)]]
        elif (part, 1) in order:
            nums = [order[sn] for sn in want if sn[0] == part]
        evidence[key] = {"photos": nums, "needs_onsite": False, "guide": guide, "field": by,
                         "text": _text(it.get("reason"), "reason", 300) or ("AI 判定" if guide == "unknown" else "")}
        if guide in ("a", "b", "c"):
            items[key] = res
    sz = body.get("size") or {}
    if not isinstance(sz, dict):
        raise SubmitError(400, "size は {w,h,depth,gl_m,by}")
    w, h = _num(sz.get("w"), "size.w", 0, 100000), _num(sz.get("h"), "size.h", 0, 100000)
    depth, gl = _num(sz.get("depth"), "size.depth", 0, 100000), _num(sz.get("gl_m"), "size.gl_m", -1, 100)
    if sz.get("by") not in (None, "ai", "human"):
        raise SubmitError(400, f"size.by は ai / human: {sz.get('by')!r}")

    # ---- ここから台帳に書く ----
    site = _find_site(ledger, address)
    created_site = site is None
    if site is None:
        site = Site(id=f"field-{sid[:8]}", name=address, address=address, lat=lat, lng=lng, municipality=muni)
        ledger.upsert_site(site)
    bsha = hashlib.sha256(f"field:{sid}".encode()).hexdigest()
    bundle = ledger.find_bundle_by_sha(bsha)
    bid = bundle["id"] if bundle else ledger.add_bundle(f"field://{sid}", bsha, pages=0)
    photos = []
    for sn in want:
        p = paths[sn]
        with Image.open(p) as im:
            wpx, hpx = im.size
        ledger.upsert_page(bid, order[sn], kind="写真", kind_basis="現場ウィザードで撮影", image_path=ledger.rel_path(p),
                           image_source="field", width=wpx, height=hpx)
        ledger.set_page_tag(bid, order[sn], SLOT_TAG.get(sn[0], sn[0]))
        photos.append(ledger.rel_path(p))
    ledger.set_bundle(bid, status="pages_registered", pages=len(want), site_id=site.id)

    s = Sign(id=sign_id, status="draft", draft_by=f"{BY}:{sid[:4]}")
    s.name = _text(body.get("name"), "name", 80) or (f"{KIND_TYPE[kind]}看板" if kind in KIND_TYPE else "看板")
    s.site_id, s.address = site.id, address
    s.municipality = muni or site.municipality
    s.lat, s.lng = lat, lng
    s.type = KIND_TYPE.get(kind)
    s.photos, s.source_bundle = photos, bid
    s.items, s.evidence = items, evidence
    s.remark = _text(body.get("notes"), "notes", 300)
    if w and h:
        s.size_w, s.size_h = round(w), round(h)
        s.size_source = "estimated" if sz.get("by") == "ai" else "measured"
        s.size_by, s.size_at = BY, f"{now():%Y-%m-%d}"
        s.size_d = round(depth) if depth else None
        if gl is not None:
            s.gl_mm, s.gl_edge = round(gl * 1000), GL_EDGE
            s.height_m = round(gl + s.size_h / 1000, 2)
    ledger.upsert_sign(s)
    for key, res in items.items():
        ledger.add_correction(sign_id, key, None, res, BY)
    return {"sign_id": sign_id, "site_id": site.id, "site_created": created_site, "bundle_id": bid, "status": "draft",
            "existed": False, "photos": len(photos), "items": len(items)}
