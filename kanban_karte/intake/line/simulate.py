"""LINE が無くても検証できる模擬 webhook。署名つきの配信を作って送る。

画像の中身は本物の LINE ならサーバが content エンドポイントから取るが、模擬では content_dir に
<メッセージID>.<拡張子> で置き、DirContentClient(LineClient の差し替え)がそこから読む。返信は送らず集める。
"""
from __future__ import annotations

import json
import shutil
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

from .webhook import WEBHOOK_PATH, sign

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".webp", ".heic"}


class DirContentClient:
    """LineClient の差し替え。画像は content_dir から読み、返信は replies に溜める(外部には送らない)。"""

    def __init__(self, content_dir: str | Path, echo: bool = False):
        self.dir = Path(content_dir)
        self.replies: list[tuple[str, str]] = []
        self.echo = echo

    def get_content(self, message_id: str) -> bytes:
        for p in self.dir.glob(f"{message_id}.*"):
            return p.read_bytes()
        raise FileNotFoundError(f"模擬の画像が無い: {message_id}")

    def reply(self, reply_token: str, text: str, quick_replies=None, button=None) -> None:
        self.replies.append((reply_token, text))
        if self.echo:
            print(f"[返信 {reply_token}] {text}", flush=True)
            if quick_replies:
                print("  [" + " / ".join(i["action"]["label"] for i in quick_replies) + "]", flush=True)


@dataclass
class Plan:
    deliveries: list[bytes] = field(default_factory=list)
    message_ids: list[str] = field(default_factory=list)
    stats: dict = field(default_factory=dict)


def _event(user_id: str, token: str, message: dict, ts: int) -> dict:
    return {"type": "message", "mode": "active", "timestamp": ts, "replyToken": token,
            "source": {"type": "user", "userId": user_id}, "message": message}


def build_deliveries(images: list[Path], content_dir: str | Path, *, user_id: str = "Usimulated0000000000000000000000",
                     run_id: str | None = None, one_by_one: bool = False, text: str | None = None,
                     location: tuple[float, float] | None = None) -> Plan:
    """images を content_dir にメッセージID名で置き、webhook の本文(JSON のバイト列)を作る。"""
    run_id = run_id or str(int(time.time()))
    cdir = Path(content_dir)
    cdir.mkdir(parents=True, exist_ok=True)
    ts = int(time.time() * 1000)
    events: list[dict] = []
    plan = Plan()
    for i, img in enumerate(images, 1):
        mid = f"sim{run_id}x{i:03d}"
        shutil.copyfile(img, cdir / f"{mid}{img.suffix.lower()}")
        plan.message_ids.append(mid)
        events.append(_event(user_id, f"tok-{mid}", {"type": "image", "id": mid,
                                                    "contentProvider": {"type": "line"}}, ts + i))
    if text:
        mid = f"sim{run_id}t001"
        events.append(_event(user_id, f"tok-{mid}", {"type": "text", "id": mid, "text": text}, ts + 900))
    if location:
        mid = f"sim{run_id}l001"
        events.append(_event(user_id, f"tok-{mid}", {"type": "location", "id": mid, "title": "模擬の位置",
                                                    "address": "", "latitude": location[0],
                                                    "longitude": location[1]}, ts + 901))
    groups = [[e] for e in events] if one_by_one else [events]
    for g in groups:
        if g:
            plan.deliveries.append(json.dumps({"destination": "Usimulated", "events": g},
                                              ensure_ascii=False).encode("utf-8"))
    return plan


def _postback(user_id: str, token: str, data: str, ts: int) -> dict:
    return {"type": "postback", "mode": "active", "timestamp": ts, "replyToken": token,
            "source": {"type": "user", "userId": user_id}, "postback": {"data": data}}


def build_guide_deliveries(plan_file: str | Path, content_dir: str | Path, *,
                           user_id: str = "Usimulated0000000000000000000000", run_id: str | None = None) -> Plan:
    """ガイド付き点検の台本(JSON)から、人が押す・送る順の配信を作る(1イベント=1配信。LINE の実際の返事の数と同じ)。

    台本: {"job": "J-001", "signs": [{"name": "① 正面の袖看板", "overview": "photos/a.png", "front": ["photos/f.png"], "kind": "proj",
           "parts": [{"photos": ["photos/b.png"], "answers": ["a", "b", "unknown"]}, ...6部位]}]}
    front が空(か無い)なら「正面から撮れない」を押す。kind は shoot.KINDS のキー(無ければ「その他」)。
    写真は台本のあるフォルダ基準。photos が空の部位は「この部位は無い」を押す(answers は書かない)。
    看板の "measure" は寸法の実測(measure() の docstring。無ければ "skip")。
    Plan.stats に写真の枚数・押す回数・送る文の数。
    """
    from urllib.parse import urlencode
    from . import measure as M
    from . import shoot as S
    from .guide import MAX_DRAWING_PHOTOS, MAX_PART_PHOTOS, PARTS

    plan_file = Path(plan_file)
    doc = json.loads(plan_file.read_text(encoding="utf-8"))
    run_id = run_id or str(int(time.time()))
    cdir = Path(content_dir)
    cdir.mkdir(parents=True, exist_ok=True)
    ts0 = int(time.time() * 1000)
    out = Plan()
    out.stats = {"photos": 0, "taps": 0, "texts": 0}
    n = [0]

    def push(ev_fn):
        n[0] += 1
        out.deliveries.append(json.dumps({"destination": "Usimulated", "events": [ev_fn(f"tok-g{run_id}-{n[0]:03d}", ts0 + n[0])]},
                                         ensure_ascii=False).encode("utf-8"))

    def tap(**data):
        out.stats["taps"] += 1
        push(lambda tok, ts: _postback(user_id, tok, urlencode({"act": "guide", **data}), ts))

    def text(t):
        out.stats["texts"] += 1
        mid = f"sim{run_id}g{n[0] + 1:03d}"
        push(lambda tok, ts: _event(user_id, tok, {"type": "text", "id": mid, "text": t}, ts))

    def image(rel):
        src = plan_file.parent / rel
        mid = f"sim{run_id}g{n[0] + 1:03d}"
        shutil.copyfile(src, cdir / f"{mid}{src.suffix.lower()}")
        out.message_ids.append(mid)
        out.stats["photos"] += 1
        push(lambda tok, ts: _event(user_id, tok, {"type": "image", "id": mid, "contentProvider": {"type": "line"}}, ts))

    def measure(spec):
        """寸法の実測。"skip"=最初の問いで飛ばす / "inherit"=台帳に寸法がある看板(問いは出ない)/
        {"drawing": [図面の画像]}=図面で送る / dict=測る
        {"w": "3.6m", "h": "900", "d": "300"(無ければ「分からない」), "gl": "2500"(無ければ「分からない」),
         "mount": "アンカーボルト"(一覧に無い文は「その他」で文字入力), "post": "□50"(無ければ「無し」)}"""
        if spec == "inherit":
            return
        if spec == "skip":
            return tap(m="skip")
        if "drawing" in spec:
            tap(m="draw")
            for rel in spec["drawing"][:MAX_DRAWING_PHOTOS]:
                image(rel)
            if len(spec["drawing"]) < MAX_DRAWING_PHOTOS:
                tap(m="drawn")
            return
        tap(m="go")
        for k in ("w", "h", "d", "gl"):
            if spec.get(k) is None:
                tap(m="glx" if k == "gl" else "dx")
                continue
            text(spec[k])
            r = (M.parse_length(spec[k], lo=0, allow_zero=True) if k == "gl"
                 else M.parse_length(spec[k], lo=M.DEPTH_MIN_MM, hi=M.DEPTH_MAX_MM) if k == "d" else M.parse_length(spec[k]))
            if r.confirm:
                tap(m="conf")  # 単位の無い数字は確認が返る
        mount = spec.get("mount") or M.UNKNOWN
        if mount in M.MOUNTS:
            tap(mount=M.MOUNTS.index(mount))
        else:
            tap(mount=M.MOUNTS.index(M.MOUNT_OTHER))
            text(mount)
        text(spec["post"]) if spec.get("post") else tap(m="nopost")

    tap(start=1)
    if doc.get("job"):
        tap(site=doc["job"])
    for si, sg in enumerate(doc["signs"]):
        text(sg["name"])
        image(sg["overview"])
        fronts = sg.get("front") or []
        for rel in fronts[:S.MAX_FRONT_PHOTOS]:
            image(rel)
        if not fronts:
            tap(front="skip")
        elif len(fronts) < S.MAX_FRONT_PHOTOS:
            tap(front="next")
        tap(kind=sg.get("kind") or "other")
        measure(sg.get("measure", "skip"))
        for pi, (part, items) in enumerate(PARTS):
            spec = sg["parts"][pi]
            photos = spec.get("photos", [])
            if not photos:
                tap(nopart=1, p=pi)
                continue
            for rel in photos[:MAX_PART_PHOTOS]:
                image(rel)
            if len(photos) < MAX_PART_PHOTOS:
                tap(next=1, p=pi)
            for (key, _), ans in zip(items, spec["answers"]):
                tap(ans=ans, item=key)
        tap(more=1) if si + 1 < len(doc["signs"]) else tap(end=1)
    return out


def post(url: str, secret: str, body: bytes, signature: str | None = None) -> int:
    """署名つきで POST して HTTP ステータスを返す。signature を渡すとその値を使う(誤署名の試験用)。"""
    req = urllib.request.Request(url, data=body, headers={
        "Content-Type": "application/json", "X-Line-Signature": signature or sign(secret, body)})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status
    except urllib.error.HTTPError as e:
        e.close()
        return e.code


def list_images(d: str | Path) -> list[Path]:
    return sorted(p for p in Path(d).iterdir() if p.suffix.lower() in IMAGE_EXT)


def webhook_url(base: str) -> str:
    return base if base.rstrip("/").endswith(WEBHOOK_PATH) else base.rstrip("/") + WEBHOOK_PATH
