// 規則と出力: 有資格者の訂正から育った規則(矛盾の印つき)、現場ごとの様式出力(xlsx)
"use strict";
(function () {
  const { el } = KK;

  const BY_KEY = "kk_by";
  const STATUS = { candidate: ["候補", "wait"], approved: ["承認済み", "ok"], rejected: ["却下", "no"] };
  const getBy = () => (document.getElementById("rule-by")?.value || localStorage.getItem(BY_KEY) || "").trim();

  // 矛盾している(却下されていない)規則を、つながり単位でまとめる
  function conflictGroups(rules) {
    const live = new Map(rules.filter((r) => r.status !== "rejected").map((r) => [r.id, r]));
    const seen = new Set(), groups = [];
    for (const r of live.values()) {
      if (seen.has(r.id) || !r.conflicts.some((c) => live.has(c))) continue;
      const g = [], stack = [r.id];
      while (stack.length) {
        const id = stack.pop();
        if (seen.has(id) || !live.has(id)) continue;
        seen.add(id); g.push(live.get(id)); stack.push(...live.get(id).conflicts);
      }
      groups.push(g.sort((x, y) => x.id - y.id));
    }
    return groups;
  }

  function ruleBody(r) {
    const [label, cls] = STATUS[r.status] || STATUS.candidate;
    return [
      el("div", { class: "rule-top" }, el("b", { text: `規則 #${r.id}` }), el("span", { class: `chip rstatus ${cls}`, text: label })),
      el("div", { text: r.text }),
      el("div", { class: "sub", text: `由来: ${r.source || "—"} / ${KK.fmtTime(r.created)}` }),
      r.approved_by ? el("div", { class: "sub", text: `${label}: ${r.approved_by} / ${KK.fmtTime(r.approved_at)}` }) : null,
      r.resolution ? el("div", { class: "sub", text: `矛盾の解き方: ${r.resolution}` }) : null];
  }

  function decideButtons(r, act, msg) {
    const go = async (action) => {
      const by = getBy();
      if (!by) { msg.hidden = false; msg.textContent = "承認する人の名前を、上の「有資格者」に入れてください。"; return; }
      try { await KK.api(`/api/rules/${r.id}/${action}`, { by }); await act(); } catch (e) { msg.hidden = false; msg.textContent = e.message; }
    };
    return el("div", { class: "btn-row" },
      r.status !== "approved" ? el("button", { type: "button", class: "btn", onclick: () => go("approve"), text: "承認" }) : null,
      r.status !== "rejected" ? el("button", { type: "button", class: "btn ghost", onclick: () => go("reject"), text: "却下" }) : null);
  }

  function ruleItem(r, act, msg) {
    return el("li", { class: `rule st-${r.status}`, id: `rule-${r.id}` }, ruleBody(r), decideButtons(r, act, msg));
  }

  // 矛盾の組: 並べて見せ、「こちらを残す」を押すと、残す側 承認・他方 却下・理由を両方に記録(1操作)
  function conflictBlock(group, act, msg) {
    const reason = el("input", { type: "text", class: "reason", placeholder: "理由(任意。例: 全周に回っているので要改善)", maxlength: 200, "aria-label": "矛盾の解き方" });
    const keep = async (r) => {
      const by = getBy();
      if (!by) { msg.hidden = false; msg.textContent = "承認する人の名前を、上の「有資格者」に入れてください。"; return; }
      try {
        for (const o of group.filter((x) => x.id !== r.id)) await KK.api("/api/rules/resolve", { keep: r.id, drop: o.id, by, reason: reason.value.trim() || null });
        await act();
      } catch (e) { msg.hidden = false; msg.textContent = e.message; }
    };
    return el("li", { class: "conflict-pair" },
      el("div", { class: "chip fix", text: `矛盾の可能性: ${group.map((r) => `#${r.id}`).join(" と ")}` }),
      el("p", { class: "sub", text: "同じ項目・同じ所見で、別の区分の規則があります。どちらを残すか選びます(残す側は承認、他方は却下)。" }),
      el("div", { class: "pair-cols" }, group.map((r) => el("div", { class: `rule conflict st-${r.status}`, id: `rule-${r.id}` }, ruleBody(r),
        el("button", { type: "button", class: "btn", onclick: () => keep(r), text: "こちらを残す" })))),
      reason);
  }

  function rulesCard(rules, act) {
    const msg = el("div", { class: "notice err", role: "alert", hidden: true });
    const groups = conflictGroups(rules), inGroup = new Set(groups.flat().map((r) => r.id));
    const open = rules.filter((r) => r.status !== "rejected" && !inGroup.has(r.id)).reverse();
    const rejected = rules.filter((r) => r.status === "rejected").reverse();
    const n = (st) => rules.filter((r) => r.status === st).length;
    const by = el("input", { id: "rule-by", type: "text", autocomplete: "name", placeholder: "名前(承認・却下に記録)", value: localStorage.getItem(BY_KEY) || "",
      oninput: (e) => localStorage.setItem(BY_KEY, e.target.value.trim()) });
    return el("section", { class: "card rules" }, el("h2", { text: `規則 ${rules.length} 件` }),
      el("p", { class: "sub", text: `有資格者の訂正から育つ規則です。候補 ${n("candidate")} / 承認済み ${n("approved")} / 却下 ${n("rejected")}。次回の下書きに効くのは承認済みだけです。矛盾する規則は並べて、どちらを残すか選びます。` }),
      el("label", { class: "by" }, "有資格者 ", by), msg,
      rules.length ? el("ol", { class: "rule-list" }, groups.map((g) => conflictBlock(g, act, msg)), open.map((r) => ruleItem(r, act, msg)),
        rejected.length ? el("li", { class: "rejected-head sub", text: `却下 ${rejected.length} 件` }) : null, rejected.map((r) => ruleItem(r, act, msg)))
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
    const sites = await KK.api("/api/sites");
    const holder = el("div", {});
    const act = async () => { holder.replaceChildren(rulesCard(await KK.api("/api/rules"), act)); };
    await act();
    KK.setView(
      el("div", { class: "rules rules-page" },
        el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: "規則と出力" }), el("h1", { text: "規則と出力" }))),
        el("div", { class: "two-col" },
          holder,
          el("section", { class: "card" }, el("h2", { text: "様式を出力" }),
            el("p", { class: "sub", text: "台帳の内容が様式(Excel)に入ります。出力は常に下書きで、有資格者の確認・署名が要ります。" }),
            sites.length ? sites.map(outputCard) : el("p", { class: "muted", text: "現場がありません。" })))));
  };
})();
