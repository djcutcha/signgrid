// K 判定の規則(旧名 学んだこと、本実装)。有資格者の訂正から育った規則の一覧と、候補の承認・却下。
// API: GET /api/rules / GET /api/meta(項目の名前)/ GET /api/signs/ID(由来の看板の名前)/
//      POST /api/rules/ID/approve|reject {by} / POST /api/rules/resolve {keep, drop, by}(矛盾の組で片方を残す)
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const BY_KEY = "kk_by";   // 旧画面(rules.js / karte.js)と N5 と同じ保存先
  const STATUS = { candidate: ["候補", "draft"], approved: ["承認済み", "done"], rejected: ["却下", "no"] };
  let items = {};   // "表示部-4" → {group, no, text}
  let signs = {};   // 看板 id → {name, site_id, site_name}(由来の表示)
  let rules = [];
  let busy = false;

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let j = null;
    try { j = await r.json(); } catch (e) { /* 本文なし */ }
    if (!r.ok) throw new Error((j && j.error) || `通信に失敗しました(${r.status})`);
    return j;
  }
  function toast(t) { const e = $("toast"); e.textContent = t; e.classList.add("on"); clearTimeout(toast.t); toast.t = setTimeout(() => e.classList.remove("on"), 3200); }
  const lsGet = () => { try { return (localStorage.getItem(BY_KEY) || "").trim(); } catch (e) { return ""; } };
  const lsSet = (v) => { try { localStorage.setItem(BY_KEY, v); } catch (e) { /* 保存できない端末 */ } };

  // ---- 名前(承認・却下の記録に要る) ----
  function askBy(force) {
    const have = lsGet();
    if (have && !force) return Promise.resolve(have);
    return new Promise((resolve) => {
      const dlg = $("byDlg"), inp = $("byIn");
      inp.value = have;
      const close = (v) => { dlg.classList.remove("on"); $("byOk").onclick = $("byCancel").onclick = null; resolve(v); };
      $("byOk").onclick = () => { const v = inp.value.trim(); if (!v) { inp.focus(); return; } lsSet(v); close(v); renderBy(); };
      $("byCancel").onclick = () => close(null);
      inp.onkeydown = (e) => { if (e.key === "Enter") $("byOk").click(); };
      dlg.classList.add("on"); inp.focus();
    });
  }
  function renderBy() {
    const by = lsGet(), el = $("byLine");
    const hasBtn = rules.some((r) => r.status !== "approved");   // 候補の承認・却下、却下の承認し直し
    el.hidden = !hasBtn;   // 押すボタンが無い間は出さない
    el.innerHTML = by ? `承認・却下は「${esc(by)}」の名前で記録します。<button type="button" id="byChange">名前を変える</button>`
      : `承認・却下を押すと、記録に残す名前(有資格者)を最初に一度だけ聞きます。`;
    const b = $("byChange");
    if (b) b.onclick = () => askBy(true);
  }

  // ---- 規則の見え方 ----
  const day = (iso) => (iso || "").slice(0, 10);
  function condText(c) {
    if (!c) return "";
    if (typeof c === "string") return c;
    return c.text || [c.range, c.degree].filter(Boolean).join("・");
  }
  // 構造(structured)があれば「表示部 4(表示面の汚染…)で、「所見」(条件)は 区分」。無ければ台帳の文そのまま
  function ruleText(r) {
    const s = r.structured;
    const it = s && items[s.item];
    if (!s || !it || !s.result) return esc(r.text);
    const cond = condText(s.condition);
    const what = s.finding ? `「${esc(s.finding)}」${cond ? `(${esc(cond)})` : ""}は` : "有資格者の判定は";
    return `${esc(it.group)} ${esc(it.no)}(${esc(it.text)})で、${what} ${esc(s.result)}` +
      `<span class="was">下書きは ${esc(s.draft || "判断できず")}</span>`;
  }
  function sourceOf(r) {
    const src = r.source || "";
    const m = /^(.+?) \/ (.+)$/.exec(src);   // 訂正から生まれた規則は「看板 id / 訂正した人」
    const s = r.structured, it = s && items[s.item];
    const itemTxt = it ? ` / ${esc(it.group)} ${esc(it.no)}` : "";
    if (!m) return src ? `どこから: ${esc(src)}` : "";
    const sg = signs[m[1]];
    const sign = sg ? `<a href="n5.html?site=${encodeURIComponent(sg.site_id)}&sign=${encodeURIComponent(m[1])}">${esc((sg.site_name ? sg.site_name + " " : "") + sg.name)}</a>`
      : `看板 ${esc(m[1])}`;
    return `どの訂正から: ${sign}${itemTxt} / ${esc(m[2])}`;
  }
  const byId = () => Object.fromEntries(rules.map((r) => [r.id, r]));
  function liveConflicts(r) { const m = byId(); return (r.conflicts || []).map((i) => m[i]).filter((x) => x && x.status !== "rejected"); }
  // 画面に出す食い違いの相手。却下した規則は承認済みの相手だけ(そのまま承認し直すと 409 になるので「こちらを残す」にする)
  const shownConflicts = (r) => r.status === "rejected" ? liveConflicts(r).filter((o) => o.status === "approved") : liveConflicts(r);
  // 台帳の resolution は「却下(#4 を残した)」のように規則の番号を持つ。画面に番号は出していないので言い換える
  const resText = (s) => String(s).replace(/#\d+ を残した/g, "食い違う規則を残した").replace(/#\d+/g, "別の規則");
  function applied(r) {
    const n = [r.applied_count, r.applied, r.use_count].find((v) => typeof v === "number");
    return n == null ? "" : ` / 適用 ${n} 回`;
  }

  function ruleHtml(r) {
    const conf = shownConflicts(r);
    const [label, cls] = r.status === "candidate" && conf.length ? ["矛盾の可能性", "wait"] : (STATUS[r.status] || STATUS.candidate);
    const lines = [];
    const src = sourceOf(r);
    if (src) lines.push(`<div class="src">${src}${applied(r)}</div>`);
    const st = r.structured || {};
    if (st.question && st.answer) lines.push(`<div class="qa">聞き返し: ${esc(st.question)} → ${esc(st.answer)}</div>`);
    conf.forEach((o) => lines.push(`<div class="conf">食い違う規則: ${esc(day(o.created))}「${ruleText(o).replace(/<span class="was">.*<\/span>$/, "")}」(${esc(STATUS[o.status][0])})</div>`));
    if (r.approved_by) lines.push(`<div class="dec">${esc(STATUS[r.status][0])}: ${esc(r.approved_by)} / ${esc(day(r.approved_at))}${r.resolution ? ` / ${esc(resText(r.resolution))}` : ""}</div>`);
    let acts = "";
    if (r.status === "candidate" && conf.length) {
      acts = `<div class="acts"><button class="btn primary" type="button" data-act="keep" data-id="${r.id}">こちらを残す</button>` +
        `<button class="btn" type="button" data-act="reject" data-id="${r.id}">却下</button>` +
        `<span class="acts-note">残すと、食い違う規則は却下になります。</span></div>`;
    } else if (r.status === "candidate") {
      acts = `<div class="acts"><button class="btn primary" type="button" data-act="approve" data-id="${r.id}">承認</button>` +
        `<button class="btn" type="button" data-act="reject" data-id="${r.id}">却下</button></div>`;
    } else if (r.status === "rejected" && conf.length) {
      acts = `<div class="acts"><button class="btn sm ghost" type="button" data-act="keep" data-id="${r.id}">こちらを残す</button>` +
        `<span class="acts-note">残すと、食い違う承認済みの規則は却下になります。</span></div>`;
    } else if (r.status === "rejected") {
      acts = `<div class="acts"><button class="btn sm ghost" type="button" data-act="approve" data-id="${r.id}">承認し直す</button></div>`;
    }
    return `<article class="rule" id="rule-${r.id}"><div class="d num">${esc(day(r.created))}</div>` +
      `<div class="tx-w"><div class="tx">${ruleText(r)}</div>${lines.join("")}${acts}</div>` +
      `<span class="chip ${cls}">${label}</span></article>`;
  }

  function render() {
    const n = (st) => rules.filter((r) => r.status === st).length;
    const live = rules.filter((r) => r.status !== "rejected").sort((a, b) => (b.created || "").localeCompare(a.created || "") || b.id - a.id);
    const rej = rules.filter((r) => r.status === "rejected").sort((a, b) => (b.created || "").localeCompare(a.created || "") || b.id - a.id);
    const cand = rules.filter((r) => r.status === "candidate");
    const confN = cand.filter((r) => liveConflicts(r).length).length;
    $("hero").hidden = false;
    $("heroNum").textContent = n("approved");
    $("heroTxt").textContent = n("approved") ? "本の規則を、AI がこの会社から学び、下書きに効いています" : "本。下書きに効いている規則は、まだありません";
    $("summary").hidden = !rules.length;
    $("summary").textContent = rules.length
      ? `有資格者の訂正から育った規則のうち、承認済みの ${n("approved")} 本が効いています。` +
        (cand.length ? `${[cand.length - confN ? `候補 ${cand.length - confN} 本` : "", confN ? `矛盾の可能性 ${confN} 本` : ""].filter(Boolean).join("と")}は、承認されるまで下書きには使いません。` : "承認待ちの候補はありません。")
      : "";
    $("legend").hidden = !rules.length;
    $("rules").innerHTML = live.length ? live.map(ruleHtml).join("")
      : rules.length ? `<div class="empty"><b>効いている規則も、承認待ちの候補もありません。</b>却下した規則は下にまとめてあります。看板ごとの確認で訂正を重ねると、新しい候補が並びます。</div>`
      : `<div class="empty"><b>まだ規則はありません。</b>看板ごとの確認で訂正と聞き返しを重ねると、ここに規則の候補が並びます。<br>候補は、有資格者が承認するまで下書きには使いません。<div><a class="btn" href="h.html">確認待ちの看板を見る</a></div></div>`;
    $("rejected").hidden = !rej.length;
    $("rejSum").textContent = `却下した規則 ${rej.length} 本(下書きには使いません)`;
    $("rejList").innerHTML = rej.map(ruleHtml).join("");
    renderBy();
  }

  // ---- 承認・却下・矛盾の片方を残す ----
  async function act(action, id) {
    if (busy) return;
    const r = byId()[id];
    if (!r) return;
    const by = await askBy();
    if (!by) return;
    busy = true;
    document.querySelectorAll("[data-act]").forEach((b) => (b.disabled = true));
    try {
      if (action === "keep") {
        for (const o of shownConflicts(r)) await api("/api/rules/resolve", { keep: r.id, drop: o.id, by });
        toast("こちらを残しました。食い違う規則は却下し、次の下書きからはこの規則が効きます。");
      } else {
        await api(`/api/rules/${r.id}/${action}`, { by });
        toast(action === "approve" ? "承認しました。次の下書きから効きます。" : "却下しました。下書きには使いません。");
      }
    } catch (e) {
      toast(e.message);
    }
    busy = false;
    await load();
  }
  document.addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (b) act(b.dataset.act, +b.dataset.id);
  });

  async function loadSigns() {
    const ids = [...new Set(rules.map((r) => (/^(.+?) \/ /.exec(r.source || "") || [])[1]).filter((i) => i && !(i in signs)))].slice(0, 40);
    await Promise.all(ids.map((i) => api(`/api/signs/${encodeURIComponent(i)}`)
      .then((d) => { signs[i] = { name: d.name || i, site_id: d.site_id, site_name: d.site_name }; })
      .catch(() => { signs[i] = null; })));   // 消えた看板は id のまま出す
  }
  async function load() {
    try {
      rules = await api("/api/rules");
      await loadSigns();
      render();
    } catch (e) {
      $("rules").innerHTML = `<div class="empty"><b>規則を読み込めませんでした。</b>${esc(e.message)}<div><button class="btn" type="button" id="retry">もう一度読む</button></div></div>`;
      $("retry").onclick = load;
    }
  }

  api("/api/meta").then((m) => {
    items = Object.fromEntries((m.items || []).map((i) => [i.key, i]));
    const co = document.querySelector(".company");
    if (co) co.textContent = SG.TODAY;   // 見本の会社名は出さない(h.js と同じ)。/api/meta が会社名を返せば出す
    const nm = m.company || m.company_name;
    if (nm && co) co.innerHTML = "<b>" + esc(nm) + "</b>" + esc(SG.TODAY);
  }).catch(() => {}).finally(load);
})();
