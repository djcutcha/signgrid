// 規則と出力: 有資格者の訂正から育った規則(矛盾の印つき)、現場ごとの様式出力(xlsx)
"use strict";
(function () {
  const { el } = KK;

  function ruleItem(r, byId) {
    const conflicts = r.conflicts_with.filter((id) => byId.has(id));
    return el("li", { class: "rule" + (conflicts.length ? " conflict" : ""), id: `rule-${r.id}` },
      el("div", { class: "rule-top" }, el("b", { text: `規則 #${r.id}` }),
        conflicts.length ? el("span", { class: "chip fix", title: "同じ項目・同じ所見で、別の区分の規則がある", text: `矛盾の可能性 ${conflicts.map((c) => `#${c}`).join("・")}` }) : null),
      el("div", { text: r.text }),
      el("div", { class: "sub", text: `由来: ${r.source || "—"} / ${KK.fmtTime(r.created)}` }));
  }

  function rulesCard(rules) {
    const byId = new Map(rules.map((r) => [r.id, r]));
    return el("section", { class: "card rules" }, el("h2", { text: `規則 ${rules.length} 件` }),
      el("p", { class: "sub", text: "有資格者の訂正から育つ規則です。次回の下書きで先に効きます。既存の規則と矛盾する訂正は、赤い印で知らせます。" }),
      rules.length ? el("ol", { class: "rule-list" }, rules.slice().reverse().map((r) => ruleItem(r, byId)))
        : el("p", { class: "notice", text: "まだ規則はありません。カルテでマスを直すと、ここに積まれます。" }));
  }

  async function download(url, msg, btn) {
    btn.disabled = true;
    msg.className = "sub"; msg.textContent = "作っています…";
    try {
      const r = await fetch(url);
      if (!r.ok) throw new Error((await r.json().catch(() => ({}))).error || r.statusText);
      const name = /filename\*=UTF-8''([^;]+)/.exec(r.headers.get("Content-Disposition") || "")?.[1];
      const a = el("a", { href: URL.createObjectURL(await r.blob()), download: name ? decodeURIComponent(name) : "output.xlsx" });
      document.body.append(a); a.click(); a.remove();
      msg.textContent = "出力しました(下書きです。有資格者の確認・署名が要ります)。";
    } catch (e) { msg.className = "notice err"; msg.textContent = e.message; }
    btn.disabled = false;
  }

  function outputCard(site) {
    const msg = el("div", { class: "sub", role: "status" });
    const mk = (kind, label) => {
      const b = el("button", { type: "button", class: "btn", onclick: () => download(`/api/sites/${encodeURIComponent(site.id)}/output/${kind}.xlsx`, msg, b), text: label });
      return b;
    };
    const pending = site.sign_count - site.confirmed_count;
    return el("div", { class: "out-site" },
      el("div", {}, el("b", { text: site.name }), el("div", { class: "sub", text: `看板 ${site.sign_count} 基 / 確定 ${site.confirmed_count}` })),
      pending ? el("div", { class: "notice warn", text: `未確定が ${pending} 基あります。出力には下書きとして入ります。` }) : null,
      el("div", { class: "btn-row" }, mk("tokyo", "東京都 第2号様式を出力"), mk("client-a", "客先A型を出力")), msg);
  }

  KK.views.rules = async function () {
    const [rules, sites] = await Promise.all([KK.api("/api/rules"), KK.api("/api/sites")]);
    KK.setView(
      el("div", { class: "rules rules-page" },
        el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: "規則と出力" }), el("h1", { text: "規則と出力" }))),
        el("div", { class: "two-col" },
          rulesCard(rules),
          el("section", { class: "card" }, el("h2", { text: "様式を出力" }),
            el("p", { class: "sub", text: "台帳の内容が様式(Excel)に入ります。出力は常に下書きで、有資格者の確認・署名が要ります。" }),
            sites.length ? sites.map(outputCard) : el("p", { class: "muted", text: "現場がありません。" })))));
  };
})();
