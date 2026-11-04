// オーナー画面(スマホ幅を先に設計): 種類を先に選ぶ → 種類ごとの撮り方で撮る → 推定の枠が浮かぶ → (袖・自立は)歩道の切れ目を人がタップ
// → 撮影後に判定。種類ごとの項目(壁面= 壁面率・上端、袖= 出幅・下端の地上高・道路占用)は壁面と袖だけ(Cutcha 2026-11-04 16:05)。
// 写真は保存しない。「写真から推定」を押した時だけ縮小版を API へ送る。DB に書かない。安全の判定(ランク・18項目)は出さない。
// 境界線(歩道の切れ目)は人が置く。AI に当てさせない。
"use strict";
(function () {
  const { el } = KK;
  const DUTY_NOTE = "管理・点検・有資格者・行政への報告は、それぞれ別の義務です。条例ごとに掛かり方が違うので、許可が不要でも義務がある場合があります。";

  // 種類の絵(外部画像なし。固定の SVG。利用者の文字は入らない)
  const ICON = {
    wall: '<svg viewBox="0 0 64 48" aria-hidden="true"><rect x="10" y="6" width="44" height="40" fill="none" stroke="currentColor" stroke-width="2"/><rect x="18" y="12" width="28" height="9" fill="#e5281e"/><rect x="16" y="28" width="9" height="18" fill="none" stroke="currentColor" stroke-width="1.5"/><rect x="34" y="28" width="12" height="8" fill="none" stroke="currentColor" stroke-width="1.5"/><line x1="2" y1="46" x2="62" y2="46" stroke="currentColor" stroke-width="2"/></svg>',
    projecting: '<svg viewBox="0 0 64 48" aria-hidden="true"><rect x="6" y="4" width="20" height="42" fill="none" stroke="currentColor" stroke-width="2"/><line x1="26" y1="12" x2="30" y2="12" stroke="currentColor" stroke-width="2"/><rect x="30" y="8" width="14" height="20" fill="#e5281e"/><line x1="2" y1="46" x2="62" y2="46" stroke="currentColor" stroke-width="2"/><line x1="40" y1="34" x2="40" y2="46" stroke="#f08c00" stroke-width="2" stroke-dasharray="3 2"/></svg>',
    freestanding: '<svg viewBox="0 0 64 48" aria-hidden="true"><rect x="20" y="4" width="24" height="16" fill="#e5281e"/><line x1="32" y1="20" x2="32" y2="46" stroke="currentColor" stroke-width="3"/><line x1="2" y1="46" x2="62" y2="46" stroke="currentColor" stroke-width="2"/><line x1="52" y1="30" x2="52" y2="46" stroke="#f08c00" stroke-width="2" stroke-dasharray="3 2"/></svg>',
    rooftop: '<svg viewBox="0 0 64 48" aria-hidden="true"><rect x="14" y="20" width="36" height="26" fill="none" stroke="currentColor" stroke-width="2"/><line x1="22" y1="20" x2="22" y2="14" stroke="currentColor" stroke-width="2"/><line x1="42" y1="20" x2="42" y2="14" stroke="currentColor" stroke-width="2"/><rect x="18" y="4" width="28" height="10" fill="#e5281e"/><line x1="2" y1="46" x2="62" y2="46" stroke="currentColor" stroke-width="2"/></svg>',
    unknown: '<svg viewBox="0 0 64 48" aria-hidden="true"><rect x="12" y="6" width="40" height="34" rx="4" fill="none" stroke="currentColor" stroke-width="2" stroke-dasharray="4 3"/><text x="32" y="31" text-anchor="middle" font-size="22" font-weight="700" fill="currentColor">?</text></svg>',
  };
  const icon = (id, cls) => { const s = el("span", { class: `kind-icon ${cls || ""}` }); s.innerHTML = ICON[id] || ICON.unknown; return s; };

  // 写真の上で人が置く線(袖: 壁の面・歩道の切れ目・地面 / 自立: 歩道の切れ目・道路側)
  const TAP = {
    wall: { label: "壁の面", hint: "看板が付いている壁の面(縦の線)をタップ", axis: "x", color: "#5b6470" },
    boundary: { label: "歩道の切れ目", hint: "歩道の切れ目(縁石・境界ブロック・塀の根元など。道路との境)をタップ", axis: "x", color: "#f08c00" },
    ground: { label: "地面", hint: "看板の真下の地面(歩道の面)をタップ", axis: "y", color: "#8a5a2b" },
    road_side: { label: "道路側", hint: "歩道の切れ目より道路側(車道のある側)のどこかをタップ", axis: "pt", color: "#f08c00" },
  };

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

  function basisLine(r, input) {
    const t = r.basis === "range"
      ? `写真から推定した範囲の両端(${r.area_low_m2.toFixed(1)}〜${r.area_high_m2.toFixed(1)} ㎡)で判定。両端で結論が分かれた項目は「要実測」です。`
      : `入力値(幅 ${input.w} m × 高さ ${input.h} m)で判定。`;
    return el("li", { class: "line-basis" }, el("span", { class: "k", text: "判定の元" }), el("span", { text: t }));
  }

  // ---- 種類ごとの項目の表 ----
  const VERDICT_CLASS = { "適合": "good", "超過": "fix", "要実測": "watch", "未入力": "na" };
  const fmtM = (v) => (v === null || v === undefined ? "—" : `${(Math.round(v * 100) / 100).toFixed(2)} m`);
  function valueText(row) {
    if (row.value === null || row.value === undefined) return "—";
    if (row.item === "wall_ratio") {
      const hi = row.value_high;
      return hi !== undefined && hi !== null && hi !== row.value ? `${(row.value * 100).toFixed(1)}〜${(hi * 100).toFixed(1)}%` : `${(row.value * 100).toFixed(1)}%`;
    }
    if (row.item === "road_protrusion") return row.value > 0 ? `道路側へ ${fmtM(row.value)}` : `境界の内側 ${fmtM(-row.value)}`;
    return fmtM(row.value);
  }
  function limitText(row) {
    if (row.limit === null || row.limit === undefined) return row.verified === "unverified" ? "未確認" : "—";
    if (row.item === "wall_ratio") return `${Math.round(row.limit * 1000) / 10}% 以下`;
    if (row.item === "road_protrusion") return "道路上に出ない";
    if (row.item === "clearance") return `${row.limit} m 以上`;
    return `${row.limit} m 以下`;
  }
  const VERIFIED_TEXT = { official: "公式の条文で照合済み", copy: "条例の写しのみ(公式と未照合)", unverified: "未確認" };
  function kindTable(rows, title) {
    if (!rows || !rows.length) return null;
    return el("div", { class: "kind-table" },
      title ? el("h3", { text: title }) : null,
      el("ul", { class: "kind-rows" }, rows.map((r) => el("li", { class: `kind-row v-${VERDICT_CLASS[r.verdict] || "na"}` },
        el("div", { class: "kr-top" }, el("span", { class: "kr-label", text: r.label }),
          el("span", { class: `chip ${VERDICT_CLASS[r.verdict] || "na"}`, text: r.verdict })),
        el("div", { class: "kr-vals" }, el("span", {}, "写真・入力 ", el("b", { text: valueText(r) })), el("span", {}, "基準 ", el("b", { text: limitText(r) }))),
        r.note ? el("div", { class: "sub kr-note", text: r.note }) : null,
        (r.branches || []).length ? el("ul", { class: "kr-branches" }, r.branches.map((b) => el("li", { class: "sub", text: `${b.when}: ${b.verdict}${b.note ? `(${b.note})` : ""}` }))) : null,
        el("div", { class: "kr-cite sub" },
          el("span", { class: `tag ${r.verified === "official" ? "" : "guide"}`, text: VERIFIED_TEXT[r.verified] || r.verified }), " ",
          (r.citations || []).slice(0, 2).map((c) => el("span", { class: "kr-art" }, `${c.source || ""} ${c.article || ""}${c.verbatim === false ? "(要旨)" : ""} `,
            c.url ? el("a", { href: c.url, target: "_blank", rel: "noopener", text: "原文" }) : null, " ")))))));
  }

  function roadBox(ro) {
    if (!ro) return null;
    const cls = ro.status === "要" ? "fix" : ro.status === "不要" ? "good" : "watch";
    return el("div", { class: `road-box ${cls}` },
      el("div", { class: "kr-top" }, el("b", { text: "道路占用・道路使用" }), el("span", { class: `chip ${cls}`, text: `道路占用許可 ${ro.status}` })),
      el("p", { text: ro.text }),
      (ro.by_manager || []).length ? el("ul", { class: "kr-branches" }, ro.by_manager.map((b) => el("li", { class: "sub" },
        `${b.manager}: ${b.rows && b.rows.length ? b.rows.map((x) => `${x.label} ${x.verdict}`).join("、") : (b.note || "数値は未確認。道路管理者に確認")}`
          + (b.verified !== "official" ? "(未確認)" : "")))) : null,
      el("div", { class: "sub" }, (ro.citations || []).map((c) => el("span", { class: "kr-art" }, `${c.article} `,
        c.url ? el("a", { href: c.url, target: "_blank", rel: "noopener", text: "e-Gov" }) : null, " "))));
  }

  // 要実測・未入力の項目を 1 か所にまとめる(確定させない物)
  function measureList(r) {
    const rows = [...(r.kind_checks || [])].filter((x) => x.verdict === "要実測" || x.verdict === "未入力");
    if (!rows.length && r.permit_required !== "要実測") return null;
    return el("div", { class: "notice warn" }, el("b", { text: "要実測(写真だけでは確定しない)" }),
      el("ul", {}, rows.map((x) => el("li", { text: `${x.label}: ${x.verdict === "未入力" ? "値を入れてください" : (x.note || "基準の境目付近。スケールで測るか窓口で確認")}` })),
        r.permit_required === "要実測" && !rows.length ? el("li", { text: "許可の要否が決まりません(寸法・区域を確かめてください)" }) : null));
  }

  function resultCard(r, input, kindLabel) {
    const subject = "看板の点検のお願い";
    const body = `住所: ${input.address}\n種類: ${kindLabel || "—"}\n看板の大きさ: 幅 ${input.w} m × 高さ ${input.h} m(約 ${r.area_m2.toFixed(1)} ㎡)\n点検をお願いします。`;
    const showPermitRows = r.permit_required !== "不要" && (r.kind_permit_checks || []).length;
    return el("section", { class: "card owner-result", "aria-live": "polite" },
      el("h2", { text: `撮影後に判定した結果${kindLabel ? `(${kindLabel})` : ""}` }),
      el("ul", { class: "three" }, basisLine(r, input),
        ...threeLines(r, r.basis === "range" ? `${r.area_m2.toFixed(1)} ㎡(${r.area_low_m2.toFixed(1)}〜${r.area_high_m2.toFixed(1)} ㎡)` : null, r.place)),
      kindTable(r.kind_checks, "種類ごとの項目(自家用で許可が要らない枠)"),
      showPermitRows ? kindTable(r.kind_permit_checks, "許可を受ける場合の基準") : null,
      roadBox(r.road_occupancy),
      measureList(r),
      r.notes && r.notes.length ? el("ul", { class: "legal-notes" }, r.notes.map((n) => el("li", { class: "sub", text: n }))) : null,
      el("p", { class: "sub", text: DUTY_NOTE }),
      el("h3", { text: `根拠条文 ${(r.citations || []).length} 件${r.ordinance_key ? `(${r.ordinance_key}の条例)` : ""}` }),
      KK.citations(r.citations),
      el("p", { class: "sub", text: "写真からの寸法は概算で、基準の境目(±15%)は要実測にしています。判定の確定は有資格者が行います。安全の判定ではありません。" }),
      el("a", { class: "btn block ask", href: `mailto:?subject=${encodeURIComponent(subject)}&body=${encodeURIComponent(body)}`, text: "有資格者に点検を頼む" }));
  }

  // 写真の上の枠(赤= 看板、緑= 壁面、青= 物差し)。撮った後に走査線が流れてから点線が浮かぶ(1.5 秒)
  function boxFigure(p, url, many, opts) {
    const box = (b, cls, label) => {
      const [x0, y0, x1, y1] = b;
      return el("div", { class: `bbox ${cls}${y0 < 0.07 ? " near-top" : ""}`, style: `left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%` },
        el("span", { class: "bbox-label", text: label }));
    };
    const refs = (p.references || []).filter((r) => r.box);
    const frame = el("div", { class: `est-frame${opts && opts.scan ? " scanning" : ""}` },
      el("img", { src: url, alt: `写真${p.photo + 1}(推定の枠を重ねた表示。この端末の中だけ)` }),
      p.wall_box ? box(p.wall_box, "wall", "壁面") : null,
      p.sign_box ? box(p.sign_box, "sign", "看板") : null,
      ...refs.map((r) => box(r.box, "ref", r.name)),
      opts && opts.scan ? el("div", { class: "scanline", "aria-hidden": "true" }) : null,
      el("div", { class: "tap-layer" }));
    return el("figure", { class: "est-fig", dataset: { photo: String(p.photo) } }, frame,
      many || !p.sign_box ? el("figcaption", { class: "sub" },
        (many ? `写真${p.photo + 1}。` : "") + (p.sign_box ? "" : "この写真では看板の位置が返りませんでした。")) : null);
  }

  async function shrink(file, edge = 1600) {
    const bmp = await createImageBitmap(file, { imageOrientation: "from-image" });
    const s = Math.min(1, edge / Math.max(bmp.width, bmp.height));
    const c = document.createElement("canvas");
    c.width = Math.round(bmp.width * s); c.height = Math.round(bmp.height * s);
    c.getContext("2d").drawImage(bmp, 0, 0, c.width, c.height);
    return c.toDataURL("image/jpeg", 0.85).split(",")[1];
  }
  KK.shrink = shrink;  // 行政のスマホ投入(gov.js)でも使う

  // 人が置いた線から、袖の出幅・下端の地上高を概算する(看板の枠の幅・高さを物差しにする)。
  // 写真は真横から撮る前提(看板の面が見える)。斜めだとずれるので、結果は欄に入れて人が直す
  function fromTaps(kind, taps, sb, w_mm, h_mm) {
    if (!sb) return {};
    const [x0, y0, x1, y1] = sb;
    const mx = (w_mm / 1000) / Math.max(1e-3, x1 - x0), my = (h_mm / 1000) / Math.max(1e-3, y1 - y0);
    const out = {};
    if (kind === "projecting") {
      if (taps.wall !== undefined) {
        const far = Math.abs(x0 - taps.wall) > Math.abs(x1 - taps.wall) ? x0 : x1;
        const dir = Math.sign(far - taps.wall) || 1;
        out.protrusion_wall_m = Math.abs(far - taps.wall) * mx;
        if (taps.boundary !== undefined) out.protrusion_road_m = (far - taps.boundary) * dir * mx;
      }
      if (taps.ground !== undefined) out.ground_clearance_m = Math.max(0, (taps.ground - y1) * my);
    } else if (kind === "freestanding" && taps.boundary !== undefined && taps.road_side) {
      const dir = Math.sign(taps.road_side.x - taps.boundary) || 1;
      out.over_road_m = Math.max((x0 - taps.boundary) * dir, (x1 - taps.boundary) * dir) * mx;
    }
    return out;
  }
  KK.ownerFromTaps = fromTaps;  // 試験用

  KK.views.owner = async function () {
    let meta;
    try { meta = await KK.api("/api/owner/kinds"); } catch (e) { KK.setView(KK.error(e)); return; }
    const kinds = [...meta.kinds, meta.unknown];
    const byId = Object.fromEntries(kinds.map((k) => [k.id, k]));
    const st = { kind: null, files: [null, null], est: null, urls: [], taps: {}, fromEst: null };
    const canEstimate = !!(KK.meta && KK.meta.can_estimate);
    const val = (id) => { const x = document.getElementById(id); return x ? x.value.trim() : ""; };
    const numOr = (id) => { const v = parseFloat(val(id)); return Number.isFinite(v) ? v : null; };
    const setNum = (id, v) => { const x = document.getElementById(id); if (x && v !== null && v !== undefined && Number.isFinite(v)) x.value = (Math.round(v * 100) / 100).toFixed(2); };

    const out = el("div", { id: "owner-out" });
    const estOut = el("div", { id: "owner-est-out" });
    const shootBox = el("div", { class: "shoot" }, el("p", { class: "sub", text: "① で種類を選ぶと、その種類の撮り方が出ます。" }));
    const kindInputs = el("div", { class: "kind-inputs" });
    const msg = el("div", { class: "notice err", role: "alert", hidden: true });
    const estMsg = el("div", { class: "notice err", role: "alert", hidden: true });
    const preview = el("div", { class: "owner-preview" });

    // ① 種類を選ぶ
    const kindBtns = kinds.map((k) => el("button", { type: "button", class: "kind-btn", dataset: { kind: k.id }, "aria-pressed": "false",
      onclick: () => pickKind(k.id) }, icon(k.id), el("span", { class: "kb-label", text: k.label }), k.sub ? el("span", { class: "kb-sub", text: k.sub }) : null));
    function pickKind(id, keepEst) {
      st.kind = id;
      kindBtns.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.kind === id)));
      const k = byId[id];
      shootBox.replaceChildren(...[icon(id, "big"), el("p", { class: "shoot-line" }, el("b", { text: "撮り方: " }), k.shoot),
        id === "projecting" || id === "freestanding" ? el("p", { class: "sub", text: "撮った後に、写真の上で歩道の切れ目(道路との境)を指でタップして線を置きます。AI は境界を決めません。" }) : null,
        id === "unknown" ? el("p", { class: "sub", text: "写真を送ると、AI が種類の候補を出します。候補から選んでください。" }) : null].filter(Boolean));
      renderKindInputs();
      if (!keepEst) { estOut.replaceChildren(); st.est = null; st.taps = {}; }
      out.replaceChildren();
    }

    // 種類ごとの入力欄(壁面と袖だけ。自立・屋上は面積と高さのまま)
    function renderKindInputs() {
      const num = (id, label, ph, extra) => el("label", { class: "num", for: id }, el("span", { text: label }),
        el("input", { id, type: "number", inputmode: "decimal", step: "0.01", placeholder: ph, ...(extra || { min: "0" }) }));
      const k = st.kind;
      if (k === "wall") {
        kindInputs.replaceChildren(el("h3", { text: "壁面" }),
          el("div", { class: "dims" }, num("owner-wall-area", "壁面の面積(㎡)", "60"), num("owner-eave", "軒高(m、任意)", "7")),
          el("p", { class: "sub", text: "壁面の面積は、看板が付いている壁 1 面(窓・ドアなどの開口部を含む)。推定すると写真の緑の枠から概算して入ります。実測した値があれば直してください。" }));
      } else if (k === "projecting") {
        kindInputs.replaceChildren(el("h3", { text: "袖(突出し)" }),
          el("div", { class: "dims" }, num("owner-pw", "壁面からの出幅(m)", "0.9"), num("owner-pr", "道路境界から(m)", "0.3", { step: "0.01" })),
          el("div", { class: "dims" }, num("owner-gc", "下端の地上高(m)", "3.5"), num("owner-wallh", "壁面の高さ(m、任意)", "8")),
          el("div", { class: "dims" },
            el("label", { class: "num", for: "owner-over" }, el("span", { text: "看板の下は" }),
              el("select", { id: "owner-over" }, el("option", { value: "", text: "分からない" }), el("option", { value: "sidewalk", text: "歩道" }),
                el("option", { value: "roadway", text: "車道・歩道の無い道路" }))),
            el("label", { class: "num", for: "owner-mgr" }, el("span", { text: "道路の管理者(任意)" }),
              el("select", { id: "owner-mgr" }, el("option", { value: "", text: "分からない" }), ...meta.road_managers.map((m) => el("option", { value: m, text: m }))))),
          el("p", { class: "sub", text: "「道路境界から」は歩道の切れ目より道路側へ出ている長さ(内側に収まっていれば負の値)。写真の上で線を置くと、出幅と地上高の概算が入ります。基準の境目(±15%)は要実測になります。" }));
      } else if (k === "freestanding" || k === "rooftop") {
        kindInputs.replaceChildren(el("p", { class: "sub", text: `${byId[k].label}は、面積と高さで判定します(種類ごとの項目はまだ対象外)。` }));
      } else {
        kindInputs.replaceChildren();
      }
    }

    // ③ 推定。写真の上の枠と、線を置く UI
    function tapPanel(fig, p) {
      const kind = st.kind;
      const order = kind === "projecting" ? ["wall", "boundary", "ground"] : kind === "freestanding" ? ["boundary", "road_side"] : [];
      if (!order.length || !p.sign_box) return null;
      const layer = fig.querySelector(".tap-layer");
      const frame = fig.querySelector(".est-frame");
      const guide = el("p", { class: "tap-guide" });
      const result = el("p", { class: "sub tap-result" });
      const draw = () => {
        layer.replaceChildren(...order.filter((t) => st.taps[t] !== undefined).map((t) => {
          const d = TAP[t], v = st.taps[t];
          if (d.axis === "x") return el("div", { class: `tline v${t === "wall" ? " first" : ""}`, style: `left:${v * 100}%;border-color:${d.color}` }, el("span", { class: "tl-label", style: `background:${d.color}`, text: d.label }));
          if (d.axis === "y") return el("div", { class: "tline h", style: `top:${v * 100}%;border-color:${d.color}` }, el("span", { class: "tl-label", style: `background:${d.color}`, text: d.label }));
          return el("div", { class: "tdot", style: `left:${v.x * 100}%;top:${v.y * 100}%;background:${d.color}` });
        }));
        const next = order.find((t) => st.taps[t] === undefined);
        guide.textContent = next ? `${order.indexOf(next) + 1}/${order.length} ${TAP[next].hint}` : "線を置きました。違っていたら「置き直す」を押してください。";
        frame.classList.toggle("tapping", !!next);
        const v = fromTaps(kind, st.taps, p.sign_box, st.est.w_mm, st.est.h_mm);
        if (kind === "projecting") {
          setNum("owner-pw", v.protrusion_wall_m); setNum("owner-pr", v.protrusion_road_m); setNum("owner-gc", v.ground_clearance_m);
          const parts = [];
          if (v.protrusion_wall_m !== undefined) parts.push(`壁面からの出幅 ≈ ${fmtM(v.protrusion_wall_m)}`);
          if (v.protrusion_road_m !== undefined) parts.push(v.protrusion_road_m > 0 ? `道路側へ ≈ ${fmtM(v.protrusion_road_m)}` : `境界の内側 ≈ ${fmtM(-v.protrusion_road_m)}`);
          if (v.ground_clearance_m !== undefined) parts.push(`下端の地上高 ≈ ${fmtM(v.ground_clearance_m)}`);
          result.textContent = parts.length ? `写真からの概算: ${parts.join("、")}(欄に入れました。直せます)` : "";
        } else if (v.over_road_m !== undefined) {
          result.textContent = v.over_road_m > 0 ? `写真の上では、看板が歩道の切れ目より道路側へ ≈ ${fmtM(v.over_road_m)} 出ています(要実測。道路に出ていれば道路占用許可が別に要ります)`
            : "写真の上では、看板は歩道の切れ目の内側に収まっています(境界の位置は実測か道路台帳で確認)。";
        }
      };
      frame.addEventListener("click", (ev) => {
        const next = order.find((t) => st.taps[t] === undefined);
        if (!next) return;
        const rc = frame.getBoundingClientRect();
        const x = Math.min(1, Math.max(0, (ev.clientX - rc.left) / rc.width)), y = Math.min(1, Math.max(0, (ev.clientY - rc.top) / rc.height));
        st.taps[next] = TAP[next].axis === "x" ? x : TAP[next].axis === "y" ? y : { x, y };
        draw();
      });
      const reset = el("button", { type: "button", class: "btn ghost", text: "置き直す", onclick: () => { st.taps = {}; draw(); } });
      setTimeout(draw, 0);
      return el("div", { class: "tap-panel" }, el("h3", { text: "歩道の切れ目を置く(人が置く)" }), guide, result, reset);
    }

    function estimateCard(res) {
      const e = res.estimate, j = res.judgement;
      const m = (mm) => (mm / 1000).toFixed(2).replace(/\.?0+$/, "");
      const refs = (e.references || []).filter((r) => r.name);
      const shots = (e.photos || []).filter((p) => st.urls[p.photo]);
      const figs = shots.map((p) => boxFigure(p, st.urls[p.photo], shots.length > 1, { scan: true }));
      const main = shots.findIndex((p) => p.sign_box);
      const tp = main >= 0 ? tapPanel(figs[main], shots[main]) : null;
      const cands = (e.kind_candidates || []).filter((c) => byId[c.kind]);
      return el("section", { class: "card owner-result owner-estimate", "aria-live": "polite" },
        el("h2", { text: "写真から推定した大きさ" }),
        figs.length ? el("div", { class: "est-figs" }, figs) : null,
        figs.length ? el("p", { class: "sub est-legend" }, el("span", { class: "key sign" }), "看板 ",
          shots.some((p) => p.wall_box) ? [el("span", { class: "key wall" }), "壁面 "] : null, el("span", { class: "key ref" }), "物差しにした物。",
          "点線の枠が看板とずれている時は、推定も外れています。スケールで測って欄に入れてください(要実測)。") : null,
        st.kind === "unknown" ? el("div", { class: "cands" }, el("h3", { text: "種類の候補(AI)。選んで確定してください" }),
          cands.length ? cands.map((c) => el("button", { type: "button", class: "btn ghost cand", onclick: () => {
            pickKind(c.kind, true);
            estOut.querySelector(".cands").replaceChildren(el("p", { class: "sub", text: `種類を「${byId[c.kind].label}」に確定しました。${c.kind === "wall" ? "壁面の面積は欄に入れてください。" : ""}${c.kind === "projecting" || c.kind === "freestanding" ? "線を置くには、もう一度「写真から推定」を押してください。" : ""}` }));
          } }, icon(c.kind), byId[c.kind].label, el("span", { class: "sub", text: c.why ? `(${c.why})` : "" })))
            : el("p", { class: "sub", text: "候補が返りませんでした。上の ① で種類を選んでください。" })) : null,
        tp,
        el("p", { class: "est-size" }, `W≈${m(e.w_mm)} m × H≈${m(e.h_mm)} m(±${e.w_pct}% / ±${e.h_pct}%)、面積≈${e.area_m2.toFixed(1)} ㎡`),
        e.wall ? el("p", { class: "sub", text: `壁面(緑の枠)≈ ${m(e.wall.w_mm)} m × ${m(e.wall.h_mm)} m ≈ ${e.wall.area_m2.toFixed(1)} ㎡(${e.wall.area_range[0].toFixed(0)}〜${e.wall.area_range[1].toFixed(0)} ㎡)。壁面率 ≈ ${(e.area_m2 / e.wall.area_m2 * 100).toFixed(1)}%` }) : null,
        el("p", { class: "sub", text: `範囲: 幅 ${m(e.w_range[0])}〜${m(e.w_range[1])} m、高さ ${m(e.h_range[0])}〜${m(e.h_range[1])} m。信頼度: ${{ high: "高", mid: "中", low: "低" }[e.confidence]}` }),
        refs.length ? el("p", { class: "sub" }, "物差しにした物: " + refs.map((r) => r.name).join("、")) : el("p", { class: "sub", text: "物差しにできる物が見つかりませんでした。" }),
        e.read_from_text ? el("p", { class: "sub", text: "写真の中の文字から読んだ数値を使っています。" }) : null,
        e.notes ? el("p", { class: "sub", text: e.notes }) : null,
        el("p", { class: "sub", text: `幅・高さ${e.wall ? "・壁面の面積" : ""}の欄に入れました。下の「調べる」で、種類ごとの項目を判定します(撮影後に判定)。` }),
        j && j.permit ? el("p", { class: "sub" }, "推定の範囲の両端での許可の見込み: ", KK.permitChip(j.permit)) : null);
    }

    const setFile = (i, f) => {
      st.files[i] = f || null;
      preview.replaceChildren(...st.files.filter(Boolean).map((x) => el("img", { src: URL.createObjectURL(x), alt: "選んだ写真(この端末の中だけに表示)" })));
      estRow.hidden = !st.files[0];
      estOut.replaceChildren(); st.est = null; st.taps = {};
    };
    const target = el("input", { id: "owner-target", type: "text", placeholder: "例 入口上の横長の看板(任意)" });
    const estBtn = el("button", { type: "button", class: "btn block", text: "写真から推定", disabled: !canEstimate, onclick: async () => {
      estMsg.hidden = true; estOut.replaceChildren(); out.replaceChildren();
      const address = val("owner-address");
      if (!address) { estMsg.hidden = false; estMsg.textContent = "住所を入れてください(③ の欄)。許可の要否を調べるのに使います。"; return; }
      estBtn.disabled = true; estBtn.textContent = "推定しています…";
      try {
        const picked = st.files.filter(Boolean);
        const photos = await Promise.all(picked.map((f) => shrink(f)));
        const height = numOr("owner-height");
        const res = await KK.api("/api/owner/estimate", { photos, address, kind: st.kind, target_name: target.value.trim() || null, height_m: height > 0 ? height : null });
        st.est = res.estimate; st.urls = picked.map((f) => URL.createObjectURL(f)); st.taps = {};
        document.getElementById("owner-w").value = (res.estimate.w_mm / 1000).toFixed(2);
        document.getElementById("owner-h").value = (res.estimate.h_mm / 1000).toFixed(2);
        if (res.estimate.wall) setNum("owner-wall-area", res.estimate.wall.area_m2);
        st.fromEst = { w: val("owner-w"), h: val("owner-h"), low: res.judgement.area_low, high: res.judgement.area_high };
        refreshArea();
        estOut.replaceChildren(estimateCard(res));
        estOut.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (e) { estMsg.hidden = false; estMsg.textContent = e.message; }
      estBtn.textContent = "写真から推定"; estBtn.disabled = !canEstimate;
    } });
    const estRow = el("div", { class: "est-row", hidden: true },
      el("label", { class: "txt", for: "owner-target" }, el("span", { text: "どの看板か(複数写る時)" }), target),
      estBtn,
      canEstimate ? null : el("p", { class: "sub", text: "写真からの推定は使えません: ANTHROPIC_API_KEY(環境変数か .env)が設定されていません。寸法は手で入れてください。" }),
      estMsg);
    const area = el("output", { id: "owner-area", class: "area", text: "—" });
    const refreshArea = () => {
      const w = parseFloat(val("owner-w")), h = parseFloat(val("owner-h"));
      area.textContent = w > 0 && h > 0 ? `${(w * h).toFixed(2)} ㎡` : "—";
    };
    const submit = el("button", { type: "button", class: "btn block", text: "調べる(撮影後に判定)", onclick: async () => {
      msg.hidden = true; out.replaceChildren();
      const w = parseFloat(val("owner-w")), h = parseFloat(val("owner-h")), address = val("owner-address");
      if (!address) { msg.hidden = false; msg.textContent = "住所を入れてください(LINE の写真には位置が残らないので、手入力です)。"; return; }
      if (!(w > 0 && h > 0)) { msg.hidden = false; msg.textContent = "看板の幅と高さを m で入れてください。"; return; }
      const height = numOr("owner-height");
      submit.disabled = true;
      try {
        const kind = st.kind && st.kind !== "unknown" ? st.kind : null;
        const q = { address, area_m2: Math.round(w * h * 100) / 100, height_m: height > 0 ? height : null, kind };
        if (kind === "wall") Object.assign(q, { wall_area_m2: numOr("owner-wall-area"), eave_height_m: numOr("owner-eave") });
        if (kind === "projecting") Object.assign(q, { protrusion_wall_m: numOr("owner-pw"), protrusion_road_m: numOr("owner-pr"),
          ground_clearance_m: numOr("owner-gc"), wall_height_m: numOr("owner-wallh"), over: val("owner-over") || null, road_manager: val("owner-mgr") || null });
        // 0 以下は「未入力」扱い(道路境界からの出幅だけは負= 境界の内側を取る)
        for (const k of ["wall_area_m2", "eave_height_m", "protrusion_wall_m", "ground_clearance_m", "wall_height_m"]) if (q[k] !== undefined && q[k] !== null && q[k] <= 0) q[k] = null;
        const fe = st.fromEst;
        if (fe && fe.w === val("owner-w") && fe.h === val("owner-h") && fe.low < fe.high) { q.area_low_m2 = fe.low; q.area_high_m2 = fe.high; }
        const r = await KK.api("/api/owner/judge", q);
        out.replaceChildren(resultCard(r, { address, w, h }, kind ? byId[kind].label : null));
        out.scrollIntoView({ behavior: "smooth", block: "start" });
      } catch (e) { msg.hidden = false; msg.textContent = e.message; }
      submit.disabled = false;
    } });

    KK.setView(
      el("div", { class: "page-head" }, el("div", {}, el("div", { class: "eyebrow", text: "種類を選んで、撮って、撮影後に判定" }), el("h1", { text: "オーナー" }))),
      el("div", { class: "owner" },
        el("section", { class: "card step" }, el("h2", { text: "① 看板の種類を選ぶ" }), el("div", { class: "kind-grid" }, kindBtns),
          el("p", { class: "sub", text: "種類で、撮り方と調べる項目が変わります。分からない時は「分からない」で、写真から候補を出します。" })),
        el("section", { class: "card step", id: "owner-step2" }, el("h2", { text: "② 撮る" }), shootBox,
          el("label", { class: "btn ghost block pick", for: "owner-photo" }, "写真を撮る・選ぶ"),
          el("input", { id: "owner-photo", class: "visually-hidden", type: "file", accept: "image/*", capture: "environment", onchange: (e) => setFile(0, e.target.files[0]) }),
          el("label", { class: "pick2", for: "owner-photo2" }, "もう1枚(別の角度、任意)"),
          el("input", { id: "owner-photo2", class: "visually-hidden", type: "file", accept: "image/*", onchange: (e) => setFile(1, e.target.files[0]) }),
          preview,
          el("p", { class: "sub", text: "写真は「写真から推定」を押した時だけ、寸法の推定のために AI(Claude)へ送ります。保存はしません。押さなければ送りません。" })),
        el("section", { class: "card step" }, el("h2", { text: "③ 看板の場所" }),
          el("label", { class: "txt", for: "owner-address" }, el("span", { text: "住所" }),
            el("input", { id: "owner-address", type: "text", autocomplete: "street-address", placeholder: "例 東京都豊島区○○町1-2-3" })),
          el("p", { class: "sub", text: "LINE で送った写真には位置が残らないので、住所は手で入れます。" })),
        el("section", { class: "card step" }, el("h2", { text: "④ 看板の大きさ" }), estRow,
          el("div", { class: "dims" },
            el("label", { class: "num", for: "owner-w" }, el("span", { text: "幅(m)" }), el("input", { id: "owner-w", type: "number", inputmode: "decimal", min: "0", step: "0.01", placeholder: "1.95" })),
            el("span", { class: "times", text: "×" }),
            el("label", { class: "num", for: "owner-h" }, el("span", { text: "高さ(m)" }), el("input", { id: "owner-h", type: "number", inputmode: "decimal", min: "0", step: "0.01", placeholder: "0.6" }))),
          el("div", { class: "area-row" }, el("span", { class: "k", text: "面積" }), area),
          el("label", { class: "num wide", for: "owner-height" }, el("span", { text: "地面から看板の上端までの高さ(m、分かれば)" }),
            el("input", { id: "owner-height", type: "number", inputmode: "decimal", min: "0", step: "0.1", placeholder: "4" })),
          kindInputs),
        estOut,
        el("section", { class: "card step" }, el("h2", { text: "⑤ 調べる" }), submit, msg),
        out));
    document.getElementById("owner-w").addEventListener("input", refreshArea);
    document.getElementById("owner-h").addEventListener("input", refreshArea);
    // 見本や撮影用に ?kind=wall などで種類を先に選んだ状態で開ける
    // 市民の報告(/report/)から来た時: 送った写真をこの端末の sessionStorage から引き継ぐ(サーバからは取らない)
    try {
      const ho = new URLSearchParams(location.search).get("from") === "report" && sessionStorage.getItem("kk.report.handoff");
      if (ho) {
        sessionStorage.removeItem("kk.report.handoff");
        const { photo, address } = JSON.parse(ho);
        const bin = atob(photo), u8 = new Uint8Array(bin.length);
        for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
        const f = new File([u8], "report.jpg", { type: "image/jpeg" });
        setFile(0, f);
        if (address) document.getElementById("owner-address").value = address;
        document.getElementById("owner-step2").prepend(el("p", { class: "notice", id: "owner-handoff", text: "報告で送った写真を引き継ぎました。① で種類を選んでから「写真から推定」を押してください。住所は手で入れてください。" }));
      }
    } catch (_) { /* 引き継げなければ普通に始める */ }
    const qk = new URLSearchParams(location.search).get("kind");
    if (qk && byId[qk]) pickKind(qk);
  };
})();
