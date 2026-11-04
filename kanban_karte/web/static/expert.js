/* 有資格者の言葉づけ: 写真を見て、危なさの直感・根拠・気になる点を 1 件ずつ即時保存する */
(function () {
  "use strict";
  var $ = function (id) { return document.getElementById(id); };
  var S = { queue: [], idx: 0, item: null, meta: null, rank: null, scoreTouched: false, busy: false, photos: 0 };
  var MID = { 1: 12, 2: 37, 3: 62, 4: 87 };       // 段階を押したとき、スライダーが未入力ならこの値に寄せる(触っていれば動かさない)
  var LABEL = ["良好", "経過観察", "要改善", "即時対応"];

  function jget(url) { return fetch(url).then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; }); }); }
  function jpost(url, body) {
    return fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.error || r.status); return j; }); });
  }
  function msg(t, bad) { var m = $("ex-msg"); m.textContent = t || ""; m.style.color = bad ? "var(--fix)" : ""; }
  function imgUrl(id, n) { return "/api/expert/img?id=" + encodeURIComponent(id) + "&n=" + n; }

  function buildStatic() {
    var r = $("ex-ranks");
    LABEL.forEach(function (name, i) {
      var b = document.createElement("button");
      b.type = "button"; b.className = "ex-rank"; b.dataset.r = i + 1;
      b.setAttribute("role", "radio"); b.setAttribute("aria-checked", "false");
      b.innerHTML = name + "<small>" + (i + 1) + "</small>";
      b.addEventListener("click", function () { setRank(S.rank === i + 1 ? null : i + 1); });
      r.appendChild(b);
    });
    fill("ex-signs", S.meta.signs, "sign", "checkbox");
    fill("ex-kinds", S.meta.kinds, "kind", "radio");
  }
  function fill(id, list, name, type) {
    var box = $(id);
    list.forEach(function (t) {
      var l = document.createElement("label"), i = document.createElement("input");
      i.type = type; i.name = name; i.value = t;
      l.appendChild(i); l.appendChild(document.createTextNode(t)); box.appendChild(l);
    });
  }

  function setRank(r, keepScore) {
    S.rank = r;
    Array.prototype.forEach.call(document.querySelectorAll(".ex-rank"), function (b) { b.setAttribute("aria-checked", String(+b.dataset.r === r)); });
    if (r && !S.scoreTouched && !keepScore) { $("ex-score").value = MID[r]; showScore(false); }
  }
  function showScore(touched) {
    S.scoreTouched = touched;
    $("ex-score-out").textContent = touched ? $("ex-score").value : "未入力";
    document.querySelector(".ex-slider").classList.toggle("untouched", !touched);
  }

  function collect(skipped) {
    var signs = Array.prototype.map.call(document.querySelectorAll("#ex-signs input:checked"), function (i) { return i.value; });
    var k = document.querySelector("#ex-kinds input:checked");
    return {
      id: S.item.id, skipped: !!skipped, rank: S.rank, score: S.scoreTouched ? +$("ex-score").value : null,
      reasons: $("ex-reasons").value, unknowns: $("ex-unknowns").value, signs: signs, kind: k ? k.value : ""
    };
  }
  function hasContent(n) { return n.rank || n.score !== null || n.reasons.trim() || n.unknowns.trim() || n.signs.length; }

  function progress() {
    var done = 0, skip = 0;
    S.queue.forEach(function (q) { if (q[1] === 1) done++; else if (q[1] === 2) skip++; });
    var total = S.queue.length;
    $("ex-progress").textContent = (S.idx + 1) + " / " + total + "(済 " + done + "・飛ばし " + skip + ")";
    $("ex-pos").textContent = "";
    $("ex-bar-fill").style.width = (total ? (done + skip) / total * 100 : 0) + "%";
  }

  function render(item) {
    S.item = item; S.photos = item.n_photos;
    var c = S.meta.counts[item.source] || {};
    $("ex-src").textContent = item.source_label;
    var box = $("ex-photos");
    box.innerHTML = ""; box.className = "ex-photos" + (item.n_photos === 1 ? " single" : "");
    for (var i = 0; i < item.n_photos; i++) {
      (function (n) {
        var b = document.createElement("button"), im = document.createElement("img");
        b.type = "button"; b.setAttribute("aria-label", "写真 " + (n + 1) + " を拡大");
        im.src = imgUrl(item.id, n); im.alt = "写真 " + (n + 1) + " / " + item.n_photos; im.loading = n < 6 ? "eager" : "lazy";
        b.appendChild(im); b.addEventListener("click", function () { openLb(n); }); box.appendChild(b);
      })(i);
    }
    if (!item.n_photos) box.innerHTML = '<p class="muted">写真を読み込めませんでした(「分からない・飛ばす」で進めます)</p>';
    var s = item.saved;
    $("ex-reasons").value = s ? s.reasons : ""; $("ex-unknowns").value = s ? s.unknowns : "";
    Array.prototype.forEach.call(document.querySelectorAll("#ex-signs input"), function (i) { i.checked = !!s && s.signs.indexOf(i.value) >= 0; });
    var kind = s && s.kind ? s.kind : item.kind_default;
    Array.prototype.forEach.call(document.querySelectorAll("#ex-kinds input"), function (i) { i.checked = i.value === kind; });
    $("ex-score").value = s && s.score !== null ? s.score : 50;
    setRank(s && s.rank ? s.rank : null, true);
    showScore(!!s && s.score !== null);
    msg(s ? (s.skipped ? "飛ばした件です(" + s.updated_at + ")" : "保存済み(" + s.updated_at + ")。直して保存すると上書きします") : "");
    progress();
    window.scrollTo(0, 0);
    prefetch(S.idx + 1);
  }
  function prefetch(i) {
    var q = S.queue[i]; if (!q) return;
    jget("/api/expert/item?id=" + encodeURIComponent(q[0])).then(function (it) {
      for (var n = 0; n < Math.min(it.n_photos, 3); n++) new Image().src = imgUrl(it.id, n);
    }).catch(function () {});
  }

  function go(i) {
    if (i < 0 || i >= S.queue.length) { msg(i < 0 ? "最初の件です" : "最後の件です。全件を通しました"); return Promise.resolve(); }
    S.idx = i;
    return jget("/api/expert/item?id=" + encodeURIComponent(S.queue[i][0])).then(render).catch(function (e) { msg("読み込めません: " + e.message, true); });
  }

  function commit(skipped, step) {
    if (S.busy || !S.item) return;
    var n = collect(skipped);
    var next = S.idx + step;
    if (!skipped && !hasContent(n) && !S.item.saved) { msg("未入力のまま進みました(保存していません)"); return go(next); }
    S.busy = true;
    return jpost("/api/expert/save", n).then(function (r) {
      S.queue[S.idx][1] = r.skipped ? 2 : 1;
      S.busy = false;
      return go(next);
    }).catch(function (e) { S.busy = false; msg("保存できません: " + e.message, true); });
  }

  // 写真の拡大
  var lbN = 0;
  function openLb(n) { lbN = n; showLb(); var d = $("ex-lb"); if (!d.open) d.showModal(); }
  function showLb() {
    $("ex-lb-img").innerHTML = ""; var im = document.createElement("img");
    im.src = imgUrl(S.item.id, lbN); im.alt = "写真 " + (lbN + 1); $("ex-lb-img").appendChild(im);
    $("ex-lb-title").textContent = "写真 " + (lbN + 1) + " / " + S.photos;
  }
  function lbMove(d) { if (!S.photos) return; lbN = (lbN + d + S.photos) % S.photos; showLb(); }

  function typing(t) { return t && (t.tagName === "TEXTAREA" || (t.tagName === "INPUT" && t.type === "text")); }
  function onKey(e) {
    if ($("ex-lb").open) {
      if (e.key === "ArrowLeft") { lbMove(-1); e.preventDefault(); } else if (e.key === "ArrowRight") { lbMove(1); e.preventDefault(); }
      return;
    }
    if (e.isComposing) return;                       // 日本語入力の変換確定の Enter は無視
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); return commit(false, 1); }
    if (typing(e.target) || e.altKey || e.ctrlKey || e.metaKey) return;
    var t = e.target;
    if (e.key >= "1" && e.key <= "4") { setRank(+e.key); e.preventDefault(); }
    else if (e.key === "Enter" && t.tagName !== "BUTTON" && !(t.tagName === "INPUT" && t.type === "checkbox")) { e.preventDefault(); commit(false, 1); }
    else if (e.key === "ArrowLeft" && t.tagName !== "INPUT") { e.preventDefault(); commit(false, -1); }
    else if (e.key === "ArrowRight" && t.tagName !== "INPUT") { e.preventDefault(); commit(false, 1); }
    else if (e.key === "s" || e.key === "S") { e.preventDefault(); commit(true, 1); }
  }

  function start() {
    jget("/api/expert/queue").then(function (m) {
      S.meta = m; S.queue = m.queue;
      if (m.warnings && m.warnings.length) { var w = $("ex-warn"); w.hidden = false; w.textContent = m.warnings.join(" / "); }
      buildStatic();
      var first = 0;
      for (var i = 0; i < S.queue.length; i++) { if (S.queue[i][1] === 0) { first = i; break; } }
      if (!S.queue.length) { $("ex-progress").textContent = "写真がありません"; return; }
      go(first);
    }).catch(function (e) { $("ex-progress").textContent = "読み込めません: " + e.message; });
    $("ex-form").addEventListener("submit", function (e) { e.preventDefault(); commit(false, 1); });
    $("ex-prev").addEventListener("click", function () { commit(false, -1); });
    $("ex-skip").addEventListener("click", function () { commit(true, 1); });
    $("ex-score").addEventListener("input", function () { showScore(true); });
    $("ex-score-clear").addEventListener("click", function () { showScore(false); });
    $("ex-lb-close").addEventListener("click", function () { $("ex-lb").close(); });
    $("ex-lb-prev").addEventListener("click", function () { lbMove(-1); });
    $("ex-lb-next").addEventListener("click", function () { lbMove(1); });
    $("ex-lb").addEventListener("click", function (e) { if (e.target === $("ex-lb")) $("ex-lb").close(); });
    document.addEventListener("keydown", onKey);
  }
  start();
})();
