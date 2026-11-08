// UI v3 の共通の枠: 上の枠 / 段の進み(N1〜N7)/ 下の「次へ」。
// 各画面は <body data-page="n3"> と、本文を <main class="page"> に書く。
(function () {
  const STEPS = ["物件", "入口", "取り込み", "不明点", "看板ごとの確認", "署名", "報告書"];
  const page = document.body.dataset.page;
  const stepNo = /^n(\d)$/.test(page) ? +page.slice(1) : 0;
  const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };

  const top = h(`<header class="topbar">
    <a class="brand" href="h.html"><i></i>SignGrid</a>
    <nav class="topnav">
      <a href="h.html"${page === "h" || stepNo ? ' aria-current="page"' : ""}>ホーム</a>
      <a href="l.html"${page === "l" ? ' aria-current="page"' : ""}>台帳</a>
      <a href="k.html"${page === "k" ? ' aria-current="page"' : ""}>判定の規則</a>
    </nav>
    <div class="company"><b></b>${SG.TODAY}</div>
  </header>`);
  document.body.prepend(top);
  // 会社名は /api/meta から(見本データでは既定の社名)。返らなければ空のまま
  fetch("/api/meta").then((r) => r.json()).then((m) => { const b = top.querySelector(".company b"); const nm = m.company || m.company_name; if (b && nm) b.textContent = nm; }).catch(() => {});

  if (stepNo) {
    const st = h(`<div class="stepper"><div class="stepper-in">
      <div class="now">新規点検 <b>${stepNo}/7</b> ${STEPS[stepNo - 1]}</div>
      <ol>${STEPS.map((s, i) => `<li class="${i + 1 < stepNo ? "done" : i + 1 === stepNo ? "cur" : ""}">${i + 1} ${s}</li>`).join("")}</ol>
    </div></div>`);
    top.after(st);
    // 下の「戻る」「次へ」。画面側で <body data-next-label="…" data-no-foot> で変えられる
    if (!("noFoot" in document.body.dataset)) {
      const prev = stepNo === 1 ? "h.html" : `n${stepNo - 1}.html`;
      const next = stepNo === 7 ? "h.html" : `n${stepNo + 1}.html`;
      const label = document.body.dataset.nextLabel || (stepNo === 7 ? "ホームへ戻る" : "次へ");
      document.body.append(h(`<div class="wizard-foot"><div class="in">
        <a class="btn" href="${prev}">戻る</a><a class="btn primary lg" id="nextBtn" href="${next}">${label}</a>
      </div></div>`));
    }
  }
})();
