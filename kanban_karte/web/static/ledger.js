// 台帳画面: 現場を選ぶ → 看板1基=1行。法規の列(自家用・許可・期限)と安全の列(ランク・判断できず・写真・前回との差・状態)
"use strict";
(function () {
  const { el } = KK;

  function diffCell(s) {
    if (!s.rank || !s.prev_rank) return el("span", { class: "muted", text: "—" });
    const d = s.rank - s.prev_rank;
    if (d === 0) return el("span", { text: "変わらず" });
    return el("span", { class: d > 0 ? "worse" : "better", title: `前回 ${KK.RANK[s.prev_rank]} → 今回 ${KK.RANK[s.rank]}` },
      `${KK.RANK[s.prev_rank]} → ${KK.RANK[s.rank]} ${d > 0 ? "▲" : "▼"}`);
  }

  function statusCell(s) {
    const label = KK.STATUS[s.status] || s.status;
    if (s.status === "confirmed") {
      return el("span", {}, el("span", { class: "chip status confirmed", text: label }),
        s.confirmed_by ? el("div", { class: "sub", text: `${s.confirmed_by} ${KK.fmtTime(s.confirmed_at)}` }) : null);
    }
    return el("span", { class: `chip status ${s.status}`, text: label + (s.status === "draft" ? "(AI)" : "") });
  }

  function row(s) {
    const undec = s.status === "draft" || s.status === "confirmed" ? s.undecided : null;
    const cell = (k, cls, ...kids) => el("td", { class: cls, dataset: { k } }, ...kids);
    const href = `#/sign/${encodeURIComponent(s.id)}`;
    const tr = el("tr", { class: "clickable", tabindex: "-1", onclick: () => { location.hash = href; } },
      cell("", "c-name", el("a", { href, text: s.name || s.id }), el("div", { class: "sub", text: s.id })),
      cell("種類", "c-type", s.type || "—"),
      cell("寸法", "c-size", s.size || "—"),
      cell("自家用", "c-law", KK.triText(s.is_self_use, "自家用", "自家用でない")),
      cell("許可", "c-law", KK.permitChip(s.permit_required) || "—"),
      cell("期限", "c-law", s.permit_expires || s.next_inspection
        ? el("span", {}, s.permit_expires ? el("div", { text: `許可 ${s.permit_expires}` }) : null,
          s.next_inspection ? el("div", { class: s.permit_expires ? "sub" : "", text: `点検 ${s.next_inspection}` }) : null)
        : "—"),
      cell("", "c-rank", KK.rankChip(s.rank, s.status)),
      cell("判断できず", "c-undec", undec === null ? el("span", { class: "muted", text: "—" })
        : undec > 0 ? el("span", { class: "chip q", title: "判断できず(要実測)の項目。有資格者が直す", text: `${undec} / 18` })
          : el("span", { text: "なし" })),
      cell("写真", "c-photos", `${s.photo_count} 枚`),
      cell("前回", "c-diff", diffCell(s)),
      cell("状態", "c-status", statusCell(s)));
    return tr;
  }

  function table(signs) {
    const th = (t, o) => el("th", o, t);
    return el("div", { class: "table-wrap" }, el("table", { class: "ledger" },
      el("thead", {},
        el("tr", { class: "groups" }, th("", { colspan: 3 }), th("法規の列", { colspan: 3, class: "g-law" }), th("安全の列", { colspan: 5, class: "g-safe" })),
        el("tr", {}, th("看板"), th("種類"), th("寸法"), th("自家用"), th("許可"), th("期限"),
          th("ランク"), th("判断できず"), th("写真"), th("前回との差"), th("状態"))),
      el("tbody", {}, signs.map(row))));
  }

  function stats(signs) {
    const stat = (n, t) => el("div", { class: "stat" }, el("b", { text: String(n) }), el("span", { text: t }));
    const drafts = signs.filter((s) => s.status === "draft");
    return el("div", { class: "stats" },
      stat(signs.length, "看板(基)"), stat(drafts.length, "下書き(有資格者の確認待ち)"),
      stat(signs.filter((s) => s.status === "confirmed").length, "確定"),
      stat(drafts.reduce((a, s) => a + s.undecided, 0), "判断できず(要実測)の項目"));
  }

  function bundlesPanel(bundles, reload) {
    if (!bundles.length) return null;
    const msg = el("div", { class: "sub", role: "status" });
    const canDraft = KK.meta.can_draft;
    const rows = bundles.map((b) => {
      const btn = el("button", {
        type: "button", class: "btn small", disabled: !canDraft || null,
        title: canDraft ? "束の写真から下書きを作る(API を使う)" : "ANTHROPIC_API_KEY が無いので使えません(環境変数か .env)",
        onclick: async () => {
          btn.disabled = true;
          msg.textContent = `束 ${b.id} の下書きを作っています…(数分かかります)`;
          try {
            const r = await KK.api(`/api/bundles/${b.id}/draft`, {});
            msg.textContent = `下書き: ${r.results.map((x) => `${x.sign_id} ${x.status}`).join(" / ")}`;
            reload();
          } catch (e) { msg.textContent = e.message; btn.disabled = false; }
        },
      }, "下書きを作る");
      return el("tr", {}, el("td", { text: `束 ${b.id}` }), el("td", { text: b.file }), el("td", { text: `${b.pages} ページ` }),
        el("td", { text: `看板 ${b.sign_count} 基(下書き ${b.draft_count})` }), el("td", {}, btn));
    });
    return el("details", { class: "card bundles" }, el("summary", { text: `受け付けた束 ${bundles.length} 件` }),
      el("div", { class: "table-wrap" }, el("table", { class: "plain" }, el("tbody", {}, rows))),
      canDraft ? null : el("p", { class: "sub", text: "ANTHROPIC_API_KEY が無いので、下書きは作れません。" }), msg);
  }

  KK.views.ledger = async function (_, params) {
    const sites = await KK.api("/api/sites");
    if (!sites.length) {
      KK.setView(el("p", { class: "notice", text: "台帳に現場がありません。" }));
      return;
    }
    let id = params.get("site") || localStorage.getItem("kk_site");
    if (!sites.some((s) => s.id === id)) id = sites[0].id;
    localStorage.setItem("kk_site", id);
    const site = sites.find((s) => s.id === id);
    const [signs, bundles] = await Promise.all([KK.api(`/api/signs?site=${encodeURIComponent(id)}`), KK.api("/api/bundles")]);
    const picker = sites.length > 1
      ? el("select", { "aria-label": "現場", onchange: (e) => { location.hash = `#/?site=${encodeURIComponent(e.target.value)}`; } },
        sites.map((s) => el("option", { value: s.id, selected: s.id === id || null, text: s.name })))
      : null;
    KK.setView(
      el("div", { class: "page-head" },
        el("div", {}, el("div", { class: "eyebrow", text: "看板管理台帳(看板1基=1行)" }),
          picker || el("h1", { text: site.name }),
          el("div", { class: "sub", text: [site.address, site.municipality].filter(Boolean).join(" / ") }))),
      stats(signs),
      signs.length ? table(signs) : el("p", { class: "notice", text: "この現場に看板がありません。" }),
      el("p", { class: "legend" }, el("span", { class: "chip q", text: "黄色" }),
        " = 判断できない・確信が低いマス。AI は確定しません。行を開いて有資格者が直し、確定します。"),
      bundlesPanel(bundles, () => KK.route()));
  };
})();
