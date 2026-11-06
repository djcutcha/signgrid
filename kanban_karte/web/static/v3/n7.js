// N7 報告書(本実装)。
// API: GET /api/sites / GET /api/signs?site=ID / GET /api/signs/ID / GET /api/sites/ID/output/{tokyo|client-a}.xlsx
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const RES = { "良好": "r1", "経過観察": "r2", "要改善": "r3", "対象外": "na" };
  const SITE = new URLSearchParams(location.search).get("site");
  // 出力 API(api.py OUTPUTS)にある 2 様式だけ置く
  const FORMS = [
    { kind: "tokyo", seg: "東京都 第2号様式", name: "点検報告書(東京都 第2号様式)", sub: "東京都 第2号様式", title: "屋外広告物等点検報告書" },
    { kind: "client-a", seg: "客先A型", name: "点検報告書(客先A型)", sub: "客先A型", title: "点検報告書(客先提出用)" },
  ];
  // 転記の照合の印。API に「紙の原本との照合」が無いので、訂正の有無と判断できずから出す
  const VM = { fix: ["✎", "人が直した", "m-fix"], chk: ["!", "要確認", "m-chk"] };
  let site = null, signs = [], details = [], form = 0;

  async function get(path) {
    const r = await fetch(path, { cache: "no-store" });
    let j = null; try { j = await r.json(); } catch (e) { /* 本文なし */ }
    if (!r.ok) throw new Error((j && j.error) || `通信に失敗しました(${r.status})`);
    return j;
  }
  function toast(t) { const e = $("toast"); e.textContent = t; e.classList.add("on"); clearTimeout(toast.t); toast.t = setTimeout(() => e.classList.remove("on"), 2800); }
  const q = encodeURIComponent;

  function itemRes(it) { return it.value || (it.guide === "na" || (it.evidence && it.evidence.guide === "na") ? "対象外" : null); }
  function mark(it) { return it.corrected ? "fix" : it.yellow ? "chk" : null; }
  function vmHtml(m) { return m ? `<span class="vm ${VM[m][2]}" title="転記の照合: ${VM[m][1]}" aria-label="転記の照合: ${VM[m][1]}">${VM[m][0]}</span>` : ""; }
  const first = (arr) => arr.filter(Boolean).sort()[0] || null;

  function paperHtml(d, f) {
    let rows = "", g = "";
    d.items.forEach((it) => {
      if (it.group !== g) { g = it.group; rows += `<tr><td class="g" colspan="2">${esc(g)}</td></tr>`; }
      const r = itemRes(it);
      rows += `<tr><td>${it.no}. ${esc(it.text)}</td><td class="r">${r ? `<span class="res ${RES[r]}">${esc(r)}</span>` : `<span class="unk">判断できず</span>`}${vmHtml(mark(it))}</td></tr>`;
    });
    const day = d.last_inspected || (d.confirmed_at ? String(d.confirmed_at).slice(0, 10) : null);
    const who = d.confirmed_by ? `${esc(d.confirmed_by)}(確定)` : "未署名(下書き)";
    const orderer = site && site.orderer_type ? `<dt>発注元</dt><dd>${esc(site.orderer_type)}</dd>` : "";
    return `<article class="paper" aria-label="報告書の見た目 ${esc(d.name || d.id)}">
      <div class="form-name"><span>${esc(f.name)}</span><span>${esc(f.sub)}</span></div>
      <h2>${esc(f.title)}</h2>
      <dl><dt>物件</dt><dd>${esc(d.site_name || (site && site.name) || "")}<br><span class="muted">${esc(d.address || (site && site.address) || "住所は未入力")}</span></dd>
        <dt>看板</dt><dd>${esc(d.name || d.id)} ${esc(d.type || "")} ${esc(d.size || "")}</dd>
        <dt>点検日</dt><dd class="num">${day ? esc(day) : "未確定"}</dd>
        <dt>点検者</dt><dd>${who}</dd>${orderer}</dl>
      <p class="legend-v"><b>転記の照合</b>${vmHtml("fix")}人が直した${vmHtml("chk")}要確認<span class="muted">(判断できなかった項目と、有資格者が訂正した項目に付けています)</span></p>
      <div class="table-wrap"><table class="t"><thead><tr><th>点検項目(${d.items.length} 項目)</th><th>結果</th></tr></thead><tbody>${rows}</tbody></table></div>
      <div class="total"><b>総合</b>${d.rank_label ? `<span class="res r${d.rank >= 3 ? 3 : d.rank}">${esc(d.rank_label)}</span>` : `<span class="muted">未判定</span>`}<span class="muted">${esc(d.remark || "")}</span></div>
      <div class="sign-row">${d.confirmed_by ? `点検者署名 ${esc(d.confirmed_by)}(${esc(String(d.confirmed_at || "").slice(0, 10))} 確定)` : "点検者署名 なし(確認・署名の前です)"}</div>
    </article>`;
  }
  function drawPapers() {
    const f = FORMS[form];
    $("papers").innerHTML = details.map((d) => paperHtml(d, f)).join("");
    $("xlsx").href = `/api/sites/${q(SITE)}/output/${f.kind}.xlsx`;
    $("xlsx").setAttribute("download", "");
    document.querySelectorAll("#forms button").forEach((b, i) => b.setAttribute("aria-pressed", i === form));
  }
  function message(title, body, link) {
    $("app").innerHTML = `<div class="empty"><b>${esc(title)}</b>${esc(body || "")}${link ? `<br><a class="btn" href="${esc(link[0])}">${esc(link[1])}</a>` : ""}</div>`;
  }
  function draw() {
    const unsigned = signs.filter((s) => s.status !== "confirmed").length;
    const nextIns = first(signs.map((s) => s.next_inspection)), exp = first(signs.map((s) => s.permit_expires));
    $("app").innerHTML = `
  <div class="page-head"><div><h1>報告書ができました</h1><p class="lead">${esc((site && site.name) || SITE)}${site && site.address ? `(${esc(site.address)})` : ""}の ${signs.length} 基を、様式に当てはめて表示しています。</p></div></div>
  ${unsigned ? `<div class="callout warn" role="status">確認済みでない看板が ${unsigned} 基あります。出力は下書きです(有資格者の確認・署名が要ります)。</div>` : ""}
  <div class="seg" role="group" aria-label="様式の切替" id="forms">${FORMS.map((f, i) => `<button type="button" data-f="${i}" aria-pressed="${i === form}">${esc(f.seg)}</button>`).join("")}</div>
  <div class="cols7">
    <div id="papers"></div>
    <aside class="side stack">
      <div class="card stack-sm">
        <h3>出力</h3>
        <a class="btn primary block" id="xlsx" href="#">Excel で出す</a>
        <button class="btn block" type="button" id="pdf">PDF で出す</button>
        <p class="hint">PDF は、ブラウザの印刷画面で「PDF として保存」を選んでください。</p>
      </div>
      <div class="callout"><span>次の点検日 <b class="num">${nextIns ? esc(nextIns) : "未設定"}</b> / 許可の期限 <b class="num">${exp ? esc(exp) : "未設定"}</b></span></div>
      <a class="btn block no-print" href="h.html">ホームへ戻る</a>
    </aside>
  </div>`;
    $("forms").onclick = (e) => { const b = e.target.closest("button"); if (!b) return; form = +b.dataset.f; drawPapers(); };
    $("pdf").onclick = () => window.print();
    drawPapers();
  }
  async function init() {
    const c = document.querySelector(".topbar .company"); if (c) c.innerHTML = `<b>${new Date().toLocaleDateString("ja-JP", { year: "numeric", month: "long", day: "numeric" })}</b>`;
    document.body.insertAdjacentHTML("beforeend", `<div class="wizard-foot"><div class="in"><a class="btn" href="n6.html${SITE ? "?site=" + q(SITE) : ""}">戻る</a><a class="btn primary lg" href="h.html">ホームへ戻る</a></div></div>`);
    if (!SITE) return message("現場が選ばれていません", "N1 の物件の選択から入ってください。", ["n1.html", "物件を選ぶ"]);
    try {
      const all = await get("/api/sites");
      site = all.find((s) => s.id === SITE) || null;
      if (!site) return message("この現場が台帳にありません", SITE, ["h.html", "ホームへ戻る"]);
      signs = await get(`/api/signs?site=${q(SITE)}`);
      if (!signs.length) return message("この現場にはまだ看板がありません", "看板ができると、報告書が作れます。", ["h.html", "ホームへ戻る"]);
      details = await Promise.all(signs.map((s) => get(`/api/signs/${q(s.id)}`)));
    } catch (e) { return message("報告書を読み込めませんでした", e.message, ["n7.html?site=" + q(SITE), "もう一度読み込む"]); }
    draw();
  }
  init();
})();
