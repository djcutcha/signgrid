"""ガイド付き点検の答えを台帳に書く(guide.py の sink)。

  signs        看板は下書き(status=draft)で作る。name=送られた名前、draft_by='line_guide:<userId 末尾4桁>'。
               看板全体の rank は付けない(有資格者が確定する)。
  pages        写真は束の pages に入る。tag に overview / 部位名(どの部位の写真か)。
  items        a→良好 / b→経過観察 / c→要改善。「分からない」「対象外」は入れない。
  evidence     項目ごとに現場の回答を残す: {"guide": a|b|c|unknown|na, "photos": [その部位の写真番号], "text": ...}。
               pipeline/draft.py の only_missing は、unknown と未回答の項目だけを写真から下書きする(na は触らない)。
  corrections  a/b/c の答えは by='line_guide' で1行(有資格者の訂正と区別できる)。
"""
from __future__ import annotations

from datetime import datetime

from ...ledger import Ledger, Sign
from .guide import ANSWER_LABEL, ANSWER_RESULT, GuideState
from .session import safe_name

BY = "line_guide"


def draft_by(user_id: str) -> str:
    return f"{BY}:{safe_name(user_id)[-4:]}"


class LedgerSink:
    """ledger は LineApp が配信ごとに差し替える。"""

    def __init__(self, ledger: Ledger | None = None, now=None):
        self.ledger = ledger
        self.now = now or datetime.now

    def _sign(self, st: GuideState) -> Sign:
        s = self.ledger.get_sign(st.sign_id) if st.sign_id else None
        if s is None:
            raise LookupError(f"ガイドの看板 {st.sign_id} が台帳にありません")
        return s

    def sign_named(self, user_id: str, st: GuideState) -> None:
        site = self.ledger.get_site(st.site_id) if st.site_id else None
        if st.sign_id is None:
            base = f"g{safe_name(user_id)[-4:]}-{self.now():%Y%m%d%H%M%S}"
            sid, n = base, 1
            while self.ledger.get_sign(sid) is not None:
                n += 1
                sid = f"{base}-{n}"
            st.sign_id = sid
            s = Sign(id=sid, status="draft", draft_by=draft_by(user_id))
        else:
            s = self._sign(st)
        s.name = st.name
        s.site_id = st.site_id
        if site is not None:
            s.address, s.municipality = site.address, site.municipality
        self.ledger.upsert_sign(s)

    def photo(self, user_id: str, st: GuideState, tag: str, path, bundle_id: int, page_no: int) -> None:
        self.ledger.set_page_tag(bundle_id, page_no, tag)
        self.ledger.set_page_kind(bundle_id, page_no, "写真", "ガイド付き点検で撮影")
        s = self._sign(st)
        s.photos.append(self.ledger.rel_path(path))
        if s.source_bundle is None:
            s.source_bundle = bundle_id
        self.ledger.upsert_sign(s)
        b = self.ledger.get_bundle(bundle_id)
        if b is not None and b["site_id"] is None and s.site_id:
            self.ledger.set_bundle(bundle_id, site_id=s.site_id)

    def _part_photo_numbers(self, s: Sign, part: str) -> list[int]:
        """看板の写真(1 始まり)のうち、その部位の tag の写真。"""
        tags = {self.ledger.rel_path(r["image_path"]): r["tag"] for r in self.ledger.conn.execute(
            "SELECT image_path, tag FROM pages WHERE image_path IS NOT NULL")}
        return [i + 1 for i, p in enumerate(s.photos) if tags.get(p) == part]

    def answer(self, user_id: str, st: GuideState, key: str, ans: str) -> None:
        s = self._sign(st)
        part = key.rsplit("-", 1)[0]
        old = s.items.get(key)
        s.evidence[key] = {"photos": self._part_photo_numbers(s, part), "needs_onsite": False, "guide": ans,
                           "text": f"現場の回答(ガイド): {ANSWER_LABEL[ans]}"}
        if ans in ANSWER_RESULT:
            new = ANSWER_RESULT[ans]
            s.items[key] = new
        else:
            s.items.pop(key, None)
            new = None
        self.ledger.upsert_sign(s)
        if new is not None and new != old:
            self.ledger.add_correction(s.id, key, old, new, BY, note=ANSWER_LABEL[ans])

