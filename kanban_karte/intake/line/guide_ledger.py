"""ガイド付き点検の答えを台帳に書く(guide.py の sink)。

  signs        看板は下書き(status=draft)で作る。name=送られた名前、draft_by='line_guide:<userId 末尾4桁>'。
               看板全体の rank は付けない(有資格者が確定する)。
  pages        写真は束の pages に入る。tag に overview / 部位名(どの部位の写真か)。
  items        a→良好 / b→経過観察 / c→要改善。「分からない」「対象外」は入れない。
  evidence     項目ごとに現場の回答を残す: {"guide": a|b|c|unknown|na, "photos": [その部位の写真番号], "text": ...}。
               pipeline/draft.py の only_missing は、unknown と未回答の項目だけを写真から下書きする(na は触らない)。
  寸法(実測) signs.size_w / size_h / size_d(厚み)/ gl_mm / gl_edge / mount / post と、出どころ size_source='measured'・size_by・size_at。
               全高 height_m = GL + 高さ。「図面で送る」の図面は pages.tag='図面'(看板の写真の末尾。下書きの入力には入れない)。
  種類       signs.type(ポール / 自立 / 壁面 / 突出 / 屋上 / その他)。台帳に既にあれば上書きしない。
  正面       表示面を正面から撮った写真は pages.tag='正面'。表示部の項目の根拠の写真に数える(全景は基礎部・上部構造に数える)。
               同じ現場・同じ名前の看板に寸法が既にあれば、実測の問いを出さずに引き継ぐ(known_size)。
  corrections  a/b/c の答えは by='line_guide' で1行(有資格者の訂正と区別できる)。
"""
from __future__ import annotations

from datetime import datetime

from ...ledger import Ledger, Sign
from ...ledger.db import FIGURE_TAG, MEASURE_TAG
from .guide import ANSWER_LABEL, ANSWER_RESULT, GuideState
from .measure import GL_EDGE
from .shoot import FRONT_TAG, KIND_TYPE
from .session import safe_name

BY = "line_guide"
# 部位の根拠の写真に、その部位の tag の写真のほかに数える tag
PART_EXTRA_TAGS = {"表示部": (FRONT_TAG,), "基礎部・上部構造": ("overview",)}


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
        rel = self.ledger.rel_path(path)
        if tag in (MEASURE_TAG, FIGURE_TAG):
            s.photos.append(rel)
        else:  # 写真の番号(根拠の「写真3」)がずれないよう、実測・図面の写真は末尾にまとめる
            hidden = self.ledger.measure_paths() | self.ledger.figure_paths()
            at = len(s.photos)
            while at > 0 and s.photos[at - 1] in hidden:
                at -= 1
            s.photos.insert(at, rel)
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
        want = {part, *PART_EXTRA_TAGS.get(part, ())}
        return [i + 1 for i, p in enumerate(s.photos) if tags.get(p) in want]

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
            self.ledger.add_correction(s.id, key, old, new, BY)


    # ---- 寸法の実測 ----
    def known_size(self, user_id: str, st: GuideState) -> dict | None:
        """同じ現場・同じ名前の看板で、幅×高さが台帳にあるもの(いちばん新しい)。あればその寸法をこの看板に引き継ぐ。"""
        s = self._sign(st)
        prev = [p for p in self.ledger.list_signs(site_id=st.site_id)
                if p.id != s.id and p.site_id == s.site_id and (p.name or "").strip() == (s.name or "").strip()
                and p.size_w and p.size_h]
        if not prev:
            return None
        p = max(prev, key=lambda x: x.size_at or "")
        for col in ("size_w", "size_h", "size_d", "size_source", "size_by", "size_at", "gl_mm", "gl_edge", "mount", "post", "height_m"):
            setattr(s, col, getattr(p, col))
        self.ledger.upsert_sign(s)
        return {"source": p.size_source, "at": p.size_at, "by": p.size_by}

    def measure(self, user_id: str, st: GuideState) -> None:
        """実測の入力を、揃った分だけ書く。幅・高さが揃えば signs.size に入れて出どころを measured にする。
        GL は看板の下端まで(gl_edge='bottom')。全高 height_m = GL + 高さ(法規の判定は開くたびに面積・全高で再判定される)。"""
        s, m = self._sign(st), st.m
        if m.get("w") and m.get("h"):
            s.size_w, s.size_h = int(m["w"]), int(m["h"])
            s.size_source, s.size_by, s.size_at = "measured", draft_by(user_id), f"{self.now():%Y-%m-%d}"
            if m.get("gl") is not None:
                s.gl_mm, s.gl_edge = int(m["gl"]), GL_EDGE
                s.height_m = round((s.gl_mm + s.size_h) / 1000, 2)
            else:
                s.gl_mm = s.gl_edge = None
            s.size_d = int(m["d"]) if m.get("d") else None
            s.mount, s.post = m.get("mount"), m.get("post")
        self.ledger.upsert_sign(s)

    def sign_kind(self, user_id: str, st: GuideState) -> None:
        """看板の種類を signs.type に入れる(台帳に既にあれば上書きしない。飛ばした時は何もしない)。"""
        if not st.kind:
            return
        s = self._sign(st)
        if not s.type:
            s.type = KIND_TYPE[st.kind]
            self.ledger.upsert_sign(s)
