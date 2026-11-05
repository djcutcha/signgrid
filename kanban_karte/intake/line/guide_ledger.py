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
  照明       signs.lighting(内照式 / 外照式 / 無し。分からないは NULL)。「無し」の看板は照明装置の項目を guide=na で入れる(items には入れない)。
  戻る       写真の工程で戻ると、直前の写真の pages.tag を外す(unphoto。ファイルと signs.photos はそのまま)。
  終了       finish: まとめの材料を台帳から集める。現場の作業はすべて下書きのまま(確定は PC の台帳)。
               「AI 判定」(unknown)の項目がある看板の束は、auto_draft が True なら draft_bundles に積む
               (LineApp が応答のあと別スレッドで web.api.draft_bundle を呼ぶ。only_missing は draft_bundle が決める)。
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from ...ledger import Ledger, Sign
from ...ledger.db import FIGURE_TAG, MEASURE_TAG
from ...ledger.items import ITEM_KEYS
from .guide import ANSWER_LABEL, ANSWER_RESULT, GuideState, SignRow, Summary
from .measure import GL_EDGE, LIGHTING_VALUE
from .shoot import FRONT_TAG, KIND_TYPE
from .session import safe_name

BY = "line_guide"
# 部位の根拠の写真に、その部位の tag の写真のほかに数える tag
PART_EXTRA_TAGS = {"表示部": (FRONT_TAG,), "基礎部・上部構造": ("overview",)}


def draft_by(user_id: str) -> str:
    return f"{BY}:{safe_name(user_id)[-4:]}"


class LedgerSink:
    """ledger は LineApp が配信ごとに差し替える。"""

    def __init__(self, ledger: Ledger | None = None, now=None, auto_draft: Callable[[], bool] | None = None):
        self.ledger = ledger
        self.now = now or datetime.now
        self.auto_draft = auto_draft or (lambda: False)  # 終了時に AI 判定の項目を写真から自動で下書きするか(LINE_GUIDE_AUTO_DRAFT=1 かつ鍵あり)
        self.draft_bundles: list[int] = []  # 終了で積んだ、自動で下書きする束(LineApp が取り出して走らせる)

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

    def lighting(self, user_id: str, st: GuideState) -> None:
        s = self._sign(st)
        s.lighting = LIGHTING_VALUE.get(st.lighting)
        self.ledger.upsert_sign(s)

    def _tagged(self, s: Sign, tag: str) -> list[tuple[str, int, int]]:
        """看板の写真(並び順)のうち tag の頁: [(相対パス, bundle_id, page_no)]。"""
        rows = {self.ledger.rel_path(r["image_path"]): (r["bundle_id"], r["page_no"]) for r in self.ledger.conn.execute(
            "SELECT bundle_id, page_no, image_path FROM pages WHERE tag=? AND image_path IS NOT NULL", (tag,))}
        return [(p, *rows[p]) for p in s.photos if p in rows]

    def unphoto(self, user_id: str, st: GuideState, tag: str) -> bool:
        """直前に撮った tag の写真の tag を外す(戻る)。ファイルと signs.photos はそのまま。"""
        found = self._tagged(self._sign(st), tag)
        if not found:
            return False
        _, bundle_id, page_no = found[-1]
        self.ledger.set_page_tag(bundle_id, page_no, None)
        return True

    def photo_count(self, user_id: str, st: GuideState, tag: str) -> int:
        return len(self._tagged(self._sign(st), tag))

    # ---- 終了 ----
    @staticmethod
    def _row(s: Sign, photos: int) -> SignRow:
        vals = list(s.items.values())
        unknown = sum(1 for k in ITEM_KEYS if (s.evidence.get(k) or {}).get("guide") == "unknown")
        return SignRow(name=s.name or s.id, type=s.type, size_w=s.size_w, size_h=s.size_h, size_d=s.size_d,
                       gl_mm=s.gl_mm, photos=photos, bad=vals.count("要改善"), watch=vals.count("経過観察"),
                       good=vals.count("良好"), unknown=unknown)

    def _visible_photos(self, s: Sign) -> int:
        hidden = self.ledger.measure_paths() | self.ledger.figure_paths()  # 実測・図面は看板の写真に数えない
        return sum(1 for p in s.photos if p not in hidden)

    def finish(self, user_id: str, st: GuideState) -> Summary:
        """終了のまとめの材料。AI 判定の項目があり、自動の下書きが有効なら、その看板の束を draft_bundles に積む。"""
        signs = [s for s in (self.ledger.get_sign(i) for i in dict.fromkeys(st.sign_ids)) if s is not None]
        ai_items = sum(self._row(s, 0).unknown for s in signs)
        auto = bool(ai_items) and self.auto_draft()
        if auto:
            for b in dict.fromkeys(s.source_bundle for s in signs if self._row(s, 0).unknown and s.source_bundle is not None):
                if b not in self.draft_bundles:
                    self.draft_bundles.append(b)
        site = self.ledger.get_site(st.site_id) if st.site_id else None
        rows = [self._row(self.ledger.get_sign(s.id), self._visible_photos(s)) for s in signs]
        total, n = None, 0
        if st.site_id:
            today = f"{self.now():%Y-%m-%d}"
            ids = [r["id"] for r in self.ledger.conn.execute(
                "SELECT id FROM signs WHERE site_id=? AND substr(created,1,10)=?", (st.site_id, today))]
            day = [self.ledger.get_sign(i) for i in dict.fromkeys([*ids, *(x.id for x in signs)])]
            n = len(day)
            tr = [self._row(s, 0) for s in day]
            total = SignRow(name="合計", bad=sum(r.bad for r in tr), watch=sum(r.watch for r in tr),
                            good=sum(r.good for r in tr), unknown=sum(r.unknown for r in tr))
        return Summary(site_name=site.name if site else None, rows=rows, site_total=total, site_signs=n,
                       ai_items=ai_items, auto_draft=auto)

    def sign_kind(self, user_id: str, st: GuideState) -> None:
        """看板の種類を signs.type に入れる(台帳に既にあれば上書きしない。飛ばした時は何もしない)。"""
        if not st.kind:
            return
        s = self._sign(st)
        if not s.type:
            s.type = KIND_TYPE[st.kind]
            self.ledger.upsert_sign(s)
