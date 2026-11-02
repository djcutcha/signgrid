"""条例+施行規則の原文から、Claude に規則を JSON で抽出させる(第1版・検算前)。

流れ:
  1. data/ordinances/ の 条例・施行規則 テキストを読み、Web ページ由来のごみ(目次の二重掲載・
     ナビ・内部ID)を落とす(clean_text)。
  2. 自治体ごとに 1 回(失敗時 +1 回)の呼び出しで、schema.py の形の JSON を作らせる。
     原文は 条例+施行規則 を合わせて 3〜10 万字なので、章の切り出しはせず全文を送る
     (切り出しで規則の根拠条文を落とすより、全文を送って引用させる方が安全。費用は自治体あたり数十円)。
  3. 返ってきた引用(quote)が原文にそのまま在るかを機械で照合し、quote_verified を付ける。
  4. data/ordinances/rules_<自治体>.json に保存。status は「AI抽出の第1版(検算前)」。

鍵は環境変数 ANTHROPIC_API_KEY(リポジトリ直下の .env も読む)。サンプリングパラメータは送らない。
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path

from . import schema as S

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data" / "ordinances"

# キー -> (条例ファイル, 施行規則ファイル)
ORDINANCE_FILES: dict[str, tuple[str, str]] = {
    "東京都": ("東京都.txt", "東京都_屋外広告物条例施行規則.txt"),
    "埼玉県": ("埼玉県.txt", "埼玉県_屋外広告物条例施行規則.txt"),
    "越谷市": ("越谷市.txt", "越谷市_屋外広告物条例施行規則.txt"),
    "さいたま市": ("さいたま市.txt", "さいたま市_屋外広告物条例施行規則.txt"),
}

DEFAULT_MODEL = "claude-sonnet-5-5"
DEFAULT_EFFORT = "medium"
MAX_TOKENS = 32000  # 思考トークンを含む上限。長いのでストリーミングで受ける
BUDGET_USD = 3.0    # 1回の作業での上限(本線の指示)
PRICES = {  # 1M トークンあたり USD (入力, 出力)。claude-api スキルの価格表(2026-10-06)
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-haiku-5-5": (0.10, 0.50),
}
JST = timezone(timedelta(hours=9))


def rules_path(key: str, data_dir: Path | None = None) -> Path:
    return (data_dir or DATA_DIR) / f"rules_{key}.json"


# ---------- 原文 ----------

_JUNK_LINE = re.compile(r'iPadLoadStyle|id="primary"|^e\d{9}$|^\s*\d+/\d+\s*$')


def clean_text(raw: str) -> str:
    """e-Reikinet 等の保存ページ由来のごみを落とす。条文本体は変えない。

    - 「条項目次」以降(本文の二重掲載・ナビ・内部ID)を捨てる(無い自治体はそのまま)
    - スタイル読込行・内部ID・ページ番号行を捨てる
    - 連続する空行を1つにする
    """
    i = raw.find("\n条項目次")
    if i != -1:
        raw = raw[:i]
    lines = [ln.rstrip() for ln in raw.splitlines() if not _JUNK_LINE.search(ln)]
    out, blank = [], False
    for ln in lines:
        if not ln.strip():
            if not blank:
                out.append("")
            blank = True
        else:
            out.append(ln)
            blank = False
    return "\n".join(out).strip()


def load_texts(key: str, data_dir: Path | None = None) -> dict[str, str]:
    """{"条例": 清掃済み本文, "施行規則": 清掃済み本文}"""
    d = data_dir or DATA_DIR
    ord_f, reg_f = ORDINANCE_FILES[key]
    return {"条例": clean_text((d / ord_f).read_text(encoding="utf-8")),
            "施行規則": clean_text((d / reg_f).read_text(encoding="utf-8"))}


def manifest(data_dir: Path | None = None) -> dict[str, dict]:
    """MANIFEST.tsv(file, bytes, mtime) -> {file: {bytes, mtime}}"""
    p = (data_dir or DATA_DIR) / "MANIFEST.tsv"
    out = {}
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines()[1:]:
            parts = ln.split("\t")
            if len(parts) >= 3:
                out[parts[0]] = {"bytes": int(parts[1]), "mtime": parts[2]}
    return out


# ---------- プロンプト ----------

SYSTEM_PROMPT = f"""あなたは屋外広告物条例の読み取り担当です。与えられた条例と施行規則の原文だけを根拠に、看板の許可の要否を判定するための規則を JSON に抽出します。

守ること:
- 原文に書いてある値だけを使う。書いていない値は null、読み取れない項目は confidence を下げる。推測や一般知識で補わない。
- 数値は m または ㎡ または 年 に直す(「五メートル」→5、「十平方メートル」→10)。
- 引用 quote は原文から連続した100字以内をそのまま抜く(漢数字・かな・句読点を変えない。要約や言い換えをしない)。article は「第6条第1項第3号」のように条・項・号まで書く。source は「条例」か「施行規則」。
- 自家用広告物 = 自己の氏名・名称・店名・商標・事業内容を、自己の住所・事業所・営業所・作業場に表示する広告物。条例の文言が違えば、その文言に従う。
- 許可が要らない基準(面積・高さなど)が条例ではなく施行規則や別表にあるときは、そちらから取る。区域(許可区域・禁止区域・用途地域)や種類(壁面・屋上・突出し・広告塔等)で基準が違うときは standards を分けて複数件にする。
- 点検は、有資格者による安全点検と、その結果の報告(点検報告)の義務。許可の条件や管理者の設置と結びついて書かれている場合がある。義務の根拠が原文に見当たらなければ required は "unknown"(「無い」と断定するのは、義務が無いと原文が書いているときだけ)。
- 屋外広告業の登録・罰則・手数料・審議会の条文は対象外。
- 基準が表(別表)で、区域の行と面積の列が分かれて平文に崩れていることがある。表の構造を読み取り、どの区域に何㎡が対応するかを zone_labels で明示する。読み取りに自信が無い対応は confidence を 0.5 未満にして extraction_notes に書く。
- 面積欄が「一以外に掲げる地域 合計10平方メートル以下」のように残り全部を指す書き方のときは、残りの区域すべてを zone_labels に展開して数値を入れる(面積欄が空に見える区域でも、残り全部を指す行が別にあればその数値が適用される)。
- 自家用広告物の基準が条例・施行規則の複数の条や別表に分かれるときは、面積・高さ・地上高の数値基準をすべて拾う(細かい色彩・光源の条件は conditions に短く書く)。
- JSON の文字列の中に半角のダブルクォート(")を書かない。括弧は「」を使う。改行も入れない。
- 出力は JSON オブジェクト1つだけ。前後に説明文やコードフェンスを付けない。キーは次の形のとおり。

{{
  "rules": {S.RULES_SHAPE},
  "extraction_notes": ["読み取りに迷った点・原文だけでは決まらない点(最大8件、各100字以内)"]
}}

引用(citations の要素)の形: {S.CITATION_SHAPE}"""


def build_user_message(key: str, texts: dict[str, str]) -> str:
    return (f"対象: {key}の屋外広告物条例と施行規則。\n"
            "以下の2つの原文から、指定の JSON を作ってください。\n\n"
            f"<ordinance name=\"条例\">\n{texts['条例']}\n</ordinance>\n\n"
            f"<ordinance name=\"施行規則\">\n{texts['施行規則']}\n</ordinance>")


# ---------- 引用の機械照合 ----------

def _norm(s: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", s))


def iter_citations(node):
    """rules の中の citations 配列の要素(dict)を全部たどる。"""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "citations" and isinstance(v, list):
                for c in v:
                    if isinstance(c, dict):
                        yield c
            else:
                yield from iter_citations(v)
    elif isinstance(node, list):
        for v in node:
            yield from iter_citations(v)


def verify_citations(rules: dict, texts: dict[str, str]) -> dict:
    """各引用の quote が、その source の原文に(空白差を除いて)そのまま在るか。quote_verified を付ける。"""
    normed = {k: _norm(v) for k, v in texts.items()}
    total = ok = 0
    for c in iter_citations(rules):
        total += 1
        src = normed.get(c.get("source"))
        q = _norm(c.get("quote") or "")
        c["quote_verified"] = bool(src and q and q in src)
        ok += c["quote_verified"]
    return {"total": total, "verified": ok}


# ---------- API ----------

def _dotenv_candidates() -> list[Path]:
    # worktree では .env が無い。本体リポジトリ(git の共通ディレクトリの親)の .env も読む。コピーはしない。
    c = [REPO_ROOT / ".env"]
    try:
        import subprocess
        common = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                                capture_output=True, text=True, timeout=10).stdout.strip()
        if common:
            c.append(Path(common).parent / ".env")
    except Exception:
        pass
    return c


def load_dotenv(path: Path | None = None) -> None:
    for p in ([path] if path else _dotenv_candidates()):
        if not p.exists():
            continue
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and v and k not in os.environ:
                os.environ[k] = v


def make_client():
    import anthropic
    load_dotenv()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY がありません(環境変数か .env)")
    return anthropic.Anthropic()


def cost_usd(model: str, in_tok: int, out_tok: int) -> float:
    pi, po = PRICES.get(model, PRICES[DEFAULT_MODEL])
    return in_tok / 1e6 * pi + out_tok / 1e6 * po


def estimate_input_tokens(client, key: str, model: str = DEFAULT_MODEL) -> int:
    """送る入力のトークン数を count_tokens で測る(課金なし)。"""
    texts = load_texts(key)
    r = client.messages.count_tokens(
        model=model, system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": build_user_message(key, texts)}])
    return r.input_tokens


def parse_json_object(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    if not t.startswith("{"):
        a, b = t.find("{"), t.rfind("}")
        if a == -1 or b <= a:
            raise ValueError("JSON オブジェクトが見つからない")
        t = t[a:b + 1]
    obj = json.loads(t)
    if not isinstance(obj, dict):
        raise ValueError("JSON のトップがオブジェクトでない")
    return obj


def _call(client, model, effort, messages):
    with client.messages.stream(model=model, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
                                messages=messages, output_config={"effort": effort}) as st:
        return st.get_final_message()


def extract(key: str, *, client=None, model: str = DEFAULT_MODEL, effort: str = DEFAULT_EFFORT,
            data_dir: Path | None = None, max_attempts: int = 2) -> tuple[dict, dict]:
    """1自治体の規則を抽出して (保存用の dict, 使用量) を返す。保存は save_rules で。

    JSON が壊れている/スキーマ不適合なら、エラー文を添えてもう1回だけ作り直させる。
    """
    client = client or make_client()
    texts = load_texts(key, data_dir)
    messages = [{"role": "user", "content": build_user_message(key, texts)}]
    in_tok = out_tok = 0
    last_err = None
    attempt_errors: list[str] = []
    for attempt in range(max_attempts):
        resp = _call(client, model, effort, messages)
        in_tok += resp.usage.input_tokens
        out_tok += resp.usage.output_tokens
        if resp.stop_reason == "refusal":
            raise RuntimeError(f"モデルが応答を拒否した: {getattr(resp, 'stop_details', None)}")
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            if resp.stop_reason == "max_tokens":
                raise ValueError("出力が max_tokens で途切れた")
            obj = parse_json_object(text)
            errs = S.validate_rules(obj.get("rules"))
            if errs:
                raise ValueError("スキーマ不適合: " + " / ".join(errs[:12]))
            break
        except (ValueError, json.JSONDecodeError) as e:
            last_err = str(e)
            attempt_errors.append(last_err[:600])
            try:  # 失敗した生の出力は調査用に runs/(git 管理外)へ残す
                (REPO_ROOT / "runs").mkdir(exist_ok=True)
                (REPO_ROOT / "runs" / f"legal_failed_{key}_{attempt}.txt").write_text(text, encoding="utf-8")
            except OSError:
                pass
            messages = messages + [
                {"role": "assistant", "content": text or "(空)"},
                {"role": "user", "content": f"前の出力は使えません: {last_err}\n"
                                            "同じ形式で、修正した JSON オブジェクト1つだけをもう一度出力してください。"}]
    else:
        raise RuntimeError(f"{key}: 抽出に失敗({max_attempts}回): {last_err}")

    rules = obj["rules"]
    check = verify_citations(rules, texts)
    man = manifest(data_dir)
    ord_f, reg_f = ORDINANCE_FILES[key]
    doc = {
        "schema_version": S.SCHEMA_VERSION,
        "ordinance_key": key,
        "status": S.STATUS_FIRST_DRAFT,
        "model": model,
        "effort": effort,
        "extracted_at": datetime.now(JST).isoformat(timespec="seconds"),
        "sources": [{"role": role, "file": f, **man.get(f, {})}
                    for role, f in (("条例", ord_f), ("施行規則", reg_f))],
        "rules": rules,
        "extraction_notes": [str(n) for n in obj.get("extraction_notes", [])][:8],
        "citation_check": check,
    }
    usage = {"input_tokens": in_tok, "output_tokens": out_tok, "attempts": attempt + 1, "attempt_errors": attempt_errors,
             "usd": round(cost_usd(model, in_tok, out_tok), 4)}
    doc["usage"] = usage
    return doc, usage


def save_rules(doc: dict, data_dir: Path | None = None) -> Path:
    p = rules_path(doc["ordinance_key"], data_dir)
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return p


def load_rules(key: str, data_dir: Path | None = None) -> dict:
    p = rules_path(key, data_dir)
    if not p.exists():
        raise FileNotFoundError(f"{p} がない。先に `extract --municipality {key}` を実行する")
    return json.loads(p.read_text(encoding="utf-8"))
