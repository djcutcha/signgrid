// 市民が見かけた看板を送る画面(/report/、ログイン不要、スマホ優先の 1 画面)と、受付番号で状態を見る画面(/report/status?code=…)。
// 写真は端末で長辺 1600px に縮めてから送る。位置はブラウザの位置情報(写真の EXIF の位置は使わない。サーバも EXIF を捨てて保存し直す)。
// 写真・一言は公開しない。呼び名はサーバの定数(kanban_karte/citizen/__init__.py の BRAND)1 か所から受け取る。
"use strict";
(function () {
  const { el } = KK;
  const HANDOFF_KEY = "kk.report.handoff";   // /owner/ へ写真を引き継ぐ(この端末の sessionStorage だけ。サーバへは送らない)

  async function shrink(file, edge = 1600) {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const s = Math.min(1, edge / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85).split(",")[1];
  }

  function head(meta, eyebrow, title) {
    return el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: eyebrow }), el("h1", { text: title })));
  }

  // ---- 受付番号で状態を見る ----
  async function statusView(meta) {
    const code0 = new URLSearchParams(location.search).get("code") || "";
    const out = el("div", { id: "rp-status-out" });
    const input = el("input", { id: "rp-code", type: "text", value: code0, placeholder: "例 ACDE-3479", autocomplete: "off", autocapitalize: "characters", inputmode: "text" });
    const check = async () => {
      out.replaceChildren();
      const code = input.value.trim();
      if (!code) return;
      try {
        const r = await KK.api(`/api/report/status?code=${encodeURIComponent(code)}`, undefined);
        out.replaceChildren(el("section", { class: "card rp-status" },
          el("div", { class: "rp-code-show", text: r.code }),
          el("p", {}, el("span", { class: `chip rp-st st-${r.status}`, text: r.status }), " ", el("span", { class: "sub", text: `受付 ${r.date}` })),
          el("p", { text: r.text })));
      } catch (e) { out.replaceChildren(el("p", { class: "notice err", role: "alert", text: e.message })); }
    };
    KK.setView(head(meta, "受付番号(合言葉)で確かめる", "報告の状態"),
      el("div", { class: "report" },
        el("section", { class: "card step" },
          el("label", { class: "txt", for: "rp-code" }, el("span", { text: "受付番号" }), input),
          el("button", { type: "button", class: "btn block", text: "状態を見る", onclick: check }),
          el("p", { class: "sub", text: "状態だけを表示します。写真や一言は表示しません。" })),
        out,
        el("p", { class: "sub" }, el("a", { href: "/report/", text: "看板を報告する" }))));
    if (code0) check();
  }

  // ---- 送る ----
  async function reportView(meta) {
    const st = { file: null, lat: null, lng: null, acc: null, busy: false };
    const msg = el("div", { class: "notice err", role: "alert", hidden: true });
    const preview = el("div", { class: "owner-preview" });
    const locText = el("p", { class: "sub", id: "rp-loc-text", text: "まだ位置を取っていません。" });
    const addrRow = el("label", { class: "txt", for: "rp-address" }, el("span", { text: "住所・目印(位置が取れない時)" }),
      el("input", { id: "rp-address", type: "text", autocomplete: "street-address", maxlength: "120", placeholder: "例 越谷市役所の前の交差点" }));
    const locBtn = el("button", { type: "button", class: "btn ghost block", id: "rp-loc", text: "今いる場所を使う", onclick: () => {
      if (!navigator.geolocation) { locText.textContent = "この端末では位置が取れません。下に住所か目印を入れてください。"; return; }
      locBtn.disabled = true; locText.textContent = "位置を取っています…";
      navigator.geolocation.getCurrentPosition((p) => {
        st.lat = p.coords.latitude; st.lng = p.coords.longitude; st.acc = p.coords.accuracy;
        locText.textContent = `位置を取りました(誤差 約 ${Math.round(st.acc)} m)。地図には約 100 m に丸めた位置だけを使います。`;
        locBtn.disabled = false; locBtn.textContent = "位置を取り直す";
      }, () => {
        locText.textContent = "位置が取れませんでした(許可されていない、または電波が弱い)。下に住所か目印を入れてください。";
        locBtn.disabled = false;
      }, { enableHighAccuracy: true, timeout: 15000, maximumAge: 60000 });
    } });
    const checks = el("div", { class: "rp-checks" }, meta.signs.map((s) => el("label", { class: "rp-check" },
      el("input", { type: "checkbox", value: s, name: "rp-sign" }), el("span", { text: s }))));
    const note = el("textarea", { id: "rp-note", rows: "3", maxlength: "200", placeholder: "例 強い風の日に揺れていました(任意)" });
    const out = el("div", { id: "rp-out" });
    const submit = el("button", { type: "button", class: "btn block", id: "rp-send", text: "送る", onclick: async () => {
      msg.hidden = true;
      if (!st.file) { msg.hidden = false; msg.textContent = "看板の写真を撮ってください。"; return; }
      const address = document.getElementById("rp-address").value.trim();
      if (st.lat === null && !address) { msg.hidden = false; msg.textContent = "「今いる場所を使う」を押すか、住所・目印を入れてください。"; return; }
      submit.disabled = true; submit.textContent = "送っています…";
      try {
        const photo = await shrink(st.file);
        const signs = [...document.querySelectorAll("input[name=rp-sign]:checked")].map((x) => x.value);
        const r = await KK.api("/api/report", { photo, lat: st.lat, lng: st.lng, address: address || null, note: note.value.trim() || null, signs });
        try { sessionStorage.setItem(HANDOFF_KEY, JSON.stringify({ photo, address })); } catch (_) { /* 容量が足りない時は引き継がない */ }
        form.hidden = true;
        out.replaceChildren(doneCard(r));
        window.scrollTo({ top: 0, behavior: "smooth" });
      } catch (e) { msg.hidden = false; msg.textContent = e.message; }
      submit.disabled = false; submit.textContent = "送る";
    } });
    function doneCard(r) {
      const statusUrl = `/report/status?code=${encodeURIComponent(r.code)}`;
      return el("section", { class: "card rp-done" },
        el("h2", { text: "受け付けました" }),
        el("p", { class: "sub", text: "受付番号(合言葉)" }),
        el("div", { class: "rp-code-show", id: "rp-code-show", text: r.code }),
        el("p", { text: "この看板が点検されたら、この合言葉で確認できます。番号を控えるか、下のリンクを保存してください。" }),
        el("a", { class: "btn ghost block", href: statusUrl, text: "状態を見る(合言葉で確認)" }),
        el("a", { class: "btn block", href: "/owner/?from=report", text: "許可の要否を自分で見る(看板の持ち主向け)" }),
        el("p", { class: "sub", text: "撮影後に処理します。写真と一言は公開されません。顔や車のナンバーが写っていても、そのまま公開されることはありません。" }),
        el("button", { type: "button", class: "link", text: "続けて別の看板を送る", onclick: () => location.reload() }));
    }
    const form = el("div", { class: "report", id: "rp-form" },
      el("p", { class: "rp-lead" }, "見かけた看板の写真を送ってください。", el("b", { text: "撮影後に処理します。" }), "写真は公開されません。"),
      el("section", { class: "card step" }, el("h2", { text: "① 看板を撮る" }),
        el("label", { class: "btn block pick", for: "rp-photo" }, "カメラで撮る・写真を選ぶ"),
        el("input", { id: "rp-photo", class: "visually-hidden", type: "file", accept: "image/*", capture: "environment", onchange: (e) => {
          st.file = e.target.files[0] || null;
          preview.replaceChildren(...(st.file ? [el("img", { src: URL.createObjectURL(st.file), alt: "選んだ写真(この端末の中だけに表示)" })] : []));
        } }),
        preview,
        el("p", { class: "sub", text: "看板全体が入るように。人の顔や車のナンバーはなるべく写さないでください。" })),
      el("section", { class: "card step" }, el("h2", { text: "② 場所" }), locBtn, locText, addrRow,
        el("p", { class: "sub", text: "写真に残った位置(撮影場所の情報)は使いません。送るのはこの画面で取った位置だけです。" })),
      el("section", { class: "card step" }, el("h2", { text: "③ 気になる所(任意)" }), checks),
      el("section", { class: "card step" }, el("h2", { text: "④ 一言(任意)" }),
        el("label", { class: "txt", for: "rp-note" }, el("span", { text: "200 字まで" }), note)),
      el("section", { class: "card step" }, submit, msg,
        el("p", { class: "sub", text: "送った写真・一言・位置は、看板の点検を担当する窓口だけが見ます。名前や連絡先は聞きません。" })));
    KK.setView(head(meta, "見かけた看板を送る", "看板の報告"), out, form,
      el("p", { class: "sub rp-foot" }, el("a", { href: "/report/status", text: "受付番号で状態を見る" })));
  }

  (async function start() {
    let meta;
    try { meta = await KK.api("/api/report/meta", undefined); } catch (e) { KK.setView(KK.error(e)); return; }
    document.title = `${meta.brand} 看板の報告`;
    document.getElementById("rp-brand").textContent = meta.brand;
    document.getElementById("rp-brand-sub").textContent = meta.brand_sub;
    if (location.pathname === "/report/status") statusView(meta); else reportView(meta);
  })();
})();
