"""ガイド付き点検: LINE の誘導どおりに写真を撮り、a/b/c を押すだけで点検の下書きができる。

有資格者の今の使い方(写真と用紙を送るだけ)はそのまま残り、これは別の入口。確定は従来どおり有資格者(Web の確認画面)。
1ユーザーにつき1つの進行を <inbox>/<userId>/guide.json に持つ(再起動しても続く)。API は呼ばない(文面は固定)。

流れ(step):
  site      今日の現場(位置か現場が分かっていれば飛ばす)
  sign      看板の名前か番号(テキスト1通)
  overview  全景を1枚(必須)
  photo     部位の写真 1〜3枚(0枚なら「この部位は無い」)。3枚で自動的に次へ
  ask       その部位の項目を1項目ずつ a 良好 / b 経過観察 / c 要改善 / 分からない / 対象外
  done      この看板は終わり → 次の看板 / 終了
部位の順と項目の原文は ledger/items.py のまま(6部位 × 3項目前後、計18項目)。
途中の自由な文は「戻る」「飛ばす」「やめる」だけ命令。ほかはメモ(messages.jsonl)に残す。

台帳への書き込みは sink(guide_ledger.LedgerSink)の仕事で、ここは進行と文面だけを持つ。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlencode

from ...ledger.items import _ITEMS
from .session import safe_name

START_TEXT = "点検開始"
CMD_BACK, CMD_SKIP, CMD_QUIT = "戻る", "飛ばす", "やめる"
COMMANDS = (CMD_BACK, CMD_SKIP, CMD_QUIT)
MAX_PART_PHOTOS = 3
SEEN_MAX = 20

# 回答の記号 → 台帳の区分。unknown / na は items に入れない(出どころは evidence に残る)
ANSWER_RESULT = {"a": "良好", "b": "経過観察", "c": "要改善"}
ANSWER_LABEL = {"a": "a 良好", "b": "b 経過観察", "c": "c 要改善", "unknown": "分からない", "na": "対象外"}
ANSWERS = tuple(ANSWER_LABEL)


def _parts() -> list[tuple[str, list[tuple[str, str]]]]:
    out: list[tuple[str, list[tuple[str, str]]]] = []
    for g, n, t in _ITEMS:
        if not out or out[-1][0] != g:
            out.append((g, []))
        out[-1][1].append((f"{g}-{n}", t))
    return out


PARTS = _parts()  # [(部位, [(項目キー, 項目の原文)])] 6部位


@dataclass
class Say:
    """LINE に返す1通。quick は quickReply.items。"""
    text: str
    quick: list[dict] | None = None


@dataclass
class GuideState:
    step: str = "site"
    part: int = 0                 # PARTS の何番目か
    item: int = 0                 # その部位の何項目目か
    photos: int = 0               # 今の部位の写真の枚数
    site_id: str | None = None
    sign_id: str | None = None
    name: str | None = None
    answers: dict[str, str] = field(default_factory=dict)  # 項目キー -> a/b/c/unknown/na
    signs_done: int = 0
    started: str = ""
    seen: list[str] = field(default_factory=list)          # 処理済みのテキストID(再送で名前を二重に受けない)


class Sink(Protocol):
    """進行が台帳に書く時の窓口。既定は何もしない。"""
    def sign_named(self, user_id: str, st: GuideState) -> None: ...
    def photo(self, user_id: str, st: GuideState, tag: str, path, bundle_id: int, page_no: int) -> None: ...
    def answer(self, user_id: str, st: GuideState, key: str, ans: str) -> None: ...


class NullSink:
    def sign_named(self, user_id, st): pass
    def photo(self, user_id, st, tag, path, bundle_id, page_no): pass
    def answer(self, user_id, st, key, ans): pass


def qr(label: str, **data) -> dict:
    return {"type": "action", "action": {"type": "postback", "label": label, "displayText": label,
                                         "data": urlencode({"act": "guide", **data})}}


def site_choices(jobs, other: dict) -> list[dict]:
    """現場の選択(案件ごとに act=guide&site=)。最後に既存の「その他(現在地を送る)」(act=other)を付ける。"""
    from .reply import _clip
    return [qr(_clip(j.site_name), site=j.id) for j in jobs] + [other]


class Guide:
    def __init__(self, inbox: str | Path, sink: Sink | None = None,
                 site_choices: Callable[[], list[dict]] | None = None):
        self.inbox = Path(inbox).resolve()
        self.sink: Sink = sink or NullSink()
        self.site_choices = site_choices or (lambda: [])

    # ---- 保存 ----
    def _path(self, user_id: str) -> Path:
        return self.inbox / safe_name(user_id) / "guide.json"

    def load(self, user_id: str) -> GuideState | None:
        try:
            return GuideState(**json.loads(self._path(user_id).read_text(encoding="utf-8")))
        except (OSError, ValueError, TypeError):
            return None

    def save(self, user_id: str, st: GuideState) -> None:
        p = self._path(user_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        st.seen = st.seen[-SEEN_MAX:]
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(asdict(st), ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)

    def clear(self, user_id: str) -> None:
        self._path(user_id).unlink(missing_ok=True)

    def active(self, user_id: str) -> bool:
        return self.load(user_id) is not None

    # ---- 文面 ----
    def prompt(self, st: GuideState) -> Say:
        """今のステップの問い(やり直し・戻る・メモの後の再掲に使う)。"""
        if st.step == "site":
            return Say("今日の現場はどれですか", self.site_choices())
        if st.step == "sign":
            return Say("看板の名前か番号を送ってください(例: ① 正面の袖看板)")
        if st.step == "overview":
            return Say("全景を1枚撮ってください")
        if st.step == "photo":
            part = PARTS[st.part][0]
            if st.photos == 0:
                return Say(f"{part}を撮ってください(近くで1〜3枚)", [qr("この部位は無い", nopart=1, p=st.part)])
            return Say(f"{part} {st.photos}枚\n続けて撮るか、「次へ」を押してください", [qr("次へ", next=1, p=st.part)])
        if st.step == "ask":
            part, items = PARTS[st.part]
            key, text = items[st.item]
            quick = [qr(label, ans=a, item=key) for a, label in ANSWER_LABEL.items()]
            return Say(f"{part} {st.item + 1}/{len(items)}\n{text}", quick)
        return Say("この看板は終わりです。次の看板を始めますか", [qr("次の看板", more=1), qr("終了", end=1)])

    @staticmethod
    def _with(prefix: str, say: Say) -> Say:
        return Say(f"{prefix}\n{say.text}", say.quick)

    # ---- 進行 ----
    def start(self, user_id: str, now, site_id: str | None = None) -> Say:
        """始める。位置か現場が分かっていれば現場を飛ばす。進行中なら今の問いに戻す。"""
        st = self.load(user_id)
        if st is None:
            st = GuideState(step="sign" if site_id else "site", site_id=site_id, started=now.isoformat())
            self.save(user_id, st)
        return self.prompt(st)

    def choose_site(self, user_id: str, site_id: str, site_name: str) -> Say | None:
        st = self.load(user_id)
        if st is None or st.step != "site":
            return None if st is None else self.prompt(st)
        st.site_id, st.step = site_id, "sign"
        self.save(user_id, st)
        return self._with(f"{site_name}で受け付けました", self.prompt(st))

    def location_given(self, user_id: str, site_id: str) -> Say | None:
        """現場の選択を待っている間に位置が届いた。"""
        st = self.load(user_id)
        if st is None or st.step != "site":
            return None
        st.site_id, st.step = site_id, "sign"
        self.save(user_id, st)
        return self._with("位置を受け取りました", self.prompt(st))

    def text(self, user_id: str, text: str, message_id: str) -> tuple[str, Say] | None:
        """進行中のテキスト。戻り値 (種別, 返事): command(命令)/ name(看板の名前)/ memo(メモ。呼び出し側が記録する)。進行が無ければ None。"""
        st = self.load(user_id)
        if st is None:
            return None
        t = text.strip()
        if t in COMMANDS:
            return "command", self._command(user_id, st, t)
        if message_id in st.seen:
            return "name", self.prompt(st)  # webhook の再送。二重に受けない
        if st.step == "sign":
            st.name = t
            st.seen.append(message_id)
            st.step = "overview"
            self.sink.sign_named(user_id, st)
            self.save(user_id, st)
            return "name", self.prompt(st)
        return "memo", self._with("メモを受け取りました", self.prompt(st))

    def _command(self, user_id: str, st: GuideState, cmd: str) -> Say:
        if cmd == CMD_QUIT:
            self.clear(user_id)
            return Say("ガイドをやめました。ここまでの分は台帳に残っています")
        if cmd == CMD_BACK:
            if not self._back(st):
                return self._with("これより前はありません", self.prompt(st))
        else:  # 飛ばす
            if st.step in ("site", "sign", "overview"):
                return self._with("ここは飛ばせません", self.prompt(st))
            if st.step == "done":
                return self.prompt(st)
            self._skip(user_id, st)
        self.save(user_id, st)
        return self.prompt(st)

    def _back(self, st: GuideState) -> bool:
        if st.step == "done":
            st.step, st.part = "ask", len(PARTS) - 1
            st.item = len(PARTS[st.part][1]) - 1
        elif st.step == "ask":
            if st.item > 0:
                st.item -= 1
            else:
                st.step = "photo"
        elif st.step == "photo":
            if st.part == 0:
                st.step = "overview"
            else:
                st.step, st.part = "ask", st.part - 1
                st.item = len(PARTS[st.part][1]) - 1
        elif st.step == "overview":
            st.step = "sign"
        elif st.step == "sign":
            st.step = "site"
        else:
            return False
        return True

    def _skip(self, user_id: str, st: GuideState) -> None:
        if st.step == "photo":  # 写真なしで項目へ進む
            st.step, st.item = "ask", 0
        else:  # ask: 飛ばした項目は「分からない」と同じ(あとで写真から下書き)
            self._answer(user_id, st, "unknown")

    def _answer(self, user_id: str, st: GuideState, ans: str) -> None:
        part, items = PARTS[st.part]
        key = items[st.item][0]
        st.answers[key] = ans
        self.sink.answer(user_id, st, key, ans)
        if st.item + 1 < len(items):
            st.item += 1
        else:
            self._next_part(st)

    def _next_part(self, st: GuideState) -> None:
        st.part += 1
        st.item = 0
        st.photos = 0
        st.step = "photo" if st.part < len(PARTS) else "done"
        if st.step == "done":
            st.part = len(PARTS) - 1

    def photo(self, user_id: str, bundle_id: int, page_no: int, path, now) -> Say:
        """写真が束に入った後に呼ぶ。進行に応じた部位の印(tag)を台帳に付け、次の問いを返す。"""
        st = self.load(user_id)
        if st is None:
            raise LookupError("ガイドが進行していない")
        if st.step == "overview":
            self.sink.photo(user_id, st, "overview", path, bundle_id, page_no)
            st.step, st.part, st.photos = "photo", 0, 0
            self.save(user_id, st)
            return self._with("全景を受け取りました", self.prompt(st))
        if st.step in ("photo", "ask"):
            self.sink.photo(user_id, st, PARTS[st.part][0], path, bundle_id, page_no)
            st.photos += 1
            if st.step == "photo" and st.photos >= MAX_PART_PHOTOS:
                st.step, st.item = "ask", 0
                self.save(user_id, st)
                return self._with(f"{PARTS[st.part][0]} {st.photos}枚", self.prompt(st))
            self.save(user_id, st)
            return self.prompt(st)
        return self.prompt(st)  # site / sign / done の間の写真は束に残るだけ。問いを出し直す

    def postback(self, user_id: str, q: dict[str, str]) -> Say | None:
        """act=guide の postback。古いボタン(今の問いと合わない)は押しても進まず、今の問いを出し直す。"""
        st = self.load(user_id)
        if st is None:
            return Say("ガイドは始まっていません。メニューの「ガイド付き点検」から始めてください")
        if "end" in q:
            self.clear(user_id)
            return Say("終了しました")
        if "more" in q:
            if st.step != "done":
                return self.prompt(st)
            site = st.site_id
            st = GuideState(step="sign", site_id=site, signs_done=st.signs_done + 1, started=st.started)
            self.save(user_id, st)
            return self.prompt(st)
        if "ans" in q:
            part, items = PARTS[st.part]
            if st.step != "ask" or q.get("ans") not in ANSWERS or q.get("item") != items[st.item][0]:
                return self.prompt(st)
            self._answer(user_id, st, q["ans"])
            self.save(user_id, st)
            return self.prompt(st)
        if "next" in q or "nopart" in q:
            if st.step != "photo" or q.get("p") != str(st.part):
                return self.prompt(st)
            if "nopart" in q:
                if st.photos:
                    return self.prompt(st)
                for key, _ in PARTS[st.part][1]:
                    st.answers[key] = "na"
                    self.sink.answer(user_id, st, key, "na")
                self._next_part(st)
            elif st.photos:
                st.step, st.item = "ask", 0
            else:
                return self.prompt(st)
            self.save(user_id, st)
            return self.prompt(st)
        return self.prompt(st)
