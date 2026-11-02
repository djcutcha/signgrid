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
    """会館名の鍵。評価側の現場名(例 "<社名>会館 東久留米")も NAS のフォルダ名(例 "10.東久留米会館")も同じ鍵になる。"""
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
    a = ap.parse_args(argv)
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
