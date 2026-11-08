// N4 不明点(本実装)。この現場の看板で「判断できず」(区分が空)の項目を 1 問ずつ出し、有資格者がその場で区分を選んで記録する。
// API: GET /api/signs?site=ID(undecided>0 の看板)/ GET /api/signs/ID(items[].value が空=判断できず)/ POST /api/signs/ID/items(N5・旧画面と同じ body)/ POST /api/rules/ID/answer(聞き返しの答え)
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const BY_KEY = "kk_by";   // N5・旧画面(karte.js)と同じ保存先
  const MAIN = [["良好", "r1"], ["経過観察", "r2"], ["要改善", "r3"]];
  const q = new URLSearchParams(location.search);
  const SITE = q.get("site");
  const S = encodeURIComponent(SITE || "");
  let signs = [];          // 一覧(sign_summary)
  const det = {};          // 看板 ID → 詳細
  let Q = [];              // [{sid, key}]
  let i = 0, later = 0, answered = 0;
  let cur = null;          // 表示中の写真番号
  let clarify = null;      // 聞き返し {rule_id, question}
  let busy = false, err = null;
  let locked = [], noPhoto = [];

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let j = null;
    try { j = await r.json(); } catch (e) { /* 本文なし */ }
    if (!r.ok) throw new Error((j && j.error) || `通信に失敗しました(${r.status})`);
    return j;
  }
  function toast(t) { const e = $("toast"); e.textContent = t; e.classList.add("on"); clearTimeout(toast.t); toast.t = setTimeout(() => e.classList.remove("on"), 2400); }

  function askBy() {
    let have = "";
    try { have = (localStorage.getItem(BY_KEY) || "").trim(); } catch (e) { /* 保存できない端末 */ }
    if (have) return Promise.resolve(have);
    return new Promise((resolve) => {
      const dlg = $("byDlg"), inp = $("byIn");
      const close = (v) => { dlg.classList.remove("on"); $("byOk").onclick = $("byCancel").onclick = null; resolve(v); };
      $("byOk").onclick = () => { const v = inp.value.trim(); if (!v) { inp.focus(); return; } try { localStorage.setItem(BY_KEY, v); } catch (e) { /* 覚えられなくても進める */ } close(v); };
      $("byCancel").onclick = () => close(null);
      inp.onkeydown = (e) => { if (e.key === "Enter") $("byOk").click(); };
      dlg.classList.add("on"); inp.focus();
    });
  }

  // 判断できず= 区分が空で、現場の回答が「対象外」でもなく、有資格者もまだ直していない項目(N5 の黄色「判断できず」と同じ)
  const undecided = (it) => !it.value && it.guide !== "na" && !(it.evidence && it.evidence.guide === "na") && !it.corrected;

  function message(title, body, link) {
    $("app").innerHTML = `<div class="empty"><b>${esc(title)}</b>${esc(body || "")}${link ? `<br><a class="btn" href="${esc(link[0])}">${esc(link[1])}</a>` : ""}</div>`;
  }
  function foot() {
    document.body.insertAdjacentHTML("beforeend", `<div class="wizard-foot"><div class="in"><a class="btn" href="n3.html?site=${S}">戻る</a><a class="btn primary lg" id="nextBtn" href="n5.html?site=${S}" aria-disabled="true">看板ごとの確認へ</a></div></div>`);
    $("nextBtn").addEventListener("click", (e) => { if ($("nextBtn").getAttribute("aria-disabled") === "true") e.preventDefault(); });
  }

  function skeleton() {
    $("app").innerHTML = `
  <section id="ask">
    <div class="row" style="margin-bottom:8px"><h1 style="font-size:18px">不明点</h1><span class="right muted num" id="prog"></span></div>
    <div class="bar" style="margin-bottom:24px"><i id="pbar"></i></div>
    <div class="card stack" id="card"></div>
  </section>
  <section id="fin" hidden>
    <div class="card stack" id="finCard"></div>
  </section>`;
  }

  function photoHtml(d) {
    const p = d.photos.find((x) => x.n === cur);
    if (!p) return `写真がありません<small>この看板には写真が登録されていません</small>`;
    return p.url ? `<img src="${esc(p.url)}" alt="${esc(d.name || d.id)} 写真 ${p.n}" id="qImg">` : `写真 ${p.n}<small>写真の場所が台帳にありません</small>`;
  }
  function render() {
    if (i >= Q.length) return finish();
    const { sid, key } = Q[i], d = det[sid], it = d.items.find((x) => x.key === key);
    const pos = signs.filter((s) => det[s.id]).findIndex((s) => s.id === sid);
    $("prog").textContent = `${i + 1}/${Q.length}`;
    $("pbar").style.width = (i / Q.length * 100) + "%";
    const why = (it.evidence && it.evidence.text) || "AI が写真から区分を決められなかった項目です。";
    const evPh = it.evidence && (it.evidence.photos || []).length ? `根拠の写真 ${it.evidence.photos.join("・")}` : "根拠の写真は決まっていません";
    const dis = busy ? " disabled" : "";
    let body;
    if (clarify) {
      body = `<div class="callout ok" role="status">記録しました。規則の候補に積みました(候補 #${clarify.rule_id})。</div>
      <h2 class="q-title">聞き返し: ${esc(clarify.question)}</h2>
      <input class="input" id="ansIn" aria-label="聞き返しへの答え" maxlength="200" placeholder="例: 全周に回っている。写真 3 で見える">
      <div class="row"><button type="button" class="btn primary" id="ansOk"${dis}>答える</button><button type="button" class="btn ghost" id="ansSkip"${dis}>分からない</button></div>`;
    } else {
      body = `<h2 class="q-title">${esc(it.group)} ${it.no}「${esc(it.text)}」は、どの区分ですか</h2>
      <p class="q-why">${esc(why)}(${esc(evPh)})</p>
      <div class="opts" id="opts">${MAIN.map(([r, c]) => `<button type="button" class="btn" data-pick="${r}"${dis}><span class="res ${c}">${r}</span></button>`).join("")}</div>
      <div><label class="f" for="noteIn">所見(任意・1 行)</label><input class="input" id="noteIn" maxlength="200" placeholder="例: 取付部プレートの下端に赤さびが広がっている"></div>
      <div class="opts-sub"><button type="button" class="btn sm" data-pick="対象外"${dis}>対象外(その部位が無い)</button><button type="button" class="btn ghost" id="later"${dis}>あとで</button></div>`;
    }
    $("card").innerHTML = `<div class="q-sign">看板 ${pos + 1}/${Object.keys(det).length} ${esc(d.name || d.id)}${d.type ? ` ・ ${esc(d.type)}` : ""}${d.size ? ` ・ <span class="num">${esc(d.size)}</span>` : ""}</div>
      <div class="ph q-ph" id="photo">${photoHtml(d)}</div>
      ${d.photos.length > 1 ? `<div class="q-thumbs" aria-label="この看板の写真">${d.photos.map((p) => `<button type="button" data-n="${p.n}" aria-label="写真 ${p.n}" aria-current="${p.n === cur}">${p.n}</button>`).join("")}</div>` : ""}
      ${body}
      ${busy ? `<p class="muted small" role="status">記録中…</p>` : ""}${err ? `<p class="q-err" role="alert">${esc(err)}</p>` : ""}`;
    const img = $("qImg"); if (img) img.onerror = () => { $("photo").innerHTML = `写真 ${cur}<small>写真を読み込めませんでした</small>`; };
  }
  function firstPhoto(sid, key) {
    const d = det[sid], it = d.items.find((x) => x.key === key);
    const ev = it && it.evidence && it.evidence.photos;
    return ev && ev.length ? ev[0] : d.photos.length ? d.photos[0].n : null;
  }
  function go(n) { i = n; clarify = null; err = null; if (i < Q.length) cur = firstPhoto(Q[i].sid, Q[i].key); render(); window.scrollTo({ top: 0 }); }

  async function pick(value) {
    const { sid, key } = Q[i];
    const by = await askBy(); if (!by) return;
    const note = (($("noteIn") || {}).value || "").trim();
    busy = true; err = null; render();
    try {
      const r = await api(`/api/signs/${encodeURIComponent(sid)}/items`, { item: key, value, by, note: note || null });
      det[sid] = r; busy = false; answered++;
      if (r.clarify) { clarify = r.clarify; render(); const a = $("ansIn"); a && a.focus(); return; }
      toast(r.changed ? `${value} で記録しました` : "記録しました");
      go(i + 1);
    } catch (e) { busy = false; err = e.message; render(); }
  }
  async function answer(text) {
    const by = await askBy(); if (!by) return;
    busy = true; err = null; render();
    try {
      await api(`/api/rules/${clarify.rule_id}/answer`, { question: clarify.question, answer: text, by });
      busy = false; toast("答えで規則の候補を直しました"); go(i + 1);
    } catch (e) { busy = false; err = e.message; render(); }
  }

  function finish() {
    $("ask").hidden = true; $("fin").hidden = false;
    $("nextBtn").removeAttribute("aria-disabled");
    const notes = [];
    if (noPhoto.length) notes.push(`写真が無い看板 ${noPhoto.length} 基(${noPhoto.map((s) => s.name || s.id).join("、")})は、ここでは聞いていません。取り込みで写真を入れてください。`);
    if (locked.length) notes.push(`確定済みの看板 ${locked.length} 基(${locked.map((s) => s.name || s.id).join("、")})の判断できず ${locked.reduce((a, s) => a + s.undecided, 0)} 項目は、確定時の内容のままです。`);
    let head, sub;
    if (!Q.length) { head = `<div class="callout ok" role="status"><b>${notes.length ? "ここで答える不明点はありません。" : "不明点はありません。"}</b></div>`; sub = notes.length ? "判断できずの項目が残っている看板は、下のとおりここでは聞いていません。" : "この現場の看板には、判断できない項目がありません。"; }
    else if (later) { head = `<div class="callout warn" role="status"><b>「あとで」に回した ${later} 件が残っています。</b></div>`; sub = `${answered} 件に答えました。「あとで」に回した ${later} 件は、看板ごとの確認で黄色の「判断できず」として残ります。`; }
    else { head = `<div class="callout ok" role="status"><b>不明点はありません。</b></div>`; sub = `${Q.length} 件すべてに答えました。`; }
    $("finCard").innerHTML = `${head}<p class="muted">${esc(sub)}</p>${notes.length ? `<ul class="fin-list muted">${notes.map((t) => `<li>${esc(t)}</li>`).join("")}</ul>` : ""}
      <p>次は、看板ごとの確認です。</p>
      <div class="row"><a class="btn primary lg" href="n5.html?site=${S}">看板ごとの確認へ</a>${later ? `<button type="button" class="btn" id="again">「あとで」の ${later} 件に戻る</button>` : ""}</div>`;
    const a = $("again"); if (a) a.onclick = () => location.reload();
  }

  document.addEventListener("click", (e) => {
    if (busy) return;
    const t = e.target;
    const pk = t.closest("[data-pick]"); if (pk) { pick(pk.dataset.pick); return; }
    if (t.closest("#later")) { later++; go(i + 1); return; }
    if (t.closest("#ansOk")) { const v = $("ansIn").value.trim(); if (!v) { $("ansIn").focus(); return; } answer(v); return; }
    if (t.closest("#ansSkip")) { go(i + 1); return; }
    const th = t.closest(".q-thumbs button"); if (th) { cur = +th.dataset.n; render(); }
  });

  async function init() {
    // 上の枠の会社名は見本の固定値なので、本物の画面では出さない(日付だけ)
    if (!SITE) return message("現場が選ばれていません", "ホームで物件を選んでから入ってください。", ["h.html", "ホームへ"]);
    foot();
    try { signs = await api(`/api/signs?site=${S}`); }
    catch (e) { return message("看板の一覧を読み込めませんでした", e.message, ["n4.html?site=" + S, "もう一度読み込む"]); }
    if (!signs.length) return message("この現場にはまだ看板がありません", "取り込みの画面で写真を入れると、看板ができます。", ["n3.html?site=" + S, "取り込みへ"]);
    const want = signs.filter((s) => (s.undecided || 0) > 0);
    locked = want.filter((s) => s.status !== "draft");
    let ds;
    try { ds = await Promise.all(want.filter((s) => s.status === "draft").map((s) => api(`/api/signs/${encodeURIComponent(s.id)}`))); }
    catch (e) { return message("看板を読み込めませんでした", e.message, ["n4.html?site=" + S, "もう一度読み込む"]); }
    for (const d of ds) {
      if (!d.editable) { locked.push(d); continue; }        // 読み込みの間に確定された
      if (!d.photos.length) { noPhoto.push(d); continue; }  // 写真が無い看板は聞かない(N5 と同じく確認できない)
      const its = d.items.filter(undecided);
      if (!its.length) continue;
      det[d.id] = d;
      its.forEach((it) => Q.push({ sid: d.id, key: it.key }));
    }
    skeleton();
    go(0);
  }
  init();
})();
