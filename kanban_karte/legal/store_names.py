"""指示書の件名(SignJOBZ 複製 r_shijisyo.sSHIJISYO)から、店舗名らしき文字列を抜く。

件名の例(架空): 「見本マート西町店 点検」「見本マート北原点検」「見本ストア　東口店　点検作業」
店名に『店』が付かないものが多い(チェーン名+地名+点検)。2通りで抜く:
  chain   : チェーン名(data/private/store_chains.txt に 1 行 1 つ。未追跡。無ければ使わない)+ 地名 + (店|営業所|支店|センター)?
  generic : チェーン名が分からなくても『◯◯店』『◯◯営業所』『◯◯センター』『◯◯校』(2〜15 字+接尾辞)で終わる語
チェーン名(顧客名)はコード・docs に書かず、非公開ファイルに置く。出力は件数と割合だけ。名前は持ち出さない。
実際の住所引き(store_address.py)は本線の GO 後。
"""
from __future__ import annotations

import re
import sqlite3
import unicodedata
from pathlib import Path

from .rules_extract import REPO_ROOT

CHAINS_PATH = REPO_ROOT / "data" / "private" / "store_chains.txt"
DEFAULT_DB = Path("~/Projects/fkogei-estimate-data/data/signjobz.db").expanduser()

SUFFIXES = ("営業所", "支店", "センター", "校", "店")  # 長いものから
_STOP = r"点検|看板|安全|各種|定期|修繕|更新|申請|調査|作業|撤去|工事|報告|確認"
_PLACE_CH = r"[^\s　\r\n（）()「」『』\[\]【】、,，/／]"


def load_chains(path: Path | None = None) -> list[str]:
    p = path or CHAINS_PATH
    if not p.exists():
        return []
    names = [ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    return sorted(set(names), key=len, reverse=True)  # 長い名前を先に当てる


def _nfkc(s: str) -> str:
    return unicodedata.normalize("NFKC", s or "")


def chain_regex(chains: list[str]) -> re.Pattern | None:
    if not chains:
        return None
    alts = "|".join(re.escape(_nfkc(c)) for c in sorted(chains, key=len, reverse=True))
    sfx = "|".join(SUFFIXES)
    # チェーン名 + 空白? + 地名(最短)+ 店名の接尾辞? + 区切り(空白・改行・停止語・文末)
    return re.compile(rf"(?P<chain>{alts})[\s]*(?P<place>{_PLACE_CH}+?)(?P<suffix>{sfx})?(?=[\s・]|{_STOP}|$)")


_GENERIC = re.compile(rf"(?P<name>(?:(?!{_STOP}){_PLACE_CH}){{2,15}}?(?:営業所|センター|校|店))(?=[\s・]|{_STOP}|$)")


def extract_store_name(subject: str | None, chain_rx: re.Pattern | None) -> dict | None:
    """件名 -> {"kind": "chain"|"generic", "name": 店舗名, ...} か None。名前は呼び出し側が持ち出さない前提。"""
    if not subject:
        return None
    t = _nfkc(subject)
    if chain_rx is not None:
        m = chain_rx.search(t)
        # 地名が停止語(点検…)で始まる / 数字で始まる(日付・回数: 「3月」「2回目」)ものは店名と見ない
        if m and not m.group("place").startswith(tuple(_STOP.split("|"))) and not m.group("place")[0].isdigit():
            suffix = m.group("suffix") or ""
            return {"kind": "chain", "chain": m.group("chain"), "place": m.group("place"), "has_suffix": bool(suffix),
                    "name": m.group("chain") + m.group("place") + suffix}
    m = _GENERIC.search(t)
    if m:
        return {"kind": "generic", "name": m.group("name"), "has_suffix": True}
    return None


def inspection_subjects(db: Path | None = None, since: str = "2022-01-01", keyword: str = "点検") -> list[str]:
    """点検案件の件名(受注日が since 以降で、件名に keyword を含むもの)。読み取り専用で開く。"""
    con = sqlite3.connect(f"file:{db or DEFAULT_DB}?mode=ro", uri=True)
    try:
        return [r[0] or "" for r in con.execute(
            "SELECT sSHIJISYO FROM r_shijisyo WHERE sSHIJISYO LIKE ? AND dJUUTYU >= ? ORDER BY dJUUTYU",
            (f"%{keyword}%", since))]
    finally:
        con.close()


def count_names(subjects: list[str], chains: list[str] | None = None) -> dict:
    """件数と割合だけ(名前は返さない)。"""
    chains = load_chains() if chains is None else chains
    rx = chain_regex(chains)
    kinds = {"chain": 0, "generic": 0}
    for s in subjects:
        r = extract_store_name(s, rx)
        if r:
            kinds[r["kind"]] += 1
    n, hit = len(subjects), kinds["chain"] + kinds["generic"]
    return {"subjects": n, "matched": hit, "ratio": round(hit / n, 3) if n else 0.0, "by_kind": kinds,
            "chains_configured": len(chains)}
