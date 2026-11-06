// N5 看板ごとの確認(本実装)。
// API: GET /api/signs?site=ID / GET /api/signs/ID / POST /api/signs/ID/items(項目の訂正)/ POST /api/rules/ID/answer(聞き返しの答え)/ POST /api/signs/ID/finalize
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const BY_KEY = "kk_by";   // 旧画面(karte.js)と同じ保存先
  const RES = { "良好": "r1", "経過観察": "r2", "要改善": "r3", "対象外": "na" };
  const GROUPS = ["基礎部・上部構造", "支持部", "取付部", "表示部", "照明装置", "その他"];
  const q = new URLSearchParams(location.search);
  const SITE = q.get("site");
  let signs = [];      // 一覧(sign_summary)
  let d = null;        // 開いている看板の詳細
  let cur = 1;         // 表示中の写真番号(photos[].n)
  let openFix = null;  // {key, step, res, note, clarify, err, busy}
  let openGroups = null;

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let j = null;
    try { j = await r.json(); } catch (e) { /* 本文なし */ }
    if (!r.ok) throw new Error((j && j.error) || `通信に失敗しました(${r.status})`);
    return j;
  }
  function toast(t) { const e = $("toast"); e.textContent = t; e.classList.add("on"); clearTimeout(toast.t); toast.t = setTimeout(() => e.classList.remove("on"), 2800); }

  // ---- 名前(訂正・確認済みの記録に要る) ----
  function askBy() {
    const have = (localStorage.getItem(BY_KEY) || "").trim();
    if (have) return Promise.resolve(have);
    return new Promise((resolve) => {
      const dlg = $("byDlg"), inp = $("byIn");
      const close = (v) => { dlg.classList.remove("on"); $("byOk").onclick = $("byCancel").onclick = null; resolve(v); };
      $("byOk").onclick = () => { const v = inp.value.trim(); if (!v) { inp.focus(); return; } localStorage.setItem(BY_KEY, v); close(v); };
      $("byCancel").onclick = () => close(null);
      inp.onkeydown = (e) => { if (e.key === "Enter") $("byOk").click(); };
      dlg.classList.add("on"); inp.focus();
    });
  }

  // ---- 項目の見え方 ----
  function itemState(it) {
    // 区分= value。現場の回答が「対象外」で value が空なら対象外。それ以外の空は判断できず(黄色)
    const res = it.value || (it.guide === "na" || (it.evidence && it.evidence.guide === "na") ? "対象外" : null);
    return { res, human: it.corrected };
  }
  function lastCorrection(key) {
    const rows = (d.corrections || []).filter((c) => c.item === key);
    return rows.length ? rows[rows.length - 1] : null;
  }
  function resHtml(it) {
    const s = itemState(it);
    if (s.res === null) return `<span class="unk">判断できず</span>`;
    const draft = !s.human && d.editable;
    return `<span class="res ${RES[s.res]}${draft ? " draft" : ""}">${esc(s.res)}</span>`;
  }
  function whyHtml(it) {
    if (it.corrected) {
      const c = lastCorrection(it.key);
      return esc((c && c.note) || "所見なし") + `<span class="by">(${esc((c && c.by) || "有資格者")}の判断)</span>`;
    }
    return esc((it.evidence && it.evidence.text) || (itemState(it).res === null ? "根拠の写真が決まっていません" : ""));
  }
  function jumpPhoto(it) { const p = it.evidence && it.evidence.photos; return p && p.length ? p[0] : null; }
  function rowHtml(it) {
    const fix = openFix && openFix.key === it.key ? fixHtml(it) : "";
    const ph = jumpPhoto(it);
    const lab = `${it.group} ${it.no} ${it.text}` + (ph ? ` 写真 ${ph} へ移る` : "");
    return `<div class="irow" role="button" tabindex="0" data-go="${esc(it.key)}" aria-label="${esc(lab)}">
      <div class="iname"><span class="no">${esc(it.group)} ${it.no}</span>${esc(it.text)}</div>
      <div class="iline">${resHtml(it)}${it.yellow && it.value ? `<span class="unk">要実測</span>` : ""}<span class="why">${whyHtml(it)}</span>${d.editable ? `<button class="btn sm" type="button" data-fix="${esc(it.key)}">違う</button>` : ""}</div></div>${fix}`;
  }
  function fixHtml(it) {
    const f = openFix;
    const err = f.err ? `<div class="err" role="alert">${esc(f.err)}</div>` : "";
    if (f.step === 1) return `<div class="fix"><div class="q">正しい区分を選んでください</div><div class="row">${d.result_choices.map((r) => `<button class="btn sm" type="button" data-pick="${esc(r)}">${esc(r)}</button>`).join("")}<button class="btn sm ghost" type="button" data-cancel="1">やめる</button></div></div>`;
    if (f.step === 2) return `<div class="fix"><div class="q">区分: ${esc(f.res)}。所見を 1 行で書いてください(任意)</div>
      <input class="input" id="noteIn" aria-label="所見" maxlength="200" placeholder="例: 取付部プレートの下端に赤さびが広がっている" value="${esc(f.note || "")}">
      <div class="row"><button class="btn sm primary" type="button" data-note="1"${f.busy ? " disabled" : ""}>${f.busy ? "記録中…" : "記録する"}</button><button class="btn sm ghost" type="button" data-cancel="1">やめる</button></div>${err}</div>`;
    if (f.step === 3) return `<div class="fix"><div class="q">聞き返し: ${esc(f.clarify.question)}</div>
      <input class="input" id="ansIn" aria-label="聞き返しへの答え" maxlength="200" placeholder="例: 全周に回っている。写真 3 で見える">
      <div class="row"><button class="btn sm primary" type="button" data-ans="1"${f.busy ? " disabled" : ""}>答える</button><button class="btn sm ghost" type="button" data-skip="1">分からない</button></div>${err}</div>`;
    return `<div class="fix"><div class="callout ok">${esc(f.done)}</div></div>`;
  }

  // ---- 画面 ----
  function drawJudge() {
    const lg = d.legal || {};
    const conf = typeof d.confidence === "number" ? `確信 ${Math.round(d.confidence * 100)}%` : "確信 不明";
    const changed = d.draft_rank && d.rank !== d.draft_rank;
    const yc = d.yellow_count;
    const judge = `<div class="jcard"><div class="jt">判定役 <span class="chip ${d.editable ? "draft" : "done"}">${d.editable ? "下書き" : "確定"}</span></div>
      <div class="jv">ランク ${esc(d.rank_label || "未判定")} ・ ${conf}</div>
      <div class="jd">${yc ? `判断できない項目が ${yc} つあります。` : "判断できない項目はありません。"}${changed ? "有資格者がランクを変更しています。" : ""}</div></div>`;
    let lawV, lawD;
    if (lg.error) { lawV = "法規を引けませんでした"; lawD = esc(lg.error); }
    else {
      const use = d.is_self_use === true ? "自家用" : d.is_self_use === false ? "自家用以外" : "自家用かは未入力";
      lawV = `${use} ・ 許可 ${esc(lg.permit_required == null ? "不明" : lg.permit_required)} ・ 根拠条文 ${(lg.citations || []).length} 件`;
      lawD = (lg.citations || []).length ? "押すと条文カードが開きます。" : "根拠条文はありません。";
    }
    $("judge").innerHTML = judge + `<button class="jcard" id="lawBtn" type="button" aria-haspopup="dialog"><div class="jt">参考(点検の義務外)</div>
      <div class="jv">法規 ${lawV}</div><div class="jd">${lawD}</div></button>`;
    $("lawBtn").onclick = openLaw;
  }
  function drawHead() {
    const i = signs.findIndex((s) => s.id === d.id);
    const conf = signs.filter((s) => s.status === "confirmed").length;
    $("signName").textContent = `${d.name || d.id}(${d.site_name || SITE})`;
    $("signKind").textContent = "種類 " + (d.type || "未入力");
    $("signDim").textContent = "寸法 " + (d.size || "未入力");
    $("progTxt").textContent = `${conf}/${signs.length}`;
    $("progBar").style.width = (signs.length ? conf / signs.length * 100 : 0) + "%";
    $("posTxt").textContent = `${i + 1} 基目`;
    $("signNav").innerHTML = signs.map((s, k) => `<a href="n5.html?site=${encodeURIComponent(SITE)}&sign=${encodeURIComponent(s.id)}" class="${s.status === "confirmed" ? "done" : ""}" ${s.id === d.id ? 'aria-current="true"' : ""}>${k + 1} ${esc(s.name || s.id)}</a>`).join("");
    const btn = $("confirmBtn");
    btn.hidden = false;
    if (d.status === "confirmed") { btn.textContent = "確認済みです"; btn.disabled = true; }
    else if (!d.editable) { btn.textContent = "確認できません(写真がありません)"; btn.disabled = true; }
    else { btn.textContent = "この看板を確認済みにする"; btn.disabled = false; }
  }
  function drawGroups() {
    const kept = openGroups || (() => {
      const first = GROUPS.map((g) => d.items.some((it) => it.group === g && it.yellow));
      return first.some(Boolean) ? first : GROUPS.map((_, i) => i === 0);
    })();
    $("groups").innerHTML = GROUPS.map((g, gi) => {
      const its = d.items.filter((it) => it.group === g);
      if (!its.length) return "";
      const unk = its.filter((it) => it.yellow).length;
      return `<details class="grp" data-g="${gi}"${kept[gi] ? " open" : ""}><summary>${esc(g)}<span class="cnt">${its.length} 項目${unk ? ` ・ <span class="unk">判断できず ${unk}</span>` : ""}</span></summary>
        <ul class="items">${its.map((it) => `<li data-key="${esc(it.key)}">${rowHtml(it)}</li>`).join("")}</ul></details>`;
    }).join("");
    $("itemsTitle").textContent = `点検項目(${d.items.length} 項目)`;
    showPhoto(cur);
  }
  function redrawRow(key) {
    const li = document.querySelector(`.items>li[data-key="${CSS.escape(key)}"]`);
    const it = d.items.find((x) => x.key === key);
    if (li && it) li.innerHTML = rowHtml(it);
    showPhoto(cur);
  }
  function snapshotGroups() { const g = [...document.querySelectorAll("details.grp")]; if (g.length) openGroups = GROUPS.map((_, i) => { const e = g.find((x) => +x.dataset.g === i); return e ? e.open : false; }); }

  // ---- 写真 ----
  function photoByN(n) { return d.photos.find((p) => p.n === n); }
  function showPhoto(n) {
    const ph = d.photos;
    if (!ph.length) {
      $("photo").innerHTML = `写真がありません<small>この看板には写真が登録されていません</small>`;
      $("phCnt").textContent = "写真 0/0"; $("prevPh").disabled = $("nextPh").disabled = true; return;
    }
    const idx = Math.max(0, ph.findIndex((p) => p.n === n));
    const p = ph[idx]; cur = p.n;
    $("photo").innerHTML = p.url ? `<img src="${esc(p.url)}" alt="写真 ${p.n}${p.figure ? "(図面)" : p.measure ? "(実測)" : ""}" id="phImg">` : `写真 ${p.n}<small>写真の場所が台帳にありません</small>`;
    const img = $("phImg");
    if (img) img.onerror = () => { $("photo").innerHTML = `写真 ${p.n}<small>写真を読み込めませんでした</small>`; };
    $("phCnt").textContent = `写真 ${idx + 1}/${ph.length}${p.figure ? "(図面)" : p.measure ? "(実測)" : ""}`;
    $("prevPh").disabled = $("nextPh").disabled = ph.length < 2;
    $("thumbs").innerHTML = ph.map((x) => `<button type="button" data-n="${x.n}" aria-label="写真 ${x.n}" aria-current="${x.n === cur}">${x.n}</button>`).join("");
    document.querySelectorAll(".items>li").forEach((li) => {
      const it = d.items.find((x) => x.key === li.dataset.key);
      li.classList.toggle("sel", !!(it && it.evidence && (it.evidence.photos || []).includes(cur)));
    });
  }
  function step(dir) { const ph = d.photos; if (!ph.length) return; const i = ph.findIndex((p) => p.n === cur); showPhoto(ph[(i + dir + ph.length) % ph.length].n); }

  // ---- 訂正 ----
  async function submitFix() {
    const f = openFix, it = d.items.find((x) => x.key === f.key);
    const by = await askBy(); if (!by) return;
    f.note = ($("noteIn") || {}).value ? $("noteIn").value.trim() : f.note;
    f.busy = true; f.err = null; redrawRow(f.key);
    try {
      const r = await api(`/api/signs/${encodeURIComponent(d.id)}/items`, { item: f.key, value: f.res, by, note: f.note || null });
      snapshotGroups(); d = r; f.busy = false;
      if (r.clarify) { f.clarify = r.clarify; f.step = 3; }
      else { f.step = 4; f.done = !r.changed ? "下書きと同じ区分なので、記録していません。" : r.rule_id ? `規則の候補に積みました(候補 #${r.rule_id})。承認されると、次の点検から似た写真で同じ判断を提案します。` : "訂正を記録しました。"; }
      drawJudge(); drawHead(); drawGroups();
    } catch (e) { f.busy = false; f.err = e.message; redrawRow(f.key); }
    void it;
  }
  async function sendAnswer(answer) {
    const f = openFix; const by = await askBy(); if (!by) return;
    f.busy = true; f.err = null; redrawRow(f.key);
    try {
      await api(`/api/rules/${f.clarify.rule_id}/answer`, { question: f.clarify.question, answer, by });
      f.busy = false; f.step = 4; f.done = `規則の候補に積みました(候補 #${f.clarify.rule_id})。答えで規則の形を直しました。承認されるまで下書きには効きません。`;
    } catch (e) { f.busy = false; f.err = e.message; }
    redrawRow(f.key);
  }

  document.addEventListener("click", (e) => {
    const t = e.target;
    const fx = t.closest("[data-fix]");
    if (fx) { const old = openFix && openFix.key; openFix = { key: fx.dataset.fix, step: 1 }; if (old && old !== openFix.key) redrawRow(old); redrawRow(openFix.key); return; }
    const pk = t.closest("[data-pick]"); if (pk) { openFix.res = pk.dataset.pick; openFix.step = 2; redrawRow(openFix.key); const n = $("noteIn"); n && n.focus(); return; }
    if (t.closest("[data-note]")) { submitFix(); return; }
    if (t.closest("[data-ans]")) { const v = $("ansIn").value.trim(); if (!v) { $("ansIn").focus(); return; } sendAnswer(v); return; }
    if (t.closest("[data-skip]")) { openFix.step = 4; openFix.done = `規則の候補 #${openFix.clarify.rule_id} は所見のまま積みました。`; redrawRow(openFix.key); return; }
    if (t.closest("[data-cancel]")) { const k = openFix.key; openFix = null; redrawRow(k); return; }
    const go = t.closest("[data-go]");
    if (go) { const it = d.items.find((x) => x.key === go.dataset.go); const p = it && jumpPhoto(it); if (p) showPhoto(p); return; }
    const th = t.closest("#thumbs button"); if (th) showPhoto(+th.dataset.n);
  });
  document.addEventListener("keydown", (e) => {
    const go = e.target.closest && e.target.closest("[data-go]");
    if (go && e.target === go && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); go.click(); }
    if (e.key === "Escape") { closeLaw(); }
  });

  // ---- 確認済みにする ----
  async function confirmSign() {
    const by = await askBy(); if (!by) return;
    const btn = $("confirmBtn"); btn.disabled = true; btn.textContent = "記録中…";
    try {
      const done = d.name || d.id;
      await api(`/api/signs/${encodeURIComponent(d.id)}/finalize`, { by });
      signs = await api(`/api/signs?site=${encodeURIComponent(SITE)}`);
      const i = signs.findIndex((s) => s.id === d.id);
      const next = signs.slice(i + 1).concat(signs.slice(0, i)).find((s) => s.status === "draft");
      if (next) { toast(`${done} を確認済みにしました。次は ${next.name || next.id}`); await open(next.id); window.scrollTo({ top: 0, behavior: "smooth" }); }
      else { toast(`${done} を確認済みにしました。すべての看板の確認が済みました`); await open(d.id); }
    } catch (e) { toast(e.message); drawHead(); }
  }

  // ---- 法規の窓 ----
  let lastFocus = null;
  function closeLaw() { const dl = $("lawDlg"); if (dl.classList.contains("on")) { dl.classList.remove("on"); lastFocus && lastFocus.focus(); } }
  function openLaw() {
    const lg = d.legal || {}, cs = lg.citations || [];
    lastFocus = $("lawBtn");
    $("lawTitle").textContent = `根拠条文(${cs.length} 件)`;
    $("lawCard").hidden = true;
    $("lawList").innerHTML = cs.length ? cs.map((c, k) => `<li><button class="btn" type="button" data-law="${k}">${esc(c.source)} ${esc(c.article)}</button></li>`).join("")
      : `<li class="muted">${esc(lg.error || "この看板には、参照した条文がありません。")}</li>`;
    $("lawList").onclick = (e) => { const b = e.target.closest("[data-law]"); if (!b) return; const c = cs[+b.dataset.law];
      $("lawCard").hidden = false;
      $("lawCard").innerHTML = `<h4>${esc(c.source)} ${esc(c.article)}</h4><p class="small" style="margin-top:8px">${esc(c.quote)}</p>${c.quote_verified === false ? `<p class="small muted" style="margin-top:8px">条文との照合ができていません。</p>` : ""}`; };
    $("lawDlg").classList.add("on");
    const b = $("lawList").querySelector("button"); (b || $("lawClose")).focus();
  }
  $("lawClose").onclick = closeLaw;
  $("lawDlg").addEventListener("click", (e) => { if (e.target === $("lawDlg")) closeLaw(); });

  // ---- 骨組み・読み込み ----
  function skeleton() {
    $("app").innerHTML = `
  <div class="sign-nav" id="signNav" aria-label="この現場の看板"></div>
  <div class="head-row">
    <div style="flex:1 1 320px;min-width:0">
      <h1 id="signName"></h1>
      <div class="meta"><span id="signKind"></span><span class="num" id="signDim"></span><span class="num" id="posTxt"></span></div>
    </div>
    <div class="prog"><div class="small muted"><b class="num" id="progTxt"></b> 基 確認済み</div><div class="bar"><i id="progBar"></i></div></div>
    <div class="right"><button class="btn primary" id="confirmBtn" type="button">この看板を確認済みにする</button></div>
  </div>
  <div class="judge-bar"><div class="judge" id="judge" aria-label="AI の判定と法規"></div></div>
  <div class="cols">
    <section class="photo-col viewer" aria-label="写真">
      <div class="ph" id="photo"></div>
      <div class="vctl">
        <button class="btn sm" id="prevPh" type="button" aria-label="前の写真">‹ 前へ</button>
        <div class="cnt num" id="phCnt" aria-live="polite"></div>
        <button class="btn sm" id="nextPh" type="button" aria-label="次の写真">次へ ›</button>
      </div>
      <div class="thumbs" id="thumbs"></div>
    </section>
    <section aria-labelledby="itemsTitle">
      <h2 id="itemsTitle" class="small" style="font-size:14px;margin-bottom:4px"></h2>
      <div class="small muted" style="margin-bottom:8px">項目を押すと、左の写真がその根拠の写真へ移ります。違うときは「違う」を押してください。</div>
      <div id="groups"></div>
    </section>
  </div>`;
    $("prevPh").onclick = () => step(-1); $("nextPh").onclick = () => step(1);
    $("confirmBtn").onclick = confirmSign;
  }
  function foot() {
    if (document.querySelector(".wizard-foot")) return;
    const s = encodeURIComponent(SITE);
    document.body.insertAdjacentHTML("beforeend", `<div class="wizard-foot"><div class="in"><a class="btn" href="n4.html?site=${s}">戻る</a><a class="btn primary lg" id="nextBtn" href="n6.html?site=${s}">署名へ進む</a></div></div>`);
  }
  function message(title, body, link) {
    $("app").innerHTML = `<div class="empty"><b>${esc(title)}</b>${esc(body || "")}${link ? `<br><a class="btn" href="${esc(link[0])}">${esc(link[1])}</a>` : ""}</div>`;
  }
  async function open(id) {
    d = await api(`/api/signs/${encodeURIComponent(id)}`);
    cur = 1; openFix = null; openGroups = null;
    history.replaceState(null, "", `n5.html?site=${encodeURIComponent(SITE)}&sign=${encodeURIComponent(id)}`);
    if (!$("signNav")) skeleton();
    // 最初に出す写真= 最初の「黄色」でない項目の根拠ではなく、1 枚目
    if (d.photos.length) cur = d.photos[0].n;
    drawHead(); drawJudge(); drawGroups();
  }
  async function init() {
    // 上の枠の会社名は見本の固定値なので、本物の画面では出さない(日付だけ)
    const c = document.querySelector(".topbar .company"); if (c) c.innerHTML = `<b>${new Date().toLocaleDateString("ja-JP", { year: "numeric", month: "long", day: "numeric" })}</b>`;
    if (!SITE) return message("現場が選ばれていません", "N1 の物件の選択から入ってください。", ["n1.html", "物件を選ぶ"]);
    foot();
    try { signs = await api(`/api/signs?site=${encodeURIComponent(SITE)}`); }
    catch (e) { return message("看板の一覧を読み込めませんでした", e.message, ["n5.html?site=" + encodeURIComponent(SITE), "もう一度読み込む"]); }
    if (!signs.length) return message("この現場にはまだ看板がありません", "取り込みの画面で写真を入れると、看板ができます。", ["n3.html?site=" + encodeURIComponent(SITE), "取り込みへ"]);
    const want = q.get("sign");
    const start = (signs.find((s) => s.id === want) || signs.find((s) => s.status === "draft") || signs[0]).id;
    try { await open(start); } catch (e) { message("この看板を読み込めませんでした", e.message); }
  }
  init();
})();
