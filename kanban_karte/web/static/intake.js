// 受付画面: 受け付けた束(PC の PDF・LINE の写真)の一覧。種別・状態・看板数・エラー。下書き前の束には「下書きを作る」。
"use strict";
(function () {
  const { el } = KK;
  const BUNDLE_STATUS = { new: "受付", pages_registered: "ページ登録済み", classified: "分類済み", signs_drafted: "下書き済み", error: "エラー" };
  const BEFORE_DRAFT = new Set(["new", "pages_registered", "classified"]);

  function row(b, reload) {
    const msg = el("div", { class: "sub", role: "status" });
    let action = null;
    if (BEFORE_DRAFT.has(b.status)) {
      const canDraft = KK.meta.can_draft;
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
      action = el("div", {}, btn, canDraft ? null : el("div", { class: "sub", text: "ANTHROPIC_API_KEY が無いので、下書きは作れません。" }), msg);
    }
    const cell = (k, cls, ...kids) => el("td", { class: cls, dataset: { k } }, ...kids);
    return el("tr", {},
      cell("", "c-kind", el("span", { class: `chip src ${b.source === "LINE" ? "line" : "pc"}`, text: b.source })),
      cell("ファイル", "c-file", el("div", { text: b.file }), el("div", { class: "sub", text: `束 ${b.id} / ${b.pages} ページ` })),
      cell("状態", "c-state", el("span", { class: `chip status ${b.status === "signs_drafted" ? "confirmed" : b.status}`, text: BUNDLE_STATUS[b.status] || b.status })),
      cell("", "c-count", `看板 ${b.sign_count} 基 / 下書き ${b.draft_count}`),
      cell("エラー", "c-err", b.error ? el("span", { class: "notice err", text: b.error }) : el("span", { class: "muted", text: "—" })),
      cell("", "c-go", b.site_id ? el("a", { href: `#/?site=${encodeURIComponent(b.site_id)}`, text: "台帳(現場)へ →" }) : el("span", { class: "muted", text: "現場は未定" })),
      cell("", "c-act", action));
  }

  KK.views.intake = async function () {
    const bundles = await KK.api("/api/bundles");
    const th = (t) => el("th", {}, t);
    KK.setView(
      el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: "PC の PDF と LINE の写真" }), el("h1", { text: "受付" }))),
      bundles.length
        ? el("div", { class: "table-wrap" }, el("table", { class: "ledger intake" },
          el("thead", {}, el("tr", {}, th("種別"), th("ファイル"), th("状態"), th("看板数 / 下書き数"), th("エラー"), th("台帳"), th(""))),
          el("tbody", {}, bundles.map((b) => row(b, () => KK.route())))))
        : el("p", { class: "notice", text: "受け付けた束はまだありません。" }));
  };
})();
