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

  // ---- スマホからの投入: 撮る → 送る ----
  const MAX_PHOTOS = 30;  // gov.upload.MAX_UPLOAD_PHOTOS と同じ

  // JPEG の先頭 64KB から EXIF の GPS と撮影時刻を読む(縮小すると EXIF は落ちるので、縮める前の元ファイルから読む)。
  // 読めない・無い時は {}。外部の部品は使わない。
  function parseExif(v) {
    if (v.byteLength < 4 || v.getUint16(0) !== 0xFFD8) return {};
    let off = 2;
    while (off + 4 <= v.byteLength) {
      if (v.getUint8(off) !== 0xFF) return {};
      const marker = v.getUint8(off + 1), len = v.getUint16(off + 2);
      if (marker === 0xDA || marker === 0xD9) return {};  // 画像データまで来た
      if (marker === 0xE1 && v.getUint32(off + 4) === 0x45786966 && v.getUint16(off + 8) === 0) return parseTiff(v, off + 10);
      off += 2 + len;
    }
    return {};
  }

  function parseTiff(v, base) {
    const le = v.getUint16(base) === 0x4949;
    const u16 = (o) => v.getUint16(base + o, le), u32 = (o) => v.getUint32(base + o, le);
    const SIZE = { 1: 1, 2: 1, 3: 2, 4: 4, 5: 8 };
    const ifd = (o) => {  // tag → 値の位置(TIFF 先頭からの相対)と型・個数
      const tags = {};
      const n = u16(o);
      for (let i = 0; i < n; i++) {
        const e = o + 2 + i * 12, type = u16(e + 2), count = u32(e + 4);
        tags[u16(e)] = { type, count, pos: (SIZE[type] || 1) * count <= 4 ? e + 8 : u32(e + 8) };
      }
      return tags;
    };
    const ascii = (t) => { let s = ""; for (let i = 0; i < t.count; i++) { const c = v.getUint8(base + t.pos + i); if (!c) break; s += String.fromCharCode(c); } return s; };
    const deg = (t) => {
      if (!t || t.type !== 5 || t.count !== 3) return NaN;
      const r = (i) => u32(t.pos + i * 8) / u32(t.pos + i * 8 + 4);
      return r(0) + r(1) / 60 + r(2) / 3600;
    };
    const out = {};
    const i0 = ifd(u32(4));
    let sub = {};
    if (i0[0x8769]) sub = ifd(u32(i0[0x8769].pos));
    const when = ascii(sub[0x9003] || sub[0x9004] || i0[0x0132] || { count: 0 });
    const m = /^(\d{4}):(\d\d):(\d\d) (\d\d:\d\d:\d\d)$/.exec(when);
    if (m) out.taken_at = `${m[1]}-${m[2]}-${m[3]}T${m[4]}`;
    if (i0[0x8825]) {
      const g = ifd(u32(i0[0x8825].pos));
      let lat = deg(g[2]), lng = deg(g[4]);
      if (g[1] && ascii(g[1]) === "S") lat = -lat;
      if (g[3] && ascii(g[3]) === "W") lng = -lng;
      if (Number.isFinite(lat) && Number.isFinite(lng) && Math.abs(lat) <= 90 && Math.abs(lng) <= 180 && (lat !== 0 || lng !== 0)) {
        out.lat = lat; out.lng = lng;
      }
    }
    return out;
  }

  async function readExif(file) {
    try { return parseExif(new DataView(await file.slice(0, 65536).arrayBuffer())); } catch (_) { return {}; }
  }

  function devicePosition() {  // 送る直前の1回。許可されない・取れない時は null(位置なしで送る)
    return new Promise((resolve) => {
      if (!navigator.geolocation) return resolve(null);
      navigator.geolocation.getCurrentPosition((p) => resolve({ lat: p.coords.latitude, lng: p.coords.longitude }),
        () => resolve(null), { enableHighAccuracy: true, timeout: 10000, maximumAge: 30000 });
    });
  }

  const localIso = (ms) => { const d = new Date(ms - new Date(ms).getTimezoneOffset() * 60000); return d.toISOString().slice(0, 19); };
  let lastUpload = null;  // 送った直後の結果。画面を描き直した後に1回だけ出す

  function captureCard(tq) {
    const items = [];  // {file, exif, url}
    const input = el("input", { id: "gov-photo", class: "visually-hidden", type: "file", accept: "image/*", capture: "environment", multiple: true });
    const sel = el("div", { class: "gov-sel", "aria-live": "polite" });
    const thumbs = el("div", { class: "gov-thumbs" });
    const useDevice = el("input", { id: "gov-use-device", type: "checkbox", checked: true });
    const msg = el("div", { class: "notice err", role: "alert", hidden: true });
    const result = el("div", { class: "gov-result", "aria-live": "polite" });
    const send = el("button", { type: "button", class: "btn block", text: "② 送る", disabled: true });
    const clear = el("button", { type: "button", class: "btn ghost small", text: "選び直す", hidden: true });
    const refresh = () => {
      const withPos = items.filter((x) => x.exif.lat !== undefined).length;
      sel.textContent = items.length ? `${items.length} 枚(写真に位置あり ${withPos} 枚 / 位置なし ${items.length - withPos} 枚)` : "まだ選んでいません";
      thumbs.replaceChildren(...items.map((x) => el("img", { src: x.url, alt: "選んだ写真(この端末の中だけに表示)", class: x.exif.lat !== undefined ? "has-pos" : "" })));
      send.disabled = !items.length; clear.hidden = !items.length;
    };
    input.addEventListener("change", async () => {
      msg.hidden = true;
      for (const f of [...input.files]) {
        if (items.length >= MAX_PHOTOS) { msg.hidden = false; msg.textContent = `1回に送れるのは ${MAX_PHOTOS} 枚までです。`; break; }
        if (items.some((x) => x.file.name === f.name && x.file.size === f.size && x.file.lastModified === f.lastModified)) continue;
        items.push({ file: f, exif: await readExif(f), url: URL.createObjectURL(f) });
      }
      input.value = "";  // 続けて撮れるように。選んだ分は items に溜める
      refresh();
    });
    clear.onclick = () => { items.splice(0).forEach((x) => URL.revokeObjectURL(x.url)); msg.hidden = true; refresh(); };
    send.onclick = async () => {
      msg.hidden = true; result.replaceChildren();
      send.disabled = true; send.textContent = "送っています…";
      try {
        const need = items.some((x) => x.exif.lat === undefined);
        const dev = need && useDevice.checked ? await devicePosition() : null;
        const photos = [];
        for (const x of items) {
          const p = { data: await KK.shrink(x.file) };
          const t = x.exif.taken_at || (x.file.lastModified ? localIso(x.file.lastModified) : null);
          if (t) p.taken_at = t;
          if (x.exif.lat !== undefined) Object.assign(p, { lat: x.exif.lat, lng: x.exif.lng, position_source: "exif" });
          else if (dev) Object.assign(p, { lat: dev.lat, lng: dev.lng, position_source: "device" });
          photos.push(p);
        }
        const r = await KK.api("/api/gov/upload", { photos });
        r.deviceDenied = need && useDevice.checked && !dev;
        lastUpload = r;
        const hash = `#/gov?batch=${r.batch_id}${tq}`;
        if (location.hash === hash) KK.route(); else location.hash = hash;
        return;
      } catch (e) { msg.hidden = false; msg.textContent = e.message; }
      send.textContent = "② 送る"; send.disabled = !items.length;
    };
    if (lastUpload) {
      const r = lastUpload, s = r.position_sources;
      lastUpload = null;
      result.append(el("div", { class: "notice gov-done" },
        el("b", { text: `送りました(束 ${r.batch_id})` }),
        el("div", {}, `看板の候補 ${r.candidates} 基 / 位置なし ${r.no_position} 枚`),
        el("div", { class: "sub", text: `位置: 写真の GPS ${s.exif} 枚 / 端末の位置 ${s.device} 枚 / なし ${s.none} 枚` +
          (r.skipped ? `。取り込み済みで飛ばした ${r.skipped} 枚` : "") +
          (r.deviceDenied ? "。端末の位置が取れなかったので、位置の無い写真は位置なしです" : "") +
          (r.geocode_pending ? `。市区町村は ${r.geocode_pending} 基が未判定(通信できず)` : "") })));
    }
    refresh();
    return el("section", { class: "card gov-capture", id: "gov-capture" },
      el("h2", { text: "① 撮る → ② 送る" }),
      el("label", { class: "btn ghost block pick", for: "gov-photo", text: "① 写真を撮る・選ぶ" }), input,
      sel, thumbs,
      el("label", { class: "gov-device", for: "gov-use-device" }, useDevice, el("span", { text: "位置が無い写真は、いまの端末の位置で送る(撮った場所から離れている時は外してください)" })),
      el("div", { class: "btn-row" }, send, clear), msg, result);
  }

  KK.views.gov = async function (_, params) {
    const today = params.get("today");  // 基準日(YYYY-MM-DD。見本の撮影用。既定は今日)
    const tq = today ? `&today=${encodeURIComponent(today)}` : "";
    const sent = !!lastUpload;
    const head = el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: "職員が撮った写真の一括登録" }), el("h1", { text: "行政" })),
      captureCard(tq));
    const data = await KK.api(`/api/gov/batches${today ? `?today=${encodeURIComponent(today)}` : ""}`);
    if (!data.available || !data.batches.length) {
      KK.setView(head, el("p", { class: "notice", text: "一括登録はまだありません。" }),
        el("p", { class: "sub", text: "上の「撮る → 送る」か、職員が撮った写真のフォルダを取り込む(python3 -m kanban_karte.gov.cli intake)と、ここに並びます。" }));
      return;
    }
    let id = +params.get("batch");
    if (!data.batches.some((b) => b.id === id)) id = data.batches[0].id;
    const b = data.batches.find((x) => x.id === id);
    const cand = await KK.api(`/api/gov/candidates?batch=${id}${tq}`);
    const picker = data.batches.length > 1
      ? el("select", { "aria-label": "一括登録", onchange: (e) => { location.hash = `#/gov?batch=${e.target.value}${tq}`; } },
        data.batches.map((x) => el("option", { value: x.id, selected: x.id === id || null, text: `登録 ${x.id}${x.name ? ` ${x.name}` : ""}(${KK.fmtTime(x.created)})` })))
      : null;
    const stat = (n, t) => el("div", { class: "stat" }, el("b", { text: String(n) }), el("span", { text: t }));
    KK.setView(head,
      el("div", { class: "gov-pick" }, picker, el("span", { class: "sub", text: `登録 ${b.id}${b.name ? ` ${b.name}` : ""} / ${KK.fmtTime(b.created)}` })),
      el("div", { class: "stats", id: "gov-results" }, stat(b.photo_count, "写真(枚)"), stat(b.candidate_count, "看板の候補(束ねた数)"),
        stat(b.gps_count, "位置あり"), stat(b.nogps_count, "位置なし")),
      countChips(data, b.by_color),
      el("section", { class: "card gov-map" }, el("h2", { text: "地図" }),
        el("iframe", { src: `/api/gov/map.html?batch=${id}${tq}`, title: "巡回調査マップ", loading: "lazy", sandbox: "allow-scripts allow-popups allow-popups-to-escape-sandbox" }),
        el("p", { class: "sub" }, "地図: ", el("a", { href: "https://maps.gsi.go.jp/development/ichiran.html", target: "_blank", rel: "noopener", text: "国土地理院 地理院タイル(淡色地図)" }),
          "。赤の点は無許可の疑いで、確認が要ります(断定ではありません)。")),
      candidatesTable(cand.candidates, cand.colors));
    if (sent) document.getElementById("gov-results").scrollIntoView({ block: "start" });  // 送った直後は、その束の件数・地図・候補表へ
  };
})();
