// 行政画面: 職員が撮った写真の一括登録。看板ごとに束ね、許可台帳と突き合わせた結果を 赤/橙/緑/灰 で並べる。地図は gov.map の HTML を埋める。
"use strict";
(function () {
  const { el } = KK;
  const COLOR_CLASS = { red: "c-red", orange: "c-orange", green: "c-green", gray: "c-gray" };

  function colorChip(color, name) {
    return el("span", { class: `chip gov ${COLOR_CLASS[color]}`, text: name });
  }

  function countChips(data, by) {
    return el("div", { class: "gov-chips", role: "group", "aria-label": "色別の件数" },
      Object.keys(data.colors).map((c) => el("div", { class: `gov-count ${COLOR_CLASS[c]}`, title: data.colors[c].meaning },
        el("b", { text: String(by[c] || 0) }), el("span", { text: data.colors[c].name }),
        el("small", { text: data.colors[c].meaning }))));
  }

  function expiry(c) {
    if (!c.permit_expires) return el("span", { class: "muted", text: "—" });
    return el("span", {}, c.permit_expires, el("div", { class: "sub", text: c.days_left < 0 ? `${-c.days_left} 日超過` : `残り ${c.days_left} 日` }));
  }

  function candidatesTable(cands, names) {
    const th = (t) => el("th", {}, t);
    const cell = (k, cls, ...kids) => el("td", { class: cls, dataset: { k } }, ...kids);
    const rows = cands.map((c) => el("tr", { class: `cand ${COLOR_CLASS[c.color]}` },
      cell("", "c-state", colorChip(c.color, names[c.color]), el("div", { class: "sub", text: c.label })),
      cell("位置", "c-pos", c.has_position ? "あり" : el("span", { class: "muted", text: "なし(GPS なし)" }),
        c.place ? el("div", { class: "sub", text: c.place }) : null),
      cell("写真", "c-photos", `${c.photo_count} 枚`),
      cell("台帳", "c-sign", c.sign
        ? el("span", {}, el("a", { href: `#/sign/${encodeURIComponent(c.sign.id)}`, text: c.sign.name || c.sign.id }),
          el("div", { class: "sub", text: `${c.distance_m} m` }))
        : el("span", { class: "muted", text: c.distance_m === null ? "近くに無し" : `近くに無し(最寄り ${c.distance_m} m)` })),
      cell("期限", "c-exp", expiry(c))));
    return el("div", { class: "table-wrap" }, el("table", { class: "ledger gov-table" },
      el("thead", {}, el("tr", {}, th("状態"), th("位置"), th("束ねた写真"), th("近い台帳の看板"), th("許可の期限"))),
      el("tbody", {}, rows)));
  }

  KK.views.gov = async function (_, params) {
    const head = el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: "職員が撮った写真の一括登録" }), el("h1", { text: "行政" })));
    const data = await KK.api("/api/gov/batches");
    if (!data.available || !data.batches.length) {
      KK.setView(head, el("p", { class: "notice", text: "一括登録はまだありません。" }),
        el("p", { class: "sub", text: "職員が撮った写真のフォルダを取り込む(python3 -m kanban_karte.gov.cli intake)と、ここに並びます。" }));
      return;
    }
    let id = +params.get("batch");
    if (!data.batches.some((b) => b.id === id)) id = data.batches[0].id;
    const b = data.batches.find((x) => x.id === id);
    const cand = await KK.api(`/api/gov/candidates?batch=${id}`);
    const picker = data.batches.length > 1
      ? el("select", { "aria-label": "一括登録", onchange: (e) => { location.hash = `#/gov?batch=${e.target.value}`; } },
        data.batches.map((x) => el("option", { value: x.id, selected: x.id === id || null, text: `登録 ${x.id}(${KK.fmtTime(x.created)})` })))
      : null;
    const stat = (n, t) => el("div", { class: "stat" }, el("b", { text: String(n) }), el("span", { text: t }));
    KK.setView(head,
      el("div", { class: "gov-pick" }, picker, el("span", { class: "sub", text: `登録 ${b.id} / ${KK.fmtTime(b.created)}` })),
      el("div", { class: "stats" }, stat(b.photo_count, "写真(枚)"), stat(b.candidate_count, "看板の候補(束ねた数)"),
        stat(b.gps_count, "位置あり"), stat(b.nogps_count, "位置なし")),
      countChips(data, b.by_color),
      el("section", { class: "card gov-map" }, el("h2", { text: "地図" }),
        el("iframe", { src: `/api/gov/map.html?batch=${id}`, title: "巡回調査マップ", loading: "lazy", sandbox: "allow-scripts allow-popups allow-popups-to-escape-sandbox" }),
        el("p", { class: "sub" }, "地図: ", el("a", { href: "https://maps.gsi.go.jp/development/ichiran.html", target: "_blank", rel: "noopener", text: "国土地理院 地理院タイル(淡色地図)" }),
          "。赤の点は無許可の疑いで、確認が要ります(断定ではありません)。")),
      candidatesTable(cand.candidates, cand.colors));
  };
})();
