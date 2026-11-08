// N6 署名(本実装)。この現場の確認待ち(draft)の看板を、有資格者の名前で確定する。
// API: GET /api/sites / GET /api/signs?site=ID / POST /api/signs/ID/finalize {"by": 名前}
// 確定の API は「判断できず」が残っていても通るので、判断できずが残る看板はこの画面で送らない(N5 へ戻す)。
"use strict";
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const q = encodeURIComponent;
  const BY_KEY = "kk_by";   // N5・旧画面(karte.js)と同じ保存先
  const SITE = new URLSearchParams(location.search).get("site");
  let site = null, signs = [], errs = {}, busy = false, lastDone = null;

  async function api(path, body) {
    const r = await fetch(path, body === undefined ? { cache: "no-store" } : { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    let j = null;
    try { j = await r.json(); } catch (e) { /* 本文なし */ }
    if (!r.ok) throw new Error((j && j.error) || `通信に失敗しました(${r.status})`);
    return j;
  }
  function toast(t) { const e = $("toast"); e.textContent = t; e.classList.add("on"); clearTimeout(toast.t); toast.t = setTimeout(() => e.classList.remove("on"), 2800); }
  const getBy = () => { try { return (localStorage.getItem(BY_KEY) || "").trim(); } catch (e) { return ""; } };
  const setBy = (v) => { try { localStorage.setItem(BY_KEY, v); } catch (e) { /* 覚えられなくても進める */ } };
  const when = (s) => s ? String(s).replace("T", " ").slice(0, 16) : "";
  const label = (s) => s.name || s.id;

  // 看板の区分け: 確定済み / 確定できる / 確定できない(判断できずが残る・ランクがまだ無い)
  // ランクの無い下書きは API(review.finalize)が 400 で拒むので、ここでも確定できない側に入れる
  const isDone = (s) => s.status === "confirmed";
  const isBlocked = (s) => s.status === "draft" && (s.undecided > 0 || s.rank == null);
  const isReady = (s) => s.status === "draft" && !(s.undecided > 0) && s.rank != null;
  const blockedWord = (list) => list.every((s) => s.undecided > 0) ? "判断できずが残る看板" : "確定できない看板";
  const others = (s) => !isDone(s) && s.status !== "draft";   // draft でも confirmed でもない(念のため)

  function rankHtml(s) {
    if (s.rank == null) return `<span class="res na">ランクなし</span>`;
    return `<span class="res r${s.rank}${isDone(s) ? "" : " draft"}">${esc(s.rank_label || "ランク " + s.rank)}</span>`;
  }
  function rowHtml(s) {
    const n5 = `n5.html?site=${q(SITE)}&sign=${q(s.id)}`;
    const unk = s.undecided > 0 ? `<span class="unk">判断できず ${s.undecided}</span>` : `<span class="ok0">判断できず なし</span>`;
    let chip, note = "";
    if (isDone(s)) {
      chip = `<span class="chip done">確定</span>`;
      note = `<div class="note muted">署名 ${esc(s.confirmed_by || "")}${s.confirmed_at ? ` ・ <span class="num">${esc(when(s.confirmed_at))}</span>` : ""}</div>`;
    } else if (isBlocked(s)) {
      chip = `<span class="chip wait">確定できません</span>`;
      note = s.undecided > 0
        ? `<div class="note bad">判断できずの項目が ${s.undecided} 項目残っているので確定できません。<a href="${n5}">看板ごとの確認で区分を決める</a></div>`
        : `<div class="note bad">ランクがまだ付いていないので確定できません。<a href="${n5}">看板ごとの確認で見る</a></div>`;
    } else if (s.status === "draft") {
      chip = `<span class="chip draft">確認待ち</span>`;
    } else {
      chip = `<span class="chip draft">${esc(s.status)}</span>`;
    }
    if (errs[s.id]) note += `<div class="note bad" role="alert">${esc(errs[s.id])}</div>`;
    return `<li class="${isBlocked(s) || errs[s.id] ? "blocked" : ""}"><span class="nm">${esc(label(s))}<small>${esc(s.type || "種類 未入力")}${s.size ? " ・ " + esc(s.size) : ""}</small></span>
      <span class="meta">${rankHtml(s)}${unk}${chip}</span>${note}</li>`;
  }

  function draw() {
    const total = signs.length, done = signs.filter(isDone).length;
    const ready = signs.filter(isReady), blocked = signs.filter(isBlocked);
    const allDone = total > 0 && done === total;
    const by = ($("byIn") && $("byIn").value.trim()) || "";
    $("cntTxt").textContent = `確定 ${done}/${total} 基`;
    $("bar").className = "bar" + (allDone ? " ok" : "");
    $("bar").firstElementChild.style.width = (total ? done / total * 100 : 0) + "%";
    let box;
    if (allDone) box = `<div class="callout ok"><span>この現場の看板 ${total} 基はすべて確定済みです。報告書へ進めます。</span></div>`;
    else if (blocked.length) box = `<div class="callout bad"><span><b>${blockedWord(blocked)} ${blocked.length} 基</b>: ${blocked.map((s) => esc(label(s))).join("、")}。${blocked.every((s) => s.undecided > 0) ? "判断できずの項目の区分を決めるまで" : "判断できずの項目を決めてランクが付くまで"}確定できません。${ready.length ? `ほかの ${ready.length} 基は先に確定できます。` : ""}</span></div>`;
    else box = `<div class="callout"><span>確認待ちの看板 ${ready.length} 基を確定できます。</span></div>`;
    $("ngBox").innerHTML = box;
    const sorted = [...signs].sort((a, b) => rankOf(a) - rankOf(b));
    $("signs").innerHTML = sorted.map(rowHtml).join("");
    $("actCard").hidden = allDone || (!ready.length && !blocked.length);
    $("whoCard").hidden = allDone;
    const btn = $("signBtn");
    btn.textContent = busy ? "確定しています…" : `確定して署名(${ready.length} 基)`;
    btn.disabled = busy || !ready.length || !by;
    $("whyOff").textContent = busy ? "" : !ready.length ? "確定できる看板がありません。判断できずの項目を先に決めてください。" : !by ? "署名する有資格者の名前を入れてください。" : "";
    const nx = $("nextBtn");
    nx.classList.toggle("is-off", !allDone);
    nx.setAttribute("aria-disabled", String(!allDone));
    nx.tabIndex = allDone ? 0 : -1;
    drawDone(allDone);
  }
  // 並び: 確定できる → 確定できない(判断できず・ランクなし) → その他 → 確定済み
  function rankOf(s) { return isReady(s) ? 0 : isBlocked(s) ? 1 : others(s) ? 2 : 3; }

  function drawDone(allDone) {
    const c = $("doneCard");
    if (!lastDone) { c.hidden = true; return; }
    c.hidden = false;
    $("dName").textContent = lastDone.by;
    $("dAt").textContent = when(lastDone.at) || "—";
    $("dSigns").textContent = `${lastDone.names.length} 基(${lastDone.names.join("、")})`;
    $("dNext").innerHTML = allDone
      ? `<a class="btn primary" href="n7.html?site=${q(SITE)}">報告書へ進む</a>`
      : `<span class="small muted">まだ確定していない看板があります。上の一覧を見てください。</span>`;
  }

  // ---- 確定して署名 ----
  let lf = null;
  function closeDlg() { $("dlg").classList.remove("on"); lf && lf.focus(); }
  function openDlg() {
    const by = $("byIn").value.trim(); if (!by) { $("byIn").focus(); return; }
    const ready = signs.filter(isReady), blocked = signs.filter(isBlocked);
    lf = $("signBtn");
    $("dBody").innerHTML = `<b>${esc(by)}</b> の名前で、看板 ${ready.length} 基(${ready.map((s) => esc(label(s))).join("、")})の点検結果を確定します。確定後は編集できません。`
      + (blocked.length ? `<br><span class="small muted">${blockedWord(blocked)} ${blocked.length} 基は、今回は確定しません。</span>` : "");
    $("dlg").classList.add("on"); $("cNo").focus();
  }
  async function signAll() {
    const by = $("byIn").value.trim(); if (!by) return;
    setBy(by);
    $("dlg").classList.remove("on");
    busy = true; errs = {}; draw();
    const ready = signs.filter(isReady), ok = [];
    let at = null;
    for (const s of ready) {
      try { const d = await api(`/api/signs/${q(s.id)}/finalize`, { by }); ok.push(label(s)); at = d.confirmed_at || at; }
      catch (e) { errs[s.id] = e.message; }   // API のエラー文をそのまま出す
    }
    try { signs = await api(`/api/signs?site=${q(SITE)}`); } catch (e) { toast("一覧を読み直せませんでした: " + e.message); }
    busy = false;
    if (ok.length) lastDone = { by, at, names: ok };
    draw();
    const nErr = Object.keys(errs).length;
    toast(ok.length ? `${ok.length} 基を確定しました${nErr ? `。${nErr} 基は確定できませんでした` : ""}` : "確定できませんでした。一覧の理由を見てください");
    if (ok.length) $("doneCard").scrollIntoView({ behavior: "smooth", block: "center" });
  }

  // ---- 骨組み・読み込み ----
  function skeleton() {
    const nm = site ? site.name : SITE;
    $("app").innerHTML = `
  <div class="page-head"><div><h1>有資格者の名前で確定します</h1>
    <p class="lead">${esc(nm)} の看板を、点検した有資格者の名前で確定し、署名します。確定後は編集できません。</p></div></div>
  <section class="card" id="signCard">
    <div class="card-title"><h2>この現場の看板</h2><span class="right num" id="cntTxt"></span></div>
    <div class="bar" id="bar"><i></i></div>
    <div id="ngBox" style="margin-top:16px"></div>
    <ul class="signs" id="signs"></ul>
  </section>
  <section class="card" id="whoCard">
    <div class="card-title"><h2>署名する有資格者</h2></div>
    <label class="f" for="byIn">名前(有資格者)</label>
    <div class="who-row"><input class="input" id="byIn" autocomplete="name" maxlength="40" placeholder="例: 点検 花子"></div>
    <p class="hint">確定の記録に、この名前が残ります。この端末に覚えておきます(看板ごとの確認で入れた名前と同じです)。</p>
  </section>
  <section id="actCard">
    <div class="row" style="gap:16px">
      <button class="btn primary lg" id="signBtn" type="button" disabled>確定して署名</button>
      <span class="small muted" id="whyOff"></span>
    </div>
  </section>
  <section class="card signed" id="doneCard" hidden aria-live="polite">
    <div class="card-title"><h2>確定しました</h2><span class="chip done">確定</span></div>
    <dl><dt>署名者</dt><dd id="dName"></dd><dt>確定日時</dt><dd class="num" id="dAt"></dd><dt>看板</dt><dd id="dSigns"></dd></dl>
    <div class="callout" style="margin-top:16px"><span>確定後は編集できません。確定した看板は、この後の取り込みや訂正で書き換わりません。</span></div>
    <div class="row" style="margin-top:16px" id="dNext"></div>
  </section>`;
    $("byIn").value = getBy();
    $("byIn").oninput = draw;
    $("byIn").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); if (!$("signBtn").disabled) openDlg(); } };
    $("signBtn").onclick = openDlg;
  }
  function foot() {
    const s = q(SITE);
    document.body.insertAdjacentHTML("beforeend", `<div class="wizard-foot"><div class="in"><a class="btn" href="n5.html?site=${s}">戻る</a><a class="btn primary lg is-off" id="nextBtn" href="n7.html?site=${s}" aria-disabled="true" tabindex="-1">報告書へ進む</a></div></div>`);
  }
  function message(title, body, link) {
    $("app").innerHTML = `<div class="empty"><b>${esc(title)}</b>${esc(body || "")}${link ? `<br><a class="btn" href="${esc(link[0])}">${esc(link[1])}</a>` : ""}</div>`;
  }
  $("cNo").onclick = closeDlg;
  $("cYes").onclick = signAll;
  $("dlg").addEventListener("click", (e) => { if (e.target === $("dlg")) closeDlg(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && $("dlg").classList.contains("on")) closeDlg(); });

  async function init() {
    // 上の枠の会社名は見本の固定値なので、本物の画面では出さない(日付だけ)
    if (!SITE) return message("現場が選ばれていません", "N1 の物件の選択から入ってください。", ["h.html", "ホームへ"]);
    foot();
    try { site = (await api("/api/sites")).find((s) => s.id === SITE) || null; } catch (e) { site = null; }
    try { signs = await api(`/api/signs?site=${q(SITE)}`); }
    catch (e) { return message("看板の一覧を読み込めませんでした", e.message, ["n6.html?site=" + q(SITE), "もう一度読み込む"]); }
    if (!signs.length) return message("この現場にはまだ看板がありません", "取り込みの画面で写真を入れると、看板ができます。", ["n3.html?site=" + q(SITE), "取り込みへ"]);
    skeleton(); draw();
  }
  init();
})();
