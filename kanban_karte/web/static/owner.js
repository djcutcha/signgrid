// オーナー画面(スマホ幅を先に設計): 1枚撮って、住所と寸法を入れて「調べる」。自家用の許可の要否と根拠条文を見せる。
// 写真は表示するだけで保存しない。DB に書かない。安全の判定(ランク・18項目)は出さない。
"use strict";
(function () {
  const { el } = KK;

  function threeLines(r) {
    const area = `${r.area_m2.toFixed(1)} ㎡`;
    const exempt = r.is_self_use_exempt;
    const line1 = exempt === true ? `自家用 ${area}(自家用の許可不要の基準に収まります)`
      : exempt === false ? `自家用 ${area}(自家用の基準を超えます)` : `自家用 ${area}(基準に収まるか決められません)`;
    let line3;
    if (r.permit_required === "不要" && r.inspection_required !== "要") {
      line3 = "許可不要= 点検の義務は誰にもありません(点検の網の外)";
    } else if (r.permit_required === "不要") {
      line3 = "許可は不要ですが、条例の写しでは点検の義務が「要」とされています";
    } else if (r.permit_required === "要") {
      line3 = `許可が要ります${r.permit_term_years ? `(許可の期限は ${r.permit_term_years} 年以内)` : ""}。点検の義務: ${r.inspection_required}`;
    } else {
      line3 = "実測や区域の確認が要ります。点検の義務: 要実測";
    }
    return [
      el("li", { class: "line-use" }, el("span", { class: "k", text: "自家用" }), el("span", { text: line1 })),
      el("li", { class: "line-permit" }, el("span", { class: "k", text: "許可" }), KK.permitChip(r.permit_required)),
      el("li", { class: "line-inspect" }, el("span", { class: "k", text: "点検" }), el("span", { text: line3 })),
    ];
  }

  function resultCard(r, input) {
    const subject = "看板の点検のお願い";
    const body = `住所: ${input.address}\n看板の大きさ: 幅 ${input.w} m × 高さ ${input.h} m(約 ${r.area_m2.toFixed(1)} ㎡)\n点検をお願いします。`;
    return el("section", { class: "card owner-result", "aria-live": "polite" },
      el("h2", { text: "調べた結果" }),
      el("ul", { class: "three" }, threeLines(r)),
      r.notes && r.notes.length ? el("ul", { class: "legal-notes" }, r.notes.map((n) => el("li", { class: "sub", text: n }))) : null,
      el("h3", { text: `根拠条文 ${(r.citations || []).length} 件${r.ordinance_key ? `(${r.ordinance_key}の条例)` : ""}` }),
      KK.citations(r.citations),
      el("p", { class: "sub", text: "根拠は条例の写しから機械が抜いたものです。安全の判定ではありません。" }),
      // 宛先は空のまま(プレースホルダ)。メールアプリが開き、宛先を選んで送る
      el("a", { class: "btn block ask", href: `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`, text: "看板屋に点検を頼む" }));
  }

  KK.views.owner = async function () {
    const out = el("div", { id: "owner-out" });
    const msg = el("div", { class: "notice err", role: "alert", hidden: true });
    const preview = el("div", { class: "owner-preview" });
    const num = (id, label, ph) => el("label", { class: "num", for: id }, el("span", { text: label }),
      el("input", { id, type: "number", inputmode: "decimal", min: "0", step: "0.01", placeholder: ph }));
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
        const r = await KK.api("/api/owner/judge", { address, area_m2: Math.round(w * h * 100) / 100, height_m: height > 0 ? height : null });
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
          el("input", { id: "owner-photo", class: "visually-hidden", type: "file", accept: "image/*", capture: "environment", onchange: (e) => {
            const f = e.target.files[0];
            preview.replaceChildren(f ? el("img", { src: URL.createObjectURL(f), alt: "選んだ写真(この端末の中だけに表示)" }) : "");
          } }),
          preview, el("p", { class: "sub", text: "写真はこの画面に表示するだけで、送信も保存もしません。" })),
        el("section", { class: "card step" }, el("h2", { text: "② 看板の場所" }),
          el("label", { class: "txt", for: "owner-address" }, el("span", { text: "住所" }),
            el("input", { id: "owner-address", type: "text", autocomplete: "street-address", placeholder: "例 東京都豊島区○○町1-2-3" })),
          el("p", { class: "sub", text: "LINE で送った写真には位置が残らないので、住所は手で入れます。" })),
        el("section", { class: "card step" }, el("h2", { text: "③ 看板の大きさ" }),
          el("div", { class: "dims" }, num("owner-w", "幅(m)", "1.95"), el("span", { class: "times", text: "×" }), num("owner-h", "高さ(m)", "0.6")),
          el("div", { class: "area-row" }, el("span", { class: "k", text: "面積" }), area),
          el("label", { class: "num wide", for: "owner-height" }, el("span", { text: "地面からの高さ(m、分かれば)" }),
            el("input", { id: "owner-height", type: "number", inputmode: "decimal", min: "0", step: "0.1", placeholder: "4" }))),
        el("section", { class: "card step" }, el("h2", { text: "④ 調べる" }), submit, msg),
        out));
    document.getElementById("owner-w").addEventListener("input", refreshArea);
    document.getElementById("owner-h").addEventListener("input", refreshArea);
  };
})();
