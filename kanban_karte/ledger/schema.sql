-- 看板カルテの台帳(SQLite)。看板1基=1行(signs)が製品の芯。
-- マイグレーションはこの1本。全て IF NOT EXISTS で、何度流しても同じ。版は PRAGMA user_version(db.py が管理)。

-- 現場(店・施設)。看板の置き場所
CREATE TABLE IF NOT EXISTS sites (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    address       TEXT,
    lat           REAL,
    lng           REAL,
    municipality  TEXT,                 -- 住所から引いた自治体名(legal/ の Municipality 判定)
    created       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 入口2の束(紙のスキャン PDF 1本)
CREATE TABLE IF NOT EXISTS bundles (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    file          TEXT NOT NULL,        -- 取り込んだ PDF のパス
    sha256        TEXT NOT NULL UNIQUE, -- 同じ PDF の二重取り込みを防ぐ
    pages         INTEGER NOT NULL DEFAULT 0,
    kind_counts   TEXT NOT NULL DEFAULT '{}',  -- JSON {ページ種別: 件数}
    status        TEXT NOT NULL DEFAULT 'new'
                  CHECK (status IN ('new', 'pages_registered', 'classified', 'signs_drafted', 'error')),
    error         TEXT,
    site_id       TEXT REFERENCES sites(id),  -- 束の現場(LINE 入口: 位置・案件の選択で決まる。ALTER 済みの古い台帳は db.py が足す)
    created       TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 束のページ
CREATE TABLE IF NOT EXISTS pages (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id     INTEGER NOT NULL REFERENCES bundles(id),
    page_no       INTEGER NOT NULL,     -- 1 始まり
    kind          TEXT NOT NULL DEFAULT '不明'
                  CHECK (kind IN ('指示書', '完成報告書', '点検表', '写真', '地図', '危険予知', 'その他', '不明')),
    kind_basis    TEXT,                 -- 種別の根拠(判定役の名前と理由)
    image_path    TEXT,                 -- 取り出したページ画像(無ければ NULL)
    image_source  TEXT,                 -- 'embedded'(埋め込み画像)/ 'rendered'(描画)
    src_rotate    INTEGER NOT NULL DEFAULT 0,  -- PDF の /Rotate(画像には反映済み)
    rotation      INTEGER NOT NULL DEFAULT 0,  -- さらに起こす角度(PIL 反時計回り 0/90/180/270)
    width         INTEGER,
    height        INTEGER,
    dpi           REAL,                 -- 保存した画像の解像度(切り出しの検出が縮尺に使う)
    UNIQUE (bundle_id, page_no)
);

-- 看板1基=1行。法規の列と安全の列が並ぶ
CREATE TABLE IF NOT EXISTS signs (
    id            TEXT PRIMARY KEY,
    site_id       TEXT REFERENCES sites(id),
    name          TEXT,
    type          TEXT,                 -- 広告塔・自立・壁面・袖など
    size_w        INTEGER,              -- mm。不明は NULL
    size_h        INTEGER,
    address       TEXT,
    lat           REAL,
    lng           REAL,
    municipality  TEXT,
    -- 法規の列(住所/位置から条例を引いて埋める。この段階では空)
    is_self_use   INTEGER CHECK (is_self_use IN (0, 1)),       -- 自家用広告物か
    permit_required INTEGER CHECK (permit_required IN (0, 1)), -- 許可が要るか
    permit_expires TEXT,                -- 許可の期限(YYYY-MM-DD)
    next_inspection TEXT,               -- 次回点検(YYYY-MM-DD)
    -- 安全の列(有資格者が確定する)
    rank          INTEGER CHECK (rank BETWEEN 1 AND 4),  -- 1良好 2経過観察 3要改善 4即時対応
    items         TEXT NOT NULL DEFAULT '{}',   -- JSON {点検項目キー: 良好/経過観察/要改善/対象外}(18項目)
    remark        TEXT,                 -- 所見
    photos        TEXT NOT NULL DEFAULT '[]',   -- JSON [画像パス, ...]
    last_inspected TEXT,                -- YYYY-MM-DD
    prev_rank     INTEGER CHECK (prev_rank BETWEEN 1 AND 4),  -- 前回のランク(前回との差を出す)
    source_bundle INTEGER REFERENCES bundles(id),             -- 入口2で作った看板の元の束
    -- 下書きと確定(pipeline/)。status: new=未判定 / needs_photos=写真が無く下書きできない / draft=AI の下書き / confirmed=有資格者が確定
    status        TEXT NOT NULL DEFAULT 'new'
                  CHECK (status IN ('new', 'needs_photos', 'draft', 'confirmed')),
    draft_rank    INTEGER CHECK (draft_rank BETWEEN 1 AND 4),  -- AI の下書きのランク(確定後に rank と比べる)
    confidence    REAL,                 -- 下書きの確信(0〜1)
    evidence      TEXT NOT NULL DEFAULT '{}',   -- JSON {点検項目キー: {photos: [写真番号], text: 1文, needs_onsite: bool}}
    confirmed_at  TEXT,
    confirmed_by  TEXT,
    height_m      REAL,                 -- 看板の全高(m、任意)。size は表示面の寸法
    created       TEXT NOT NULL DEFAULT (datetime('now')),
    updated       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_signs_site ON signs(site_id);
CREATE INDEX IF NOT EXISTS idx_signs_bundle ON signs(source_bundle);
CREATE INDEX IF NOT EXISTS idx_pages_bundle ON pages(bundle_id);

-- 有資格者の訂正から育つ規則(次回の下書きに先に反映する。既存の規則と矛盾すれば知らせる)
CREATE TABLE IF NOT EXISTS rules (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    text          TEXT NOT NULL,
    source        TEXT,                 -- 由来(どの看板・どの訂正か)
    created       TEXT NOT NULL DEFAULT (datetime('now')),
    conflicts_with TEXT NOT NULL DEFAULT '[]',  -- JSON [矛盾する規則の id, ...]
    -- 承認の工程: 下書きに効くのは approved だけ(candidate は積まれただけ、rejected は却下)。古い台帳には db.py が足す
    status        TEXT NOT NULL DEFAULT 'candidate' CHECK (status IN ('candidate', 'approved', 'rejected')),
    approved_by   TEXT,                 -- 承認・却下した有資格者の呼び名
    approved_at   TEXT,
    resolution    TEXT                  -- 矛盾の解き方のメモ(どちらを残し、なぜか)
);

-- 有資格者の訂正(下書きとの差分)。1行=1回の訂正。確定後は訂正できない(pipeline/review.py)
CREATE TABLE IF NOT EXISTS corrections (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    sign_id       TEXT NOT NULL REFERENCES signs(id),
    item          TEXT NOT NULL,        -- 点検項目キー(「点検箇所-番号」)。看板全体のランクの訂正は 'rank'
    old_value     TEXT,                 -- 訂正前の区分(良好/経過観察/要改善/対象外)。rank は '1'〜'4'。NULL=未記入(判断できず)
    new_value     TEXT NOT NULL,        -- 訂正後
    rank_before   INTEGER,              -- 訂正の前後の看板のランク(項目の訂正で看板のランクが動いたか)
    rank_after    INTEGER,
    corrected_by  TEXT NOT NULL,        -- 有資格者の呼び名(個人名は台帳の外の運用で決める)
    note          TEXT,                 -- 訂正の理由・所見(自由記述)
    rule_id       INTEGER REFERENCES rules(id),  -- この訂正から積んだ規則(積まなかった訂正は NULL)
    created       TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_corrections_sign ON corrections(sign_id);
