"""客先A型: センター(現場)単位の安全点検報告書 xlsx。客先の実ファイルは見られないので、この構成で自前に設計した。

シート構成:
  表紙       現場名・住所・調査日・総合評価 A〜D・看板の数と判定の内訳・点検実施者の入力欄
  看板一覧   看板1基=1行。腐食 / 破損 / 固定 の A〜D、総合、前回との差、所見、写真枚数、未判定項目数
  看板_<id>  看板別のページ。18項目の判定と、写真(最大6枚)

評価 A〜D(共通の4段階。点検の判定との対応は docs/OUTPUT.md):
  A=良好 / B=経過観察 / C=要改善 / D=即時対応
  - 看板の総合 = 台帳の rank(有資格者が確定した値。1->A ... 4->D)。AI が点検項目から決め直さない
  - 現場の総合 = 看板の総合のうち最も悪いもの。1基でも rank 未確定なら「未判定」(確定済み分の最悪は括弧で添える)
  - 腐食 / 破損 / 固定 = その視点に当たる点検項目(PERSPECTIVES)のうち最も悪い判定。D は項目からは出さない
    (項目の区分に即時対応が無いので、D は rank 4 の看板の総合にだけ付く)
  - その視点の項目に未記入が残るとき: 判定済みが無ければ「未判定」、一部だけなら暫定の記号を赤字で出す
"""
from __future__ import annotations

from openpyxl import Workbook
from openpyxl.utils import get_column_letter

from ..ledger.db import Ledger, Sign
from ..ledger.items import ITEM_KEYS, RESULT_TO_RANK
from . import common as c

GRADE = {1: "A", 2: "B", 3: "C", 4: "D"}
GRADE_LABEL = {"A": "良好", "B": "経過観察", "C": "要改善", "D": "即時対応"}
GRADE_FILL = {g: c.PatternFill("solid", fgColor=col)
              for g, col in zip("ABCD", ("E2F0D9", "FFF2CC", "FCE4D6", "F4B6B6"))}

# 視点 -> 点検項目キー(items.py の項目名に沿って自前に割り当てた。1項目が複数の視点に入ってよい)
PERSPECTIVES: dict[str, list[str]] = {
    "腐食": ["基礎部・上部構造-3", "支持部-1", "取付部-1", "表示部-1", "表示部-2", "表示部-3",
            "照明装置-2", "その他-1", "その他-2"],
    "破損": ["基礎部・上部構造-2", "支持部-1", "取付部-2", "表示部-1", "表示部-2", "表示部-4",
            "照明装置-1", "照明装置-2", "照明装置-3", "その他-1", "その他-2", "その他-3"],
    "固定": ["基礎部・上部構造-1", "基礎部・上部構造-2", "支持部-2", "取付部-1", "取付部-3", "表示部-1"],
}
assert all(k in ITEM_KEYS for ks in PERSPECTIVES.values() for k in ks)
assert set(ITEM_KEYS) <= {k for ks in PERSPECTIVES.values() for k in ks}  # どの項目もどれかの視点に入る


def perspective_grade(sign: Sign, perspective: str) -> tuple[str | None, bool]:
    """(記号, 暫定か)。記号は A〜C、全て対象外なら "-"、判定済みが無ければ None。
    暫定=その視点の項目に未記入が残っている。"""
    keys = PERSPECTIVES[perspective]
    judged = [sign.items[k] for k in keys if k in sign.items]
    partial = len(judged) < len(keys)
    ranks = [RESULT_TO_RANK[j] for j in judged if RESULT_TO_RANK[j] is not None]
    if ranks:
        return GRADE[max(ranks)], partial
    if judged and not partial:
        return "-", False
    return None, partial


def sign_grade(sign: Sign) -> str | None:
    return None if sign.rank is None else GRADE[sign.rank]


def site_grade(signs: list[Sign]) -> tuple[str | None, str | None]:
    """(現場の総合, 補足)。1基でも rank 未確定なら総合は None で、補足に確定済み分の最悪を入れる。"""
    ranks = [s.rank for s in signs if s.rank is not None]
    missing = len(signs) - len(ranks)
    if missing:
        note = f"rank 未確定 {missing}/{len(signs)} 基"
        if ranks:
            note += f"(確定済みの最悪は {GRADE[max(ranks)]})"
        return None, note
    return GRADE[max(ranks)], None


def _grade_cell(ws, rng: str, grade: str | None, *, provisional: bool = False, none_text: str = c.UNJUDGED):
    if grade is None:
        return c.unjudged(ws, rng) if none_text == c.UNJUDGED else c.put(ws, rng, none_text, align=c.CENTER)
    if grade == "-":
        return c.put(ws, rng, "-", align=c.CENTER)
    if provisional:
        return c.put(ws, rng, f"{grade}(暫定)", font=c.F_UNJUDGED, fill=GRADE_FILL[grade], align=c.CENTER)
    return c.put(ws, rng, grade, font=c.F_BOLD, fill=GRADE_FILL[grade], align=c.CENTER)


def _survey_date(signs: list[Sign]) -> str | None:
    ds = sorted({d for d in (c.fmt_date(s.last_inspected) for s in signs) if d})
    if not ds:
        return None
    return ds[0] if len(ds) == 1 else f"{ds[0]} 〜 {ds[-1]}"


def _diff(sign: Sign) -> str:
    if sign.prev_rank is None:
        return "前回なし"
    if sign.rank is None:
        return f"前回 {GRADE[sign.prev_rank]}"
    if sign.rank == sign.prev_rank:
        return f"前回 {GRADE[sign.prev_rank]}(変化なし)"
    return f"前回 {GRADE[sign.prev_rank]} から" + ("悪化" if sign.rank > sign.prev_rank else "改善")


# ---------------------------------------------------------------- 表紙
def _cover(ws, data: c.SiteData):
    for col, w in zip("ABCDEF", (18, 20, 14, 14, 14, 14)):
        ws.column_dimensions[col].width = w
    c.banner(ws, "A1:F1")
    ws.row_dimensions[1].height = 22
    c.put(ws, "A3:F3", "屋外広告物 安全点検報告書", font=c.F_TITLE, align=c.CENTER, border=False)
    ws.row_dimensions[3].height = 36
    signs = data.signs
    survey = _survey_date(signs)
    rows = [("現場名", data.site.name), ("所在地", data.site.address), ("調査日(YYYY-MM-DD)", survey),
            ("看板の数", f"{len(signs)} 基")]
    r = 5
    for label, val in rows:
        c.put(ws, f"A{r}", label, font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
        if val is None:
            c.unjudged(ws, f"B{r}:F{r}", align=c.LEFT)
        else:
            c.put(ws, f"B{r}:F{r}", val)
        ws.row_dimensions[r].height = 24
        r += 1
    g, note = site_grade(signs)
    c.put(ws, f"A{r}", "総合評価", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    if g is None:
        c.unjudged(ws, f"B{r}")
    else:
        _grade_cell(ws, f"B{r}", g)
        ws[f"B{r}"].font = c.Font(name=c.FONT, size=20, bold=True)
    c.put(ws, f"C{r}:F{r}", (f"{GRADE_LABEL[g]}" if g else note) or None, font=c.F_BASE if g else c.F_UNJUDGED)
    ws.row_dimensions[r].height = 40
    r += 2
    # 内訳
    c.put(ws, f"A{r}:F{r}", "看板の総合評価の内訳", font=c.F_BOLD, border=False)
    r += 1
    counts = {k: 0 for k in "ABCD"}
    n_un = 0
    for s in signs:
        sg = sign_grade(s)
        if sg is None:
            n_un += 1
        else:
            counts[sg] += 1
    for i, k in enumerate("ABCD"):
        col = "BCDE"[i]
        c.put(ws, f"{col}{r}", f"{k} {GRADE_LABEL[k]}", font=c.F_SMALL, fill=GRADE_FILL[k], align=c.CENTER)
        c.put(ws, f"{col}{r + 1}", f"{counts[k]} 基", align=c.CENTER)
    c.put(ws, f"F{r}", c.UNJUDGED, font=c.F_UNJUDGED, align=c.CENTER)
    c.put(ws, f"F{r + 1}", f"{n_un} 基", align=c.CENTER)
    c.put(ws, f"A{r}:A{r + 1}", "基数", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    r += 3
    c.put(ws, f"A{r}:F{r}", "評価 A=良好 / B=経過観察 / C=要改善 / D=即時対応。総合は有資格者が確定したランクによる。",
          font=c.F_SMALL, border=False)
    r += 2
    for label in ("点検実施者 氏名", "資格(名称・番号)", "確認・署名日(YYYY-MM-DD)と署名"):
        c.put(ws, f"A{r}", label, font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
        c.put(ws, f"B{r}:F{r}", None, fill=c.FILL_INPUT)
        ws.row_dimensions[r].height = 28
        r += 1
    c.page_setup(ws)
    c.add_watermark(ws, col0=1, row0=4, dx=30, dy=0)


# ---------------------------------------------------------------- 看板一覧
LIST_COLS = [("No", 5), ("看板ID", 12), ("名称", 24), ("種類", 9), ("寸法", 18), ("腐食", 10), ("破損", 10),
             ("固定", 10), ("総合", 8), ("前回との差", 16), ("所見", 40), ("写真", 6), ("未判定項目", 10)]


def _list(ws, data: c.SiteData, sheet_names: list[str]):
    for i, (_, w) in enumerate(LIST_COLS, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    last = get_column_letter(len(LIST_COLS))
    c.banner(ws, f"A1:{last}1")
    ws.row_dimensions[1].height = 22
    c.put(ws, f"A2:{last}2", f"看板一覧  {data.site.name}", font=c.F_TITLE, align=c.LEFT, border=False)
    ws.row_dimensions[2].height = 30
    for i, (h, _) in enumerate(LIST_COLS, 1):
        c.put(ws, f"{get_column_letter(i)}4", h, font=c.F_BOLD, fill=c.FILL_HEAD, align=c.CENTER)
    ws.row_dimensions[4].height = 24
    for n, (s, sn) in enumerate(zip(data.signs, sheet_names), 1):
        r = 4 + n
        ws.row_dimensions[r].height = 36
        c.put(ws, f"A{r}", n, align=c.CENTER)
        idc = c.put(ws, f"B{r}", s.id, align=c.LEFT)
        idc.hyperlink = f"#'{sn}'!A1"
        idc.font = c.Font(name=c.FONT, size=10, color="0563C1", underline="single")
        c.put(ws, f"C{r}", s.name)
        c.put(ws, f"D{r}", s.type) if s.type else c.unjudged(ws, f"D{r}")
        c.put(ws, f"E{r}", c.size_text(s)) if c.size_text(s) else c.unjudged(ws, f"E{r}")
        for col, p in zip("FGH", PERSPECTIVES):
            g, prov = perspective_grade(s, p)
            _grade_cell(ws, f"{col}{r}", g, provisional=prov)
        _grade_cell(ws, f"I{r}", sign_grade(s))
        c.put(ws, f"J{r}", _diff(s), align=c.CENTER)
        c.put(ws, f"K{r}", s.remark or None)
        c.put(ws, f"L{r}", min(len(s.photos), c.MAX_PHOTOS), align=c.CENTER)
        n_un = len(c.unjudged_item_keys(s))
        if n_un:
            c.put(ws, f"M{r}", f"{n_un} / 18", font=c.F_UNJUDGED, align=c.CENTER)
        else:
            c.put(ws, f"M{r}", 0, align=c.CENTER)
    r = 4 + len(data.signs) + 2
    c.put(ws, f"A{r}:{last}{r}",
          "腐食・破損・固定は、18項目のうち当たる項目の最悪判定(A 良好 / B 経過観察 / C 要改善)。赤字の「(暫定)」はその視点の項目に未記入が残る印。"
          "総合は有資格者が確定したランク。「写真」は載せた枚数(最大6)。",
          font=c.F_SMALL, border=False, align=c.Alignment(wrap_text=True, vertical="top"))
    ws.row_dimensions[r].height = 30
    c.page_setup(ws, landscape=True)
    c.add_watermark(ws, col0=3, row0=4, dx=0, dy=0)


# ---------------------------------------------------------------- 看板別ページ
def _sign_page(ws, data: c.SiteData, sign: Sign):
    for i in range(1, 7):
        ws.column_dimensions[get_column_letter(i)].width = 16
    c.banner(ws, "A1:F1")
    ws.row_dimensions[1].height = 22
    c.put(ws, "A2:F2", f"看板 {sign.id}  {sign.name or ''}", font=c.F_TITLE, align=c.LEFT, border=False)
    ws.row_dimensions[2].height = 28
    info = [("現場", data.site.name, "種類", sign.type), ("寸法", c.size_text(sign), "調査日(YYYY-MM-DD)", c.fmt_date(sign.last_inspected))]
    r = 3
    for l1, v1, l2, v2 in info:
        c.put(ws, f"A{r}", l1, font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
        c.put(ws, f"B{r}:C{r}", v1) if v1 else c.unjudged(ws, f"B{r}:C{r}", align=c.LEFT)
        c.put(ws, f"D{r}", l2, font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
        c.put(ws, f"E{r}:F{r}", v2) if v2 else c.unjudged(ws, f"E{r}:F{r}", align=c.LEFT)
        ws.row_dimensions[r].height = 22
        r += 1
    c.put(ws, f"A{r}", "総合評価", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    _grade_cell(ws, f"B{r}", sign_grade(sign))
    if sign.rank is not None:
        ws[f"B{r}"].value = f"{GRADE[sign.rank]}  {GRADE_LABEL[GRADE[sign.rank]]}"
    c.put(ws, f"C{r}", "腐食 / 破損 / 固定", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    for col, p in zip("DEF", PERSPECTIVES):
        g, prov = perspective_grade(sign, p)
        _grade_cell(ws, f"{col}{r}", g, provisional=prov)
        if g not in (None, "-"):
            ws[f"{col}{r}"].value = f"{p} {ws[f'{col}{r}'].value}"
        elif g is None:
            ws[f"{col}{r}"].value = f"{p} {c.UNJUDGED}"
    ws.row_dimensions[r].height = 26
    r += 1
    c.put(ws, f"A{r}", "所見", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"B{r}:F{r}", sign.remark or None, fill=None if sign.remark else c.FILL_INPUT)
    ws.row_dimensions[r].height = 40
    r += 2
    # 18項目
    c.put(ws, f"A{r}", "点検箇所", font=c.F_BOLD, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"B{r}:D{r}", "点検項目", font=c.F_BOLD, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"E{r}", "判定", font=c.F_BOLD, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"F{r}", "評価", font=c.F_BOLD, fill=c.FILL_HEAD, align=c.CENTER)
    r += 1
    start: dict[str, int] = {}
    for key, group, text in c.ITEM_ROWS:
        start.setdefault(group, r)
        ws.row_dimensions[r].height = 30
        c.put(ws, f"B{r}:D{r}", text, font=c.Font(name=c.FONT, size=9))
        res = sign.items.get(key)
        if res is None:
            c.unjudged(ws, f"E{r}")
            c.put(ws, f"F{r}", None, align=c.CENTER)
        else:
            rk = RESULT_TO_RANK[res]
            c.put(ws, f"E{r}", res, align=c.CENTER)
            _grade_cell(ws, f"F{r}", "-" if rk is None else GRADE[rk])
        r += 1
    counts: dict[str, int] = {}
    for _, g, _ in c.ITEM_ROWS:
        counts[g] = counts.get(g, 0) + 1
    for g, n in counts.items():
        c.put(ws, f"A{start[g]}:A{start[g] + n - 1}", g, font=c.Font(name=c.FONT, size=9),
              align=c.Alignment(horizontal="center", vertical="center", wrap_text=True))
    wm_row = start[next(iter(start))]
    r += 1
    c.put(ws, f"A{r}:F{r}", "写真(最大6枚)", font=c.F_BOLD, border=False)
    r += 1
    notes = c.photo_grid(ws, data, sign, first_row=r)
    r += 6
    if notes:
        c.put(ws, f"A{r}:F{r}", " / ".join(notes), font=c.F_UNJUDGED, border=False)
    c.page_setup(ws)
    c.add_watermark(ws, col0=1, row0=wm_row - 1, dx=40, dy=0)


def build_workbook(data: c.SiteData) -> Workbook:
    wb = Workbook()
    cover = wb.active
    cover.title = "表紙"
    _cover(cover, data)
    names = c.unique_sheet_names("看板", [s.id for s in data.signs])
    _list(wb.create_sheet("看板一覧"), data, names)
    for s, n in zip(data.signs, names):
        _sign_page(wb.create_sheet(n), data, s)
    wb.properties.title = f"安全点検報告書(下書き) {data.site.name}"
    wb.properties.creator = "看板カルテ"
    return wb


def write_client_a(ledger: Ledger, site_id: str, out_path) -> list[str]:
    """現場 site_id の客先A型 xlsx を out_path に書く。返り値は書いた xlsx のシート名。"""
    data = c.load_site(ledger, site_id)
    wb = build_workbook(data)
    wb.save(out_path)
    return wb.sheetnames
