// カルテ画面: 写真・18項目(3段階+対象外、判断できずは黄色)・根拠・所見・法規。有資格者がマスをタップして直す(即 POST)。
// 訂正は右側に履歴として出る。「確定して署名」で確定すると編集できない。
"use strict";
(function () {
  const { el } = KK;
  const BY_KEY = "kk_by";

  // 写真(看板を撮った物)と図面(職人の手描きスケッチ・印刷案の頁)は別の枠。番号 n は看板の画像の並びの位置で、図面は写真の後ろに付く
  function photosCard(d, hit) {
    const photos = d.photos.filter((p) => !p.figure && !p.measure);
    const tiles = photos.map((p) => el("button", {
      type: "button", class: "photo" + (hit.has(p.n) ? " hit" : ""), "aria-label": `写真 ${p.n} を拡大`,
      onclick: () => KK.lightbox.open(photos, p.n),
    }, p.url ? el("img", { src: p.url, alt: `写真 ${p.n}`, loading: "lazy" }) : el("div", { class: "ph", text: "画像なし" }),
      el("span", { class: "no", text: String(p.n) })));
    return el("section", { class: "card" }, el("h2", { text: `写真 ${photos.length} 枚` }),
      tiles.length ? el("div", { class: "photos" }, tiles) : el("p", { class: "notice warn", text: "写真がありません。下書きは作れません。" }));
  }

  // 図面: 点検票の次頁にある手描きスケッチ(寸法・取付方法)。下書きの入力には使わない
  function figuresCard(d) {
    const figs = d.photos.filter((p) => p.figure).map((p, i) => ({ ...p, no: i + 1 }));
    if (!figs.length) return null;
    const tiles = figs.map((p) => el("button", {
      type: "button", class: "photo figure", "aria-label": `図面 ${p.no} を拡大`, onclick: () => KK.lightbox.open(figs, p.n),
    }, p.url ? el("img", { src: p.url, alt: `図面 ${p.no}`, loading: "lazy" }) : el("div", { class: "ph", text: "画像なし" }),
      el("span", { class: "no", text: String(p.no) })));
    return el("section", { class: "card figures" }, el("h2", { text: `図面 ${figs.length} 枚` }),
      el("p", { class: "sub", text: "職人の手描きスケッチ(寸法・取付方法)。AI の下書きには使っていません。" }),
      el("div", { class: "photos" }, tiles));
  }

  // 寸法の出どころのバッジ。実測(日付・by)/ 推定(AI)/ 図面(スケッチ)/ 未入力
  const SIZE_SOURCE = { measured: ["実測", "good"], estimated: ["推定(AI)", "watch"], drawing: ["図面(スケッチ)", "status"], legacy: ["登録値(出どころ未記録)", "na"], none: ["未入力", "q"] };
  const byName = (by) => (by || "").replace(/^line_guide:/, "LINE ガイド ");
  function sizeBadge(info) {
    const [label, cls] = SIZE_SOURCE[info.source] || SIZE_SOURCE.none;
    const when = info.source === "measured" ? [info.at, byName(info.by)].filter(Boolean).join(" ") : "";
    return el("span", { class: `chip ${cls} size-src`, text: when ? `${label} ${when}` : label });
  }

  // 寸法: 幅×高さ+出どころのバッジ、全高、GL・取付方法・支柱(実測した時)、巻尺の写真
  function sizeCard(d) {
    const info = d.size_info, shots = d.photos.filter((p) => p.measure).map((p, i) => ({ ...p, no: i + 1 }));
    const kv = [["寸法", d.size || "—"]];
    if (d.height_m) kv.push(["全高", `${d.height_m} m`]);
    if (info.gl_mm != null) kv.push(["GL から", `${info.gl_mm} mm(${info.gl_edge === "top" ? "上端" : "看板の下端"}まで)`]);
    if (info.mount) kv.push(["取付方法", info.mount]);
    if (info.post) kv.push(["支柱", info.post]);
    const tiles = shots.map((p) => el("button", {
      type: "button", class: "photo figure", "aria-label": `実測写真 ${p.no} を拡大`, onclick: () => KK.lightbox.open(shots, p.n),
    }, p.url ? el("img", { src: p.url, alt: `実測写真 ${p.no}`, loading: "lazy" }) : el("div", { class: "ph", text: "画像なし" })));
    return el("section", { class: "card size-card" }, el("h2", {}, "寸法 ", sizeBadge(info)),
      el("div", { class: "kv" }, kv.flatMap(([k, v]) => [el("span", { class: "k", text: k }), el("span", { text: v })])),
      info.source === "estimated" ? el("p", { class: "sub", text: "写真からの推定です。実測が入ると実測を使います。" }) : null,
      tiles.length ? el("div", { class: "photos" }, tiles) : null);
  }

  // 根拠の文。「写真3」の語は押せる(その写真を拡大)
  function evidenceLine(d, ev) {
    if (!ev || !ev.text) return null;
    const parts = ev.text.split(/(写真\s*\d+)/).map((t) => {
      const m = /^写真\s*(\d+)$/.exec(t);
      return m ? el("button", { type: "button", class: "link", onclick: (e) => { e.stopPropagation(); KK.lightbox.open(d.photos.filter((q) => !q.figure && !q.measure), +m[1]); } }, t) : t;
    });
    return el("div", { class: "evidence" }, el("span", { class: "k", text: "根拠 " }), parts,
      ev.photos.length ? el("span", { class: "sub", text: ` (写真 ${ev.photos.join("・")})` }) : null);
  }

  function itemRow(d, it, state, rerender) {
    const open = state.open === it.key;
    const toggle = () => { state.open = open ? null : it.key; rerender(); };
    const head = el("div", { class: "item-head", role: "button", tabindex: "0", "aria-expanded": String(open),
      onclick: toggle, onkeydown: (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); } } },
      el("span", { class: "no", text: String(it.no) }), el("span", { class: "txt", text: it.text }),
      el("span", { class: "val" },
        it.value ? KK.resultChip(it.value) : it.guide === "na" ? KK.resultChip("対象外") : el("span", { class: "chip q", text: "判断できず(要実測)" }),
        it.value && it.yellow ? el("span", { class: "chip q", text: "要実測" }) : null,
        it.corrected ? el("span", { class: "tag", text: "訂正済み" }) : null,
        it.guide ? el("span", { class: "tag guide", text: it.guide === "unknown" ? "現場: 分からない" : it.guide === "na" ? "現場: 対象外" : "現場の回答(ガイド)" }) : null));
    const kids = [head, evidenceLine(d, it.evidence)];
    if (open) kids.push(editor(d, it, state, rerender));
    return el("div", { class: "item" + (it.yellow ? " yellow" : "") + (open ? " open" : ""), dataset: { key: it.key } }, kids);
  }

  function editor(d, it, state, rerender) {
    if (!d.editable) return el("div", { class: "editor" }, el("p", { class: "sub", text: "確定済みなので編集できません。" }));
    const msg = el("div", { class: "notice err", role: "alert", hidden: true });
    const note = el("input", { type: "text", id: "note", placeholder: "所見(任意。規則の文に使われます)", "aria-label": "所見", maxlength: 200 });
    const pick = async (value) => {
      const by = document.getElementById("by").value.trim();
      if (!by) {
        msg.hidden = false; msg.textContent = "訂正した人の名前を、上の「有資格者」に入れてください。";
        document.getElementById("by").focus();
        return;
      }
      try {
        const r = await KK.api(`/api/signs/${encodeURIComponent(d.id)}/items`, { item: it.key, value, by, note: note.value.trim() || null });
        state.open = null;
        state.flash = r.changed ? null : "下書きと同じ値なので、記録していません。";
        state.clarify = r.clarify || null;
        state.detail = r;
        rerender();
      } catch (e) { msg.hidden = false; msg.textContent = e.message; }
    };
    const seg = el("div", { class: "seg", role: "group", "aria-label": "区分" },
      d.result_choices.map((v) => el("button", { type: "button", class: `${KK.RESULT_CLASS[v]}`, "aria-pressed": String(it.value === v), onclick: () => pick(v), text: v })));
    return el("div", { class: "editor" }, seg, note, msg,
      el("p", { class: "sub", text: "押すとすぐ台帳に記録されます。写真と見比べて、判断できるほうを選びます。" }));
  }

  function rankEditor(d, state, rerender) {
    const msg = el("div", { class: "notice err", role: "alert", hidden: true });
    const pick = async (n) => {
      const by = document.getElementById("by").value.trim();
      if (!by) { msg.hidden = false; msg.textContent = "訂正した人の名前を入れてください。"; document.getElementById("by").focus(); return; }
      try { state.detail = await KK.api(`/api/signs/${encodeURIComponent(d.id)}/items`, { item: "rank", value: n, by }); rerender(); }
      catch (e) { msg.hidden = false; msg.textContent = e.message; }
    };
    return el("details", { class: "rank-edit" }, el("summary", { text: "看板全体のランクを直す" }),
      el("div", { class: "seg" }, [1, 2, 3, 4].map((n) => el("button", {
        type: "button", class: KK.RANK_CLASS[n], "aria-pressed": String(d.rank === n), onclick: () => pick(n), text: KK.RANK[n] }))), msg);
  }

  function itemsCard(d, state, rerender) {
    const groups = [];
    for (const it of d.items) {
      if (!groups.length || groups[groups.length - 1].name !== it.group) groups.push({ name: it.group, items: [] });
      groups[groups.length - 1].items.push(it);
    }
    return el("section", { class: "card items-card" },
      el("div", { class: "items-head" }, el("h2", { text: "点検 18 項目" }),
        el("label", { class: "by" }, "有資格者 ", el("input", { id: "by", type: "text", autocomplete: "name", placeholder: "名前(訂正・署名に記録)", value: localStorage.getItem(BY_KEY) || "",
          oninput: (e) => localStorage.setItem(BY_KEY, e.target.value.trim()), disabled: !d.editable || null }))),
      d.yellow_count ? el("p", { class: "notice warn", text: d.editable ? `黄色の ${d.yellow_count} マスは、判断できない・確信が低いマスです。マスをタップして直します。` : `黄色だったマス ${d.yellow_count} 件は、確定時の内容のままです。` }) : null,
      groups.map((g) => el("div", { class: "group" }, el("div", { class: "grp", text: g.name }), g.items.map((it) => itemRow(d, it, state, rerender)))),
      d.editable ? rankEditor(d, state, rerender) : null);
  }

  // 聞き返し(声→聞き返し→構造化): 所見が短いとき1問だけ。答えると規則候補の文が 項目/所見/条件/区分 に直る。答えなくても規則は所見のまま積んである
  function clarifyCard(d, state, rerender) {
    const c = state.clarify;
    if (!c) return null;
    const msg = el("div", { class: "notice err", role: "alert", hidden: true });
    const input = el("input", { type: "text", id: "clarify-answer", maxlength: 200, "aria-label": "聞き返しへの答え", placeholder: "例: 全周に回っている。写真3で見える" });
    const done = (text) => { state.clarify = null; state.flash = text; rerender(); };
    const send = async (answer) => {
      const by = (document.getElementById("by")?.value || localStorage.getItem(BY_KEY) || "").trim();
      if (!by) { msg.hidden = false; msg.textContent = "答える人の名前を、「有資格者」に入れてください。"; return; }
      try {
        const r = await KK.api(`/api/rules/${c.rule_id}/answer`, { question: c.question, answer, by });
        done(`規則候補 #${c.rule_id} を直しました: ${r.text}(承認されるまで下書きには効きません)`);
      } catch (e) { msg.hidden = false; msg.textContent = e.message; }
    };
    return el("section", { class: "card clarify" }, el("h2", { text: "聞き返し" }),
      el("p", { class: "clarify-q", text: c.question }),
      input,
      el("div", { class: "btn-row" },
        el("button", { type: "button", class: "btn", onclick: () => send(input.value.trim()), text: "答える" }),
        el("button", { type: "button", class: "btn ghost", onclick: () => done(`規則候補 #${c.rule_id} は所見のまま積みました。`), text: "分からない" })),
      msg,
      el("p", { class: "sub", text: `規則候補 #${c.rule_id} を、答えで 項目 / 所見 / 条件(範囲・程度)/ 区分 の形に直します。答えなくても候補は所見のまま残ります。` }));
  }

  function historyCard(d) {
    const li = (c) => el("li", {},
      el("div", { class: "h-top" }, el("b", { text: c.item === "rank" ? "看板全体のランク" : c.item_label }),
        el("span", { class: "sub", text: ` ${KK.fmtTime(c.created)}` })),
      el("div", {}, c.item === "rank" ? `${KK.RANK[c.old_value] || c.old_value || "—"} → ${KK.RANK[c.new_value]}` : `${c.old_value || "判断できず"} → ${c.new_value}`,
        c.rank_before !== c.rank_after ? el("span", { class: "sub", text: `(ランク ${KK.RANK[c.rank_before] || "—"} → ${KK.RANK[c.rank_after] || "—"})` }) : null),
      c.note ? el("div", { class: "sub", text: `所見: ${c.note}` }) : null,
      el("div", { class: "sub", text: `${c.by === "line_guide" ? "現場の回答(ガイド)" : c.by}${c.rule_id ? ` / 規則候補 #${c.rule_id} を積んだ` : ""}` }));
    return el("section", { class: "card" }, el("h2", { text: `訂正の履歴 ${d.corrections.length} 件` }),
      d.corrections.length ? el("ol", { class: "history" }, d.corrections.slice().reverse().map(li))
        : el("p", { class: "sub", text: "まだ訂正はありません。AI の下書きのままです。" }));
  }

  // 全高(m)。表示面の寸法とは別の値で、法規の判定(高さの基準)に渡る。空で保存すると消える
  function heightForm(d, state, rerender) {
    const input = el("input", { type: "number", id: "height-m", min: "0", max: "99", step: "0.1", inputmode: "decimal", value: d.height_m ?? "", placeholder: "例 4.5" });
    const msg = el("p", { class: "notice err", role: "alert", hidden: true });
    const save = async () => {
      const byEl = document.getElementById("by");
      const by = ((byEl && byEl.value) || localStorage.getItem(BY_KEY) || "").trim();
      if (!by) { msg.hidden = false; msg.textContent = "入力した人の名前が要ります。看板の画面の「有資格者」に名前を入れてください。"; return; }
      try { state.detail = await KK.api(`/api/signs/${encodeURIComponent(d.id)}/height`, { height_m: input.value.trim() || null, by }, "PUT"); rerender(); }
      catch (e) { msg.hidden = false; msg.textContent = e.message; }
    };
    return el("div", { class: "height-form" },
      el("label", {}, el("span", { class: "k", text: "全高(m)" }), " ", input),
      el("button", { class: "btn", type: "button", onclick: save, text: "保存" }),
      el("p", { class: "sub", text: d.height_m ? "高さの基準がある条例では、この値で判定します。" : "未入力。高さの基準がある条例では、全高が分からないと「要実測」になります。" }), msg);
  }

  // 点検まわりの4つの義務(法規役の duties の文言のまま)。旧形式の規則のときは旧『点検』の値だけ
  function dutyKv(L) {
    const es = KK.dutyEntries(L.duties);
    if (!es.length) return [el("span", { class: "k", text: "点検" }), el("span", {}, `点検の義務 ${L.inspection_required}(旧形式の規則。再抽出が要る)`)];
    return es.flatMap((e) => [el("span", { class: "k", text: e.title }), el("span", {}, e.text)]);
  }

  function legalCard(d, state, rerender) {
    const kv = (k, v) => [el("span", { class: "k", text: k }), el("span", {}, v)];
    const L = d.legal || {};
    const term = L.permit_term_years === null || L.permit_term_years === undefined ? null : `許可の期限 ${L.permit_term_years} 年(上限)`;
    const judged = L.error ? el("p", { class: "notice err", text: `法規の判定ができませんでした: ${L.error}` }) : el("div", { class: "legal-judge" },
      el("div", { class: "kv" },
        kv("自家用", L.is_self_use_exempt === null || L.is_self_use_exempt === undefined ? "要実測(自家用の基準に収まるか決められない)"
          : L.is_self_use_exempt ? `自家用の許可不要の基準に収まる${L.area_m2 ? `(${L.area_m2} ㎡)` : ""}` : "自家用の基準を超える、または自家用でない"),
        KK.placeText(L.place) ? kv("用途地域", KK.placeText(L.place)) : null,
        kv("許可", KK.permitChip(L.permit_required)),
        term ? kv("期限", term) : null,
        ...dutyKv(L)),
      el("p", { class: "sub", text: "管理・点検・有資格者・行政への報告は別の義務です。条例ごとに掛かり方が違い、許可が不要でも義務がある場合があります。" }),
      el("h3", { text: `根拠条文 ${(L.citations || []).length} 件${L.ordinance_key ? `(${L.ordinance_key}の条例)` : ""}` }),
      KK.citations(L.citations),
      (L.notes || []).length ? el("ul", { class: "legal-notes" }, L.notes.map((n) => el("li", { class: "sub", text: n }))) : null,
      heightForm(d, state, rerender),
      el("p", { class: "sub", text: "根拠は条例の写しから機械が抜いたものです。安全の判定ではありません。" }));
    return el("section", { class: "card legal-card" }, el("h2", { text: "法規" }), judged,
      el("details", { class: "ledger-law" }, el("summary", { text: "台帳に記録されている法規の値" }),
        el("div", { class: "kv" },
          kv("自家用", KK.triText(d.is_self_use, "自家用(自分の店の名前)", "自家用でない")),
          kv("許可", d.permit_required === null ? "—(未判定)" : d.permit_required ? `要${d.permit_expires ? `(期限 ${d.permit_expires})` : ""}` : "不要"),
          kv("次回点検", d.next_inspection || "—"),
          kv("場所", [d.address, d.municipality].filter(Boolean).join(" / ") || "—"),
          kv("前回", d.prev_rank ? `判定 ${KK.RANK[d.prev_rank]}` : "—"))));
  }

  function signCard(d, state, rerender) {
    if (d.status === "confirmed") {
      return el("section", { class: "card banner confirmed" }, el("h2", { text: "確定済み" }),
        el("div", { class: "kv" }, el("span", { class: "k", text: "署名" }), el("span", { text: `有資格者: ${d.confirmed_by}` }),
          el("span", { class: "k", text: "日時" }), el("span", { text: KK.fmtTime(d.confirmed_at) }),
          el("span", { class: "k", text: "下書き" }), el("span", { text: d.draft_rank && d.draft_rank !== d.rank ? `${KK.RANK[d.draft_rank]} → ${KK.RANK[d.rank]}(有資格者が変更)` : "ランクは下書きのまま" })),
        el("p", { class: "sub", text: "確定した内容は編集できません。" }),
        el("a", { class: "btn ghost", href: "#/rules", text: "様式を出力する →" }));
    }
    if (d.status !== "draft") {
      return el("section", { class: "card" }, el("h2", { text: "確認と署名" }),
        el("p", { class: "notice warn", text: d.status === "needs_photos" ? "写真がないので、下書きを作れません。" : "まだ下書きがありません。" }));
    }
    const box = el("div", {});
    const msg = el("div", { class: "notice err", role: "alert", hidden: true });
    const showAsk = () => {
      const by = document.getElementById("by").value.trim();
      if (!by) { msg.hidden = false; msg.textContent = "署名する人の名前を、上の「有資格者」に入れてください。"; document.getElementById("by").focus(); return; }
      msg.hidden = true;
      box.replaceChildren(
        el("p", { class: "notice warn" }, `「${by}」として、この看板のランク「${KK.RANK[d.rank] || "—"}」を確定します。` +
          (d.yellow_count ? ` 黄色のマスが ${d.yellow_count} 件残っています。` : "")),
        el("div", { class: "btn-row" },
          el("button", { type: "button", class: "btn", onclick: async () => {
            try { state.detail = await KK.api(`/api/signs/${encodeURIComponent(d.id)}/finalize`, { by }); state.open = null; rerender(); }
            catch (e) { msg.hidden = false; msg.textContent = e.message; }
          }, text: "確定する" }),
          el("button", { type: "button", class: "btn ghost", onclick: () => box.replaceChildren(btn), text: "戻る" })));
    };
    const btn = el("button", { type: "button", class: "btn block", onclick: showAsk, text: "確定して署名" });
    box.append(btn);
    return el("section", { class: "card" }, el("h2", { text: "確認と署名" }),
      el("p", { class: "sub", text: "AI は確定しません。直し終えたら、有資格者が確定して署名します。" }), box, msg);
  }

  function summaryCard(d) {
    const draftNote = d.draft_rank && d.draft_rank !== d.rank ? ` (下書きは ${KK.RANK[d.draft_rank]})` : "";
    return el("section", { class: "card summary" },
      el("div", { class: "sum-top" }, el("div", {}, el("div", { class: "eyebrow", text: [d.site_name, d.id].filter(Boolean).join(" / ") }),
        el("h1", { text: "カルテ" }), el("div", { class: "sign-name", text: d.name || d.id }), el("div", { class: "sub" }, [d.type, d.size].filter(Boolean).join(" / "), " ", sizeBadge(d.size_info))),
        el("div", { class: "rank-box" }, KK.rankChip(d.rank, d.status),
          el("div", { class: "sub", text: `${KK.STATUS[d.status] || d.status}${draftNote}` }))),
      d.remark ? el("div", { class: "kv" }, el("span", { class: "k", text: "所見" }), el("span", { text: d.remark })) : null,
      d.confidence !== null ? el("div", { class: "sub", text: `下書きの確信 ${Math.round(d.confidence * 100)}%` }) : null);
  }

  KK.views.karte = async function (id) {
    const state = { open: null, flash: null, clarify: null, detail: await KK.api(`/api/signs/${encodeURIComponent(id)}`) };
    const render = () => {
      const y = window.scrollY;
      const d = state.detail;
      const hit = new Set();
      if (state.open) (d.items.find((i) => i.key === state.open)?.evidence?.photos || []).forEach((n) => hit.add(n));
      const back = d.site_id ? `#/?site=${encodeURIComponent(d.site_id)}` : "#/";
      KK.setView(
        el("p", { class: "crumb" }, el("a", { href: back, text: "← 台帳に戻る" })),
        state.flash ? el("p", { class: "notice", role: "status", text: state.flash }) : null,
        el("div", { class: "karte" },
          el("div", { class: "main-col" }, summaryCard(d), clarifyCard(d, state, render), photosCard(d, hit), figuresCard(d), itemsCard(d, state, render)),
          el("div", { class: "side-col" }, signCard(d, state, render), sizeCard(d), historyCard(d), legalCard(d, state, render))));
      window.scrollTo(0, y);
    };
    render();
  };
})();
