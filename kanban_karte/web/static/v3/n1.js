// N1 物件: /api/sites の一覧から 1 つ選んで N2 入口(n2.html?site=<id>)へ。「新しい物件」は site なしで N2 へ。
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const getJSON = async (u) => { const r = await fetch(u); if (!r.ok) throw new Error(u + " " + r.status); return r.json(); };
  let sites = [];

  async function load() {
    $("loading").hidden = false; $("error").hidden = true; $("pick").hidden = true;
    try {
      sites = await getJSON("/api/sites");
      $("loading").hidden = true; $("pick").hidden = sites.length === 0;
      render();
    } catch (e) {
      $("loading").hidden = true; $("error").hidden = false; $("errMsg").textContent = "(" + e.message + ")";
    }
  }
  function render() {
    const f = $("q").value.trim();
    const shown = f ? sites.filter((s) => [s.name, s.address, s.municipality].join(" ").includes(f)) : sites;
    $("cnt").textContent = sites.length + " 件";
    $("sites").innerHTML = shown.length ? shown.map((s) =>
      `<li><span class="nm">${esc(s.name || s.id)}<small>${esc(s.address || "住所 未記入")}${s.municipality ? "・" + esc(s.municipality) : ""}</small></span>` +
      `<span class="due-dates"><span>看板 <b class="num">${s.sign_count}</b> 基</span><span>次の点検日 <b class="num">${esc(s.next_inspection || "未設定")}</b></span></span>` +
      `<a class="btn sm primary" href="n2.html?site=${encodeURIComponent(s.id)}">この物件を点検する</a></li>`).join("")
      : '<li class="empty-line">該当する物件がありません。</li>';
  }
  $("q").addEventListener("input", render);
  $("retry").onclick = load;
  load();
})();
