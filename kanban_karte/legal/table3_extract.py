"""東京都 施行規則 別表第三(第十九条関係)だけを対象に、許可の基準のうち高さ・地上高・出幅の数値を再抽出する。

第1版は別表第二(自家用の面積)が中心で、別表第三(広告塔の高さ、突出し広告の地上高など)は展開されていなかった。
ここでは別表第三の本文だけを Claude に渡し、rules["permit_standards"] を作って rules_東京都.json に足す。
(1回の呼び出し、概算 $0.2。)

注意: 別表第三は『許可を受けるための基準』で、自家用の許可不要基準(別表第二)とは別。judge では
      許可不要かどうかの判定には使わず、寸法が基準を外れる可能性を注に出す。
"""
from __future__ import annotations

import json
import re

from . import rules_extract as RX
from . import schema as S

SECTION = "別表第三"
SECTION_END = "別表第四"

SYSTEM_PROMPT = f"""あなたは東京都屋外広告物条例施行規則の別表第三(第十九条関係。許可の基準)の読み取り担当です。与えられた別表第三の原文だけを根拠に、広告物の種類ごとの数値基準(高さ・道路面からの下端の高さ・出幅・表示面積)を JSON にします。

守ること:
- 原文に書いてある数値だけを使う。書いていない値は null。推測や一般知識で補わない。数値は m または ㎡ に直す(「三・五メートル」→3.5)。
- 種類(kind)は原文の見出しに沿う: 広告塔・広告板(土地に直接設置)/ 広告塔・広告板(屋上)/ 建築物の壁面を利用する広告物等 / 建築物から突出する形式の広告物等 / 電柱・街路灯柱を利用する広告物等 / 電車・自動車の外面を利用する広告物等 など。
- 同じ種類でも、条件(商業地域内だけ13m、歩道上と車道上で地上高が違う、木造と耐火構造で違う、用途地域で33m/52m)で値が変わるときは、条件ごとに別の項目にする。条件は when に短く書く。
- height_max_m = 地盤面(または基準面)からの上端の高さの上限。ground_clearance_min_m = 道路面から下端までの高さの下限。protrusion_max_m = 出幅の上限。area_max_m2 = 表示面積の上限。数値で表せない条件(光源・色彩・ネオン等)は conditions に短く(80字以内)。
- 自家用広告物にだけ効く特例(例: 条例第十三条第五号の広告物等の13m)は self_use_only を true にする。
- 引用 quote は原文から連続した100字以内をそのまま抜く(漢数字・かな・句読点を変えない)。article は「別表第三 一(一)1」のように書く。source は「施行規則」。
- JSON の文字列の中に半角のダブルクォート(")を書かない。括弧は「」を使う。改行も入れない。
- 出力は JSON オブジェクト1つだけ。前後に説明文やコードフェンスを付けない。

{{
  "permit_standards": [
    {{
      "kind": "種類",
      "when": "条件(無ければ null)",
      "self_use_only": true|false,
      "height_max_m": 数値|null,
      "ground_clearance_min_m": 数値|null,
      "protrusion_max_m": 数値|null,
      "area_max_m2": 数値|null,
      "conditions": "数値以外の条件(文)|null",
      "citations": [{S.CITATION_SHAPE}],
      "confidence": 0〜1
    }}
  ],
  "extraction_notes": ["読み取りに迷った点(最大6件、各100字以内)"]
}}"""


def table3_text(data_dir=None) -> str:
    """施行規則の清掃済み本文から 別表第三 の節だけを切り出す(次の『別表第四』の見出し行まで)。"""
    reg = RX.load_texts("東京都", data_dir)["施行規則"]
    lines = reg.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.strip() == SECTION and i + 1 < len(lines) and "第十九条関係" in lines[i + 1])
    end = next(i for i in range(start + 1, len(lines)) if lines[i].strip() == SECTION_END)
    return "\n".join(lines[start:end])


def extract_table3(*, client=None, model: str = RX.DEFAULT_MODEL, effort: str = RX.DEFAULT_EFFORT,
                   data_dir=None, max_attempts: int = 2) -> tuple[dict, dict]:
    """(permit_standards と notes を持つ dict, 使用量)。保存は apply_table3 で。"""
    client = client or RX.make_client()
    text = table3_text(data_dir)
    messages = [{"role": "user", "content": "対象: 東京都屋外広告物条例施行規則 別表第三。以下の原文から指定の JSON を作ってください。\n\n"
                                            f"<ordinance name=\"施行規則 別表第三\">\n{text}\n</ordinance>"}]
    in_tok = out_tok = 0
    last_err = None
    for attempt in range(max_attempts):
        with client.messages.stream(model=model, max_tokens=RX.MAX_TOKENS, system=SYSTEM_PROMPT, messages=messages,
                                    output_config={"effort": effort}) as st:
            resp = st.get_final_message()
        in_tok += resp.usage.input_tokens
        out_tok += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            raise RuntimeError("モデルが応答を拒否した")
        out_text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            if resp.stop_reason == "max_tokens":
                raise ValueError("出力が max_tokens で途切れた")
            obj = RX.parse_json_object(out_text)
            errs = S.validate_permit_standards(obj.get("permit_standards"))
            if errs:
                raise ValueError("スキーマ不適合: " + " / ".join(errs[:10]))
            break
        except (ValueError, json.JSONDecodeError) as e:
            last_err = str(e)
            messages = messages + [{"role": "assistant", "content": out_text or "(空)"},
                                   {"role": "user", "content": f"前の出力は使えません: {last_err}\n"
                                                               "同じ形式で、修正した JSON オブジェクト1つだけをもう一度出力してください。"}]
    else:
        raise RuntimeError(f"別表第三の抽出に失敗({max_attempts}回): {last_err}")
    check = RX.verify_citations({"permit_standards": obj["permit_standards"]}, {"施行規則": text, "条例": ""})
    usage = {"input_tokens": in_tok, "output_tokens": out_tok, "attempts": attempt + 1,
             "usd": round(RX.cost_usd(model, in_tok, out_tok), 4)}
    return {"permit_standards": obj["permit_standards"], "extraction_notes": [str(n) for n in obj.get("extraction_notes", [])][:6],
            "citation_check": check, "model": model}, usage


def apply_table3(result: dict, data_dir=None):
    """rules_東京都.json の rules に permit_standards を足し、extraction_notes に追記して保存する。"""
    doc = RX.load_rules("東京都", data_dir)
    doc["rules"]["permit_standards"] = result["permit_standards"]
    doc["table3_extraction"] = {"model": result["model"], "citation_check": result["citation_check"],
                                "notes": result["extraction_notes"]}
    return RX.save_rules(doc, data_dir)
