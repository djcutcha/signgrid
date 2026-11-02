"""聞き返し: 声(訂正の所見)→ 聞き返し → 構造化。

有資格者の訂正の所見が短い・条件(範囲・程度・写真番号)が無いとき、規則に育てる前に **1問だけ** 聞き返す
(例: 所見「赤錆」→「赤錆は支柱の根元だけですか、全周ですか。写真何番で見えますか」)。
答えを受けて、規則の文を 項目 / 所見 / 条件(範囲・程度・写真番号)/ 区分 の構造に直し、rules.structured に書く。
答えない・「分からない」でも、規則は所見のまま候補に積んである(review.apply_correction)。何も失わない。

- FixedClarifier: API なし。固定の聞き返し文と、答えの文字列をそのまま条件にする機械的な構造化(--sample・鍵なし)。
- ClaudeClarifier: Sonnet 5.5。聞き返しの1問を書き、答えを 範囲・程度 に分ける。失敗したら呼び出し側が Fixed に落とす。
規則は承認されるまで下書きに効かない(review.approve_rule)。顧客名・店名は規則文に入れない。
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass

from ..eval.claude_judge import DEFAULT_MODEL, _extract_json, cost_usd, make_client
from ..ledger.db import Ledger
from . import review as review_mod

SHORT_NOTE_CHARS = 12
# 所見にこれらが入っていれば、条件を言っているとみなして聞き返さない
CONDITION_WORDS = ("全周", "一部", "根元", "片側", "全面", "部分", "上部", "下部", "mm", "cm", "ミリ", "センチ", "写真",
                   "幅", "深さ", "箇所", "ヶ所", "か所", "割", "%", "％", "全体", "貫通")
UNKNOWN_ANSWERS = ("分からない", "わからない", "不明", "なし", "特になし", "-", "ー")
MAX_CONDITION_CHARS = 40


@dataclass
class Context:
    item: str                   # 点検項目キー
    item_label: str             # 「点検箇所-番号 原文」
    finding: str | None         # 有資格者の所見(note)。無ければ None
    result: str                 # 訂正後の区分
    draft: str | None           # 下書きの区分
    evidence: str | None = None  # 下書きの根拠の文


def needs_question(note: str | None) -> bool:
    """所見が無い・短い・条件の語が無いとき True(聞き返す)。"""
    t = (note or "").strip()
    if not t:
        return True
    return len(t) < SHORT_NOTE_CHARS and not any(w in t for w in CONDITION_WORDS)


def is_unknown(answer: str | None) -> bool:
    t = (answer or "").strip().strip("。.!！")
    return not t or t in UNKNOWN_ANSWERS


def photo_numbers(text: str) -> list[int]:
    """答えの中の写真番号(「写真3」「写真 3 番」「3番の写真」)。"""
    nums = [int(n) for n in re.findall(r"写真\s*(\d+)", text)] + [int(n) for n in re.findall(r"(\d+)\s*番(?:の)?写真", text)]
    return sorted(set(nums))


def _plain(answer: str) -> str:
    """答えから写真番号の言い回しを除いた条件の文(規則の文に入れる。写真番号は看板ごとなので入れない)。"""
    t = re.sub(r"[、,]?\s*写真\s*\d+\s*(番)?\s*(で|に|の|から)?\s*(見え(ます|る|た)?|写って(います|いる)?|確認(できます|できる)?)?", "", answer)
    t = re.sub(r"\d+\s*番(の)?写真(で|に)?(見え(ます|る)?)?", "", t)
    t = t.strip(" 　、,。.です．ます")
    t = re.sub(r"(です|ます)$", "", t).strip(" 　、,。")
    return (t or answer.strip())[:MAX_CONDITION_CHARS]


class FixedClarifier:
    """API なし。固定の聞き返し文と、機械的な構造化。"""
    name = "fixed"

    def ask(self, ctx: Context) -> str:
        what = f"「{ctx.finding}」" if ctx.finding else "この所見"
        return f"{what}は、どの範囲(一部か全周かなど)で、どの程度ですか。写真何番で見えますか。"

    def structure(self, ctx: Context, question: str, answer: str) -> dict:
        return {"range": None, "degree": None, "text": _plain(answer), "photos": photo_numbers(answer)}


ASK_SYSTEM = """あなたは屋外広告物(看板)の点検で、有資格者の訂正を「次の人が受け継げる規則」に育てる補助者です。
有資格者が下書きの区分を直し、短い所見を付けました。規則にするには条件(範囲・程度・写真番号)が足りません。
足りない物を埋める質問を、**1問だけ**、80字以内の日本語で書いてください(質問を2つ以上に分けない)。

- 所見の言葉をそのまま使い、範囲(部分か全周か、どこか)・程度(深さ・幅・大きさ)・写真何番で見えるか、のうち足りない物を1つの質問にまとめる。
- 例: 所見「赤錆」→「赤錆は支柱の根元だけですか、全周ですか。写真何番で見えますか。」
- 範囲と写真番号は聞いてよいが、項目の中の部位を選ばせる質問(どの部品か等)は足さない。点検の用語は言い換えない。店名・社名・人名は書かない。質問以外(前置き・説明)は書かない。"""

STRUCT_SYSTEM = """有資格者の訂正の所見に対する聞き返しと、その答えがあります。答えを規則の条件に分けてください。
返答は次の形の JSON オブジェクト1つだけ(前後に文章を付けない):
{"range": 範囲(部分/全周/根元だけ 等、答えに無ければ null), "degree": 程度(幅・深さ・大きさ・退色だけ 等、答えに無ければ null), "text": 条件を20字以内の句で。所見の語は繰り返さない(例: 所見「赤錆」なら「全周に回っている」)}
写真番号は書かない。店名・社名・人名は入れない。答えに無いことを足さない。"""


class ClaudeClarifier:
    """Sonnet 5.5 で聞き返しの1問を書き、答えを 範囲・程度 に分ける。費用は self.cost_usd に積む。"""
    name = "claude"

    def __init__(self, model: str = DEFAULT_MODEL, client=None):
        self.model, self._client, self.cost_usd = model, client, 0.0

    @property
    def client(self):
        if self._client is None:
            self._client = make_client()
        return self._client

    def _call(self, system: str, user: str, max_tokens: int) -> str:
        resp = self.client.messages.create(model=self.model, max_tokens=max_tokens, system=system,
                                           messages=[{"role": "user", "content": user}])
        self.cost_usd += cost_usd(self.model, resp.usage.input_tokens, resp.usage.output_tokens) or 0.0
        return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()

    @staticmethod
    def _describe(ctx: Context) -> str:
        lines = [f"点検項目: {ctx.item_label}", f"下書きの区分: {ctx.draft or '判断できず'}", f"訂正後の区分: {ctx.result}",
                 f"所見: {ctx.finding or '(なし)'}"]
        if ctx.evidence:
            lines.append(f"下書きの根拠: {ctx.evidence}")
        return "\n".join(lines)

    def ask(self, ctx: Context) -> str:
        q = self._call(ASK_SYSTEM, self._describe(ctx), 1000).splitlines()
        q = next((x.strip() for x in q if x.strip()), "")
        if not q:
            raise RuntimeError("聞き返しが空")
        return q

    def structure(self, ctx: Context, question: str, answer: str) -> dict:
        raw = self._call(STRUCT_SYSTEM, f"{self._describe(ctx)}\n聞き返し: {question}\n答え: {answer}", 1000)
        try:
            obj = json.loads(_extract_json(raw))
        except json.JSONDecodeError as e:
            raise RuntimeError(f"構造化の応答が JSON でない: {e}") from e
        if not isinstance(obj, dict):
            raise RuntimeError("構造化の応答がオブジェクトでない")

        def s(k):
            v = obj.get(k)
            return v.strip()[:MAX_CONDITION_CHARS] if isinstance(v, str) and v.strip() else None

        text = s("text") or " ".join(x for x in (s("range"), s("degree")) if x) or _plain(answer)
        return {"range": s("range"), "degree": s("degree"), "text": text, "photos": photo_numbers(answer)}


def context_of(ledger: Ledger, sign_id: str, c: review_mod.Correction, note: str | None) -> Context:
    s = ledger.get_sign(sign_id)
    ev = (s.evidence.get(c.item) or {}).get("text") if s else None
    return Context(c.item, f"{c.item} {review_mod.ITEM_TEXT[c.item]}", (note or "").strip() or None, c.new_value, c.old_value, ev)


def ask_question(clarifier, ctx: Context) -> tuple[str, str]:
    """(質問, 使った聞き返し役の名前)。API の失敗は固定の質問に落とす(訂正の流れを止めない)。"""
    try:
        return clarifier.ask(ctx), clarifier.name
    except Exception:  # 鍵の誤り・接続・応答の不正。画面は落とさない
        fixed = FixedClarifier()
        return fixed.ask(ctx), fixed.name


def apply_answer(ledger: Ledger, rule_id: int, question: str, answer: str, by: str, clarifier) -> dict:
    """聞き返しの答えを受けて、候補の規則の文と構造を直し、構造を返す。答えが空・「分からない」なら何も変えず現状の構造を返す。
    承認・却下済みの規則は直さない(ValueError)。"""
    if not by or not by.strip():
        raise ValueError("答える人(by)が要ります")
    r = ledger.get_rule(rule_id)
    if r is None:
        raise LookupError(f"規則 {rule_id} が台帳にありません")
    if r["status"] != "candidate":
        raise ValueError(f"規則 {rule_id} は status={r['status']}。聞き返しで直せるのは候補だけ")
    st = json.loads(r["structured"] or "null")
    if not st:
        raise ValueError(f"規則 {rule_id} は構造を持ちません(聞き返しの対象外)")
    if is_unknown(answer):
        return st
    ctx = Context(st["item"], f"{st['item']} {review_mod.ITEM_TEXT[st['item']]}", st.get("finding"), st["result"], st.get("draft"))
    try:
        cond = clarifier.structure(ctx, question, answer)
    except Exception:
        cond = FixedClarifier().structure(ctx, question, answer)
    finding, cond_text = st.get("finding"), cond["text"]
    if not finding:  # 所見が無かった規則は、答えを所見にして立て直す
        finding, cond_text = cond["text"], None
        st["finding"] = finding
    text, _ = review_mod.rule_text(st["item"], finding, st["result"], st.get("draft"), cond_text)
    st.update(condition=cond, question=question, answer=answer.strip(), clarified_by=by.strip())
    ledger.update_rule(rule_id, text, st, review_mod.reconflict(ledger, rule_id, text))
    return st
