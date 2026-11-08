// N2 入口: 「どう取り込みますか」の 3 択。選んだ方で下の「次へ」の行き先を変える。
// ?site=<id> で来たら、行き先にそのまま引き継ぐ(物件名は /api/sites から出す)。保存はしない。
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const getJSON = async (u) => { const r = await fetch(u); if (!r.ok) throw new Error(u + " " + r.status); return r.json(); };

  const co = document.querySelector(".company");
  if (co) co.textContent = SG.TODAY;
  getJSON("/api/meta").then((m) => { const n = m.company || m.company_name; if (n && co) co.innerHTML = "<b>" + esc(n) + "</b>" + esc(SG.TODAY); }).catch(() => {});

  const site = new URLSearchParams(location.search).get("site") || "";
  const q = (extra) => { const p = new URLSearchParams(); if (site) p.set("site", site); if (extra) Object.entries(extra).forEach(([k, v]) => p.set(k, v)); const s = p.toString(); return s ? "?" + s : ""; };
  // 現場ウィザードには今は mode の受け口が無い(F0 で「紙の点検表を撮って送る」を選ぶ)。付けておけば、受け口ができたときにそのまま効く
  const DEST = {
    files: { href: "n3.html" + q(), label: "写真と報告書のファイルを取り込む", next: "次へ(ファイルを落とす)" },
    field: { href: "field/" + q(), label: "現場でスマホで撮る", next: "次へ(現場の画面を開く)" },
    paper: { href: "field/" + q({ mode: "paper" }), label: "紙の点検票を撮って送る", next: "次へ(点検票を撮る画面を開く)" },
  };

  // 物件の行: 来ていれば名前、来ていなければ「まだ選んでいない」
  const line = $("siteLine");
  if (!site) line.textContent = "物件はまだ選んでいません。このまま取り込めます。";
  else {
    line.innerHTML = "物件: <b>" + esc(site) + "</b>";
    getJSON("/api/sites").then((all) => {
      const s = all.find((x) => x.id === site);
      if (s) line.innerHTML = "物件: <b>" + esc(s.name) + "</b>" + (s.address ? " " + esc(s.address) : "") + (s.sign_count ? `(看板 ${s.sign_count} 基)` : "(看板はまだありません)");
      else line.innerHTML = "物件: <b>" + esc(site) + "</b>(台帳に見つかりません。そのまま取り込めます)";
    }).catch(() => {});
  }

  const fieldAbs = new URL("field/" + q(), location.href).href;
  const cards = [...document.querySelectorAll(".choice")], nb = $("nextBtn"), sel = $("sel");
  function pick(v) {
    cards.forEach((c) => { const on = c.dataset.v === v; c.setAttribute("aria-pressed", on); c.classList.toggle("dim", !on); });
    const d = DEST[v];
    if (nb) { nb.href = d.href; nb.textContent = d.next; }
    sel.textContent = "選んだ入口: " + d.label;
    const ph = v !== "files";
    $("phone").hidden = !ph;
    if (ph) {
      $("phoneUrl").textContent = fieldAbs;
      $("phoneOpen").href = d.href;
      $("phoneNote").textContent = v === "paper"
        ? "スマホでこのアドレスを開き、最初の画面で「紙の点検表を撮って送る」を選びます。"
        : "スマホでこのアドレスを開き、最初の画面で「案内に沿って撮る」を選びます。";
    }
  }
  cards.forEach((c) => c.addEventListener("click", () => pick(c.dataset.v)));
  pick("files");
})();
