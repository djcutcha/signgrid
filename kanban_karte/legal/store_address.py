"""店舗名 -> 看板の場所の住所。

得意先住所は代理店経由だと看板の場所ではないので、店舗名(「◯◯ ◯◯店」等)から公開情報で住所を引く。
手順:
  1. 台帳 sites に同じ店舗名で住所があれば、それを使う(Web は呼ばない)。
  2. Claude + web search(サーバーツール)で、公式の店舗一覧か地図サービスの公開ページから住所を引く。
     返すもの: address / source_url / confidence / candidates[]。
  3. confidence < 0.7、または出典 URL が検索結果に無いときは『住所 要確認』にする(住所は候補として残す)。
結果は data/private/store_address_cache.json(未追跡)にキャッシュする。

鍵は環境変数 ANTHROPIC_API_KEY(.env も読む)。サンプリングパラメータは送らない。
"""
from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime
from pathlib import Path

from . import rules_extract as RX

CONFIRMED, NEEDS_CHECK = "確定", "住所 要確認"
THRESHOLD = 0.7
CACHE_PATH = RX.REPO_ROOT / "data" / "private" / "store_address_cache.json"
MODEL = RX.DEFAULT_MODEL
WEB_SEARCH_TOOL = "web_search_20250305"
MAX_SEARCHES = 5
SEARCH_USD = 0.01  # web search は 1,000 回あたり $10
SOURCE_KINDS = ("official_store_list", "map_service", "other")

SYSTEM_PROMPT = f"""あなたは店舗の所在地を公開情報から調べる担当です。web_search ツールで、指定された店舗の住所を調べます。

守ること:
- 出典は、その店舗を運営する会社の公式サイトの店舗一覧・店舗ページ、または地図サービス(Google マップ・Yahoo!マップ・OpenStreetMap 等)の公開ページ。口コミ・まとめ・求人サイトは、他に無いときだけ使い、source_kind を other にする。
- 同名・似た名前の別の店舗に注意する。運営会社名・都道府県が与えられたら、それで絞る。店舗名が完全に一致するページだけを根拠にする。
- address は都道府県から番地(分かれば建物名まで)。ページに書かれた住所をそのまま写す。推測で補わない。
- source_url は実際に検索結果に出たページの URL をそのまま書く。作らない。
- confidence(0〜1): 公式の店舗一覧・店舗ページに店舗名と住所が一致して載っている=0.9以上 / 地図サービスだけ、または出典が1つだけで店舗名が完全一致=0.7〜0.89 / 店舗名が一部しか合わない・候補が複数で決められない・出典が弱い=0.7未満 / 見つからない=0。
- 決められないときは address を最も有力な候補にして confidence を下げ、他の候補を candidates に入れる。見つからなければ address は null。
- JSON の文字列の中に半角のダブルクォート(")を書かない。
- 最後に、次の形の JSON オブジェクト1つだけを出力する。前後に説明文やコードフェンスを付けない。

{{"address": "東京都…|null", "source_url": "https://…|null", "source_kind": "official_store_list|map_service|other|null",
 "confidence": 0〜1, "candidates": [{{"address": "…", "source_url": "https://…|null", "note": "どの点で候補か(40字以内)"}}],
 "note": "判断の理由(100字以内)"}}"""


def norm_name(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s or "")).lower()


def cache_key(store_name: str, operator: str | None = None, prefecture: str | None = None) -> str:
    return "|".join((norm_name(store_name), norm_name(operator or ""), norm_name(prefecture or "")))


def _load_cache(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _save_cache(path: Path, cache: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _result(store_name, operator, prefecture, **kw) -> dict:
    r = {"store_name": store_name, "operator": operator, "prefecture": prefecture, "status": NEEDS_CHECK,
         "address": None, "source_url": None, "source_kind": None, "confidence": 0.0, "candidates": [],
         "origin": None, "note": None}
    r.update(kw)
    return r


# ---------- 1. 台帳 ----------

def from_ledger(ledger, store_name: str, operator: str | None = None, prefecture: str | None = None) -> dict | None:
    """台帳 sites の name が店舗名と(空白・全半角を除いて)一致し、住所が入っているもの。複数で住所が割れたら要確認。"""
    want = norm_name(store_name)
    seen: dict[str, str] = {}
    for r in ledger.conn.execute("SELECT id, name, address FROM sites WHERE address IS NOT NULL AND address <> ''"):
        if norm_name(r["name"]) == want:
            seen.setdefault(r["address"].strip(), r["id"])
    if not seen:
        return None
    if len(seen) == 1:
        addr = next(iter(seen))
        return _result(store_name, operator, prefecture, status=CONFIRMED, address=addr, source_kind="ledger",
                       confidence=1.0, origin="ledger", note="台帳 sites の住所")
    cands = [{"address": a, "source_url": None, "note": f"台帳 sites {i}"} for a, i in seen.items()]
    return _result(store_name, operator, prefecture, address=cands[0]["address"], source_kind="ledger", confidence=0.5,
                   candidates=cands, origin="ledger", note="台帳に同名の現場が複数あり住所が違う")


# ---------- 2. Claude + web search ----------

def _user_message(store_name, operator, prefecture) -> str:
    lines = [f"店舗名: {store_name}"]
    if operator:
        lines.append(f"運営会社: {operator}")
    if prefecture:
        lines.append(f"都道府県: {prefecture}")
    return "次の店舗の住所を調べてください。\n" + "\n".join(lines)


def _blocks(resp):
    return getattr(resp, "content", None) or []


def _search_urls(blocks) -> set[str]:
    urls = set()
    for b in blocks:
        if getattr(b, "type", None) == "web_search_tool_result":
            c = getattr(b, "content", None)
            if isinstance(c, list):
                urls.update(u for u in (getattr(x, "url", None) for x in c) if u)
    return urls


def _norm_url(u: str) -> str:
    return re.sub(r"^https?://(www\.)?", "", (u or "").strip()).rstrip("/").lower()


def _final_json(blocks) -> dict:
    texts = [b.text for b in blocks if getattr(b, "type", None) == "text" and b.text.strip()]
    for t in reversed(texts):
        try:
            return RX.parse_json_object(t)
        except (ValueError, json.JSONDecodeError):
            continue
    raise ValueError("JSON の答えが見つからない")


def search_web(store_name: str, operator: str | None = None, prefecture: str | None = None, *, client=None,
               model: str = MODEL, max_searches: int = MAX_SEARCHES) -> dict:
    """web search で住所を引く。確信度が閾値未満・出典 URL が検索結果に無いときは『住所 要確認』。"""
    client = client or RX.make_client()
    messages = [{"role": "user", "content": _user_message(store_name, operator, prefecture)}]
    tools = [{"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_searches}]
    in_tok = out_tok = searches = 0
    all_blocks: list = []
    resp = None
    for _ in range(4):  # pause_turn(サーバー側の反復上限)なら続きを依頼する
        resp = client.messages.create(model=model, max_tokens=4096, system=SYSTEM_PROMPT, messages=messages, tools=tools)
        in_tok += resp.usage.input_tokens
        out_tok += resp.usage.output_tokens
        stu = getattr(resp.usage, "server_tool_use", None)
        searches += getattr(stu, "web_search_requests", 0) or 0
        all_blocks += list(_blocks(resp))
        if getattr(resp, "stop_reason", None) != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": resp.content}]
    if resp.stop_reason == "refusal":
        raise RuntimeError("モデルが応答を拒否した")
    usd = round(RX.cost_usd(model, in_tok, out_tok) + searches * SEARCH_USD, 4)
    try:
        obj = _final_json(all_blocks)
    except ValueError:
        return _result(store_name, operator, prefecture, origin="web", note="答えの JSON を読めなかった",
                       usd=usd, searches=searches)
    addr = obj.get("address") if isinstance(obj.get("address"), str) and obj["address"].strip() else None
    url = obj.get("source_url") if isinstance(obj.get("source_url"), str) and obj["source_url"].strip() else None
    kind = obj.get("source_kind") if obj.get("source_kind") in SOURCE_KINDS else None
    try:
        conf = max(0.0, min(1.0, float(obj.get("confidence"))))
    except (TypeError, ValueError):
        conf = 0.0
    notes = [str(obj.get("note") or "")[:100]]
    if addr is None:
        conf = 0.0
    if url is not None:
        found = {_norm_url(u) for u in _search_urls(all_blocks)}
        if found and _norm_url(url) not in found:
            conf = min(conf, 0.5)
            notes.append("出典 URL が検索結果に無い")
        elif not found:
            conf = min(conf, 0.5)
            notes.append("検索結果の URL を確認できない")
    elif addr is not None:
        conf = min(conf, 0.5)
        notes.append("出典 URL が無い")
    cands = [{"address": str(c.get("address")), "source_url": c.get("source_url"), "note": str(c.get("note") or "")[:40]}
             for c in (obj.get("candidates") or []) if isinstance(c, dict) and c.get("address")][:5]
    ok = addr is not None and conf >= THRESHOLD
    return _result(store_name, operator, prefecture, status=CONFIRMED if ok else NEEDS_CHECK, address=addr, source_url=url,
                   source_kind=kind, confidence=round(conf, 2), candidates=cands, origin="web",
                   note=" / ".join(n for n in notes if n) or None, usd=usd, searches=searches)


# ---------- 入口 ----------

def lookup(store_name: str, operator: str | None = None, prefecture: str | None = None, *, ledger=None, client=None,
           cache_path: Path | None = None, refresh: bool = False, web: bool = True) -> dict:
    """台帳 -> キャッシュ -> web search の順。web=False なら API を呼ばない(見つからなければ要確認)。"""
    if ledger is not None:
        r = from_ledger(ledger, store_name, operator, prefecture)
        if r is not None:
            return r
    path = cache_path or CACHE_PATH
    key = cache_key(store_name, operator, prefecture)
    cache = _load_cache(path)
    if key in cache and not refresh:
        return {**cache[key], "origin": "cache"}
    if not web:
        return _result(store_name, operator, prefecture, note="台帳にもキャッシュにも無い(web 検索は未実行)")
    r = search_web(store_name, operator, prefecture, client=client)
    cache[key] = {**r, "looked_up_at": datetime.now(RX.JST).isoformat(timespec="seconds")}
    _save_cache(path, cache)
    return r
