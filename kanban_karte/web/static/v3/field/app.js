// 現場ウィザード(スマホ)F0〜F6。1 枚の index.html の中で段を切り替える単一ページ。
// 写真はこのページのメモリにだけ置く(sessionStorage・ページ遷移なし)。タブを閉じれば消える。
// API: GET /api/field/parts, GET /api/field/locate, POST /api/owner/estimate, POST /api/field/draft, POST /api/intake/upload
(function () {
  "use strict";
  const $ = (s, r) => (r || document).querySelector(s);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const fmt = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const NS = "http://www.w3.org/2000/svg";
  const STEPS = ["看板の種類", "全景を撮る", "正面を撮る", "部位を撮る", "寸法と所見", "送信"];
  const CLS = { "良好": "r1", "経過観察": "r2", "要改善": "r3", "対象外": "na" };
  const RESULTS = ["良好", "経過観察", "要改善", "対象外"];
  const UNDECIDED = "判断できず";
  const KINDS = [["wall", "壁面", "壁に貼る・取り付ける"], ["projecting", "袖", "壁から直角に突き出す"],
    ["freestanding", "自立", "地面に立つ"], ["rooftop", "屋上", "建物の屋上に載る"], ["unknown", "分からない", "あとで事務所で決めます"]];
  const KIND_ICON = {
    wall: '<rect x="10" y="6" width="44" height="40" fill="none" stroke="currentColor" stroke-width="2"/><rect x="18" y="12" width="28" height="9" fill="#e5281e"/><rect x="16" y="28" width="9" height="18" fill="none" stroke="currentColor" stroke-width="1.5"/><rect x="34" y="28" width="12" height="8" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="2" y1="46" x2="62" y2="46" stroke="currentColor" stroke-width="2"/>',
    projecting: '<rect x="6" y="4" width="20" height="42" fill="none" stroke="currentColor" stroke-width="2"/><line x1="26" y1="12" x2="30" y2="12" stroke="currentColor" stroke-width="2"/><rect x="30" y="8" width="14" height="20" fill="#e5281e"/><line x1="2" y1="46" x2="62" y2="46" stroke="currentColor" stroke-width="2"/><line x1="40" y1="34" x2="40" y2="46" stroke="#f08c00" stroke-width="2" stroke-dasharray="3 2"/>',
    freestanding: '<rect x="20" y="4" width="24" height="16" fill="#e5281e"/><line x1="32" y1="20" x2="32" y2="46" stroke="currentColor" stroke-width="3"/><line x1="2" y1="46" x2="62" y2="46" stroke="currentColor" stroke-width="2"/><line x1="52" y1="30" x2="52" y2="46" stroke="#f08c00" stroke-width="2" stroke-dasharray="3 2"/>',
    rooftop: '<rect x="14" y="20" width="36" height="26" fill="none" stroke="currentColor" stroke-width="2"/><line x1="22" y1="20" x2="22" y2="14" stroke="currentColor" stroke-width="2"/><line x1="42" y1="20" x2="42" y2="14" stroke="currentColor" stroke-width="2"/><rect x="18" y="4" width="28" height="10" fill="#e5281e"/><line x1="2" y1="46" x2="62" y2="46" stroke="currentColor" stroke-width="2"/>',
    unknown: '<rect x="12" y="6" width="40" height="34" rx="4" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="4 3"/><text x="32" y="31" text-anchor="middle" font-size="22" font-weight="700" fill="currentColor">?</text>',
  };
  const PIN = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21s-6.5-6.2-6.5-11a6.5 6.5 0 0 1 13 0c0 4.8-6.5 11-6.5 11z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="12" cy="10" r="2.4" fill="currentColor"/></svg>';
  const CAM = '<svg viewBox="0 0 24 24" width="22" height="22" aria-hidden="true"><path d="M4 8h3l1.6-2h6.8L17 8h3v11H4z" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><circle cx="12" cy="13.5" r="3.4" fill="none" stroke="currentColor" stroke-width="1.8"/></svg>';

  // ---- 状態(1 基ぶん)。「次の看板へ」で作り直す ----
  let sign = 1;
  function newSid() {   // 端末で作る送信の id(http では randomUUID が無いので乱数で作る)
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const a = new Uint8Array(16); (window.crypto || { getRandomValues: (x) => x.map(() => Math.random() * 256) }).getRandomValues(a);
    return Array.from(a, (b) => b.toString(16).padStart(2, "0")).join("");
  }
  let S;
  function fresh() {
    return { sid: newSid(), lat: null, lng: null, mode: "guide", kind: null, wide: null, front: null, address: "", muni: null, inScope: true, loc: { state: "idle" },
      f3: "idle", f3err: "", est: null, pts: null, vb: null, kx: 1, ky: 1, sel: -1, hand: false, dim: null, dimAI: null,
      meas: { w: "", h: "", t: "", g: "", note: "", name: "" }, touched: {}, pi: 0, paper: [], sent: null, sending: false, sendErr: "" };
  }
  S = fresh();
  let PARTS = null;          // [{part, items:[{key,no,text}]}] /api/field/parts
  let PS = [];               // 部位ごとの状態: {photos:[{url,b64}], rows:[{key,no,text,ai,evidence,photo,pick,shown}], status, err, skipped}
  let cur = "s0", hist = [];

  // ---- 共通の小物 ----
  async function api(path, body) {
    let r, j = {};
    try {
      r = await fetch(path, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : undefined);
    } catch (e) { throw new Error("通信できませんでした。電波のよい所でもう一度。"); }
    try { j = await r.json(); } catch (e) { /* 本文なし */ }
    if (!r.ok) { const er = new Error(j.error || ("エラー " + r.status)); er.missing = j.missing; throw er; }
    return j;
  }
  function toast(msg) { const t = $("#toast"); t.textContent = msg; t.classList.add("on"); clearTimeout(toast.t); toast.t = setTimeout(() => t.classList.remove("on"), 3000); }

  // 撮影 → 長辺 1600px の JPEG に縮める(API の写真の上限に収める)
  async function shrink(file) {
    let bmp;
    try { bmp = await createImageBitmap(file, { imageOrientation: "from-image" }); }
    catch (e) {
      bmp = await new Promise((ok, ng) => { const u = URL.createObjectURL(file), im = new Image(); im.onload = () => ok(im); im.onerror = ng; im.src = u; });
    }
    const w0 = bmp.width || bmp.naturalWidth, h0 = bmp.height || bmp.naturalHeight, sc = Math.min(1, 1600 / Math.max(w0, h0));
    const w = Math.round(w0 * sc), h = Math.round(h0 * sc), c = document.createElement("canvas");
    c.width = w; c.height = h;
    c.getContext("2d").drawImage(bmp, 0, 0, w, h);
    const url = c.toDataURL("image/jpeg", 0.85);
    return { url, b64: url.split(",")[1], w, h };
  }
  function capture(cb) {
    const i = document.createElement("input");
    i.type = "file"; i.accept = "image/*"; i.setAttribute("capture", "environment"); i.hidden = true;
    document.body.appendChild(i);
    i.addEventListener("change", async () => {
      const f = i.files && i.files[0]; i.remove();
      if (!f) return;
      try { cb(await shrink(f)); } catch (e) { toast("写真を読めませんでした。もう一度撮ってください。"); }
    });
    i.click();
  }

  // ---- 写真 1 枚ごとの裏の送信(撮るたびに /api/field/photo。失敗した分だけ再送) ----
  const REG = new Map(); let pidN = 0; const upq = []; let upBusy = false;
  const UPT = { idle: "準備中", wait: "待機中", send: "送信中…", ok: "✓ 送信済み", fail: "送れません(タップで再送)" };
  function reg(p, slot, n) {
    Object.assign(p, { id: ++pidN, sid: S.sid, slot, n, up: "idle", err: "" }); if (p.replaces) p.replaces.gone = true; REG.set(p.id, p);
    p.up = "wait"; upq.push(p); pump(); return p;
  }
  function upBadge(p) { return `<span class="upb is-${p.up}" data-pid="${p.id}"${p.up === "fail" ? ' role="button" tabindex="0"' : ""}>${UPT[p.up]}</span>`; }
  function badges() {
    document.querySelectorAll("[data-pid]").forEach((el) => {
      const p = REG.get(+el.dataset.pid); if (!p) return;
      el.className = el.className.replace(/is-\w+/, "is-" + p.up); el.textContent = UPT[p.up];
      if (p.up === "fail") { el.setAttribute("role", "button"); el.tabIndex = 0; } else el.removeAttribute("role");
    });
    if (cur === "s6") paintSendRows();
  }
  async function pump() {
    if (upBusy) return; upBusy = true;
    while (upq.length) {
      const p = upq.shift(); if (p.gone || p.up === "ok") continue;
      p.up = "send"; badges();
      try { await api("/api/field/photo", { submission_id: p.sid, slot: p.slot, n: p.n, name: p.slot + ".jpg", data: p.b64 }); p.up = "ok"; p.err = ""; }
      catch (e) { p.up = "fail"; p.err = e.message; }
      badges();
    }
    upBusy = false;
  }
  function retryUp(p) { p.up = "wait"; upq.push(p); badges(); pump(); }
  document.addEventListener("click", (e) => {
    const b = e.target.closest(".upb.is-fail"); if (b) retryUp(REG.get(+b.dataset.pid));
  });

  // ---- 枠: 上の戻る / 段の進み / 下の大きなボタン ----
  const VIEW = $("#view");
  let nextFn = null, ghostFn = null;
  function setFoot(f) {
    const el = $("#foot"), n = $("#nextBtn"), g = $("#ghostBtn");
    nextFn = f && f.go; ghostFn = f && f.ghost && f.ghost.go;
    if (!f) { el.hidden = true; return; }
    el.hidden = false;
    n.textContent = f.label; n.classList.toggle("is-off", !f.ok);
    if (f.ok) n.removeAttribute("aria-disabled"); else n.setAttribute("aria-disabled", "true");
    g.hidden = !f.ghost; if (f.ghost) g.textContent = f.ghost.label;
  }
  $("#nextBtn").addEventListener("click", () => { if (nextFn && $("#nextBtn").getAttribute("aria-disabled") !== "true") nextFn(); });
  $("#ghostBtn").addEventListener("click", () => { if (ghostFn) ghostFn(); });
  function frame(no) {
    $("#fstep").hidden = false;
    $("#fnow").innerHTML = no ? `<b>${no}/6</b><span>${STEPS[no - 1]}</span>` : "<span>はじめに</span>";
    $("#fbar").innerHTML = STEPS.map((s, i) => `<li class="${i + 1 < no ? "done" : i + 1 === no ? "cur" : ""}"></li>`).join("");
    $("#ttl").textContent = "看板 " + sign + " 基目";
  }
  function go(id, back) {
    if (!back && cur !== id) hist.push(cur);
    cur = id;
    backVis(!hist.length || (id === "s6" && !!S.sent));
    VIEW.scrollTop = 0; window.scrollTo(0, 0);
    ({ s0, s0b, s1, s2, s3, s4, s5, s6 })[id]();
  }
  function backVis(off) { $("#backBtn").style.visibility = off ? "hidden" : "visible"; }
  function goBack() {
    if (cur === "s4" && S.pi > 0) { S.pi--; return s4(); }
    if (hist.length) go(hist.pop(), true);
  }
  $("#backBtn").addEventListener("click", goBack);
  window.addEventListener("beforeunload", (e) => {
    if (!S.sent && (S.wide || S.paper.length)) { e.preventDefault(); e.returnValue = ""; }
  });

  // ---- F0 やり方を選ぶ ----
  function s0() {
    frame(0); setFoot(null);
    VIEW.innerHTML = `<h1>どちらで点検しますか?</h1>
      <p class="lead">1 つ選んでください。あとから変えられます。</p>
      <div class="how">
        <button type="button" class="entry-card main" id="goGuide">
          <div class="top"><div class="ic" aria-hidden="true">1</div><h3>案内に沿って撮る</h3><span class="tagm">おすすめ</span></div>
          <p>看板の種類から順に、画面の案内どおりに撮ります。AI が項目の下書きを作ります。</p>
          <span class="go">はじめる ›</span></button>
        <button type="button" class="entry-card" id="goPaper">
          <div class="top"><div class="ic" aria-hidden="true">2</div><h3>紙の点検表を撮って送る</h3></div>
          <p>紙に書いた点検表を、判定欄・仕様欄・手描きのスケッチもそのまま撮って送ります。</p>
          <span class="go">点検表を撮る ›</span></button>
      </div>`;
    $("#goGuide").addEventListener("click", () => { S.mode = "guide"; go("s1"); });
    $("#goPaper").addEventListener("click", () => { S.mode = "paper"; go("s0b"); });
  }

  // ---- F0b 点検表を撮る ----
  function s0b() {
    frame(0);
    const paint = () => {
      VIEW.innerHTML = `<h1>点検表を撮る</h1>
        <p class="lead">判定欄・仕様欄・手描きのスケッチが、文字が読める大きさで入るように撮ります。</p>
        ${S.paper.length ? `<div class="paper-grid">${S.paper.map((p, i) => `<div class="ph shot"><img src="${p.url}" alt="撮った点検表 ${i + 1} 枚目"><span class="chip done small">✓ 撮れました</span><button type="button" class="x" data-del="${i}" aria-label="${i + 1} 枚目を消す">×</button></div>`).join("")}</div>` : `<div class="ph photo-big">まだ撮っていません<small>下のボタンで 1 枚目を撮ります</small></div>`}
        <button type="button" class="btn block lg camera" id="shot">${CAM}${S.paper.length ? "もう 1 枚撮る" : "点検表を撮る"}</button>
        <p class="hint">送ると、事務所で項目に書き起こして有資格者が確認します。</p>`;
      $("#shot").addEventListener("click", () => capture((p) => { S.paper.push(p); paint(); }));
      VIEW.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", () => { S.paper.splice(+b.dataset.del, 1); paint(); }));
      setFoot({ label: "送信する", ok: S.paper.length > 0, go: () => go("s6") });
    };
    paint();
  }

  // ---- F1 看板の種類 ----
  function s1() {
    frame(1);
    VIEW.innerHTML = `<h1>どんな看板ですか?</h1><p class="lead">付き方でいちばん近い絵を 1 つ選んでください。</p>
      <div class="kind-grid" id="kindGrid" role="group" aria-label="看板の種類">${KINDS.map((k) =>
        `<button type="button" class="pick" data-v="${k[0]}" aria-pressed="${S.kind === k[0]}"><svg class="ico" viewBox="0 0 64 48" aria-hidden="true">${KIND_ICON[k[0]]}</svg><span class="pl">${k[1]}</span><span class="ps">${k[2]}</span></button>`).join("")}</div>`;
    const foot = () => setFoot({ label: "次へ", ok: !!S.kind, go: () => go("s2") });
    $("#kindGrid").addEventListener("click", (e) => {
      const b = e.target.closest(".pick"); if (!b) return;
      S.kind = b.dataset.v;
      VIEW.querySelectorAll(".pick").forEach((c) => c.setAttribute("aria-pressed", String(c === b)));
      foot();
    });
    foot();
  }

  // ---- F2 全景 + 位置 ----
  function locate() {
    S.loc = { state: "loading" }; paintLoc();
    if (!navigator.geolocation) { S.loc = { state: "err", msg: "この端末では位置情報が使えません。" }; return paintLoc(); }
    navigator.geolocation.getCurrentPosition(async (p) => {
      try {
        const j = await api(`/api/field/locate?lat=${p.coords.latitude}&lng=${p.coords.longitude}`);
        S.address = j.address; S.muni = j.municipality; S.lat = j.lat; S.lng = j.lng; S.inScope = j.in_scope !== false;
        S.loc = { state: "ok" };
      } catch (e) { S.loc = { state: "err", msg: e.message }; }
      paintLoc();
    }, (err) => {
      S.loc = { state: "err", msg: err && err.code === 1 ? "位置情報が許可されていません。" : "位置を読めませんでした。" };
      paintLoc();
    }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
  }
  function paintLoc() {
    if (cur !== "s2") return;
    const box = $("#loc"), L = S.loc;
    const inp = `<label class="f" for="addr">住所<span class="opt">${L.state === "ok" ? "違えば手で直せます" : "手で入れてください"}</span></label>
      <input class="input" id="addr" type="text" autocomplete="street-address" placeholder="例: 埼玉県越谷市越ヶ谷1-1-7" value="${esc(S.address)}">`;
    let top = "";
    if (L.state === "loading") top = '<div class="loc-read"><i class="spin"></i>位置情報を読んでいます…</div>';
    else if (L.state === "ok") top = `<div class="callout ${S.inScope ? "ok" : "warn"}">${PIN}<span>${S.muni ? `<b>${esc(S.muni)}</b>${S.inScope ? "の条例が適用されます" : "です。対象自治体(東京都・埼玉県)の外です"}` : "住所を直しました"}</span></div>`;
    else if (L.state === "err") top = `<div class="callout warn">${PIN}<span>${esc(L.msg)}</span></div><button type="button" class="btn sm" id="reloc" style="margin-top:8px">位置を読み直す</button>`;
    if (L.state === "ok" && !S.muni) top = "";
    box.innerHTML = top + (L.state === "loading" ? "" : inp);
    const a = $("#addr", box);
    if (a) a.addEventListener("input", () => { S.address = a.value; S.muni = null; foot2(); const c = $(".callout", box); if (c) c.remove(); });
    const r = $("#reloc", box); if (r) r.addEventListener("click", locate);
    foot2();
  }
  const foot2 = () => { if (cur === "s2") setFoot({ label: "次へ", ok: !!(S.wide && S.address.trim()), go: () => go("s3") }); };
  function s2() {
    frame(2);
    const paint = () => {
      VIEW.innerHTML = `<h1>全景を撮る</h1><p class="lead">建物の角と地面が入るように、少し離れて 1 枚。</p>
        ${S.wide ? `<div class="ph photo-big shot"><img src="${S.wide.url}" alt="撮った全景の写真"><span class="tag">✓ 撮れました</span>${upBadge(S.wide)}</div>` : `<div class="ph photo-big">全景<small>まだ撮っていません</small></div>`}
        <button type="button" class="btn block lg camera ${S.wide ? "" : "primary"}" id="shot">${CAM}${S.wide ? "撮り直す" : "全景を撮る"}</button>
        <div class="loc" id="loc" aria-live="polite"></div>`;
      $("#shot").addEventListener("click", () => capture((p) => { if (S.wide) S.wide.gone = true; S.wide = reg(p, "全景", 1); if (S.loc.state !== "ok") locate(); paint(); paintLoc(); }));
      paintLoc();
    };
    paint();
    if (S.loc.state === "idle") locate();
  }

  // ---- F3 正面 + 寸法の推定(角を指で動かすと数字が変わる) ----
  function el(tag, attrs, parent) { const e = document.createElementNS(NS, tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
  function dims() {
    const p = S.pts;
    const dx = (a, b) => Math.abs(p[a][0] - p[b][0]), dy = (a, b) => Math.abs(p[a][1] - p[b][1]);
    // 横は水平方向の差、縦は垂直方向の差(角をずらしても傾いた長さを足さない)。0 をふせぐ
    const r = (v) => Math.max(10, Math.round(v / 10) * 10);
    return { w: r((dx(0, 1) + dx(3, 2)) / 2 * S.kx), h: r((dy(0, 3) + dy(1, 2)) / 2 * S.ky) };
  }
  function f3foot() {
    if (cur !== "s3") return;
    setFoot({ label: "次へ", ok: S.f3 === "done" || S.f3 === "skipped", go: () => go("s4") });
  }
  async function estimate() {
    S.f3 = "loading"; S.f3err = ""; S.hand = false; S.sel = -1; S.dim = null; S.est = null; paintF3(); f3foot();
    try {
      const j = await api("/api/owner/estimate", { address: S.address.trim(), photos: [S.front.b64], kind: S.kind });
      const e = j.estimate, ph = (e.photos && e.photos[0]) || {}, b = ph.sign_box || [0.25, 0.3, 0.75, 0.55];
      // 看板が写っていない写真では推定が W 1 × H 1 mm のような値になる。その尺度で角を動かしても数字は変わらないので、測れなかったとして扱う
      if (!(e.w_mm >= 100 && e.h_mm >= 100)) throw new Error(`写真の中に看板を見つけられませんでした(推定 W ${fmt(e.w_mm)} × H ${fmt(e.h_mm)} mm)。看板が正面に大きく写るように撮り直すか、スキップして 5/6 で寸法を手で入れてください。`);
      const W = S.front.w, H = S.front.h;
      S.vb = { w: W, h: H };
      S.pts = [[b[0] * W, b[1] * H], [b[2] * W, b[1] * H], [b[2] * W, b[3] * H], [b[0] * W, b[3] * H]];
      S.kx = e.w_mm / Math.max(1, (b[2] - b[0]) * W); S.ky = e.h_mm / Math.max(1, (b[3] - b[1]) * H);
      S.est = e; S.dimAI = { w: e.w_mm, h: e.h_mm }; S.dim = dims();
      S.f3 = "done";
    } catch (err) { S.f3 = "error"; S.f3err = err.message; }
    if (cur === "s3") { paintF3(); f3foot(); }
  }
  function s3() {
    frame(3);
    if (S.front && S.f3 === "idle") { estimate(); return; }
    paintF3(); f3foot();
  }
  function paintF3() {
    if (cur !== "s3") return;
    const head = `<h1>正面を撮る</h1><p class="lead">看板の真正面から 1 枚。ドアなど大きさの分かる物が写ると、寸法が合いやすくなります。</p>`;
    if (!S.front) {
      VIEW.innerHTML = head + `<div class="ph photo-big">正面<small>まだ撮っていません</small></div>
        <button type="button" class="btn block lg camera primary" id="shot">${CAM}正面を撮る</button>
        <div class="skip-row"><button type="button" class="btn ghost" id="skip">スキップ</button></div>
        <p class="hint" style="text-align:center">点検なので、寸法は後でもかまいません。</p>`;
      $("#shot").addEventListener("click", onShot); $("#skip").addEventListener("click", skip);
      return;
    }
    const loading = S.f3 === "loading", done = S.f3 === "done", W = S.front.w, H = S.front.h;
    VIEW.innerHTML = head + `<div class="stage${done ? " done" : ""}" id="stage">
        <svg class="main" id="svg" viewBox="0 0 ${W} ${H}" role="img" aria-label="撮った正面の写真と、AI が置いた看板の枠"><g id="world"><image href="${S.front.url}" x="0" y="0" width="${W}" height="${H}"/><g id="ov"></g></g></svg>
        <span class="dimtag" id="tagW"></span><span class="dimtag" id="tagH"></span>
        <div class="lens" id="lens" hidden aria-hidden="true"><svg id="lensSvg" viewBox="0 0 100 100"><rect x="-9000" y="-9000" width="20000" height="20000" fill="#bcd6ee"/><use href="#world"/></svg><i class="cross h"></i><i class="cross v"></i><span class="lab">3 倍</span></div>
        ${loading ? '<div class="scan"><span class="pill"><i class="spin"></i>推定中…</span></div>' : ""}
      </div>
      ${S.f3 === "error" ? `<div class="callout bad" style="margin-top:16px"><span>${esc(S.f3err)}</span></div>
        <button type="button" class="btn block" id="retry" style="margin-top:8px">もう一度測る</button>` : `
      <div class="card tight dim"><div class="top"><span class="small muted">概寸</span>${upBadge(S.front)}<span class="chip draft" id="dimChip" ${done && !S.hand ? "" : "hidden"}>AI の下書き</span></div>
        <div class="dim-num${done ? "" : " est"}" id="dimNum" aria-live="polite">${done ? "" : "推定中…"}</div>
        <p class="hint" id="dimNote">${done ? "" : "看板の寸法を、写真から測っています。"}</p></div>`}
      ${S.f3 === "skipped" ? "" : `<div class="skip-row"><button type="button" class="btn ghost" id="skip">スキップ</button></div>
      <p class="hint" style="text-align:center">点検なので、寸法は後でもかまいません。</p>`}
      <button type="button" class="btn block camera" id="shot" style="margin-top:16px">${CAM}撮り直す</button>`;
    $("#shot").addEventListener("click", onShot);
    const sk = $("#skip"); if (sk) sk.addEventListener("click", skip);
    const rt = $("#retry"); if (rt) rt.addEventListener("click", estimate);
    if (S.pts) { refresh(); wireDrag(); }
  }
  function onShot() { capture((p) => { if (S.front) S.front.gone = true; S.front = reg(p, "正面", 1); S.f3 = "idle"; S.pts = null; estimate(); }); }
  function skip() { S.f3 = "skipped"; go("s4"); }
  function pct(x, y) { return [x / S.vb.w * 100, y / S.vb.h * 100]; }
  function renderOv() {
    const u = S.vb.w / 360, poly = S.pts.map((p) => p.join(",")).join(" ");
    let h = `<polygon class="frame" points="${poly}"/><polygon class="frame2" points="${poly}"/>`;
    S.pts.forEach((p, i) => { h += `<circle class="ring${i === S.sel ? " sel" : ""}" cx="${p[0]}" cy="${p[1]}" r="${(i === S.sel ? 10 : 8) * u}"/>`; });
    $("#ov").innerHTML = h;
    VIEW.querySelectorAll(".hit").forEach((n) => n.remove());
    S.pts.forEach((p, i) => { const c = el("circle", { class: "hit", cx: p[0], cy: p[1], r: 28 * u }, $("#svg")); c.dataset.i = i; });
    const top = pct((S.pts[0][0] + S.pts[1][0]) / 2, Math.min(S.pts[0][1], S.pts[1][1])), rt = pct(Math.max(S.pts[1][0], S.pts[2][0]), Math.max(S.pts[2][1], S.pts[3][1]));
    const d = dims(), tw = $("#tagW"), th = $("#tagH");
    tw.textContent = "W " + fmt(d.w); tw.style.left = top[0] + "%"; tw.style.top = top[1] + "%"; tw.style.transform = "translate(-50%,-135%)";
    th.textContent = "H " + fmt(d.h); th.style.left = rt[0] + "%"; th.style.top = rt[1] + "%"; th.style.transform = "translate(-100%,14px)";
  }
  function renderNum() {
    const d = dims(), n = $("#dimNum"); if (!n) return;
    S.dim = d; S.meas.w = S.touched.w ? S.meas.w : fmt(d.w); S.meas.h = S.touched.h ? S.meas.h : fmt(d.h);
    n.classList.remove("est");
    n.innerHTML = `W ${fmt(d.w)} × H ${fmt(d.h)} <small>mm</small>`;
    $("#dimChip").hidden = S.hand;
    $("#dimNote").textContent = S.hand ? `手で直しました。AI の推定は W ${fmt(S.dimAI.w)} × H ${fmt(S.dimAI.h)} mm でした。` : "枠の角の丸を指で動かすと、数字が変わります。";
  }
  function renderLens() {
    const lens = $("#lens"); if (!lens) return;
    if (S.sel < 0) { lens.hidden = true; return; }
    const p = S.pts[S.sel], sc = $("#svg").getBoundingClientRect().width / S.vb.w, w = (lens.clientWidth || 116) / (sc * 3);
    lens.hidden = false;
    lens.className = "lens " + (p[0] < S.vb.w / 2 ? "right" : "left");
    $("#lensSvg").setAttribute("viewBox", `${p[0] - w / 2} ${p[1] - w / 2} ${w} ${w}`);
  }
  function refresh() { renderOv(); renderLens(); if (S.f3 === "done") renderNum(); }
  let drag = null;
  function toSvg(e) { const s = $("#svg"), q = s.createSVGPoint(); q.x = e.clientX; q.y = e.clientY; return q.matrixTransform(s.getScreenCTM().inverse()); }
  function wireDrag() {
    $("#svg").addEventListener("pointerdown", (e) => {
      const h = e.target.closest(".hit");
      if (!h) { if (S.sel >= 0) { S.sel = -1; refresh(); } return; }
      e.preventDefault();
      S.sel = +h.dataset.i;
      const q = toSvg(e); drag = { dx: S.pts[S.sel][0] - q.x, dy: S.pts[S.sel][1] - q.y };
      refresh();
    });
  }
  window.addEventListener("pointermove", (e) => {
    if (!drag || S.sel < 0 || cur !== "s3") return;
    const q = toSvg(e);
    S.pts[S.sel][0] = Math.max(0, Math.min(S.vb.w, q.x + drag.dx));
    S.pts[S.sel][1] = Math.max(0, Math.min(S.vb.h, q.y + drag.dy));
    const d = dims(); S.hand = S.hand || d.w !== S.dimAI.w || d.h !== S.dimAI.h;
    refresh();
  });
  const endDrag = () => { drag = null; };
  window.addEventListener("pointerup", endDrag); window.addEventListener("pointercancel", endDrag);

  // ---- F4 部位ごとに撮る。撮るたびにその部位の項目へ下書きが「入っていく」 ----
  async function loadParts() {
    if (PARTS) return;
    PARTS = (await api("/api/field/parts")).map((g) => g);
    PS = PARTS.map((g) => ({ photos: [], status: "idle", err: "", skipped: false,
      rows: g.items.map((it) => ({ key: it.key, no: it.no, text: it.text, ai: null, evidence: "", photo: null, pick: null, shown: false })) }));
  }
  async function s4() {
    frame(4);
    if (!PARTS) {
      VIEW.innerHTML = '<div class="loc-read"><i class="spin"></i>部位の項目を読んでいます…</div>'; setFoot(null);
      try { await loadParts(); } catch (e) {
        VIEW.innerHTML = `<div class="callout bad"><span>${esc(e.message)}</span></div><button type="button" class="btn block" id="retry" style="margin-top:12px">もう一度読む</button>`;
        $("#retry").addEventListener("click", s4); return;
      }
      if (cur !== "s4") return;
    }
    paintPart();
  }
  const resHtml = (r) => {
    if (!r.shown) return '<span class="thinking"><i class="spin"></i>下書きを作っています…</span>';
    const photo = r.photo ? `<span class="pno">写真 ${r.photo}</span>` : "";
    if (r.pick) return `<span class="res ${CLS[r.pick]}">${r.pick}</span><span class="why">${r.pick === r.ai ? "AI の下書きのまま" : "手で選びました"}</span>${r.evidence ? `<span class="ev">${esc(r.evidence)}${photo}</span>` : ""}`;
    if (r.ai) return `<span class="res ${CLS[r.ai]} draft">${r.ai}</span><span class="why">(AI 下書き)</span>${r.evidence ? `<span class="ev">${esc(r.evidence)}${photo}</span>` : ""}`;
    return `<span class="unk">${UNDECIDED}</span><span class="why">${esc(r.evidence || "人が選んでください")}</span>`;
  };
  function rowHtml(r, i, fx) {
    const idle = !PS[S.pi].photos.length;
    return `<button type="button" class="irow${fx ? " pop glow" : ""}" data-i="${i}"${r.shown ? "" : " disabled"}><div class="k">${esc(PARTS[S.pi].part)} ${r.no}</div><div class="tx">${esc(r.text)}</div><div class="rs">${idle && !r.shown ? '<span class="why">撮ると AI が下書きを入れます</span>' : resHtml(r)}</div></button>`;
  }
  function sumHtml() {
    const p = PS[S.pi], rows = p.rows;
    if (!rows.every((r) => r.shown) || p.status !== "done") return null;
    const ai = rows.filter((r) => r.ai && !r.pick).length, unk = rows.filter((r) => !r.ai && !r.pick).length;
    return unk ? ["warn", `AI の下書き ${ai} 件、判断できず ${unk} 件。確定は事務所の有資格者がします。`] : ["ok", "全部の項目に区分が入りました。確定は事務所の有資格者がします。"];
  }
  function paintPart() {
    if (cur !== "s4") return;
    const pi = S.pi, g = PARTS[pi], p = PS[pi], n = PARTS.length;
    const folds = PS.slice(0, pi).map((q, j) => {
      const st = q.skipped ? "飛ばしました" : q.status === "loading" ? "下書き中…" : q.status === "error" ? "下書きできず" : q.status === "done" ? `下書き ${q.rows.filter((r) => r.shown).length} 件` : "撮っていません";
      const li = q.rows.filter((r) => r.shown).map((r) => `<li><span class="res ${r.pick ? CLS[r.pick] : r.ai ? CLS[r.ai] + " draft" : ""}">${r.pick || r.ai || UNDECIDED}</span><span>${esc(PARTS[j].part)} ${r.no} ${esc(r.text)}</span></li>`).join("");
      return `<details class="fold"><summary><span class="ok">${q.skipped || q.status === "idle" ? "–" : "✓"}</span><span>${esc(PARTS[j].part)}</span><span class="chip draft">${st}</span></summary>${li ? `<ul>${li}</ul>` : ""}</details>`;
    }).join("");
    const stT = p.status === "loading" ? ["AI が項目を見ています", `${p.photos.length} 枚を小さく判定中(10〜20 秒)`]
      : p.status === "done" ? ["下書きができました", "違う所は項目をタップして直せます"]
      : p.status === "error" ? ["下書きを作れませんでした", p.err]
      : ["この部位を撮ってください", "撮ると、下の項目に AI の下書きが入ります"];
    VIEW.innerHTML = `<div class="grp"><div><div class="no">部位 ${pi + 1}/${n}</div><h1>${esc(g.part)}</h1></div>
        <div class="nx">${pi + 1 < n ? `次の部位<br><b>${esc(PARTS[pi + 1].part)}</b>` : "最後の部位"}</div></div>
      <ol class="gbar" aria-hidden="true">${PARTS.map((_, i) => `<li class="${i < pi ? "done" : i === pi ? "cur" : ""}"></li>`).join("")}</ol>
      ${folds}
      <div class="shot-row">
        ${p.photos.length ? `<div class="thumbs">${p.photos.map((x, k) => `<div class="ph shot"><img src="${x.url}" alt="撮った${esc(g.part)}の写真 ${k + 1}"><small class="pn">写真 ${k + 1}</small>${upBadge(x)}</div>`).join("")}</div>`
          : `<div class="ph">${esc(g.part)}<small>未撮影</small></div>`}
        <div class="st"><b>${stT[0]}</b><span class="muted">${esc(stT[1])}</span>
          ${p.photos.length ? `<br><button type="button" class="btn sm ghost" id="retake" style="padding:0;height:28px">撮り直す</button>${p.photos.length < 2 ? ' <button type="button" class="btn sm ghost" id="more" style="padding:0 0 0 12px;height:28px">もう 1 枚撮る</button>' : ""}` : ""}</div>
      </div>
      <div class="card items" id="items"><div class="items-head">${esc(g.part)}の項目<span class="right">タップで区分を選ぶ</span></div><div id="rows">${p.rows.map((r, i) => rowHtml(r, i, false)).join("")}</div></div>
      <div class="callout sum" id="sum" hidden></div>`;
    $("#items").addEventListener("click", (e) => { const b = e.target.closest(".irow"); if (b && !b.disabled) openSheet(+b.dataset.i); });
    const rt = $("#retake"); if (rt) rt.addEventListener("click", () => shoot(true));
    const mo = $("#more"); if (mo) mo.addEventListener("click", () => shoot(false));
    paintSum(); partFoot();
  }
  function paintSum() {
    const s = sumHtml(), box = $("#sum"); if (!box) return;
    box.hidden = !s; if (s) { box.className = "callout sum " + s[0]; box.textContent = s[1]; }
  }
  function partFoot() {
    if (cur !== "s4") return;
    const pi = S.pi, p = PS[pi], last = pi + 1 >= PARTS.length, name = PARTS[pi].part;
    const adv = () => { if (!last) { S.pi++; paintPart(); window.scrollTo(0, 0); } else go("s5"); };
    if (!p.photos.length) return setFoot({ label: `${name}を撮る`, ok: true, go: () => shoot(true), ghost: { label: last ? "この部位は飛ばして進む" : "この部位は飛ばす", go: () => { p.skipped = true; adv(); } } });
    if (p.status === "error") return setFoot({ label: "もう一度下書きを作る", ok: true, go: () => draft(pi), ghost: { label: last ? "このまま進む" : "次の部位へ", go: adv } });
    setFoot({ label: last ? "寸法と所見へ" : "次の部位を撮る", ok: true, go: adv });
  }
  function shoot(replace) {
    const pi = S.pi;
    capture((ph) => {
      const p = PS[pi];
      p.skipped = false;
      if (replace) { p.photos.forEach((x) => { x.gone = true; }); p.photos = [reg(ph, PARTS[pi].part, 1)]; } else p.photos.push(reg(ph, PARTS[pi].part, p.photos.length + 1));
      draft(pi);
    });
  }
  async function draft(pi) {
    const p = PS[pi];
    p.status = "loading"; p.err = ""; p.rows.forEach((r) => { r.shown = false; r.pick = null; r.ai = null; r.evidence = ""; r.photo = null; });
    if (cur === "s4" && S.pi === pi) { paintPart(); }
    let out;
    try { out = await api("/api/field/draft", { part: PARTS[pi].part, photos: p.photos.map((x) => x.b64) }); }
    catch (e) { p.status = "error"; p.err = e.message; if (cur === "s4" && S.pi === pi) paintPart(); return; }
    const got = {}; (out.items || []).forEach((it) => { got[it.key] = it; });
    p.rows.forEach((r) => {
      const it = got[r.key];
      r.ai = it && RESULTS.includes(it.result) ? it.result : null;
      r.evidence = it && it.evidence || "";
      const m = r.evidence.match(/写真\s*([0-9])/);
      r.photo = m ? +m[1] : (p.photos.length === 1 && r.ai ? 1 : null);
    });
    p.status = "done";
    const live = () => cur === "s4" && S.pi === pi && p.photos.length;
    if (live()) { const st = $(".st b"); if (st) { st.textContent = "下書きを入れています"; } }
    // 項目を 1 件ずつ 300〜500ms おきに欄へ入れる(入る瞬間に軽く光る)
    for (let i = 0; i < p.rows.length; i++) {
      await wait(300 + Math.random() * 200);
      p.rows[i].shown = true;
      if (live()) {
        const old = $(`#rows .irow[data-i="${i}"]`);
        if (old) { old.outerHTML = rowHtml(p.rows[i], i, true); }
        const nowEl = $(`#rows .irow[data-i="${i}"]`); if (nowEl) nowEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
      }
    }
    if (live()) { paintPart(); } else if (cur === "s4") partFoot();
  }
  // 区分を選ぶ小さな窓(下から出るシート)
  let sheetI = -1;
  function openSheet(i) {
    sheetI = i; const r = PS[S.pi].rows[i];
    $("#sheetK").textContent = `${PARTS[S.pi].part} ${r.no}`; $("#sheetT").textContent = r.text;
    $("#opts").innerHTML = RESULTS.map((v) => `<button type="button" class="copt" data-v="${v}" aria-pressed="${v === (r.pick || r.ai)}"><span class="res ${CLS[v]}">${v}</span>${v === r.ai ? '<span class="ai">AI 下書き</span>' : ""}</button>`).join("");
    $("#sheetBack").classList.add("on"); $("#opts").firstChild.focus();
  }
  const closeSheet = () => $("#sheetBack").classList.remove("on");
  $("#opts").addEventListener("click", (e) => {
    const b = e.target.closest(".copt"); if (!b) return;
    const r = PS[S.pi].rows[sheetI]; r.pick = b.dataset.v; closeSheet();
    const old = $(`#rows .irow[data-i="${sheetI}"]`); if (old) old.outerHTML = rowHtml(r, sheetI, false);
    paintSum();
  });
  $("#sheetClose").addEventListener("click", closeSheet);
  $("#sheetBack").addEventListener("click", (e) => { if (e.target === $("#sheetBack")) closeSheet(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeSheet(); });

  // ---- F5 寸法と所見 ----
  function s5() {
    frame(5);
    const d = S.dim, M = S.meas;
    if (d && !S.touched.w) M.w = fmt(d.w);
    if (d && !S.touched.h) M.h = fmt(d.h);
    const aiChip = (k) => d && !S.touched[k] && !S.hand ? '<span class="chip draft" id="c' + k + '">AI の下書き</span>' : "";
    VIEW.innerHTML = `<h1>寸法と所見を確かめる</h1><p class="lead">数字は手で直せます。単位は mm、地上高だけ m です。</p>
      <div class="card tight">
        <div class="nrow"><label class="nm" for="vw">幅(W)<small>${d ? "正面の写真で測った概寸" : "正面を測っていません"}</small>${aiChip("w")}</label><div class="unit"><input class="input" id="vw" inputmode="numeric" autocomplete="off" value="${esc(M.w)}"><span>mm</span></div></div>
        <div class="nrow"><label class="nm" for="vh">高さ(H)<small>${d ? "正面の写真で測った概寸" : "正面を測っていません"}</small>${aiChip("h")}</label><div class="unit"><input class="input" id="vh" inputmode="numeric" autocomplete="off" value="${esc(M.h)}"><span>mm</span></div></div>
        <div class="nrow"><label class="nm" for="vt">厚み<small>看板の奥行き</small></label><div class="unit"><input class="input" id="vt" inputmode="numeric" autocomplete="off" value="${esc(M.t)}"><span>mm</span></div></div>
        <div class="nrow"><label class="nm" for="vg">地上高<small>地面から看板の下端まで</small></label><div class="unit"><input class="input" id="vg" inputmode="decimal" autocomplete="off" value="${esc(M.g)}"><span>m</span></div></div>
      </div>
      <label class="f" for="nm" style="margin-top:24px">看板の呼び名<span class="opt">任意</span></label>
      <input class="input note-in" id="nm" type="text" maxlength="40" placeholder="例: 入口の袖看板" value="${esc(M.name)}">
      <label class="f" for="note" style="margin-top:16px">所見(1 行)<span class="opt">任意</span></label>
      <input class="input note-in" id="note" type="text" maxlength="60" placeholder="例: 取付部のアンカーボルトにさびがある" value="${esc(M.note)}">
      <p class="hint">AI の下書きと事務所での確定は、この後に続きます。</p>`;
    [["vw", "w"], ["vh", "h"], ["vt", "t"], ["vg", "g"]].forEach(([id, k]) => {
      const n = $("#" + id);
      n.addEventListener("input", () => { M[k] = n.value; S.touched[k] = true; n.classList.add("hand"); const c = $("#c" + k); if (c) c.hidden = true; });
      n.addEventListener("blur", () => { if (k === "g") return; const v = parseInt(n.value.replace(/[^\d]/g, ""), 10); if (!isNaN(v)) { n.value = fmt(v); M[k] = n.value; } });
    });
    $("#note").addEventListener("input", (e) => { M.note = e.target.value; });
    $("#nm").addEventListener("input", (e) => { M.name = e.target.value; });
    setFoot({ label: "次へ", ok: true, go: () => go("s6") });
  }

  // ---- F6 送信 ----
  // 案内に沿って撮った時: 写真は撮るたびに裏で届いている。ここで残りを送り直し、/api/field/submit で看板を下書き(確認待ち)にする。
  // 紙の点検表: 従来どおり /api/intake/upload に束で送る。
  function guideList() {
    const out = [];
    if (S.wide) out.push({ nm: "全景", sub: "", p: S.wide });
    if (S.front) out.push({ nm: "正面", sub: "", p: S.front });
    PS.forEach((q, i) => q.photos.forEach((p, k) => out.push({ nm: PARTS[i].part, sub: q.photos.length > 1 ? `写真 ${k + 1}` : "", p })));
    return out;
  }
  function paperList() { return S.paper.map((p, i) => ({ nm: "点検表", sub: `${i + 1} 枚目`, p, file: `paper_${String(i + 1).padStart(2, "0")}.jpg` })); }
  const allPhotos = () => (S.mode === "paper" ? paperList() : guideList());
  let sendSt = "ready", sendProg = 0;
  function s6() {
    frame(6);
    const L = allPhotos();
    if (!L.length) { VIEW.innerHTML = '<div class="empty"><b>送る写真がありません</b>前の段に戻って、撮ってください。</div>'; setFoot(null); return; }
    sendSt = S.sent ? "done" : S.sendErr ? "fail" : S.sending ? "send" : "ready";
    paintSend(L, sendSt, sendProg);
  }
  function rowStt(x, st) {
    if (S.mode === "paper") return st === "done" ? "✓ 送信済み" : st === "fail" ? "送れませんでした" : st === "send" ? "送信中" : "✓ 準備できました";
    const p = x.p;
    return p.up === "fail" ? `送れませんでした<button type="button" class="btn sm" data-re="${p.id}">再送</button>` : p.up === "ok" ? "✓ 送信済み" : p.up === "send" ? "送信中" : "待機中";
  }
  const rowCls = (x, st) => S.mode === "paper" ? (st === "fail" ? "fail" : st === "done" ? "done" : st === "send" ? "send" : "wait") : ({ ok: "done", fail: "fail", send: "send" }[x.p.up] || "wait");
  function paintSendRows() {
    const ul = $(".sendlist"); if (!ul || S.mode === "paper") return;
    const L = guideList();
    ul.innerHTML = L.map((x) => rowHtml6(x, sendSt)).join("");
    const n = L.filter((x) => x.p.up === "ok").length, f = L.filter((x) => x.p.up === "fail").length;
    const c = $("#cnt"); if (c && sendSt !== "send" && sendSt !== "done") c.textContent = n + "/" + L.length;
    const bi = $("#barIn"); if (bi && sendSt !== "send" && sendSt !== "done") bi.style.width = n / L.length * 100 + "%";
    const bad = $("#sendBad"); if (bad) bad.hidden = !f;
  }
  const rowHtml6 = (x, st) => `<li class="is-${rowCls(x, st)}"><div class="ph"><img src="${x.p.url}" alt=""></div><div class="nm">${esc(x.nm)}${x.sub ? "<small>" + esc(x.sub) + "</small>" : ""}</div><div class="stt">${rowStt(x, st)}</div></li>`;
  function paintSend(L, st, prog) {
    sendSt = st;
    const paper = S.mode === "paper", ok = L.filter((x) => !paper && x.p.up === "ok").length, fail = L.filter((x) => !paper && x.p.up === "fail").length;
    const cnt = st === "done" ? L.length + "/" + L.length : st === "send" && paper ? Math.round(prog * 100) + "%" : paper ? "0/" + L.length : ok + "/" + L.length;
    const w = st === "done" ? 100 : paper ? (st === "send" ? prog * 100 : 0) : ok / L.length * 100;
    VIEW.innerHTML = `<h1>${st === "done" ? "送れました" : "写真を送る"}</h1>
      ${st === "done" ? "" : `<p class="lead">${L.length} 枚を事務所に送ります。${st === "send" ? "送っています。この画面を開いたままにしてください。" : "電波が弱いと失敗することがあります。"}</p>`}
      ${st === "done" ? `<div class="card donebox"><h2>送信が終わりました <span class="chip wait">確認待ち</span></h2>
        <p style="margin-top:8px">事務所の有資格者の確認待ちになりました。</p>${S.sent && S.sent.sign_id ? `<p class="hint">看板 ${esc(S.sent.sign_id)} を下書きとして台帳に登録しました。事務所の PC の確認待ちに載ります。</p>` : '<p class="hint">事務所の PC の確認待ちに載ります。</p>'}</div>` : ""}
      <div class="sendsum" style="margin-top:16px"><b id="cnt">${cnt}</b>
        <div class="bar${st === "done" ? " ok" : ""}" role="progressbar" aria-valuemin="0" aria-valuemax="100" aria-valuenow="${Math.round(w)}"><i id="barIn" style="width:${w}%"></i></div></div>
      <div id="sendBad" ${st === "fail" || fail ? "" : "hidden"}>${st === "fail" || fail ? `<div class="callout bad" style="margin-top:16px">${paper || !fail ? `送れませんでした(${esc(S.sendErr)})。電波のよい所で「再送」を押してください。写真はこの画面に残っています。` : `${fail} 枚が送れませんでした。電波のよい所で「再送」を押してください。残りは届いています。`}</div>` : ""}</div>
      <ul class="sendlist">${L.map((x) => rowHtml6(x, st)).join("")}</ul>`;
    if (st === "done") {
      setFoot({ label: "次の看板へ", ok: true, go: nextSign, ghost: { label: "終わる", go: () => { location.href = "/v3/"; } } });
      window.scrollTo(0, 0);
    } else if (st === "send") setFoot({ label: "送信中…", ok: false, go: null });
    else setFoot({ label: st === "fail" ? "再送する" : "送信する", ok: true, go: () => send(L) });
  }
  VIEW.addEventListener("click", (e) => { const b = e.target.closest("[data-re]"); if (b) { retryUp(REG.get(+b.dataset.re)); } });

  // 紙の点検表: 束で 1 回
  function uploadBundle(files, onProg) {
    return new Promise((ok, ng) => {
      const x = new XMLHttpRequest();
      x.open("POST", "/api/intake/upload"); x.setRequestHeader("Content-Type", "application/json"); x.timeout = 300000;
      x.upload.onprogress = (e) => { if (e.lengthComputable) onProg(e.loaded / e.total); };
      x.onload = () => { let j = {}; try { j = JSON.parse(x.responseText); } catch (e) { /* 本文なし */ } x.status < 300 ? ok(j) : ng(new Error(j.error || ("エラー " + x.status))); };
      x.onerror = () => ng(new Error("通信できませんでした")); x.ontimeout = () => ng(new Error("時間切れ"));
      x.send(JSON.stringify({ files }));
    });
  }
  const num = (v) => { const n = parseFloat(String(v == null ? "" : v).replace(/[^\d.]/g, "")); return isNaN(n) ? null : n; };
  function payload() {
    const M = S.meas, w = num(M.w), h = num(M.h), items = [];
    PS.forEach((q, i) => {
      if (!q.photos.length) return;
      q.rows.forEach((r) => {
        const res = r.pick || r.ai || UNDECIDED;
        items.push({ key: r.key, result: res, reason: r.evidence || "", photo_no: r.photo || undefined, by: r.pick && r.pick !== r.ai ? "human" : "ai" });
      });
    });
    const hand = S.touched.w || S.touched.h || S.hand || !S.dimAI;
    return { submission_id: S.sid, kind: S.kind, name: M.name.trim() || undefined, address: S.address.trim(), municipality: S.muni || undefined,
      lat: S.lat == null ? undefined : S.lat, lng: S.lng == null ? undefined : S.lng,
      photos: guideList().map((x) => ({ slot: x.p.slot, n: x.p.n })),
      size: w && h ? { w, h, depth: num(M.t) || undefined, gl_m: num(M.g) == null ? undefined : num(M.g), by: hand ? "human" : "ai" } : undefined,
      items, notes: M.note.trim() || undefined };
  }
  async function send(L) {
    S.sending = true; S.sendErr = "";
    paintSend(L, "send", 0);
    try {
      if (S.mode === "paper") {
        S.sent = await uploadBundle(L.map((x) => ({ name: x.file, data: x.p.b64 })), (p) => {
          sendProg = p; if (cur !== "s6") return;
          const c = $("#cnt"), b = $("#barIn"); if (c) c.textContent = Math.round(p * 100) + "%"; if (b) b.style.width = p * 100 + "%";
        });
      } else {
        L.forEach((x) => { if (x.p.up !== "ok" && x.p.up !== "send" && x.p.up !== "wait") retryUp(x.p); });
        while (L.some((x) => x.p.up === "wait" || x.p.up === "send")) await wait(300);          // 裏の送信が終わるのを待つ
        if (L.some((x) => x.p.up === "fail")) throw new Error("写真が届いていません");
        while (PS.some((q) => q.status === "loading")) await wait(500);                      // 部位の下書きが終わるのを待つ
        let body = payload();
        for (let k = 0; ; k++) {
          try { S.sent = await api("/api/field/submit", body); break; }
          catch (e) {
            const miss = e.missing;
            if (!miss || k > 0) throw e;
            L.forEach((x) => { if (miss.some((m) => m.slot === x.p.slot && m.n === x.p.n)) retryUp(x.p); });     // 届いていない写真だけ送り直す
            while (L.some((x) => x.p.up === "wait" || x.p.up === "send")) await wait(300);
            if (L.some((x) => x.p.up === "fail")) throw new Error("写真が届いていません");
          }
        }
      }
    } catch (e) { S.sendErr = e.message; }
    S.sending = false;
    if (cur === "s6") { backVis(!!S.sent); paintSend(L, S.sent ? "done" : "fail", 0); }
  }
  function nextSign() {
    sign++; S = fresh(); hist = []; PARTS = null; PS = []; drag = null; REG.clear();
    go("s1"); hist = []; backVis(true);
  }

  s0();
})();
