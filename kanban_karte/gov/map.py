"""突き合わせの結果を1枚の静的 HTML 地図にする。

- 地図ライブラリ: Leaflet 1.9.4(cdnjs から読み込む。SRI 付き)。
- タイル: 国土地理院タイル「淡色地図」https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png
  一覧と利用条件 https://maps.gsi.go.jp/development/ichiran.html 。出典(地理院タイル)を地図の右下に必ず出す。
  (OpenStreetMap の標準タイルは使わない。使う場合は帰属表示と利用規約 https://operations.osmfoundation.org/policies/tiles/ が要る。)
- 点の色: 赤=台帳に無い/期限切れ  橙=期限90日以内・許可情報の不備  緑=許可あり/不要  灰=位置なし。
  位置なしの写真は地図に置けないので、左の一覧に灰で出す。
- 件数は HTML 本文に直接書く(スクリプトが動かなくても読める)。データは JSON で埋め込み、画面の組み立ては textContent で行う。
- 市民からの報告の層(citizen= citizen.store.Store.summary()): 紫の点線の丸。ピンと件数だけ(丸めた位置・状態・日付)。
  写真・一言・住所は地図に入れない。層は右上の切り替えで消せる。
"""
from __future__ import annotations

import html
import json
from collections import Counter
from datetime import date
from pathlib import Path

from ..ledger.db import Sign
from .match import STATUS_COLOR, STATUS_LABEL, MatchResult

LEAFLET_JS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.js"
LEAFLET_JS_SRI = "sha512-puJW3E/qXDqYp9IfhAI54BJEaWIfloJ7JWs7OeD5i6ruC9JZL1gERT1wjtwXFlh7CjE7ZJ+/vcRZRkIYIb6p4g=="
LEAFLET_CSS = "https://cdnjs.cloudflare.com/ajax/libs/leaflet/1.9.4/leaflet.min.css"
LEAFLET_CSS_SRI = "sha512-h9FcoyWjHcOcmEVkxOfTLnmZFWIH0iZhZT1H2TbOq55xssQGEJHEaIm+PgoUaZbRvQTNTluNOEfb1ZRy6D3BOw=="
TILE_URL = "https://cyberjapandata.gsi.go.jp/xyz/pale/{z}/{x}/{y}.png"

COLORS = {"red": "#c62828", "orange": "#ef6c00", "green": "#2e7d32", "gray": "#757575"}
COLOR_NAME = {"red": "赤", "orange": "橙", "green": "緑", "gray": "灰"}
CITIZEN_COLORS = {"未把握": "#7b1fa2", "把握済み": "#9c7cc0", "点検済み": "#00897b", "受付": "#9e9e9e"}
CITIZEN_MEANING = {"未把握": "台帳の看板が近く(30 m)に無い", "把握済み": "台帳の看板と一致", "点検済み": "報告の後に点検された",
                   "受付": "位置が無い(地図に置けない)"}

COLOR_MEANING = {
    "red": "台帳に無い(無許可の疑い・要確認)/ 許可期限切れ",
    "orange": "許可の期限が近い(90日以内)/ 台帳の許可情報が未記入",
    "green": "許可あり / 台帳で許可不要",
    "gray": "位置なし(GPS の無い写真)",
}


def summarize(results: list[MatchResult]) -> dict:
    by_color = Counter(r.color for r in results)
    by_status = Counter(r.status for r in results)
    return {"total": len(results), "by_color": {c: by_color.get(c, 0) for c in COLORS},
            "by_status": dict(by_status)}


def _result_json(r: MatchResult) -> dict:
    c = r.cand
    d = {
        "color": r.color, "status": r.status, "label": r.label, "note": r.note,
        "lat": c.lat, "lng": c.lng, "photos": c.photos, "n_photos": len(c.photos),
        "first_taken": c.first_taken, "last_taken": c.last_taken,
        "place": c.place if c.place else ("自治体不明(未判定)" if not c.geocoded and c.lat is not None else None),
        "in_scope": (c.pref in ("東京都", "埼玉県")) if c.pref else None,
        "distance_m": None if r.distance_m is None else round(r.distance_m, 1),
        "days_left": r.days_left, "permit_expires": r.permit_expires, "renew_from": r.renew_from,
        "sign": None,
    }
    if r.sign is not None:
        s = r.sign
        d["sign"] = {"id": s.id, "name": s.name, "type": s.type, "address": s.address,
                     "permit_required": s.permit_required}
    return d


def _json_for_script(obj) -> str:
    return json.dumps(obj, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")


def render_map(results: list[MatchResult], *, today: date, ledger_signs: list[Sign] | None = None,
               warn_days: int = 90, title: str = "巡回調査マップ", citizen: dict | None = None) -> str:
    sm = summarize(results)
    n_pos = sum(1 for r in results if r.status != "no_position")
    n_nopos = sm["by_color"]["gray"]
    signs_with_pos = [s for s in (ledger_signs or []) if s.lat is not None and s.lng is not None]
    confirmed = {r.sign.id for r in results if r.sign is not None}
    unseen = [s for s in signs_with_pos if s.id not in confirmed]
    esc = html.escape

    legend_rows = []
    for col in ("red", "orange", "green", "gray"):
        n = sm["by_color"][col]
        legend_rows.append(
            f'<li><span class="dot" style="background:{COLORS[col]}"></span>'
            f'<span class="lg-name">{COLOR_NAME[col]}</span>'
            f'<span class="lg-n" data-count="{col}">{n}</span>'
            f'<span class="lg-mean">{esc(COLOR_MEANING[col])}</span></li>')
    status_rows = "".join(
        f'<li data-status="{k}"><span class="dot sm" style="background:{COLORS[STATUS_COLOR[k]]}"></span>'
        f'{esc(STATUS_LABEL[k])}: <b>{sm["by_status"].get(k, 0)}</b></li>'
        for k in STATUS_LABEL if sm["by_status"].get(k, 0))

    data = {"results": [_result_json(r) for r in results], "colors": COLORS, "warn_days": warn_days,
            "today": today.isoformat(),
            "citizen": {"pins": (citizen or {}).get("pins", []), "colors": CITIZEN_COLORS} if citizen else None}
    citizen_html = ""
    if citizen:
        rows = "".join(
            f'<li><span class="dot cz" style="border-color:{CITIZEN_COLORS[k]}"></span><span class="lg-name">{esc(k)}</span>'
            f'<span class="lg-n" data-citizen="{esc(k)}">{citizen["by_status"].get(k, 0)}</span><span class="lg-mean">{esc(CITIZEN_MEANING[k])}</span></li>'
            for k in ("未把握", "把握済み", "点検済み", "受付"))
        citizen_html = (f'<h2>市民からの報告 <span id="citizen-total">{citizen["total"]}</span> 件</h2><ul class="legend cz-legend">{rows}</ul>'
                        '<p class="note">ピンと件数だけを出します(写真・一言・住所は出しません)。位置は約 100 m に丸めています。'
                        '右上の切り替えで層を消せます。</p>')
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(title)}</title>
<link rel="stylesheet" href="{LEAFLET_CSS}" integrity="{LEAFLET_CSS_SRI}" crossorigin="anonymous" referrerpolicy="no-referrer">
<style>
:root {{ --bg:#f6f7f8; --fg:#1d2126; --muted:#5b6670; --card:#fff; --line:#d9dee3; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#15181b; --fg:#e8eaed; --muted:#9aa4ad; --card:#1f2327; --line:#333a40; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; display:flex; flex-direction:column; min-height:100vh; font-family:"Hiragino Sans","Hiragino Kaku Gothic ProN",system-ui,sans-serif; background:var(--bg); color:var(--fg); }}
header {{ padding:12px 16px; border-bottom:1px solid var(--line); background:var(--card); }}
header h1 {{ margin:0; font-size:18px; }}
header p {{ margin:4px 0 0; font-size:13px; color:var(--muted); }}
.layout {{ display:flex; flex-direction:column; flex:1; min-height:0; }}
#map {{ height:60vh; min-height:320px; }}
aside {{ padding:12px 16px; background:var(--card); border-top:1px solid var(--line); }}
@media (min-width: 900px) {{
  body {{ height:100vh; }}
  .layout {{ flex-direction:row; }}
  #map {{ flex:1; height:auto; }}
  aside {{ width:380px; flex:none; overflow:auto; border-top:0; border-left:1px solid var(--line); }}
}}
@media (max-width: 899px) {{
  /* スマホ(画面に埋め込んだ iframe の中): 地図を低めにし、凡例を 2×2 に詰めて 4 色とも見えるようにする */
  header {{ padding:8px 12px; }}
  header h1 {{ font-size:15px; }}
  header p {{ font-size:11.5px; }}
  #map {{ height:44vh; min-height:240px; }}
  aside {{ padding:8px 12px; }}
  .legend {{ display:grid; grid-template-columns:1fr 1fr; gap:4px 12px; }}
  .legend li {{ grid-template-columns:14px auto 1fr; gap:4px; padding:3px 0; border-bottom:0; }}
  .legend .lg-n {{ text-align:left; font-size:16px; }}
  .legend .lg-mean {{ grid-column:1 / -1; font-size:10.5px; line-height:1.35; }}
}}
h2 {{ font-size:14px; margin:14px 0 6px; }}
h2:first-child {{ margin-top:0; }}
ul {{ list-style:none; margin:0; padding:0; }}
.legend li {{ display:grid; grid-template-columns:18px 2em 3em 1fr; align-items:center; gap:6px; padding:5px 0; font-size:13px; border-bottom:1px solid var(--line); }}
.legend .lg-n {{ font-weight:700; font-size:18px; text-align:right; }}
.legend .lg-mean {{ color:var(--muted); font-size:12px; }}
.dot {{ display:inline-block; width:14px; height:14px; border-radius:50%; border:2px solid #fff; box-shadow:0 0 0 1px #0004; }}
.dot.cz {{ background:transparent; border:2px dashed; box-shadow:none; }}
.legend.cz-legend li {{ grid-template-columns:18px 4.5em 2em 1fr; }}
.dot.sm {{ width:10px; height:10px; margin-right:6px; border-width:1px; }}
.stat li {{ font-size:12.5px; padding:2px 0; color:var(--muted); }}
.stat li b {{ color:var(--fg); }}
.list li {{ padding:7px 8px; border:1px solid var(--line); border-radius:6px; margin-bottom:6px; font-size:12.5px; cursor:pointer; }}
.list li:hover, .list li:focus {{ background:var(--bg); outline:none; }}
.list .t {{ font-weight:600; }}
.list .m {{ color:var(--muted); }}
.note {{ font-size:12px; color:var(--muted); margin-top:12px; line-height:1.6; }}
.popup {{ font-size:12.5px; line-height:1.55; min-width:210px; }}
.popup b.hd {{ font-size:13.5px; }}
.popup .tag {{ display:inline-block; color:#fff; border-radius:3px; padding:0 6px; margin-bottom:4px; }}
</style>
</head>
<body>
<header>
  <h1>{esc(title)}</h1>
  <p>基準日 {today.isoformat()}(期限の判定に使った日) / 看板の候補 {n_pos} 基 / 位置なしの写真 {n_nopos} 枚</p>
</header>
<div class="layout">
  <div id="map" role="application" aria-label="巡回調査の地図"></div>
  <aside>
    <h2>凡例と件数</h2>
    <ul class="legend" id="legend">{''.join(legend_rows)}</ul>
    {citizen_html}
    <h2>内訳</h2>
    <ul class="stat">{status_rows or '<li>なし</li>'}</ul>
    <p class="note" id="ledger-note">台帳(位置つき)の看板 {len(signs_with_pos)} 基のうち、今回の巡回で写真と一致したのは {len(confirmed)} 基、
    写っていなかったのは {len(unseen)} 基{('(' + '、'.join(esc(s.name or s.id) for s in unseen[:5]) + ('ほか' if len(unseen) > 5 else '') + ')') if unseen else ''}。</p>
    <h2>要対応(赤・橙)</h2>
    <ul class="list" id="todo"></ul>
    <h2>位置なしの写真(灰)</h2>
    <ul class="list" id="nopos"></ul>
    <p class="note">
      このデモでは、自治体の許可台帳の代わりに自社の施工台帳を使っています。「台帳に無い」は無許可と決まったという意味ではなく、
      台帳に載っていない(または位置が30m以上離れている)ということで、現地確認が要ります。
      更新手続きの目安日は、期限の{warn_days}日前としたこの画面の設定値で、条例の定めではありません。<br>
      地図: <a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>(淡色地図) /
      市区町村の判定: 国土地理院 逆ジオコーダ
    </p>
  </aside>
</div>
<script src="{LEAFLET_JS}" integrity="{LEAFLET_JS_SRI}" crossorigin="anonymous" referrerpolicy="no-referrer"></script>
<script>
const DATA = {_json_for_script(data)};
(function () {{
  function el(tag, cls, text) {{ const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; }}
  function row(p, k, v) {{ if (v == null || v === "") return; const d = el("div"); d.appendChild(el("span", null, k + ": ")); d.appendChild(el("span", null, v)); p.appendChild(d); }}
  function fmtT(t) {{ return t ? t.replace("T", " ") : null; }}
  function popup(r) {{
    const p = el("div", "popup");
    const tag = el("span", "tag", r.label); tag.style.background = DATA.colors[r.color]; p.appendChild(tag);
    if (r.sign) {{ p.appendChild(el("div")).appendChild(el("b", "hd", r.sign.name || r.sign.id)); row(p, "台帳の看板", r.sign.id + (r.sign.type ? "(" + r.sign.type + ")" : "")); row(p, "台帳の住所", r.sign.address); }}
    row(p, "判定", r.note);
    if (r.permit_expires) {{ row(p, "許可の期限", r.permit_expires + (r.days_left != null ? "(" + (r.days_left >= 0 ? "残り" + r.days_left + "日" : -r.days_left + "日超過") + ")" : "")); }}
    if (r.renew_from) row(p, "更新手続きの目安", r.renew_from + " ごろから(期限の" + DATA.warn_days + "日前・設定値)");
    if (r.distance_m != null) row(p, "台帳との距離", r.distance_m + " m");
    row(p, "自治体", r.place); if (r.in_scope === false) row(p, "注意", "対象自治体(東京都・埼玉県)の外");
    row(p, "写真", r.n_photos + " 枚"); row(p, "撮影", r.first_taken ? fmtT(r.first_taken) + (r.last_taken && r.last_taken !== r.first_taken ? " 〜 " + fmtT(r.last_taken) : "") : null);
    row(p, "ファイル", r.photos.slice(0, 6).join(", ") + (r.photos.length > 6 ? " ほか" : ""));
    return p;
  }}
  function item(r, onclick) {{
    const li = el("li"); li.tabIndex = 0;
    const t = el("div", "t"); const d = el("span", "dot sm"); d.style.background = DATA.colors[r.color]; t.appendChild(d);
    t.appendChild(document.createTextNode((r.sign ? (r.sign.name || r.sign.id) : (r.lat == null ? r.photos[0] : "台帳に無い候補")) + " : " + r.label));
    li.appendChild(t); li.appendChild(el("div", "m", [r.place, r.note].filter(Boolean).join(" / ")));
    li.addEventListener("click", onclick); li.addEventListener("keydown", function (e) {{ if (e.key === "Enter") onclick(); }});
    return li;
  }}
  const map = L.map("map", {{ zoomControl: true }});
  L.tileLayer({_json_for_script(TILE_URL)}, {{ minZoom: 5, maxZoom: 18,
    attribution: '<a href="https://maps.gsi.go.jp/development/ichiran.html" target="_blank" rel="noopener">地理院タイル</a>(淡色地図)' }}).addTo(map);
  const pts = [], layers = [];
  DATA.results.forEach(function (r) {{
    if (r.lat == null) return;
    const m = L.circleMarker([r.lat, r.lng], {{ radius: 10, color: "#fff", weight: 2, fillColor: DATA.colors[r.color], fillOpacity: 0.95 }}).addTo(map);
    m.bindPopup(function () {{ return popup(r); }}); m.bindTooltip(r.sign ? (r.sign.name || r.sign.id) : "台帳に無い候補");
    pts.push([r.lat, r.lng]); layers.push([r, m]);
  }});
  if (DATA.citizen) {{
    map.createPane("citizen"); map.getPane("citizen").style.zIndex = 650;   // 巡回の点の上に、輪だけを重ねる(下の点が見える)
    const cz = L.layerGroup();
    DATA.citizen.pins.forEach(function (c) {{
      const col = DATA.citizen.colors[c.status] || "#7b1fa2";
      L.circleMarker([c.lat, c.lng], {{ radius: 13, color: col, weight: 3, dashArray: "4 3", fill: false, pane: "citizen" }})
        .bindTooltip("市民からの報告: " + c.status + "(" + c.date + ")").addTo(cz);
      pts.push([c.lat, c.lng]);
    }});
    cz.addTo(map);
    L.control.layers(null, {{ "市民からの報告": cz }}, {{ collapsed: false }}).addTo(map);
  }}
  if (pts.length) map.fitBounds(pts, {{ padding: [40, 40], maxZoom: 17 }}); else map.setView([36.0, 138.0], 6);
  function focus(r) {{ const f = layers.find(function (x) {{ return x[0] === r; }}); if (f) {{ map.setView([r.lat, r.lng], Math.max(map.getZoom(), 17)); f[1].openPopup(); }} }}
  const todo = document.getElementById("todo"), nopos = document.getElementById("nopos");
  DATA.results.forEach(function (r) {{
    if (r.color === "red" || r.color === "orange") todo.appendChild(item(r, function () {{ focus(r); }}));
    if (r.lat == null) nopos.appendChild(item(r, function () {{ alert(r.photos.join("\\n")); }}));
  }});
  if (!todo.children.length) todo.appendChild(el("li", "m", "なし"));
  if (!nopos.children.length) nopos.appendChild(el("li", "m", "なし"));
}})();
</script>
</body>
</html>
"""


def write_map(path: str | Path, results: list[MatchResult], **kw) -> dict:
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_map(results, **kw), encoding="utf-8")
    return summarize(results)
