// N3 取り込み: 落とす → POST /api/intake/upload → GET /api/intake/<束>/progress を 2 秒おき(仕分け → 転記 → 下書き)。
// 反証役(progress.stages の verify)は画面に置かない。?bundle=ID で開くと、その束の進み具合から再開する。
(function () {
  const $ = (id) => document.getElementById(id);
  const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
  const getJSON = async (u) => { const r = await fetch(u); if (!r.ok) { const e = new Error(await errText(r)); e.status = r.status; throw e; } return r.json(); };
  async function errText(r) { try { const j = await r.json(); return j.error || j.message || ("通信エラー " + r.status); } catch (e) { return "通信エラー " + r.status; } }

  const co = document.querySelector(".company");
  if (co) co.textContent = SG.TODAY;
  getJSON("/api/meta").then((m) => { const n = m.company || m.company_name; if (n && co) co.innerHTML = "<b>" + esc(n) + "</b>" + esc(SG.TODAY); }).catch(() => {});

  const VIEWS = ["before", "sending", "running", "done", "error"];
  const view = (v) => VIEWS.forEach((x) => { $("v-" + x).hidden = x !== v; });
  const STAGES = [["sort", "仕分け", "写真を看板ごと・部位ごとに"], ["transcribe", "転記", "紙の文字を項目に。紙の判定欄・仕様欄・手描きのスケッチ(寸法)を読む"], ["draft", "下書き", "判定役"]];
  const MAX_FILES = 100;
  let timer = null, lastFiles = null, bundleId = null;

  // ---- 落とす・選ぶ ----
  const drop = $("drop");
  ["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", async (e) => {
    const items = [...(e.dataTransfer.items || [])];
    const entries = items.map((i) => (i.webkitGetAsEntry ? i.webkitGetAsEntry() : null));   // await の前に同期で取る
    let files;
    if (entries.some(Boolean)) files = (await Promise.all(entries.filter(Boolean).map(walk))).flat();
    else files = [...e.dataTransfer.files];
    start(files);
  });
  $("pick").addEventListener("change", (e) => { start([...e.target.files]); e.target.value = ""; });
  $("pickDir").addEventListener("change", (e) => { start([...e.target.files]); e.target.value = ""; });
  function walk(entry) {
    if (entry.isFile) return new Promise((ok) => entry.file((f) => ok([f]), () => ok([])));
    const rd = entry.createReader(), all = [];
    return new Promise((ok) => {
      const next = () => rd.readEntries(async (es) => {
        if (!es.length) return ok((await Promise.all(all.map(walk))).flat());
        all.push(...es); next();
      }, () => ok([]));
      next();
    });
  }

  const kindOf = (f) => {
    const n = f.name.toLowerCase();
    if (f.type === "application/pdf" || n.endsWith(".pdf")) return "pdf";
    if (/\.(jpe?g|png|webp)$/.test(n) || /^image\/(jpeg|png|webp)$/.test(f.type)) return "img";
    if (/\.(heic|heif)$/.test(n) || /heic|heif/.test(f.type)) return "heic";
    return "other";   // .DS_Store など。黙って外す
  };
  function pickError(msg) { const b = $("pickErr"); b.textContent = msg; b.hidden = !msg; }

  function start(files) {
    pickError("");
    const pdfs = files.filter((f) => kindOf(f) === "pdf"), imgs = files.filter((f) => kindOf(f) === "img"), heic = files.filter((f) => kindOf(f) === "heic");
    if (pdfs.length > 1) return pickError("PDF は 1 本だけ落とせます(" + pdfs.length + " 本ありました)。1 本ずつ取り込んでください。");
    if (pdfs.length && imgs.length) return pickError("PDF と写真は一緒に落とせません。どちらか一方にしてください。");
    if (imgs.length > MAX_FILES) return pickError("写真は " + MAX_FILES + " 枚までです(" + imgs.length + " 枚ありました)。分けて取り込んでください。");
    if (!pdfs.length && !imgs.length) return pickError(heic.length ? "HEIC の写真は読めません。JPEG に書き出してから落としてください(" + heic.length + " 枚)。" : "PDF か写真(JPEG / PNG / WEBP)が見つかりませんでした。");
    const use = pdfs.length ? pdfs : imgs;
    if (heic.length) pickError("HEIC の写真 " + heic.length + " 枚は読めないので外しました。JPEG に書き出せば取り込めます。");
    send(use);
  }

  async function send(files) {
    lastFiles = files;
    view("sending");
    $("sendMsg").textContent = (files[0].name.toLowerCase().endsWith(".pdf") ? "点検表の PDF を" : "写真 " + files.length + " 枚を") + "送っています。";
    try {
      const body = { files: await Promise.all(files.map(async (f) => ({ name: f.name, data: await b64(f) }))) };
      const r = await fetch("/api/intake/upload", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (!r.ok) throw new Error(await errText(r));
      const j = await r.json();
      bundleId = j.bundle_id;
      history.replaceState(null, "", "?bundle=" + bundleId);
      watch();
    } catch (e) { fail(e.message || String(e)); }
  }
  const b64 = (f) => new Promise((ok, ng) => { const r = new FileReader(); r.onload = () => ok(String(r.result).split(",", 2)[1] || ""); r.onerror = () => ng(new Error(f.name + " を読めませんでした")); r.readAsDataURL(f); });

  // ---- 進み具合 ----
  $("stages").innerHTML = STAGES.map((s, i) => `<div class="stage idle" id="st${i}"><div class="nm">${i + 1} ${s[1]}<small>${s[2]}</small></div><div class="ct num" id="ct${i}">—</div><div class="bar"><i id="br${i}" style="width:0"></i></div></div>`).join("");
  function watch() { view("running"); clearInterval(timer); tick(); timer = setInterval(tick, 2000); }
  async function tick() {
    try {
      const p = await getJSON("/api/intake/" + bundleId + "/progress");
      paint(p);
      if (p.status === "error") { clearInterval(timer); fail(p.error || "途中で止まりました。"); }
      else if (p.status === "done") { clearInterval(timer); finish(p); }
    } catch (e) {
      if (e.status === 404 || /\b404\b/.test(e.message || "")) { clearInterval(timer); fail("その取り込みが見つかりません。取り込み前に戻ってやり直してください。"); return; }
      /* 一時的な通信失敗は次の 2 秒で取り直す */
    }
  }
  function paint(p) {
    STAGES.forEach((s, i) => {
      const st = p.stages.find((x) => x.key === s[0]) || {}, has = st.total != null;
      $("ct" + i).textContent = has ? st.done + "/" + st.total : "—";
      $("br" + i).style.width = has && st.total ? Math.round(st.done / st.total * 100) + "%" : "0";
      $("st" + i).classList.toggle("idle", !has || st.done === 0);
    });
    const log = $("log");
    log.innerHTML = (p.log || []).slice(-6).map((l) => `<div><span class="who">${who(l)}</span>${esc(l)}</div>`).join("");
  }
  const who = (l) => /下書き|ランク|判定/.test(l) ? "下書き" : /転記|読んだ/.test(l) ? "転記" : "仕分け";

  async function finish(p) {
    paint(p);
    const draft = (p.stages.find((x) => x.key === "draft") || {}).done || 0;
    let unk = null;
    if (p.site_id) {   // 不明点= この現場の看板の「判断できず」の合計(束だけで絞る API が無い)
      try { unk = (await getJSON("/api/signs?site=" + encodeURIComponent(p.site_id))).reduce((a, s) => a + (s.undecided || 0), 0); } catch (e) { /* 数字なしで出す */ }
    }
    $("sumline").textContent = "看板 " + p.sign_count + " 基、下書き " + draft + (unk == null ? "" : "、不明点 " + unk);
    $("sumUnk").hidden = unk == null; if (unk != null) $("sumUnk").textContent = "判断できず " + unk;
    $("toN5").href = "n4.html" + (p.site_id ? "?site=" + encodeURIComponent(p.site_id) : "");
    view("done");
  }
  function fail(msg) { clearInterval(timer); $("errText").textContent = msg; view("error"); }

  // やり直し: 送った直後なら同じ写真を送り直す。束が残っている(仕分けまで済んで下書きで止まった等)なら、送り直さず下書きだけやり直す
  async function redraft() {
    view("running"); $("errText").textContent = "";
    try {
      const r = await fetch("/api/bundles/" + encodeURIComponent(bundleId) + "/draft", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
      if (!r.ok) { fail(await errText(r)); return; }
    } catch (e) { fail("通信エラー " + e.message); return; }
    watch();
  }
  $("retry").onclick = () => (lastFiles ? send(lastFiles) : (bundleId ? redraft() : view("before")));
  $("back").onclick = () => { history.replaceState(null, "", location.pathname); bundleId = null; view("before"); };
  $("again").onclick = $("back").onclick;

  const q = new URLSearchParams(location.search).get("bundle");
  if (/^\d+$/.test(q || "")) { bundleId = +q; watch(); } else view("before");
})();
