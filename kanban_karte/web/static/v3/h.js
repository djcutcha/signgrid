// H ホーム: /api/sites(現場と確認待ちの数)、/api/bundles と /api/intake/<束>/progress(右上の帯)、/api/meta(会社名があれば)
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const getJSON = async (u) => { const r = await fetch(u); if (!r.ok) throw new Error(u + " " + r.status); return r.json(); };

  $("lead").textContent = SG.TODAY + " 時点";
  const co = document.querySelector(".company");
  if (co) co.textContent = SG.TODAY;
  // 会社名: 今の /api/meta には無い。company を返すようになれば自動で出る
  getJSON("/api/meta").then((m) => { const n = m.company || m.company_name; if (n && co) co.innerHTML = "<b>" + esc(n) + "</b>" + esc(SG.TODAY); }).catch(() => {});

  let sitesById = {};
  async function load() {
    $("loading").hidden = false; $("error").hidden = true;
    try {
      const all = await getJSON("/api/sites");
      const sites = all.filter((s) => s.sign_count > 0);
      sitesById = Object.fromEntries(all.map((s) => [s.id, s]));
      $("loading").hidden = true;
      $("viewEmpty").hidden = sites.length > 0; $("viewFull").hidden = sites.length === 0;
      if (sites.length) render(sites);
      pollIngest();
    } catch (e) {
      $("loading").hidden = true; $("error").hidden = false; $("errMsg").textContent = "(" + e.message + ")";
    }
  }
  function render(sites) {
    const waiting = sites.filter((s) => s.draft_count > 0).sort((a, b) => b.draft_count - a.draft_count);
    const tot = waiting.reduce((a, s) => a + s.draft_count, 0);
    $("waitSum").innerHTML = tot + "<small>基が確認待ち</small>";
    $("waitList").innerHTML = waiting.length ? waiting.map((s) =>
      `<li><span class="nm">${esc(s.name)}${s.address ? `<small>${esc(s.address)}</small>` : ""}</span><span class="chip wait">確認待ち ${s.draft_count} 基</span><a class="btn sm primary" href="n5.html?site=${encodeURIComponent(s.id)}">確認を始める</a></li>`).join("")
      : '<li class="empty-line">確認待ちの看板はありません。</li>';
    // 期限は台帳の next_inspection / permit_expires。今の /api/sites には無い(入れば自動で出る)
    const soon = (s) => [s.next_inspection, s.permit_expires].filter(Boolean).sort()[0];
    const due = sites.filter(soon).sort((a, b) => soon(a).localeCompare(soon(b)));
    $("dueList").innerHTML = due.length ? due.map((s) =>
      `<li><span class="nm">${esc(s.name)}</span><span class="due-dates"><span>次の点検日 <b class="num">${esc(s.next_inspection || "未設定")}</b></span><span>許可の期限 <b class="num">${esc(s.permit_expires || "未設定")}</b></span></span><a class="btn sm" href="l.html?site=${encodeURIComponent(s.id)}">台帳で開く</a></li>`).join("")
      : '<li class="empty-line">次の点検日や許可の期限が入った物件は、まだありません。</li>';
  }
  async function pollIngest() {
    const box = $("ingest");
    try {
      const bs = (await getJSON("/api/bundles")).filter((b) => b.status !== "signs_drafted" && !b.error).slice(-5);
      const ps = (await Promise.all(bs.map((b) => getJSON("/api/intake/" + b.id + "/progress").catch(() => null)))).filter((p) => p && p.status === "running");
      box.hidden = ps.length === 0;
      box.innerHTML = ps.map((p) => {
        const st = p.stages.filter((s) => s.total != null && s.key !== "verify");   // 反証役は画面に置かない
        const cur = st.find((s) => s.done < s.total) || st[st.length - 1];
        const site = p.site_id && sitesById[p.site_id] ? sitesById[p.site_id].name : "取り込み中の束 " + p.bundle_id;
        const pct = cur && cur.total ? Math.round(cur.done / cur.total * 100) : 0;
        return `<div class="t"><span><b>${esc(site)}</b>${cur ? ": " + esc(cur.label) + " " + cur.done + "/" + cur.total : ""}</span><span class="num">${pct}%</span></div><div class="bar" aria-hidden="true"><i style="width:${pct}%"></i></div>`;
      }).join("");
    } catch (e) { box.hidden = true; }
    clearTimeout(pollIngest.t); pollIngest.t = setTimeout(pollIngest, 4000);
  }
  $("retry").onclick = load;
  load();
})();
