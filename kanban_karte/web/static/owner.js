// オーナー画面(スマホ幅を先に設計): 1枚撮って、住所と寸法を入れて「調べる」。自家用の許可の要否と根拠条文を見せる。
// 写真は保存しない。「寸法を推定」を押した時だけ縮小版を API へ送る。DB に書かない。安全の判定(ランク・18項目)は出さない。
"use strict";
(function () {
  const { el } = KK;
  const DUTY_NOTE = "管理・点検・有資格者・行政への報告は、それぞれ別の義務です。条例ごとに掛かり方が違うので、許可が不要でも義務がある場合があります。";

  // 点検まわりの4行(管理 / 点検 / 有資格者 / 行政への報告)は法規役の duties の文言のまま(盛らない)。
  // 旧形式の規則(duties が空)のときだけ、旧『点検』の値を1行で出す
  function dutyLines(r) {
    const es = KK.dutyEntries(r.duties);
    if (!es.length) {
      return [el("li", { class: "line-duty" }, el("span", { class: "k", text: "点検" }), el("span", { text: `点検の義務: ${r.inspection_required}(旧形式の規則。再抽出が要ります)` }))];
    }
    return es.map((e) => el("li", { class: "line-duty" }, el("span", { class: "k", text: e.title }), el("span", { text: e.text })));
  }

  function placeLine(place) {
    const t = KK.placeText(place);
    return t ? el("li", { class: "line-place" }, el("span", { class: "k", text: "用途地域" }), el("span", { text: t })) : null;
  }

  function threeLines(r, areaText, place) {
    const area = areaText || `${r.area_m2.toFixed(1)} ㎡`;
    const exempt = r.is_self_use_exempt;
    const line1 = exempt === true ? `自家用 ${area}(自家用の許可不要の基準に収まります)`
      : exempt === false ? `自家用 ${area}(自家用の基準を超えます)` : `自家用 ${area}(基準に収まるか決められません)`;
    return [
      el("li", { class: "line-use" }, el("span", { class: "k", text: "自家用" }), el("span", { text: line1 })),
      placeLine(place),
      el("li", { class: "line-permit" }, el("span", { class: "k", text: "許可" }), KK.permitChip(r.permit_required)),
      ...dutyLines(r),
    ];
  }

  // 判定の元: 推定の範囲の両端(range)か、欄の入力値 1 点(input)か
  function basisLine(r, input) {
    const t = r.basis === "range"
      ? `写真から推定した範囲の両端(${r.area_low_m2.toFixed(1)}〜${r.area_high_m2.toFixed(1)} ㎡)で判定。両端で結論が分かれた項目は「要実測」です。`
      : `入力値(幅 ${input.w} m × 高さ ${input.h} m)で判定。`;
    return el("li", { class: "line-basis" }, el("span", { class: "k", text: "判定の元" }), el("span", { text: t }));
  }

  function resultCard(r, input) {
    const subject = "看板の点検のお願い";
    const body = `住所: ${input.address}\n看板の大きさ: 幅 ${input.w} m × 高さ ${input.h} m(約 ${r.area_m2.toFixed(1)} ㎡)\n点検をお願いします。`;
    return el("section", { class: "card owner-result", "aria-live": "polite" },
      el("h2", { text: "調べた結果" }),
      el("ul", { class: "three" }, basisLine(r, input),
        ...threeLines(r, r.basis === "range" ? `${r.area_m2.toFixed(1)} ㎡(${r.area_low_m2.toFixed(1)}〜${r.area_high_m2.toFixed(1)} ㎡)` : null, r.place)),
      r.notes && r.notes.length ? el("ul", { class: "legal-notes" }, r.notes.map((n) => el("li", { class: "sub", text: n }))) : null,
      el("p", { class: "sub", text: DUTY_NOTE }),
      el("h3", { text: `根拠条文 ${(r.citations || []).length} 件${r.ordinance_key ? `(${r.ordinance_key}の条例)` : ""}` }),
      KK.citations(r.citations),
      el("p", { class: "sub", text: "根拠は条例の写しから機械が抜いたものです。安全の判定ではありません。" }),
      // 宛先は空のまま(プレースホルダ)。メールアプリが開き、宛先を選んで送る
      el("a", { class: "btn block ask", href: `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`, text: "看板屋に点検を頼む" }));
  }

  // 寸法の推定の結果。judgement は judge_from_range の結果(許可は permit)
  function estimateCard(res) {
    const e = res.estimate, j = res.judgement;
    const m = (mm) => (mm / 1000).toFixed(2).replace(/\.?0+$/, "");
    const refs = (e.references || []).filter((r) => r.name);
    const legal = { permit_required: j.permit, inspection_required: j.inspection_required, is_self_use_exempt: j.is_self_use_exempt,
      permit_term_years: j.permit_term_years, duties: j.duties };
    const areaText = j.area_low === j.area_high ? `${j.area_mid.toFixed(1)} ㎡` : `${j.area_mid.toFixed(1)} ㎡(${j.area_low.toFixed(1)}〜${j.area_high.toFixed(1)} ㎡)`;
    return el("section", { class: "card owner-result owner-estimate", "aria-live": "polite" },
      el("h2", { text: "写真から推定した大きさ" }),
      el("p", { class: "est-size" }, `W≈${m(e.w_mm)} m × H≈${m(e.h_mm)} m(±${e.w_pct}% / ±${e.h_pct}%)、面積≈${e.area_m2.toFixed(1)} ㎡`),
      el("p", { class: "sub", text: `範囲: 幅 ${m(e.w_range[0])}〜${m(e.w_range[1])} m、高さ ${m(e.h_range[0])}〜${m(e.h_range[1])} m。信頼度: ${{ high: "高", mid: "中", low: "低" }[e.confidence]}` }),
      refs.length ? el("p", { class: "sub" }, "物差しにした物: " + refs.map((r) => r.name).join("、")) : el("p", { class: "sub", text: "物差しにできる物が見つかりませんでした。" }),
      e.read_from_text ? el("p", { class: "sub", text: "写真の中の文字から読んだ数値を使っています。" }) : null,
      e.notes ? el("p", { class: "sub", text: e.notes }) : null,
      el("p", { class: "sub", text: "幅・高さの欄に入れました。実測した値があれば、手で直して「調べる」を押してください。" }),
      el("h2", { text: "範囲の両端で調べた結果" }),
      el("ul", { class: "three" }, threeLines(legal, areaText, res.place)),
      j.notes && j.notes.length ? el("ul", { class: "legal-notes" }, j.notes.map((n) => el("li", { class: "sub", text: n }))) : null,
      el("h3", { text: `根拠条文 ${(j.citations || []).length} 件${j.ordinance_key ? `(${j.ordinance_key}の条例)` : ""}` }),
      KK.citations(j.citations),
      el("p", { class: "sub", text: "寸法は写真からの見積もりで、誤差があります。根拠は条例の写しから機械が抜いたものです。安全の判定ではありません。" }));
  }

  // 選んだ写真を長辺 1600px の JPEG(base64)にする。端末の中で縮め、送るのはこの縮小版だけ
  async function shrink(file, edge = 1600) {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const s = Math.min(1, edge / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85).split(",")[1];
  }
  KK.shrink = shrink;  // 行政のスマホ投入(gov.js)でも使う

  KK.views.owner = async function () {
    const out = el("div", { id: "owner-out" });
    const estOut = el("div", { id: "owner-est-out" });
    const files = [null, null];
    let fromEst = null;  // 推定で欄に入れた値と範囲。欄が手で直されていなければ「調べる」も範囲の両端で判定する
    const canEstimate = !!(KK.meta && KK.meta.can_estimate);
    const msg = el("div", { class: "notice err", role: "alert", hidden: true });
    const preview = el("div", { class: "owner-preview" });
    const num = (id, label, ph) => el("label", { class: "num", for: id }, el("span", { text: label }),
      el("input", { id, type: "number", inputmode: "decimal", min: "0", step: "0.01", placeholder: ph }));
    const setFile = (i, f) => {
      files[i] = f || null;
      preview.replaceChildren(...files.filter(Boolean).map((x) => el("img", { src: URL.createObjectURL(x), alt: "選んだ写真(この端末の中だけに表示)" })));
      estRow.hidden = !files[0];
      estOut.replaceChildren();
    };
    const estMsg = el("div", { class: "notice err", role: "alert", hidden: true });
    const target = el("input", { id: "owner-target", type: "text", placeholder: "例 入口上の横長の看板(任意)" });
    const estBtn = el("button", { type: "button", class: "btn block", text: "寸法を推定", disabled: !canEstimate, onclick: async () => {
      estMsg.hidden = true; estOut.replaceChildren(); out.replaceChildren();
      const address = val("owner-address");
      if (!address) { estMsg.hidden = false; estMsg.textContent = "住所を入れてください(② の欄)。許可の要否を調べるのに使います。"; return; }
      estBtn.disabled = true; estBtn.textContent = "推定しています…";
      try {
        const photos = await Promise.all(files.filter(Boolean).map((f) => shrink(f)));
        const height = parseFloat(val("owner-height"));  // 全高(分かれば)。高さの基準がある条例では、これが無いと要実測になる
        const res = await KK.api("/api/owner/estimate", { photos, address, target_name: target.value.trim() || null, height_m: height > 0 ? height : null });
        document.getElementById("owner-w").value = (res.estimate.w_mm / 1000).toFixed(2);
        document.getElementById("owner-h").value = (res.estimate.h_mm / 1000).toFixed(2);
        fromEst = { w: val("owner-w"), h: val("owner-h"), low: res.judgement.area_low, high: res.judgement.area_high };
        refreshArea();
        estOut.replaceChildren(estimateCard(res));
        estOut.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (e) { estMsg.hidden = false; estMsg.textContent = e.message; }
      estBtn.textContent = "寸法を推定"; estBtn.disabled = !canEstimate;
    } });
    const estRow = el("div", { class: "est-row", hidden: true },
      el("label", { class: "txt", for: "owner-target" }, el("span", { text: "どの看板の寸法か(複数写る時)" }), target),
      estBtn,
      canEstimate ? null : el("p", { class: "sub", text: "寸法の推定は使えません: ANTHROPIC_API_KEY(環境変数か .env)が設定されていません。幅と高さは手で入れてください。" }),
      estMsg);
    const area = el("output", { id: "owner-area", class: "area", text: "—" });
    const val = (id) => document.getElementById(id).value.trim();
    const refreshArea = () => {
      const w = parseFloat(val("owner-w")), h = parseFloat(val("owner-h"));
      area.textContent = w > 0 && h > 0 ? `${(w * h).toFixed(2)} ㎡` : "—";
    };
    const submit = el("button", { type: "button", class: "btn block", text: "調べる", onclick: async () => {
      msg.hidden = true; out.replaceChildren();
      const w = parseFloat(val("owner-w")), h = parseFloat(val("owner-h")), address = val("owner-address");
      if (!address) { msg.hidden = false; msg.textContent = "住所を入れてください(LINE の写真には位置が残らないので、手入力です)。"; return; }
      if (!(w > 0 && h > 0)) { msg.hidden = false; msg.textContent = "看板の幅と高さを m で入れてください。"; return; }
      const height = parseFloat(val("owner-height"));
      submit.disabled = true;
      try {
        const q = { address, area_m2: Math.round(w * h * 100) / 100, height_m: height > 0 ? height : null };
        if (fromEst && fromEst.w === val("owner-w") && fromEst.h === val("owner-h") && fromEst.low < fromEst.high) {
          q.area_low_m2 = fromEst.low; q.area_high_m2 = fromEst.high;
        }
        const r = await KK.api("/api/owner/judge", q);
        out.replaceChildren(resultCard(r, { address, w, h }));
        out.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (e) { msg.hidden = false; msg.textContent = e.message; }
      submit.disabled = false;
    } });
    KK.setView(
      el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: "1枚撮って、自分の看板を調べる" }), el("h1", { text: "オーナー" }))),
      el("div", { class: "owner" },
        el("section", { class: "card step" }, el("h2", { text: "① 写真を撮る" }),
          el("label", { class: "btn ghost block pick", for: "owner-photo" }, "写真を撮る・選ぶ"),
          el("input", { id: "owner-photo", class: "visually-hidden", type: "file", accept: "image/*", capture: "environment", onchange: (e) => setFile(0, e.target.files[0]) }),
          el("label", { class: "pick2", for: "owner-photo2" }, "もう1枚(別の角度、任意)"),
          el("input", { id: "owner-photo2", class: "visually-hidden", type: "file", accept: "image/*", onchange: (e) => setFile(1, e.target.files[0]) }),
          preview,
          el("p", { class: "sub", text: "写真は、下の「寸法を推定」を押した時だけ、寸法の推定のために AI(Claude)へ送ります。保存はしません。押さなければ送りません。" })),
        el("section", { class: "card step" }, el("h2", { text: "② 看板の場所" }),
          el("label", { class: "txt", for: "owner-address" }, el("span", { text: "住所" }),
            el("input", { id: "owner-address", type: "text", autocomplete: "street-address", placeholder: "例 東京都豊島区○○町1-2-3" })),
          el("p", { class: "sub", text: "LINE で送った写真には位置が残らないので、住所は手で入れます。" })),
        el("section", { class: "card step" }, el("h2", { text: "③ 看板の大きさ" }), estRow,
          el("div", { class: "dims" }, num("owner-w", "幅(m)", "1.95"), el("span", { class: "times", text: "×" }), num("owner-h", "高さ(m)", "0.6")),
          el("div", { class: "area-row" }, el("span", { class: "k", text: "面積" }), area),
          el("label", { class: "num wide", for: "owner-height" }, el("span", { text: "地面からの高さ(m、分かれば)" }),
            el("input", { id: "owner-height", type: "number", inputmode: "decimal", min: "0", step: "0.1", placeholder: "4" }))),
        estOut,
        el("section", { class: "card step" }, el("h2", { text: "④ 調べる" }), submit, msg),
        out));
    document.getElementById("owner-w").addEventListener("input", () => { refreshArea(); estOut.replaceChildren(); });
    document.getElementById("owner-h").addEventListener("input", () => { refreshArea(); estOut.replaceChildren(); });
  };
})();
