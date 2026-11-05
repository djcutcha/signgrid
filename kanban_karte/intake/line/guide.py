"""ガイド付き点検: LINE の誘導どおりに写真を撮り、a/b/c を押すだけで点検の下書きができる。

有資格者の今の使い方(写真と用紙を送るだけ)はそのまま残り、これは別の入口。確定は従来どおり有資格者(Web の確認画面)。
1ユーザーにつき1つの進行を <inbox>/<userId>/guide.json に持つ(再起動しても続く)。API は呼ばない(文面は固定)。

流れ(step):
  site      今日の現場(位置か現場が分かっていれば飛ばす)
  sign      看板の名前か番号(テキスト1通)
  overview  全景を1枚(必須)
  front     表示面を正面から(面が複数あれば各面、4枚まで。撮れなければ「正面から撮れない」)
  lighting  照明(内照式 / 外照式 / 無し / 分からない)。実測の工程では取付方法の次。実測を通らない時はこの1問だけ。「無し」なら照明装置の部位は対象外にして問わない
  kind      看板の種類(ポール / 自立・野立て / 壁面 / 突出(袖)/ 屋上 / その他)。部位の問いに撮る場所を添える(shoot.py)
  measure   寸法の実測(台帳にその看板の寸法が無い時だけ。初回。最初の問いで「飛ばす」で通せる)
            [測る / 図面で送る / 飛ばす]。測る= 幅 → 高さ → 厚み(任意)→ GL(看板の下端まで)→ 取付方法 → 支柱
            図面で送る= 図面の写真・画像を 1〜3枚(数字の問いは出さない。寸法は図面から事務所で入れる)
  photo     部位の写真 1〜3枚(0枚なら「この部位は無い」)。3枚で自動的に次へ
  ask       その部位の項目を1項目ずつ a 良好 / b 経過観察 / c 要改善 / AI 判定 / 対象外
  done      この看板は終わり → 次の看板 / 終了
部位の順と項目の原文は ledger/items.py のまま(6部位 × 3項目前後、計18項目)。
途中の自由な文は「戻る」「飛ばす」「やめる」だけ命令。ほかはメモ(messages.jsonl)に残す。
どの問いにもクイックリプライの最後に「戻る」を付ける(現場の選択だけは戻る先が無い)。写真の工程で戻ると直前の写真の tag を外す(ファイルは消さない)。
終了(end=1)は「まとめ」を返す(summary_text)。長ければ 5000 字ごとに切って 1 回の返信に複数通で返す。

台帳への書き込みは sink(guide_ledger.LedgerSink)の仕事で、ここは進行と文面だけを持つ。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Protocol
from urllib.parse import urlencode

from ...ledger.db import FIGURE_TAG
from ...ledger.items import _ITEMS
from . import measure as M
from . import shoot as S
from .session import safe_name
from .shoot import FRONT_TAG

START_TEXT = "点検開始"
CMD_BACK, CMD_SKIP, CMD_QUIT = "戻る", "飛ばす", "やめる"
COMMANDS = (CMD_BACK, CMD_SKIP, CMD_QUIT)
MAX_PART_PHOTOS = 3
MAX_DRAWING_PHOTOS = 3
MEASURE_SUBS = ("ask", "w", "h", "d", "gl", "mount", "light", "post")  # 実測工程の中の順(mount_text は mount の続き、drawing は ask の分かれ)
CONFIRM_WORDS = ("はい", "ok", "OK", "合ってます", "合っています")
SEEN_MAX = 20
TEXT_MAX = 5000         # LINE のテキスト 1 通の上限(字)
REPLY_MAX_MESSAGES = 5  # 1 回の返信に載せられる通数

# 回答の記号 → 台帳の区分。unknown / na は items に入れない(出どころは evidence に残る)
ANSWER_RESULT = {"a": "良好", "b": "経過観察", "c": "要改善"}
ANSWER_LABEL = {"a": "a 良好", "b": "b 経過観察", "c": "c 要改善", "unknown": "AI 判定", "na": "対象外"}
ANSWERS = tuple(ANSWER_LABEL)
LIGHT_PART = S.LIGHTING_PART


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
    more: list[str] | None = None  # 2 通目以降(長いまとめ。同じ返信に続けて載せる)


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
    kind: str | None = None            # 看板の種類(shoot.KINDS のキー)。front_skipped は正面を撮れなかった
    front_skipped: bool = False
    # 寸法の実測(step=measure)。m の値は mm(w / h / d / gl)、mount、post。gl・d が無い=分からない。drawing=図面で送った
    m_sub: str = "ask"
    m: dict = field(default_factory=dict)
    m_photos: int = 0
    m_pending: dict | None = None      # 単位が曖昧な数字の確認待ち {"f": "w", "mm": 3600}
    m_page: int = 0                    # 取付方法のページ(0 / 1)
    lighting: str | None = None        # 照明(measure.LIGHTING_KEYS)。None=まだ聞いていない
    sign_ids: list[str] = field(default_factory=list)  # この進行で扱った看板(まとめ・確定の対象)
    measure_shown: bool = False        # この看板で実測工程を出したか(戻るの行き先)
    size_known: dict | None = None     # 台帳に寸法がある看板: {"source", "at", "by"}。実測工程は出さず、最後に1行知らせる


class Sink(Protocol):
    """進行が台帳に書く時の窓口。既定は何もしない。"""
    def sign_named(self, user_id: str, st: GuideState) -> None: ...
    def photo(self, user_id: str, st: GuideState, tag: str, path, bundle_id: int, page_no: int) -> None: ...
    def answer(self, user_id: str, st: GuideState, key: str, ans: str) -> None: ...
    def known_size(self, user_id: str, st: GuideState) -> dict | None: ...
    def measure(self, user_id: str, st: GuideState) -> None: ...
    def sign_kind(self, user_id: str, st: GuideState) -> None: ...
    def lighting(self, user_id: str, st: GuideState) -> None: ...
    def unphoto(self, user_id: str, st: GuideState, tag: str) -> bool: ...
    def photo_count(self, user_id: str, st: GuideState, tag: str) -> int: ...
    def finish(self, user_id: str, st: GuideState) -> "Summary | None": ...


@dataclass
class SignRow:
    """まとめの 1 看板分(site_total の行にも使う)。"""
    name: str
    type: str | None = None
    size_w: int | None = None
    size_h: int | None = None
    size_d: int | None = None
    gl_mm: int | None = None
    photos: int = 0
    bad: int = 0       # 要改善
    watch: int = 0     # 経過観察
    good: int = 0      # 良好
    unknown: int = 0   # AI 判定(現場が答えず、写真から下書きする項目)


@dataclass
class Summary:
    site_name: str | None
    rows: list[SignRow]
    site_total: SignRow | None = None   # その現場の今日の看板すべての合計
    site_signs: int = 0
    ai_items: int = 0                   # この進行の看板の AI 判定の項目の数
    auto_draft: bool = False            # 写真からの下書きを自動で走らせたか(LINE_GUIDE_AUTO_DRAFT=1 かつ鍵あり)


class NullSink:
    def sign_named(self, user_id, st): pass
    def photo(self, user_id, st, tag, path, bundle_id, page_no): pass
    def answer(self, user_id, st, key, ans): pass
    def known_size(self, user_id, st): return None
    def measure(self, user_id, st): pass
    def sign_kind(self, user_id, st): pass
    def lighting(self, user_id, st): pass
    def unphoto(self, user_id, st, tag): return False
    def photo_count(self, user_id, st, tag): return 0
    def finish(self, user_id, st): return None


def qr(label: str, **data) -> dict:
    return {"type": "action", "action": {"type": "postback", "label": label, "displayText": label,
                                         "data": urlencode({"act": "guide", **data})}}


def _pos(st: GuideState) -> str:
    """今の問いの位置。「戻る」のボタンに持たせ、古いボタンで余計に戻らないようにする。"""
    return f"{st.step}.{st.m_sub if st.step == 'measure' else ''}.{st.part}.{st.item}"


def _with_back(st: GuideState, say: Say) -> Say:
    """どの問いにも最後に「戻る」を付ける(現場の選択は戻る先が無いので付けない)。"""
    if st.step == "site":
        return say
    return Say(say.text, (say.quick or []) + [qr(CMD_BACK, back=1, at=_pos(st))], say.more)


def split_text(text: str, limit: int = TEXT_MAX) -> list[str]:
    """LINE のテキスト 1 通の上限(5000 字)で切る。改行の所で切り、1 行が上限を超える時だけ字数で切る。"""
    out: list[str] = []
    cur = ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            out.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    out.append(cur)
    return out


def _dims(r: SignRow) -> str:
    if not (r.size_w and r.size_h):
        return "寸法 未入力"
    d = f"W{r.size_w}×H{r.size_h}" + (f"×D{r.size_d}" if r.size_d else "") + " mm"
    return d + (f"、GL {r.gl_mm} mm" if r.gl_mm is not None else "")


def _counts(r: SignRow) -> str:
    return f"要改善 {r.bad} / 経過観察 {r.watch} / 良好 {r.good} / AI 判定 {r.unknown}"


def summary_text(sm: Summary) -> Say:
    """終了のまとめ。URL は入れない(スマホから台帳に届かない)。長ければ 5000 字で切った続きを more に持つ。"""
    lines = [f"点検のまとめ{f'({sm.site_name})' if sm.site_name else ''}"]
    for r in sm.rows:
        lines += ["", f"■ {r.name}", f"{r.type or '種類 未選択'} / {_dims(r)} / 写真 {r.photos} 枚", _counts(r)]
    lines.append("")
    if sm.ai_items:
        lines.append(f"AI 判定 {sm.ai_items} 項目は写真から下書き中(数分)。結果は PC の台帳で" if sm.auto_draft
                     else "AI 判定の項目は PC で下書きします")
    lines += ["下書きとして台帳に保存しました。確認と確定は PC の台帳で行います",
              "カルテと様式は PC の台帳で確認・訂正できます", "次の看板はメニューの「ガイド付き点検」から"]
    if sm.site_total is not None:
        lines.append(f"この現場の今日の合計: 看板 {sm.site_signs} 基 / {_counts(sm.site_total)}")
    lines.append("お疲れさまでした")
    parts = split_text("\n".join(lines))
    if len(parts) > REPLY_MAX_MESSAGES:  # 1 回の返信は 5 通まで。超えた分は台帳で
        parts = parts[:REPLY_MAX_MESSAGES - 1] + ["(続きは PC の台帳で確認できます)\nお疲れさまでした"]
    return Say(parts[0], None, parts[1:] or None)


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
        """今のステップの問い(やり直し・戻る・メモの後の再掲に使う)。どの問いにも「戻る」が付く。"""
        return _with_back(st, self._prompt(st))

    def _prompt(self, st: GuideState) -> Say:
        if st.step == "site":
            return Say("今日の現場はどれですか", self.site_choices())
        if st.step == "sign":
            return Say("看板の名前か番号を送ってください(例: ① 正面の袖看板)")
        if st.step == "overview":
            return Say("全景を1枚撮ってください")
        if st.step == "front":
            if st.photos == 0:
                return Say("表示面を正面から撮ってください(文字や絵が読めるように。面が複数あれば各面)",
                           [qr("正面から撮れない", front="skip")])
            return Say(f"正面 {st.photos}枚\n別の面があれば続けて撮るか、「次へ」を押してください", [qr("次へ", front="next")])
        if st.step == "kind":
            return Say("看板の種類を選んでください", [qr(label, kind=k) for k, label, _ in S.KINDS])
        if st.step == "measure":
            return self._measure_prompt(st)
        if st.step == "lighting":
            return self._lighting_prompt()
        if st.step == "photo":
            part = PARTS[st.part][0]
            if st.photos == 0:
                hint = S.shoot_hint(part, st.kind, front_taken=not st.front_skipped, lighting=st.lighting)
                return Say(f"{part}を撮ってください(近くで1〜3枚)" + (f"\n{hint}" if hint else ""),
                           [qr("この部位は無い", nopart=1, p=st.part)])
            return Say(f"{part} {st.photos}枚\n続けて撮るか、「次へ」を押してください", [qr("次へ", next=1, p=st.part)])
        if st.step == "ask":
            part, items = PARTS[st.part]
            key, text = items[st.item]
            quick = [qr(label, ans=a, item=key) for a, label in ANSWER_LABEL.items()]
            hint = S.ASK_HINT.get(key)
            return Say(f"{part} {st.item + 1}/{len(items)}\n{text}" + (f"({hint})" if hint else ""), quick)  # 返事は3行まで
        return Say(self._done_text(st), [qr("次の看板", more=1), qr("終了", end=1)])

    @staticmethod
    def _done_text(st: GuideState) -> str:
        text = "この看板は終わりです。次の看板を始めますか"
        k = st.size_known
        if not k:
            return text
        label = {"measured": "実測", "drawing": "図面", "estimated": "推定"}.get(k.get("source"))
        at = f"({k['at']})" if k.get("at") else ""
        line = (f"寸法は前回{at}の実測を引き継ぎました" if label == "実測"
                else f"寸法は台帳の{label or ''}の値を使いました{at}。実測が要る時はカルテから入れてください")
        return f"{line}\n{text}"

    def _measure_prompt(self, st: GuideState) -> Say:
        sub = st.m_sub
        if st.m_pending:
            return Say(st.m_pending["text"], [qr("はい", m="conf"), qr("入れ直す", m="redo")])
        if sub == "ask":
            return Say("この看板の寸法を測りますか(初回の点検では、測って台帳に残します。図面があれば図面で送れます)",
                       [qr("測る", m="go"), qr("図面で送る", m="draw"), qr("飛ばす", m="skip")])
        if sub == "drawing":
            if st.m_photos == 0:
                return Say(f"図面の写真か画像を送ってください(1〜{MAX_DRAWING_PHOTOS}枚)", [qr("数字で入れる", m="go")])
            return Say(f"図面 {st.m_photos}枚\n続けて送るか、「次へ」を押してください", [qr("次へ", m="drawn")])
        if sub == "w":
            return Say("幅(mm)を送ってください(例: 3600。3.6m のように単位を付けても受けます)")
        if sub == "h":
            return Say("高さ(mm)を送ってください(例: 900)")
        if sub == "d":
            return Say("厚み(奥行き、mm)を送ってください(例: 300)。分からなければ「分からない」", [qr("分からない", m="dx")])
        if sub == "gl":
            return Say("GL(地面)から看板の下端までの高さ(mm)を送ってください(例: 2500)。分からなければ「分からない」",
                       [qr("分からない", m="glx")])
        if sub == "light":
            return self._lighting_prompt()
        if sub == "mount":
            # 13 択+「戻る」は上限(13)を超えるので 2 ページ。ボタンの番号は MOUNTS 全体での番号のまま
            lo, hi = (0, M.MOUNT_PAGE) if st.m_page == 0 else (M.MOUNT_PAGE, len(M.MOUNTS))
            quick = [qr(M.MOUNTS[i], mount=i) for i in range(lo, hi)]
            quick.append(qr("次のページ(その他・分からない)", m="mpage") if st.m_page == 0 else qr("前のページ", m="mprev"))
            return Say("取付方法を選んでください" + ("(続き)" if st.m_page else ""), quick)
        if sub == "mount_text":
            return Say(f"取付方法を文字で送ってください({M.MOUNT_TEXT_MAX}字まで)")
        return Say("支柱があれば送ってください(例: □50)。無ければ「無し」", [qr("無し", m="nopost")])

    @staticmethod
    def _lighting_prompt() -> Say:
        return Say("照明はありますか", [qr(label, light=k) for k, label, _ in M.LIGHTINGS])

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
        """進行中のテキスト。戻り値 (種別, 返事): command(命令)/ name(看板の名前)/ memo(メモ。呼び出し側が記録する)/ dup(再送)。進行が無ければ None。"""
        st = self.load(user_id)
        if st is None:
            return None
        if message_id in st.seen:
            return "dup", self.prompt(st)  # webhook の再送。二重に進めない
        st.seen.append(message_id)
        t = text.strip()
        if t in COMMANDS:
            return "command", self._command(user_id, st, t)
        if st.step == "sign":
            st.name = t
            st.step = "overview"
            self.sink.sign_named(user_id, st)
            if st.sign_id and st.sign_id not in st.sign_ids:
                st.sign_ids.append(st.sign_id)
            st.size_known = self.sink.known_size(user_id, st)
            self.save(user_id, st)
            return "name", self.prompt(st)
        if st.step == "measure":
            kind, say = self._measure_text(user_id, st, t)
            self.save(user_id, st)
            return kind, say
        if st.step == "lighting" and t in M.LIGHTING_LABEL.values():
            say = self._set_lighting(user_id, st, next(k for k, v in M.LIGHTING_LABEL.items() if v == t))
            self.save(user_id, st)
            return "lighting", say
        if st.step == "kind" and t in S.KIND_LABEL.values():
            say = self._set_kind(user_id, st, next(k for k, v in S.KIND_LABEL.items() if v == t))
            self.save(user_id, st)
            return "kind", say
        self.save(user_id, st)
        return "memo", self._with("メモを受け取りました", self.prompt(st))

    def _command(self, user_id: str, st: GuideState, cmd: str) -> Say:
        if cmd == CMD_QUIT:
            self.clear(user_id)
            return Say("ガイドをやめました。ここまでの分は台帳に残っています")
        if cmd == CMD_BACK:
            if not self._back(user_id, st):
                return self._with("これより前はありません", self.prompt(st))
        else:  # 飛ばす
            if st.step in ("site", "sign", "overview"):
                return self._with("ここは飛ばせません", self.prompt(st))
            if st.step == "lighting":  # 飛ばした照明は「分からない」と同じ
                say = self._set_lighting(user_id, st, "unknown")
                self.save(user_id, st)
                return say
            if st.step == "front":
                self._front_done(st, skipped=True)
                self.save(user_id, st)
                return self.prompt(st)
            if st.step == "kind":
                say = self._set_kind(user_id, st, None)
                self.save(user_id, st)
                return say
            if st.step == "done":
                return self.prompt(st)
            if st.step == "measure":
                prefix = self._measure_skip(user_id, st)
                self.save(user_id, st)
                return self._with(prefix, self.prompt(st)) if prefix else self.prompt(st)
            self._skip(user_id, st)
        self.save(user_id, st)
        return self.prompt(st)

    def _prev_part(self, st: GuideState) -> None:
        """前の部位の最後の項目へ。照明が「無し」の看板は、対象外にした照明装置を飛び越える。"""
        st.step, st.part = "ask", st.part - 1
        if PARTS[st.part][0] == LIGHT_PART and st.lighting == "none" and st.part > 0:
            st.part -= 1
        st.item = len(PARTS[st.part][1]) - 1

    def _untag(self, user_id: str, st: GuideState, tag: str) -> None:
        """戻って撮り直せるよう、直前の写真の tag を外す(ファイルは消さない)。"""
        self.sink.unphoto(user_id, st, tag)

    def _back(self, user_id: str, st: GuideState) -> bool:
        if st.step == "done":
            st.step, st.part = "ask", len(PARTS) - 1
            st.item = len(PARTS[st.part][1]) - 1
        elif st.step == "ask":
            if st.item > 0:
                st.item -= 1
            else:
                st.step = "photo"
                st.photos = self.sink.photo_count(user_id, st, PARTS[st.part][0]) or st.photos
        elif st.step == "measure":
            if st.m_pending:
                st.m_pending = None
            elif st.m_sub == "drawing" and st.m_photos > 0:
                self._untag(user_id, st, FIGURE_TAG)
                st.m_photos -= 1
            elif st.m_sub == "drawing":
                st.m_sub = "ask"
            elif st.m_sub == "mount_text":
                st.m_sub = "mount"
            elif st.m_sub == "mount" and st.m_page:
                st.m_page = 0
            elif st.m_sub == "ask":
                st.step = "kind"
            else:
                st.m_sub = MEASURE_SUBS[MEASURE_SUBS.index(st.m_sub) - 1]
                st.m_page = 0
        elif st.step == "lighting":
            if st.measure_shown:
                st.step, st.m_pending = "measure", None
                st.m_sub = "drawing" if st.m.get("drawing") else "ask"
            else:
                st.step = "kind"
        elif st.step == "photo":
            if st.photos > 0:
                self._untag(user_id, st, PARTS[st.part][0])
                st.photos -= 1
            elif st.part == 0 and st.m.get("w"):
                st.step, st.m_pending, st.m_sub = "measure", None, "post"
            elif st.part == 0:
                st.step = "lighting"
            else:
                self._prev_part(st)
        elif st.step == "kind":
            st.step = "front"
            st.photos = self.sink.photo_count(user_id, st, FRONT_TAG) or 0
            st.front_skipped = False
        elif st.step == "front":
            if st.photos > 0:
                self._untag(user_id, st, FRONT_TAG)
                st.photos -= 1
            else:
                self._untag(user_id, st, "overview")
                st.step = "overview"
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
        else:  # ask: 飛ばした項目は「AI 判定」と同じ(あとで写真から下書き)
            self._answer(user_id, st, "unknown")

    def _answer(self, user_id: str, st: GuideState, ans: str) -> None:
        items = PARTS[st.part][1]
        key = items[st.item][0]
        st.answers[key] = ans
        self.sink.answer(user_id, st, key, ans)
        if st.item + 1 < len(items):
            st.item += 1
        else:
            self._next_part(user_id, st)

    def _next_part(self, user_id: str, st: GuideState) -> None:
        st.part += 1
        st.item = 0
        st.photos = 0
        while st.part < len(PARTS) and PARTS[st.part][0] == LIGHT_PART and st.lighting == "none":
            for key, _ in PARTS[st.part][1]:  # 照明が無い看板: 照明装置は対象外にして問わない
                st.answers[key] = "na"
                self.sink.answer(user_id, st, key, "na")
            st.part += 1
        st.step = "photo" if st.part < len(PARTS) else "done"
        if st.step == "done":
            st.part = len(PARTS) - 1

    # ---- 寸法の実測 ----
    def _measure_advance(self, user_id: str, st: GuideState) -> None:
        """実測工程の次の問いへ。最後(写真)の次は部位へ。台帳には今ある分を書く。"""
        st.m_pending = None
        self.sink.measure(user_id, st)
        i = MEASURE_SUBS.index("mount" if st.m_sub == "mount_text" else st.m_sub) + 1
        if i < len(MEASURE_SUBS):
            st.m_sub, st.m_page = MEASURE_SUBS[i], 0
        else:
            st.step, st.part, st.photos = "photo", 0, 0

    def _measure_abandon(self, st: GuideState) -> None:
        """実測を通らずに先へ。照明の 1 問だけ聞いて部位の写真へ。"""
        st.step, st.part, st.photos, st.m_pending = "lighting", 0, 0, None

    def _measure_skip(self, user_id: str, st: GuideState) -> str:
        """「飛ばす」。最初の問いと幅・高さ(まだ揃っていない時)は実測そのものを飛ばす。それ以外はその1問だけ。"""
        if st.m_sub == "drawing":
            return self._drawing_done(st)
        if st.m_sub == "ask" or (st.m_sub in ("w", "h") and not (st.m.get("w") and st.m.get("h"))):
            self._measure_abandon(st)
            return "寸法の実測は飛ばしました"
        if st.m_sub == "d":
            st.m.pop("d", None)
        if st.m_sub == "light":  # 飛ばした照明は「分からない」と同じ
            self._set_lighting(user_id, st, "unknown")
            return ""
        self._measure_advance(user_id, st)
        return ""

    def _drawing_done(self, st: GuideState) -> str:
        """図面で送った(か、図面を送らずに飛ばした)。数字の問いは出さずに部位へ。"""
        self._measure_abandon(st)
        if st.m_photos:
            st.m["drawing"] = st.m_photos
            return f"図面 {st.m_photos}枚を受け取りました。寸法は図面から事務所で入れます"
        return "寸法の実測は飛ばしました"

    def _measure_set(self, user_id: str, st: GuideState, mm: int) -> None:
        st.m[st.m_sub] = mm
        self._measure_advance(user_id, st)

    def _measure_text(self, user_id: str, st: GuideState, t: str) -> tuple[str, Say]:
        sub = st.m_sub
        if st.m_pending and t in CONFIRM_WORDS:
            self._measure_set(user_id, st, st.m_pending["mm"])
            return "measure", self.prompt(st)
        if sub in ("w", "h", "d", "gl"):
            if sub in ("gl", "d") and t == M.UNKNOWN:
                st.m.pop(sub, None)
                self._measure_advance(user_id, st)
                return "measure", self.prompt(st)
            r = (M.parse_length(t, lo=0, hi=M.GL_MAX_MM, allow_zero=True) if sub == "gl"
                 else M.parse_length(t, lo=M.DEPTH_MIN_MM, hi=M.DEPTH_MAX_MM) if sub == "d" else M.parse_length(t))
            if r.error:
                return "measure", self._with(r.error, self.prompt(st))
            if r.confirm:
                st.m_pending = {"f": sub, "mm": r.mm, "text": r.confirm}
                return "measure", self.prompt(st)
            self._measure_set(user_id, st, r.mm)
            return "measure", self.prompt(st)
        if sub == "light" and t in M.LIGHTING_LABEL.values():
            return "measure", self._set_lighting(user_id, st, next(k for k, v in M.LIGHTING_LABEL.items() if v == t))
        if sub == "mount" and t in M.MOUNTS:
            return "measure", self._set_mount(user_id, st, t)
        if sub == "mount_text":
            if not t or len(t) > M.MOUNT_TEXT_MAX:
                return "measure", self._with(f"{M.MOUNT_TEXT_MAX}字以内で送ってください", self.prompt(st))
            st.m["mount"] = t
            self._measure_advance(user_id, st)
            return "measure", self.prompt(st)
        if sub == "post":
            post = M.parse_post(t)
            if post is None:
                return "measure", self._with(f"{M.POST_MAX}字以内で送ってください(例: □50)", self.prompt(st))
            st.m["post"] = post
            self._measure_advance(user_id, st)
            return "measure", self.prompt(st)
        return "memo", self._with("メモを受け取りました", self.prompt(st))

    def _measure_postback(self, user_id: str, st: GuideState, q: dict[str, str]) -> Say:
        """act=guide の m= / mount=。今の問いと合わない古いボタンは進めず、今の問いを出し直す。"""
        sub, act = st.m_sub, q.get("m")
        if st.step != "measure":
            return self.prompt(st)
        if st.m_pending:
            if act == "conf":
                self._measure_set(user_id, st, st.m_pending["mm"])
            elif act == "redo":
                st.m_pending = None
            self.save(user_id, st)
            return self.prompt(st)
        if sub == "mount" and act in ("mpage", "mprev"):
            st.m_page = 1 if act == "mpage" else 0
            self.save(user_id, st)
            return self.prompt(st)
        if "mount" in q and sub == "mount" and q["mount"].isdigit() and int(q["mount"]) < len(M.MOUNTS):
            say = self._set_mount(user_id, st, M.MOUNTS[int(q["mount"])])
            self.save(user_id, st)
            return say
        elif sub in ("ask", "drawing") and act == "go":
            st.m_sub = "w"
        elif sub == "ask" and act == "draw":
            st.m_sub, st.m_photos = "drawing", 0
        elif sub == "drawing" and act == "drawn":
            prefix = self._drawing_done(st)
            self.save(user_id, st)
            return self._with(prefix, self.prompt(st))
        elif sub == "ask" and act == "skip":
            self._measure_abandon(st)
            self.save(user_id, st)
            return self._with("寸法の実測は飛ばしました", self.prompt(st))
        elif sub == "gl" and act == "glx":
            st.m.pop("gl", None)
            self._measure_advance(user_id, st)
        elif sub == "d" and act == "dx":
            st.m.pop("d", None)
            self._measure_advance(user_id, st)
        elif sub == "post" and act == "nopost":
            st.m["post"] = M.NONE_POST
            self._measure_advance(user_id, st)
        self.save(user_id, st)
        return self.prompt(st)

    def _set_lighting(self, user_id: str, st: GuideState, key: str) -> Say:
        """照明を決めて、実測の中なら次の問い(支柱)へ、実測を通らない時は部位の写真へ。"""
        st.lighting = key
        self.sink.lighting(user_id, st)
        if st.step == "measure":
            self._measure_advance(user_id, st)
        else:
            st.step, st.part, st.photos = "photo", 0, 0
        return self.prompt(st)

    def _set_mount(self, user_id: str, st: GuideState, label: str) -> Say:
        if label == M.MOUNT_OTHER:
            st.m_sub = "mount_text"
            return self.prompt(st)
        st.m["mount"] = None if label == M.UNKNOWN else label
        self._measure_advance(user_id, st)
        return self.prompt(st)

    # ---- 正面と種類 ----
    def _front_done(self, st: GuideState, skipped: bool = False) -> None:
        st.front_skipped = skipped
        st.step, st.photos = "kind", 0

    def _set_kind(self, user_id: str, st: GuideState, kind: str | None) -> Say:
        """種類を決めて(飛ばした時は None)、寸法の実測(初回)か部位の写真へ。"""
        st.kind = kind
        self.sink.sign_kind(user_id, st)
        st.part, st.photos = 0, 0
        if st.size_known:
            st.step = "lighting"
        else:
            st.step, st.m_sub, st.m_pending, st.measure_shown = "measure", "ask", None, True
        return self.prompt(st)

    def photo(self, user_id: str, bundle_id: int, page_no: int, path, now) -> Say:
        """写真が束に入った後に呼ぶ。進行に応じた部位の印(tag)を台帳に付け、次の問いを返す。"""
        st = self.load(user_id)
        if st is None:
            raise LookupError("ガイドが進行していない")
        if st.step == "overview":
            self.sink.photo(user_id, st, "overview", path, bundle_id, page_no)
            st.step, st.photos = "front", 0
            self.save(user_id, st)
            return self._with("全景を受け取りました", self.prompt(st))
        if st.step == "front":
            self.sink.photo(user_id, st, S.FRONT_TAG, path, bundle_id, page_no)
            st.photos += 1
            if st.photos >= S.MAX_FRONT_PHOTOS:
                n = st.photos
                self._front_done(st)
                self.save(user_id, st)
                return self._with(f"正面 {n}枚", self.prompt(st))
            self.save(user_id, st)
            return self.prompt(st)
        if st.step == "measure" and st.m_sub == "drawing" and not st.m_pending:
            self.sink.photo(user_id, st, FIGURE_TAG, path, bundle_id, page_no)
            st.m_photos += 1
            if st.m_photos >= MAX_DRAWING_PHOTOS:
                prefix = self._drawing_done(st)
                self.save(user_id, st)
                return self._with(prefix, self.prompt(st))
            self.save(user_id, st)
            return self.prompt(st)
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
            sm = self.sink.finish(user_id, st)
            self.clear(user_id)
            return summary_text(sm) if sm is not None else Say("終了しました")
        if "back" in q:
            if q.get("at") != _pos(st):  # 古いボタン。余計に戻らず今の問いを出し直す
                return self.prompt(st)
            return self._command(user_id, st, CMD_BACK)
        if "light" in q:
            if (st.step == "measure" and st.m_sub != "light") or st.step not in ("lighting", "measure") \
                    or st.m_pending or q["light"] not in M.LIGHTING_KEYS:
                return self.prompt(st)
            say = self._set_lighting(user_id, st, q["light"])
            self.save(user_id, st)
            return say
        if "more" in q:
            if st.step != "done":
                return self.prompt(st)
            site = st.site_id
            st = GuideState(step="sign", site_id=site, signs_done=st.signs_done + 1, started=st.started,
                            sign_ids=st.sign_ids)
            self.save(user_id, st)
            return self.prompt(st)
        if "front" in q:
            if st.step != "front" or (q["front"] == "skip") != (st.photos == 0):
                return self.prompt(st)
            self._front_done(st, skipped=q["front"] == "skip")
            self.save(user_id, st)
            return self.prompt(st)
        if "kind" in q:
            if st.step != "kind" or q["kind"] not in S.KIND_KEYS:
                return self.prompt(st)
            say = self._set_kind(user_id, st, q["kind"])
            self.save(user_id, st)
            return say
        if "m" in q or "mount" in q:
            return self._measure_postback(user_id, st, q)
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
                self._next_part(user_id, st)
            elif st.photos:
                st.step, st.item = "ask", 0
            else:
                return self.prompt(st)
            self.save(user_id, st)
            return self.prompt(st)
        return self.prompt(st)
