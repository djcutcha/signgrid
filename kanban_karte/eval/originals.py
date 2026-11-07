"""評価の看板を NAS の原本写真と突き合わせ、索引(TSV)を作る。

  python3 -m kanban_karte.eval.originals build --nas-dirs <フォルダ一覧.txt> --out <作業フォルダ>/originals_index.tsv

評価データの写真は報告書に貼られた縮小版(長辺 約500〜1000px)。同じ看板の原本(NAS)で判定し直して、
「見逃しの天井は写真の解像度か」を測るための対応づけ。索引は顧客のパスを含むので、リポジトリに置かない
(NAS 側の作業フォルダか、.gitignore 済みの data/private/ に置く)。

突き合わせ(看板ごと、判定に使う先頭 6 枚の貼り付け写真について):
  1. 年(inspection_date)→ 年フォルダ、現場名 → 会館(センター)フォルダ。無ければ対応なし
  2. 看板名がフォルダ名と部分一致する原本を先に探す(basis=name)。無ければ会館全体から探す(basis=phash)
  3. 探し方は画像照合: 貼り付けと原本を同じ長辺 256px に縮小し、32x32 グレーにして正規化相関を取る。
     しきい値(THRESHOLD)未満は対応なし。同じ画像が複数フォルダにあれば、看板名一致 → 長辺が大きい方を採る
貼り付け写真の一部だけ対応した看板は、対応した分だけを原本にする(n_matched < n_pasted で記録)。
"""
from __future__ import annotations

import argparse
import csv
import os
import pickle
import re
import sys
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from . import dataset

IMG_EXT = (".jpg", ".jpeg", ".png")
SIG_LONG = 256       # 照合の前に、貼り付けも原本も長辺をこれに縮小する
SIG_SIDE = 32        # さらに SIG_SIDE x SIG_SIDE のグレーにして正規化相関を取る
THRESHOLD = 0.90     # これ未満は対応なし(同じ写真なら 0.99 以上、同じ場所の別カットは 0.8 前後まで)
ASPECT_TOL = 0.15    # 縦横比の対数差がこれを超える組は照合しない
DUP_TOL = 0.005      # 最良との差がこれ以内なら同一画像の複製とみなして、名前一致・長辺で選ぶ
MAX_PHOTOS = 6       # 判定に使う写真の最大枚数(run_eval の --max-photos 既定と同じ)
COLUMNS = ["sign_id", "year", "folder", "originals", "basis", "similarity", "long_edge",
           "n_pasted", "n_matched", "reason"]
SOURCES = ("xlsx_karte", "xlsx_center")


# ---------- 顧客ごとの語彙(コードに持たない) ----------

@dataclass
class Vocab:
    """NAS のフォルダ名・現場名に出る顧客固有の語。リポジトリには置かず、data/private/originals.toml(未追跡)で与える。

      [originals]
      karte_strip = ["...", "..."]       # 会館名の鍵を作るとき取り除く語(長い方から)
      karte_year_dir = "...(\\d{4})"      # 年フォルダ名(空白除去後)にフルマッチする正規表現
      center_strip = ["...", "..."]      # センター名の鍵を作るとき取り除く語
      center_root = "..."                # センター側の根フォルダ名(空白除去後)
      center_root_within = "..."         # そのフォルダのパスに含まれる語
    """
    karte_strip: tuple[str, ...]
    karte_year_dir: str
    center_strip: tuple[str, ...]
    center_root: str
    center_root_within: str = ""

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Vocab":
        import tomllib
        p = Path(path or os.environ.get("KANBAN_KARTE_ORIGINALS") or dataset.REPO_ROOT / "data" / "private" / "originals.toml")
        if not p.exists():
            raise FileNotFoundError(f"{p} がありません(--vocab か data/private/originals.toml。書式は originals.Vocab の docstring)")
        with open(p, "rb") as f:
            d = tomllib.load(f)["originals"]
        return cls(tuple(d["karte_strip"]), d["karte_year_dir"], tuple(d["center_strip"]),
                   d["center_root"], d.get("center_root_within", ""))


# ---------- 名前の正規化 ----------

def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def squash(s: str) -> str:
    """空白(全角スペース・NBSP を含む)を全て除く。"""
    return "".join(c for c in s if not c.isspace())


def _key(s: str) -> str:
    return squash(unicodedata.normalize("NFKC", s))


def _strip_suffix(s: str, suffixes: tuple[str, ...]) -> str:
    for suf in suffixes:
        if s.endswith(suf) and len(s) > len(suf):
            return s[: -len(suf)]
    return s


def kaikan_key(name: str, v: "Vocab") -> str:
    """会館名の鍵。評価側の現場名(例 "<社名>会館 西丘")も NAS のフォルダ名(例 "10.西丘会館")も同じ鍵になる。"""
    s = re.sub(r"^\d+[.．]", "", unicodedata.normalize("NFKC", name).strip())
    s = squash(s)
    for w in v.karte_strip:
        s = s.replace(w, "")
    s = _strip_suffix(s, ("写真",))
    return _strip_suffix(s, ("会館", "営業所"))


def center_key(name: str, v: "Vocab") -> str:
    """営業センター名の鍵(評価側の現場名から。NAS 側は鍵がパスに含まれるかで見る)。"""
    s = _key(name)
    for w in v.center_strip:
        s = s.replace(w, "")
    s = _strip_suffix(s, ("センター",))
    return _strip_suffix(s, ("営業",))


_LEADING_NUM = re.compile(r"^[0-9.．、,，\-_]+")


def sign_key(name: str) -> str:
    """看板名の鍵(先頭の番号を除く。NFKC なので丸数字・全角数字も数字になる)。"""
    return _LEADING_NUM.sub("", _key(name)).lower()


def names_match(sign_name: str, folder_name: str) -> bool:
    a, b = sign_key(sign_name), sign_key(folder_name)
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 2 and (a in b or b in a))


# ---------- 画像の特徴 ----------

def signature(path: str) -> tuple[np.ndarray, float, int]:
    """(正規化相関用のベクトル, 縦横比, 原本の長辺)。向きは EXIF で直す。"""
    with Image.open(path) as im:
        long_edge = max(im.size)
        if im.format == "JPEG":
            im.draft("L", (SIG_LONG * 2, SIG_LONG * 2))  # 縮小デコードで速くする(長辺は上で取得済み)
        g = ImageOps.exif_transpose(im).convert("L")
    g.thumbnail((SIG_LONG, SIG_LONG), Image.BILINEAR)
    aspect = g.width / g.height
    v = np.asarray(g.resize((SIG_SIDE, SIG_SIDE), Image.BOX), dtype=np.float32).ravel()
    v -= v.mean()
    norm = float(np.linalg.norm(v))
    return (v / norm if norm > 1e-6 else np.zeros_like(v)), aspect, long_edge


class SigCache:
    """path -> signature。NAS の読み込みが遅いので、作業フォルダに pickle で残して再実行を速くする。"""

    def __init__(self, path: Path | None = None, workers: int = 8):
        self.path = path
        self.workers = workers
        self.d: dict[str, tuple[np.ndarray, float, int]] = {}
        if path and path.exists():
            with open(path, "rb") as f:
                self.d = pickle.load(f)

    def get_many(self, paths: list[str]) -> None:
        todo = [p for p in dict.fromkeys(paths) if p not in self.d]
        if not todo:
            return

        def one(p):
            try:
                return p, signature(p)
            except Exception:
                return p, None  # 壊れた画像・読めない形式は候補から外す

        with ThreadPoolExecutor(self.workers) as ex:
            for k, (p, s) in enumerate(ex.map(one, todo), 1):
                self.d[p] = s
                if k % 500 == 0:
                    print(f"  特徴量 {k}/{len(todo)}", file=sys.stderr)
        if self.path:
            with open(self.path, "wb") as f:
                pickle.dump(self.d, f)


@dataclass
class Pool:
    """原本の候補の集まり。"""
    paths: list[str]
    folders: list[str]   # 画像の親フォルダ名(NFC)
    mat: np.ndarray      # (N, SIG_SIDE**2)
    aspect: np.ndarray
    long_edge: np.ndarray

    @classmethod
    def build(cls, paths: list[str], cache: SigCache) -> "Pool":
        cache.get_many(paths)
        ok = [p for p in paths if cache.d.get(p) is not None]
        sig = [cache.d[p] for p in ok]
        mat = np.stack([s[0] for s in sig]) if sig else np.zeros((0, SIG_SIDE ** 2), np.float32)
        return cls(ok, [nfc(os.path.basename(os.path.dirname(p))) for p in ok], mat,
                   np.array([s[1] for s in sig]), np.array([s[2] for s in sig]))

    def sims(self, vec: np.ndarray, aspect: float) -> np.ndarray:
        s = self.mat @ vec if len(self.paths) else np.zeros(0)
        bad = np.abs(np.log(self.aspect / aspect)) > ASPECT_TOL if len(self.paths) else np.zeros(0, bool)
        return np.where(bad, -1.0, s)


def match_photo(pool: Pool, vec: np.ndarray, aspect: float, name_mask: np.ndarray,
                threshold: float = THRESHOLD) -> tuple[int, float, str] | None:
    """貼り付け写真 1 枚に対する原本。(候補の番号, 類似度, "name"|"phash")。しきい値未満は None。"""
    if not len(pool.paths):
        return None
    s = pool.sims(vec, aspect)
    via, region = "name", s.copy()
    region[~name_mask] = -1.0
    if region.max() < threshold:
        via, region = "phash", s
    best = float(region.max())
    if best < threshold:
        return None
    dup = np.flatnonzero(region >= best - DUP_TOL)
    pick = min(dup, key=lambda i: (not name_mask[i], -int(pool.long_edge[i]), pool.paths[i]))
    return int(pick), float(region[pick]), via


def match_sign(pasted: list[str], sign_name: str, pool: Pool, cache: SigCache,
               threshold: float = THRESHOLD) -> dict:
    """看板 1 基の対応。貼り付け写真の先頭 MAX_PHOTOS 枚を照合する。"""
    pasted = pasted[:MAX_PHOTOS]
    name_mask = np.array([names_match(sign_name, f) for f in pool.folders], dtype=bool)
    cache.get_many(pasted)
    hits, sims, vias = [], [], []
    for p in pasted:
        sg = cache.d.get(p)
        if sg is None:
            continue
        m = match_photo(pool, sg[0], sg[1], name_mask, threshold)
        if m is None:
            continue
        i, sim, via = m
        if i not in hits:  # 貼り付けの 2 枚が同じ原本に当たったら 1 枚にまとめる
            hits.append(i)
            sims.append(sim)
            vias.append(via)
    return {"idx": hits, "sims": sims, "vias": vias, "n_pasted": len(pasted)}


# ---------- NAS の走査 ----------

def read_dirs(path: str | os.PathLike) -> list[str]:
    """フォルダ一覧(1 行 1 パス、バイト列そのまま)。"""
    return Path(path).read_bytes().decode("utf-8").splitlines()


def list_images(root: str) -> list[str]:
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if not d.startswith((".", "__MACOSX")))
        out += [os.path.join(dp, f) for f in sorted(fn)
                if f.lower().endswith(IMG_EXT) and not f.startswith(".")]
    return out


def karte_year_dirs(dirs: list[str], v: "Vocab") -> dict[int, list[str]]:
    """年フォルダ(例 "<社名>点検 2024")。年 -> パスのリスト。"""
    out: dict[int, list[str]] = {}
    pat = re.compile(v.karte_year_dir)
    for d in dirs:
        m = pat.fullmatch(squash(nfc(os.path.basename(d))))
        if m:
            out.setdefault(int(m.group(1)), []).append(d)
    return out


def karte_folders(year_dirs: list[str], v: "Vocab") -> dict[str, list[str]]:
    """年フォルダ(複数可)の直下の会館フォルダ。会館の鍵 -> パスのリスト。"""
    out: dict[str, list[str]] = {}
    for yd in year_dirs:
        for c in sorted(os.listdir(yd)):
            p = os.path.join(yd, c)
            if os.path.isdir(p) and not c.startswith("."):
                out.setdefault(kaikan_key(c, v), []).append(p)
    return out


def center_root(dirs: list[str], v: "Vocab") -> str | None:
    for d in dirs:
        if squash(nfc(os.path.basename(d))) == v.center_root and v.center_root_within in d:
            return d
    return None


# ---------- 索引の作成 ----------

def _row(sign_id: str, year: str, folder: str, r: dict | None, pool: Pool | None, reason: str = "") -> dict:
    if not r or not r["idx"]:
        return {"sign_id": sign_id, "year": year, "folder": folder, "originals": "", "basis": "none",
                "similarity": "", "long_edge": "", "n_pasted": r["n_pasted"] if r else 0,
                "n_matched": 0, "reason": reason or "below_threshold"}
    edges = sorted(int(pool.long_edge[i]) for i in r["idx"])
    return {"sign_id": sign_id, "year": year, "folder": folder,
            "originals": ";".join(pool.paths[i] for i in r["idx"]),
            "basis": "name" if all(v == "name" for v in r["vias"]) else "phash",
            "similarity": f"{min(r['sims']):.3f}", "long_edge": edges[len(edges) // 2],
            "n_pasted": r["n_pasted"], "n_matched": len(r["idx"]), "reason": ""}


def build_index(items: list[dataset.EvalItem], dirs: list[str], cache: SigCache, v: "Vocab",
                threshold: float = THRESHOLD) -> list[dict]:
    rows: list[dict] = []
    years = karte_year_dirs(dirs, v)
    kfold = {y: karte_folders(ds, v) for y, ds in years.items()}
    karte_pools: dict[tuple[int, str], Pool] = {}
    croot = center_root(dirs, v)
    center_paths = list_images(croot) if croot else []
    center_rel = [_key(os.path.relpath(p, croot)) for p in center_paths]
    center_pools: dict[str, Pool] = {}

    for it in items:
        if it.source == "xlsx_karte":
            y = (it.meta.get("inspection_date") or "")[:4]
            key = kaikan_key(it.meta["site"], v)
            if not it.photo_paths:
                rows.append(_row(it.sign_id, y, "", None, None, "no_pasted_photos"))
            elif not y.isdigit() or int(y) not in kfold:
                rows.append(_row(it.sign_id, y, "", None, None, "no_year_folder"))
            elif key not in kfold[int(y)]:
                rows.append(_row(it.sign_id, y, "", None, None, "no_site_folder"))
            else:
                pk = (int(y), key)
                if pk not in karte_pools:
                    imgs = [p for d in kfold[int(y)][key] for p in list_images(d)]
                    karte_pools[pk] = Pool.build(imgs, cache)
                pool = karte_pools[pk]
                r = match_sign(it.photo_paths, it.meta.get("sign_name", ""), pool, cache, threshold)
                rows.append(_row(it.sign_id, y, nfc(os.path.basename(kfold[int(y)][key][0])), r, pool))
        elif it.source == "xlsx_center":
            y = (it.meta.get("inspection_date") or "")[:4]
            key = center_key(it.meta["site"], v)
            if not it.photo_paths:
                rows.append(_row(it.sign_id, y, "", None, None, "no_pasted_photos"))
            elif not croot:
                rows.append(_row(it.sign_id, y, "", None, None, "no_site_folder"))
            else:
                if key not in center_pools:
                    center_pools[key] = Pool.build(
                        [p for p, rel in zip(center_paths, center_rel) if key and key in rel], cache)
                pool = center_pools[key]
                if not pool.paths:
                    rows.append(_row(it.sign_id, y, "", None, None, "no_site_folder"))
                else:
                    r = match_sign(it.photo_paths, it.meta.get("sign_name", ""), pool, cache, threshold)
                    rows.append(_row(it.sign_id, y, key, r, pool))
    return rows


def write_index(rows: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, COLUMNS, delimiter="\t", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def read_index(path: str | os.PathLike) -> dict[str, dict]:
    """sign_id -> 行。originals は list[str] にして返す。"""
    out = {}
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f, delimiter="\t"):
            r["originals"] = [p for p in r["originals"].split(";") if p]
            out[r["sign_id"]] = r
    return out


def apply_override(items: list[dataset.EvalItem], index: dict[str, dict]) -> list[dataset.EvalItem]:
    """索引で対応した看板の photo_paths を原本に差し替える。対応のない看板(索引に無い・原本なし)は
    photo_paths を空にする(run_eval が「写真なし」として除外し、母数に数えない)。貼り付け写真は meta に残す。"""
    out = []
    for it in items:
        row = index.get(it.sign_id)
        paths = row["originals"] if row else []
        out.append(replace(it, photo_paths=paths,
                           meta={**it.meta, "pasted_photo_paths": it.photo_paths,
                                 "original_photos": bool(paths)}))
    return out


# ---------- 比較・対応率の報告(sign_id と基数だけ。現場名・看板名・パスは出さない) ----------

def _pct(x) -> str:
    return "n/a" if x is None else f"{x * 100:.1f}%"


def _group(row: dict, source: str) -> str:
    if source == "xlsx_center":
        return "客先A"
    y = row.get("year") or "年不明"
    return f"客先B {y}" + ("(原本 800px)" if y == "2026" else "")


def _pairs(items_by_id: dict, recs: dict, ids: list[str]) -> list[tuple]:
    from .judge import Prediction
    out = []
    for sid in ids:
        r = recs[sid]
        out.append((items_by_id[sid], Prediction(r.get("rank_pred"), r.get("item_judgments_pred"))))
    return out


def _metrics_row(label: str, m: dict, cost: float | None) -> str:
    b = m["action_breakdown"]
    it = m["items"]
    return (f"| {label} | {m['n']} | {_pct(m['exact_rate'])} | {_pct(m['within1_rate'])} | "
            f"{m['action_needed_missed']}/{m['action_needed_true']} | {b['missed']['c']}/{b['counts']['c']} | "
            f"{m['false_alarm']}/{m['not_action_true']} | {_pct(it['item_agreement_rate'])} | "
            f"{'-' if cost is None else f'${cost:.2f}'} |")


def compare(items: list[dataset.EvalItem], index: dict[str, dict], first: dict, runs: list[dict],
            reason_chars: int = 40) -> tuple[str, list[dict]]:
    """貼り付け写真(第 1 パス)と原本写真の判定を、原本で判定した看板に揃えて比べる。
    戻り値 = (Markdown, 救えた看板の行)。"""
    from . import metrics
    by_id = {i.sign_id: i for i in items}
    frecs = {r["sign_id"]: r for r in first["records"]}
    orecs = {r["sign_id"]: r for r in (x for run in runs for x in run["records"])}
    ids = sorted(i for i in orecs if i in frecs and i in by_id)
    groups: dict[str, list[str]] = {}
    for sid in ids:
        groups.setdefault(_group(index.get(sid, {}), by_id[sid].source), []).append(sid)

    def cost(recs, sids):
        cs = [(recs[s].get("usage") or {}).get("cost_usd") for s in sids]
        return None if any(c is None for c in cs) else sum(cs)

    L = ["| 区分 | 写真 | 基数 | 完全一致 | ±1 | 要対応の見逃し | 見逃し(写真可) | 誤警報 | 項目一致 | 費用 |",
         "|---|---|---|---|---|---|---|---|---|---|"]

    def block(label, sids):
        for tag, recs in (("貼り付け", frecs), ("原本", orecs)):
            L.append(_metrics_row(f"{label} | {tag}", metrics.compute(_pairs(by_id, recs, sids)), cost(recs, sids)))

    for g in sorted(groups):
        block(g, groups[g])
    no2026 = [s for g in sorted(groups) if "2026" not in g for s in groups[g]]
    block("全体(客先B 2026 を除く)", no2026)
    block("全体", ids)
    full = [s for s in no2026 if index[s]["n_matched"] == index[s]["n_pasted"]]
    block("うち貼り付け写真が全て対応(2026 除く)", full)

    rescued, lost = [], 0
    for sid in ids:
        t = by_id[sid].rank_true
        if t not in metrics.ACTION:
            continue
        p0, p1 = frecs[sid].get("rank_pred"), orecs[sid].get("rank_pred")
        if p0 not in metrics.ACTION and p1 in metrics.ACTION:
            rs = orecs[sid].get("reasons") or []
            rescued.append({"sign_id": sid, "group": _group(index[sid], by_id[sid].source), "rank_true": t,
                            "pasted_pred": p0, "original_pred": p1,
                            "reason": (rs[0] if rs else "")[:reason_chars]})
        elif p0 in metrics.ACTION and p1 not in metrics.ACTION:
            lost += 1
    missed_both = sum(1 for sid in ids if by_id[sid].rank_true in metrics.ACTION
                      and frecs[sid].get("rank_pred") not in metrics.ACTION
                      and orecs[sid].get("rank_pred") not in metrics.ACTION)
    L += ["", f"- 貼り付けで見逃し → 原本で救えた: {len(rescued)} 基 / 逆(貼り付けで拾い、原本で見逃し): {lost} 基 / "
              f"どちらでも見逃し: {missed_both} 基",
          f"- 費用: 原本 {_pct(None) if cost(orecs, ids) is None else '$%.2f' % cost(orecs, ids)}、"
          f"同じ看板の貼り付け写真(第 1 パス) {_pct(None) if cost(frecs, ids) is None else '$%.2f' % cost(frecs, ids)}"]
    return "\n".join(L), rescued


def coverage(items: list[dataset.EvalItem], index: dict[str, dict]) -> str:
    """評価用の全看板(紙・客先A・B)について、対応あり/なし/対象外の基数と、対応なしの理由。"""
    from collections import Counter
    rows = {}
    for it in items:
        if it.split != "eval" or it.rank_true is None:
            continue
        if it.source == "paper_scan":
            g = "紙(原本が NAS に無い)"
            st = "対象外"
        else:
            r = index.get(it.sign_id)
            g = _group(r or {"year": (it.meta.get("inspection_date") or "")[:4]}, it.source)
            st = "対応あり" if r and r["originals"] else "対応なし"
            if st == "対応なし":
                st += ":" + (r["reason"] if r else "not_indexed")
        rows.setdefault(g, Counter())[st] += 1
    L = ["| 区分 | 評価基数 | 対応あり | 対応なし | 対応なしの理由 |", "|---|---|---|---|---|"]
    tot = Counter()
    for g in sorted(rows):
        c = rows[g]
        n = sum(c.values())
        yes = c["対応あり"]
        no = sum(v for k, v in c.items() if k.startswith("対応なし"))
        why = "、".join(f"{k.split(':', 1)[1]} {v}" for k, v in sorted(c.items()) if k.startswith("対応なし"))
        if g.startswith("紙"):
            L.append(f"| {g} | {n} | - | - | 対象外 {c['対象外']} |")
        else:
            L.append(f"| {g} | {n} | {yes} | {no} | {why or '-'} |")
        tot.update(c)
    return "\n".join(L)


# ---------- CLI ----------

def _eval_items(config: str | None, split: str) -> list[dataset.EvalItem]:
    items = dataset.load_all(config, SOURCES)
    return [i for i in items if i.rank_true is not None and (split == "all" or i.split == split)]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="索引を作る")
    b.add_argument("--nas-dirs", required=True, help="NAS のフォルダ一覧(1 行 1 パス)")
    b.add_argument("--out", required=True, help="索引 TSV の出力先(NAS 側の作業フォルダ)")
    b.add_argument("--sources-config", default=None)
    b.add_argument("--split", choices=["eval", "example", "all"], default="eval")
    b.add_argument("--threshold", type=float, default=THRESHOLD)
    b.add_argument("--workers", type=int, default=8)
    b.add_argument("--vocab", default=None, help="顧客固有の語の設定(既定 data/private/originals.toml)")
    c = sub.add_parser("compare", help="貼り付け写真と原本写真の判定結果を比べる(基数・率だけ出力)")
    c.add_argument("--index", required=True)
    c.add_argument("--first-pass", required=True, help="貼り付け写真での結果 JSON(全看板)")
    c.add_argument("--runs", nargs="+", required=True, help="原本写真での結果 JSON(複数可)")
    c.add_argument("--sources-config", default=None)
    c.add_argument("--rescued-out", default=None, help="救えた看板の一覧 TSV の出力先(NAS 側の作業フォルダ)")
    r = sub.add_parser("coverage", help="評価用の看板の対応率")
    r.add_argument("--index", required=True)
    r.add_argument("--sources-config", default=None)
    a = ap.parse_args(argv)
    if a.cmd in ("compare", "coverage"):
        import json
        index = read_index(a.index)
        if a.cmd == "coverage":
            print(coverage(dataset.load_all(a.sources_config, dataset.AGGREGATE_SOURCES), index))
            return 0
        load = lambda p: json.loads(Path(p).read_text(encoding="utf-8"))
        md, rescued = compare(dataset.load_all(a.sources_config, SOURCES), index, load(a.first_pass),
                              [load(p) for p in a.runs])
        print(md)
        if a.rescued_out:
            with open(a.rescued_out, "w", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, ["sign_id", "group", "rank_true", "pasted_pred", "original_pred", "reason"],
                                   delimiter="\t", lineterminator="\n")
                w.writeheader()
                w.writerows(rescued)
        return 0
    out = Path(a.out)
    items = _eval_items(a.sources_config, a.split)
    cache = SigCache(out.with_suffix(".sigcache.pkl"), a.workers)
    rows = build_index(items, read_dirs(a.nas_dirs), cache, Vocab.load(a.vocab), a.threshold)
    write_index(rows, out)
    ok = sum(1 for r in rows if r["basis"] != "none")
    print(f"看板 {len(rows)} 基のうち対応あり {ok}、対応なし {len(rows) - ok}(索引= {out.name})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
