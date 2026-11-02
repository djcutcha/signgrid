// 看板カルテの画面。フレームワークなし。KK に共通の部品を置き、画面ごとに ledger.js / karte.js / rules.js が views に登録する。
"use strict";
const KK = {
  meta: null,
  views: {},
  RANK: { 1: "良好", 2: "経過観察", 3: "要改善", 4: "即時対応" },
  RANK_CLASS: { 1: "good", 2: "watch", 3: "fix", 4: "urgent" },
  RESULT_CLASS: { "良好": "good", "経過観察": "watch", "要改善": "fix", "対象外": "na" },
  STATUS: { new: "未判定", needs_photos: "写真なし", draft: "下書き", confirmed: "確定" },
};

// DOM を組み立てる。文字列は textContent で入れる(台帳の文字を HTML として解釈しない)
KK.el = function (tag, props, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(props || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "text") e.textContent = v;
    else if (k.startsWith("on")) e.addEventListener(k.slice(2), v);
    else if (k === "dataset") Object.assign(e.dataset, v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const c of kids.flat()) {
    if (c === null || c === undefined || c === false) continue;
    e.append(c.nodeType ? c : document.createTextNode(String(c)));
  }
  return e;
};

KK.api = async function (path, body, method = "POST") {
  const opt = body === undefined ? {} : { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
  const r = await fetch(path, opt);
  if (!r.ok) {
    let msg = r.statusText;
    try { msg = (await r.json()).error || msg; } catch (_) { /* 本文が JSON でない */ }
    const err = new Error(msg);
    err.status = r.status;
    throw err;
  }
  return r.json();
};

// ランクの札。下書きは薄く(枠だけ)、確定は濃く(塗りつぶし)
KK.rankChip = function (rank, status) {
  if (!rank) return KK.el("span", { class: "muted", text: "—" });
  const cls = KK.RANK_CLASS[rank];
  const confirmed = status === "confirmed";
  return KK.el("span", { class: `chip rank ${cls} ${confirmed ? "solid" : "soft"}`,
    title: confirmed ? "有資格者が確定" : "AI の下書き(有資格者が確定するまで仮)", text: KK.RANK[rank] });
};

KK.resultChip = function (value) {
  if (!value) return KK.el("span", { class: "chip q", text: "判断できず" });
  return KK.el("span", { class: `chip ${KK.RESULT_CLASS[value]}`, text: value });
};

// 許可の 要 / 不要 / 要実測 の札。値は真偽(台帳)か文字列(法規役)。無ければ出さない
KK.permitChip = function (v) {
  const label = v === true ? "要" : v === false ? "不要" : v;
  if (!label) return null;
  const cls = label === "要" ? "req" : label === "不要" ? "none" : "meas";
  return KK.el("span", { class: `chip permit ${cls}`, title: "許可(屋外広告物条例)", text: `許可 ${label}` });
};

// 法規役の根拠条文の一覧(条例名・条番号・引用・検算の札)
KK.citations = function (cites) {
  if (!cites || !cites.length) return KK.el("p", { class: "sub", text: "根拠条文はありません(条例の写しが無い、または寸法が足りません)。" });
  return KK.el("ol", { class: "cites" }, cites.map((c) => KK.el("li", {},
    KK.el("div", { class: "cite-top" }, KK.el("b", { text: `${c.source || "—"} ${c.article || ""}`.trim() }),
      c.quote_verified ? KK.el("span", { class: "chip verified", title: "原文と照合して引用が一致", text: "✓検算済み" })
        : KK.el("span", { class: "chip unverified", title: "原文との照合がまだ", text: "引用未検算" })),
    KK.el("div", { class: "quote", text: c.quote || "" }))));
};

// 設置場所の区分(用途地域)の1行。place は API の {status, zone}。引けないときは区分なしで判定したと書く
KK.placeText = function (place) {
  if (!place || place.status === "none") return null;
  if (place.zone) return `${place.zone}(${place.status === "sample" ? "見本の固定値" : "国土交通省 GIS、暫定"})`;
  return "引けず(区分なしで判定)";
};

KK.triText = function (v, yes, no) { return v === null || v === undefined ? "—" : v ? yes : no; };

KK.fmtTime = function (iso) { return (iso || "").replace("T", " ").slice(0, 16); };

KK.error = function (e) { return KK.el("p", { class: "notice err", role: "alert", text: e.message || String(e) }); };

KK.setView = function (...nodes) {
  const v = document.getElementById("view");
  v.replaceChildren(...nodes.filter(Boolean));
};

// 写真の拡大(タップ)。photos = [{n, url}]
KK.lightbox = (function () {
  let list = [], i = 0;
  const dlg = () => document.getElementById("lightbox");
  function show() {
    const p = list[i];
    document.getElementById("lb-title").textContent = `写真 ${p.n} / ${list.length}`;
    document.getElementById("lb-img").replaceChildren(p.url
      ? KK.el("img", { src: p.url, alt: `写真 ${p.n}` })
      : KK.el("div", { class: "ph", text: `写真 ${p.n}(画像なし)` }));
  }
  function step(d) { i = (i + d + list.length) % list.length; show(); }
  return {
    open(photos, n) {
      list = photos; i = Math.max(0, photos.findIndex((p) => p.n === n));
      if (!list.length) return;
      show();
      if (!dlg().open) dlg().showModal();
    },
    init() {
      document.getElementById("lb-close").onclick = () => dlg().close();
      document.getElementById("lb-prev").onclick = () => step(-1);
      document.getElementById("lb-next").onclick = () => step(1);
      dlg().addEventListener("click", (e) => { if (e.target === dlg()) dlg().close(); });
      dlg().addEventListener("keydown", (e) => {
        if (e.key === "ArrowLeft") step(-1);
        if (e.key === "ArrowRight") step(1);
      });
    },
  };
})();

// ハッシュで画面を切り替える: #/intake、#/ (台帳、?site=)、#/sign/<id>、#/gov、#/owner、#/rules
// seg[0] → [ビュー名, ナビの data-nav]
KK.ROUTES = {
  intake: ["intake", "intake"], sign: ["karte", "ledger"], gov: ["gov", "gov"], owner: ["owner", "owner"], rules: ["rules", "rules"],
};
KK.route = async function () {
  const [path, query] = location.hash.slice(1).split("?");
  const params = new URLSearchParams(query || "");
  const seg = (path || "/").split("/").filter(Boolean).map(decodeURIComponent);
  const [name, nav] = KK.ROUTES[seg[0]] || ["ledger", "ledger"];
  document.querySelectorAll("[data-nav]").forEach((a) => {
    if (a.dataset.nav === nav) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  });
  try {
    await KK.views[name](seg[1], params);
  } catch (e) {
    KK.setView(KK.error(e));
  }
};

KK.start = async function () {
  KK.lightbox.init();
  try {
    KK.meta = await KK.api("/api/meta");
    document.getElementById("sample-badge").hidden = !KK.meta.sample;
  } catch (e) {
    KK.setView(KK.error(e));
    return;
  }
  window.addEventListener("hashchange", KK.route);
  KK.route();
};

// 点検まわりの4つの義務(管理 / 点検 / 有資格者 / 行政への報告)。法規役の duties({title, value, text})を順に。
// 4つは別の義務で、条例ごとに掛かり方が違う。text は法規役が条文つきで作った1行(盛らない)
KK.DUTY_ORDER = ["maintenance", "inspection", "qualified", "report"];
KK.dutyEntries = function (duties) {
  return duties ? KK.DUTY_ORDER.filter((k) => duties[k]).map((k) => duties[k]) : [];
};
