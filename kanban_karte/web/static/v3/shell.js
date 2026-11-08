// 見本 v3 の共通の枠: 見本用の帯 / 上の枠 / 段の進み(N1〜N7)/ 下の「次へ」。
// 各画面は <body data-page="n3"> と、本文を <main class="page"> に書く。API なし・保存なし。
(function () {
  const PAGES = [
    ["h", "H ホーム"], ["n1", "N1 物件"], ["n2", "N2 入口"], ["n3", "N3 取り込み"], ["n4", "N4 不明点"],
    ["n5", "N5 看板ごとの確認"], ["n6", "N6 署名"], ["n7", "N7 報告書"], ["l", "L 台帳"], ["k", "K 学んだこと"],
    ["field/f0", "F 現場(スマホ)"],
  ];
  const STEPS = ["物件", "入口", "取り込み", "不明点", "看板ごとの確認", "署名", "報告書"];
  const page = document.body.dataset.page;
  const stepNo = /^n(\d)$/.test(page) ? +page.slice(1) : 0;
  const h = (html) => { const t = document.createElement("template"); t.innerHTML = html.trim(); return t.content.firstElementChild; };

  const band = h(`<nav class="mockband" aria-label="見本の画面一覧"><b>見本 v3(静的・保存なし)</b>${PAGES.map(([id, nm]) =>
    `<a href="${id}.html"${id === page ? ' aria-current="page"' : ""}>${nm}</a>`).join("")}</nav>`);
  const top = h(`<header class="topbar">
    <a class="brand" href="h.html"><i></i>SignGrid</a>
    <nav class="topnav">
      <a href="h.html"${page === "h" || stepNo ? ' aria-current="page"' : ""}>今日やること</a>
      <a href="/index.html#/">台帳</a>
      <a href="k.html"${page === "k" ? ' aria-current="page"' : ""}>学んだこと</a>
    </nav>
    <div class="company"><b>${SG.COMPANY}</b>${SG.TODAY}</div>
  </header>`);
  document.body.prepend(band, top);

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
