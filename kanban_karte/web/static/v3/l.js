// L 台帳: /api/sites(店舗の一覧)→ 店を選ぶと /api/signs?site=<id>(看板の一覧)。
// 店の行は N3 取り込み、看板の行は N5 看板ごとの確認へ。?site=<id> で開くとその店を選んだ状態。
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const getJSON = async (u) => { const r = await fetch(u); if (!r.ok) throw new Error(u + " " + r.status); return r.json(); };
  const enc = encodeURIComponent;
  const RK = { 1: "r1", 2: "r2", 3: "r3", 4: "r4" };
  const res = (rank, label, draft) => rank ? `<span class="res ${RK[rank] || "na"}${draft ? " draft" : ""}">${esc(label || SG.RANK[rank])}</span>` : '<span class="res na">未判定</span>';
  const chip = (st) => st === "confirmed" ? '<span class="chip done">確定</span>'
    : st === "draft" ? '<span class="chip wait">確認待ち</span>'
    : `<span class="chip draft">${st === "needs_photos" ? "写真が要る" : "下書き"}</span>`;
  const ORDERER = { direct: "直", agency: "代理店経由" };
  const num = (s) => `<span class="num">${esc(s || "未設定")}</span>`;

  let sites = [], byId = {}, cur = "all", signs = null, filter = "";
  const params = new URLSearchParams(location.search);

  async function load() {
    $("loading").hidden = false; $("error").hidden = true; $("ledger").hidden = true;
    try {
      sites = await getJSON("/api/sites");
      byId = Object.fromEntries(sites.map((s) => [s.id, s]));
      $("loading").hidden = true; $("ledger").hidden = false;
      tree();
      const want = params.get("site");
      if (want && byId[want]) await select("store:" + want); else render();
    } catch (e) {
      $("loading").hidden = true; $("error").hidden = false; $("errMsg").textContent = "(" + e.message + ")";
    }
  }

  // 木: 発注元 > チェーンブランド > 店舗。発注元・ブランドが未記入の店は「(未記入)」「(なし)」にまとめる
  function tree() {
    const g = {};
    sites.forEach((s) => {
      const o = ORDERER[s.orderer_type] || s.orderer_type || "(発注元 未記入)", b = s.chain_brand || "(ブランドなし)";
      ((g[o] = g[o] || {})[b] = g[o][b] || []).push(s);
    });
    let html = `<button type="button" class="sum" data-sel="all"><span class="kind">一覧</span>すべての店舗(${sites.length})</button>`;
    Object.entries(g).forEach(([o, brands]) => {
      html += `<details open><summary><span class="kind">発注元</span>${esc(o)}</summary>`;
      Object.entries(brands).forEach(([b, ss]) => {
        html += `<details open><summary><span class="kind">ブランド</span>${esc(b)}</summary>` +
          ss.map((s) => `<button type="button" class="leaf" data-sel="store:${esc(s.id)}"><span class="kind">店舗</span>${esc(s.name || s.id)}</button>`).join("") + `</details>`;
      });
      html += `</details>`;
    });
    $("tree").innerHTML = html;
  }

  async function select(sel) {
    cur = sel; signs = null;
    if (sel.startsWith("store:")) {
      const id = sel.slice(6);
      history.replaceState(null, "", "l.html?site=" + enc(id));
      render();
      try { signs = await getJSON("/api/signs?site=" + enc(id)); }
      catch (e) { signs = { error: e.message }; }
    } else history.replaceState(null, "", "l.html");
    render();
  }

  function render() {
    let head, rows, path, note = "", cls = "t";
    if (cur.startsWith("store:")) {
      const s = byId[cur.slice(6)];
      cls = "t store";
      path = `<a href="l.html" data-sel="all">すべての店舗</a> › ${esc(s.name || s.id)}(看板 ${s.sign_count} 基)・<a href="n3.html?site=${enc(s.id)}">この店に写真と報告書を取り込む</a>`;
      head = ["看板", "種類", "寸法", "許可", "前回の総合", "今回の総合", "不明点", "写真", "札"];
      if (!signs) rows = null;
      else if (signs.error) { rows = []; note = "看板を読めませんでした(" + signs.error + ")"; }
      else rows = signs.map((x) => ({
        href: `n5.html?site=${enc(s.id)}&sign=${enc(x.id)}`,
        cells: [esc(x.name || x.id), esc(x.type || "未記入"), num(x.size), x.permit_required ? `要<small>期限 ${esc(x.permit_expires || "未記入")}</small>` : x.permit_required === false ? "不要" : "未記入",
          x.prev_rank ? res(x.prev_rank) : '<span class="muted">なし</span>', res(x.rank, x.rank_label, x.status !== "confirmed"),
          x.undecided ? `<span class="num">${x.undecided} 件</span>` : '<span class="muted">0</span>', `<span class="num">${x.photo_count || 0} 枚</span>`, chip(x.status)],
      }));
    } else {
      path = "すべての店舗";
      head = ["店舗", "自治体", "看板数", "確認待ち", "次の点検日", "許可の期限", ""];
      rows = sites.map((s) => ({
        href: `l.html?site=${enc(s.id)}`, sel: "store:" + s.id,
        cells: [`${esc(s.name || s.id)}${s.address ? `<small>${esc(s.address)}</small>` : ""}`, esc(s.municipality || "未判定"), `<span class="num">${s.sign_count} 基</span>`,
          s.draft_count ? `<span class="chip wait">確認待ち</span> <span class="num">${s.draft_count} 基</span>` : s.sign_count ? '<span class="chip done">確定</span>' : '<span class="muted">看板なし</span>',
          num(s.next_inspection), num(s.permit_expires), `<a class="btn sm" href="n3.html?site=${enc(s.id)}">取り込み</a>`],
      }));
      if (!sites.length) note = "まだ店舗がありません。新規点検から始めてください。";
    }
    $("tbl").className = cls;
    $("crumb").innerHTML = path;
    const f = filter.trim();
    const shown = rows && (f ? rows.filter((r) => r.cells.join(" ").replace(/<[^>]+>/g, "").includes(f)) : rows);
    $("tbl").innerHTML = `<thead><tr>${head.map((h) => `<th>${h}</th>`).join("")}</tr></thead><tbody>${
      !rows ? `<tr><td colspan="${head.length}"><span class="muted">読み込んでいます…</span></td></tr>`
      : shown.length ? shown.map((r) => `<tr data-href="${r.href}"${r.sel ? ` data-sel="${esc(r.sel)}"` : ""}>${r.cells.map((c) => `<td>${c}</td>`).join("")}</tr>`).join("")
      : `<tr><td colspan="${head.length}"><div class="empty"><b>該当する行がありません</b>${f ? "検索の言葉を変えるか、左の木から選び直してください。" : "この店の看板はまだありません。"}</div></td></tr>`}</tbody>`;
    $("foot").hidden = !note; $("foot").textContent = note;
    document.querySelectorAll("#tree [data-sel]").forEach((e) => e.classList.toggle("sel", e.dataset.sel === cur));
  }

  $("tree").addEventListener("click", (e) => { const el = e.target.closest("[data-sel]"); if (el) select(el.dataset.sel); });
  $("crumb").addEventListener("click", (e) => { const el = e.target.closest("[data-sel]"); if (el) { e.preventDefault(); select(el.dataset.sel); } });
  $("tbl").addEventListener("click", (e) => {
    const tr = e.target.closest("tr[data-href]"); if (!tr || e.target.closest("a")) return;
    if (tr.dataset.sel) select(tr.dataset.sel); else location.href = tr.dataset.href;
  });
  $("q").addEventListener("input", (e) => { filter = e.target.value; render(); });
  $("retry").onclick = load;
  const w = $("treeWrap"), mq = matchMedia("(max-width:760px)");
  const fit = () => { w.open = !mq.matches; }; fit(); mq.addEventListener("change", fit);
  load();
})();
