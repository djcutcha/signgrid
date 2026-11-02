"""東京都 屋外広告物安全点検報告書(第2号様式、2026-04-01 施行)の xlsx を、台帳の現場1件から作る。

様式の原文(2026-11-02 に確認): 様式 PDF https://www.toshiseibi.metro.tokyo.lg.jp/documents/d/toshiseibi/pdf_tenken_kaisei_2025-1
  表: 報告者(住所・氏名・電話)/ 屋外広告物管理者(住所・氏名・電話・資格)/ 広告物等の種類 / 表示又は設置の場所 /
      設置年月日・点検年月日 / 前回許可年月日・番号 / 18項目 × 4区分(良好・経過観察・要改善・対象外に○)+ 異常の内容と改善の内容
  裏(注): 要改善は補修したうえで異常と改善の内容を記入。写真(全景・表示面、要改善は補修前後)を添付、様式は任意。
様式に「総合所見欄」「写真欄」は無い。ここでは総合所見(台帳の rank と remark)を表の下に、写真を任意様式の別シートに足している。

出力は看板1基につき2シート(様式_<id> と 写真_<id>)。様式は1広告物ごとに出すものなので、現場に複数の看板があれば組になって並ぶ。
"""
from __future__ import annotations

from openpyxl import Workbook

from ..ledger.db import Ledger, Sign
from ..ledger.items import RESULTS
from . import common as c

# 列: A 点検箇所 / B 点検項目 / C..F 良好・経過観察・要改善・対象外 / G 異常の内容と改善の内容
WIDTHS = [6, 46, 7.5, 7.5, 7.5, 7.5, 38]
RESULT_COL = {r: "CDEF"[i] for i, r in enumerate(RESULTS)}
NEEDS_DETAIL = ("経過観察", "要改善")  # 様式の注5・6: 異常の内容を書く
NEED_TO_WRITE = "要記入"


def _form_rows(ws, sign: Sign, address: str | None):
    """様式シートを組む。項目行の位置は返り値(キー -> 行番号)。"""
    for i, w in enumerate(WIDTHS):
        ws.column_dimensions["ABCDEFG"[i]].width = w

    c.banner(ws, "A1:G1")
    ws.row_dimensions[1].height = 22
    c.put(ws, "A2:G2", "第２号様式(第１条関係)", font=c.F_SMALL, border=False)
    c.put(ws, "A3:G3", "屋 外 広 告 物 安 全 点 検 報 告 書", font=c.F_TITLE, align=c.CENTER, border=False)
    ws.row_dimensions[3].height = 30
    c.put(ws, "A4:G4", "東京都屋外広告物条例施行規則第１条第３項の規定により、下記のとおり報告します。", border=False)
    c.put(ws, "A5:E5", "報告日(YYYY-MM-DD)", font=c.F_SMALL, align=c.Alignment(horizontal="right", vertical="center"), border=False)
    c.put(ws, "F5:G5", None, fill=c.FILL_INPUT)
    c.put(ws, "A6:G6", "東京都知事 殿", border=False)

    r = 7
    for block, fields in (("報告者(申請者)", ("住所", "氏名", "電話")),
                          ("屋外広告物管理者", ("住所", "氏名", "電話", "資格"))):
        c.put(ws, f"A{r}:B{r + len(fields) - 1}", block, font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
        for f in fields:
            c.put(ws, f"C{r}", f, font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
            c.put(ws, f"D{r}:G{r}", None, fill=c.FILL_INPUT)  # 人が書く欄(資格は有資格者が記入)
            r += 1
        if block.startswith("報告者"):
            c.put(ws, f"A{r}:G{r}", "(法人にあっては、事務所の所在地、名称及び代表者の氏名)　下記の結果は、事実に相違ありません。",
                  font=c.F_SMALL, border=False)
            r += 1
    c.put(ws, f"A{r}:G{r}", "記", align=c.CENTER, border=False)
    r += 1

    def field(row, label, value, rng_value, *, input_fill=False):
        c.put(ws, f"A{row}:B{row}", label, font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
        if value is None and not input_fill:
            c.unjudged(ws, rng_value, align=c.LEFT)
        else:
            c.put(ws, rng_value, value, fill=c.FILL_INPUT if input_fill and value is None else None)

    field(r, "広告物等の種類", sign.type, f"C{r}:G{r}")
    r += 1
    place = sign.address or address
    field(r, "表示又は設置の場所", place, f"C{r}:G{r}")
    r += 1
    field(r, "設置年月日(YYYY-MM-DD)", None, f"C{r}:D{r}", input_fill=True)
    c.put(ws, f"E{r}:F{r}", "点検年月日", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    di = c.fmt_date(sign.last_inspected)
    if di:
        c.put(ws, f"G{r}", di)
    else:
        c.unjudged(ws, f"G{r}", align=c.LEFT)
    r += 1
    field(r, "前回許可年月日・番号", None, f"C{r}:G{r}", input_fill=True)
    r += 1
    c.put(ws, f"A{r}:G{r}",
          f"台帳: 看板 {sign.id}  {sign.name or ''}  {c.size_text(sign) or ''}", font=c.F_SMALL, border=False)
    r += 1

    # ---- 点検表
    head1, head2 = r, r + 1
    c.put(ws, f"A{head1}:A{head2}", "点検\n箇所", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"B{head1}:B{head2}", "点検項目", font=c.F_BOLD, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"C{head1}:F{head1}", "点検結果", font=c.F_BOLD, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"G{head1}:G{head2}", "異常の内容と改善の内容", font=c.F_BOLD, fill=c.FILL_HEAD, align=c.CENTER)
    for res in RESULTS:
        c.put(ws, f"{RESULT_COL[res]}{head2}", res.replace("経過観察", "経過\n観察").replace("要改善", "要\n改善").replace("対象外", "対象\n外"),
              font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    ws.row_dimensions[head2].height = 28
    r = head2 + 1

    item_row: dict[str, int] = {}
    group_start: dict[str, int] = {}
    for key, group, text in c.ITEM_ROWS:
        group_start.setdefault(group, r)
        item_row[key] = r
        ws.row_dimensions[r].height = 30
        c.put(ws, f"B{r}", text, font=c.Font(name=c.FONT, size=9))
        for col in "CDEF":
            c.put(ws, f"{col}{r}", None, align=c.CENTER)
        res = sign.items.get(key)
        if res is None:
            c.unjudged(ws, f"G{r}", align=c.LEFT)
        else:
            ws[f"{RESULT_COL[res]}{r}"].value = "○"
            ws[f"{RESULT_COL[res]}{r}"].font = c.Font(name=c.FONT, size=12, bold=True)
            if res in NEEDS_DETAIL:
                # 項目ごとの異常内容は台帳にまだ無い。有資格者が書く欄として残す
                c.put(ws, f"G{r}", NEED_TO_WRITE, font=c.Font(name=c.FONT, size=9, color="9C5700"), fill=c.FILL_INPUT)
            else:
                c.put(ws, f"G{r}", None)
        r += 1
    # 点検箇所(縦書き、結合)
    groups: dict[str, int] = {}
    for _, g, _ in c.ITEM_ROWS:
        groups[g] = groups.get(g, 0) + 1
    for g, n in groups.items():
        top = group_start[g]
        c.put(ws, f"A{top}:A{top + n - 1}", g, font=c.Font(name=c.FONT, size=9),
              align=c.Alignment(horizontal="center", vertical="center", textRotation=255))

    # ---- 総合所見(様式にない欄。台帳の rank と remark)
    c.put(ws, f"A{r}:B{r}", "総合判定(台帳のランク)", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    if sign.rank is None:
        c.unjudged(ws, f"C{r}:G{r}", align=c.LEFT)
    else:
        c.put(ws, f"C{r}:G{r}", f"{sign.rank} {c.RANK_LABEL[sign.rank]}")
    r += 1
    c.put(ws, f"A{r}:B{r}", "総合所見", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"C{r}:G{r}", sign.remark or None, fill=c.FILL_INPUT if not sign.remark else None,
          align=c.Alignment(horizontal="left", vertical="top", wrap_text=True))
    ws.row_dimensions[r].height = 54
    r += 1
    # ---- 点検実施者(氏名・資格は空欄の入力欄)
    c.put(ws, f"A{r}:B{r}", "点検実施者 氏名", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"C{r}:G{r}", None, fill=c.FILL_INPUT)
    ws.row_dimensions[r].height = 22
    r += 1
    c.put(ws, f"A{r}:B{r}", "資格(名称・番号)", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"C{r}:G{r}", None, fill=c.FILL_INPUT)
    ws.row_dimensions[r].height = 22
    r += 1
    c.put(ws, f"A{r}:B{r}", "確認・署名日(YYYY-MM-DD)と署名", font=c.F_SMALL, fill=c.FILL_HEAD, align=c.CENTER)
    c.put(ws, f"C{r}:G{r}", None, fill=c.FILL_INPUT)
    ws.row_dimensions[r].height = 30
    r += 1
    c.put(ws, f"A{r}:G{r}",
          "良好=異常なし / 経過観察=安全上支障のない軽微な異常 / 要改善=安全上支障のある異常(補修のうえ内容を記入) / 対象外=該当項目なし。"
          "黄色の欄は人が記入。「未判定」は台帳にデータがない項目。(日本産業規格Ａ列４番)",
          font=c.F_SMALL, border=False, align=c.Alignment(wrap_text=True, vertical="top"))
    ws.row_dimensions[r].height = 36
    return item_row, head1 + 2


def _photo_sheet(ws, data: c.SiteData, sign: Sign):
    for col in "ABCDEF":
        ws.column_dimensions[col].width = 16
    c.banner(ws, "A1:F1")
    ws.row_dimensions[1].height = 22
    c.put(ws, "A2:F2", "点検写真(任意様式)", font=c.F_TITLE, align=c.CENTER, border=False)
    ws.row_dimensions[2].height = 28
    di = c.fmt_date(sign.last_inspected)
    c.put(ws, "A3:F3", f"{data.site.name}  /  看板 {sign.id} {sign.name or ''}  /  点検年月日 {di or c.UNJUDGED}",
          font=c.F_BASE, border=False)
    c.put(ws, "A4:F4", "全景と表示面の写真。要改善の項目は、補修前と補修後の写真を添える(様式の注7)。",
          font=c.F_SMALL, border=False)
    notes = c.photo_grid(ws, data, sign, first_row=5)
    last = 5 + 6
    if notes:
        c.put(ws, f"A{last}:F{last}", " / ".join(notes), font=c.F_UNJUDGED, border=False)
    c.page_setup(ws)


def build_workbook(data: c.SiteData) -> Workbook:
    wb = Workbook()
    wb.remove(wb.active)
    form_names = c.unique_sheet_names("様式", [s.id for s in data.signs])
    photo_names = c.unique_sheet_names("写真", [s.id for s in data.signs])
    for sign, fname, pname in zip(data.signs, form_names, photo_names):
        ws = wb.create_sheet(fname)
        _, first_item_row = _form_rows(ws, sign, data.site.address)
        c.page_setup(ws)
        c.add_watermark(ws, col0=1, row0=first_item_row - 1, dx=40, dy=10)
        _photo_sheet(wb.create_sheet(pname), data, sign)
    wb.properties.title = f"屋外広告物安全点検報告書(下書き) {data.site.name}"
    wb.properties.creator = "看板カルテ"
    return wb


def write_tokyo_form(ledger: Ledger, site_id: str, out_path) -> list[str]:
    """現場 site_id の第2号様式を out_path に書く。返り値は書いた xlsx のシート名。"""
    data = c.load_site(ledger, site_id)
    wb = build_workbook(data)
    wb.save(out_path)
    return wb.sheetnames
