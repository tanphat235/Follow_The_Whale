"""SQLite schema + helper. Moi thu fetch ve deu ghi vao day truoc khi xu ly,
nen moi buoc deu --resume duoc va khong bao gio goi lai request da goi.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import config

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

-- Transfer thuc te lay tu Blockscout getLogs (nguon chan ly).
CREATE TABLE IF NOT EXISTS transfers (
    tx_hash      TEXT NOT NULL,
    log_index    INTEGER NOT NULL,
    block_number INTEGER NOT NULL,
    ts           INTEGER,
    from_addr    TEXT NOT NULL,
    to_addr      TEXT NOT NULL,
    raw_value    TEXT NOT NULL,   -- nguyen ban wei, khong lam tron
    amount       REAL NOT NULL,   -- da chia decimals, de tien tinh toan
    src          TEXT DEFAULT 'logs',
    PRIMARY KEY (tx_hash, log_index)
);
CREATE INDEX IF NOT EXISTS ix_tr_block ON transfers(block_number);
CREATE INDEX IF NOT EXISTS ix_tr_from  ON transfers(from_addr, block_number);
CREATE INDEX IF NOT EXISTS ix_tr_to    ON transfers(to_addr, block_number);
CREATE INDEX IF NOT EXISTS ix_tr_ts    ON transfers(ts);

-- Hang goc tu 25 file CSV cua Etherscan. Chi dung lam SEED (danh sach ung vien + nametag).
-- Cot Value(USD) cua Etherscan la gia LUC EXPORT, khong phai gia tai block -> khong luu.
CREATE TABLE IF NOT EXISTS csv_transfers (
    row_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_hash      TEXT NOT NULL,
    block_number INTEGER,
    from_addr    TEXT NOT NULL,
    to_addr      TEXT NOT NULL,
    amount       REAL NOT NULL,
    method       TEXT,
    src_file     TEXT
);
CREATE INDEX IF NOT EXISTS ix_csv_tx ON csv_transfers(tx_hash);

CREATE TABLE IF NOT EXISTS blocks (
    number INTEGER PRIMARY KEY,
    ts     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS addresses (
    addr         TEXT PRIMARY KEY,
    is_contract  INTEGER,          -- NULL = chua biet
    name         TEXT,
    is_scam      INTEGER DEFAULT 0,
    first_ts     INTEGER,
    last_ts      INTEGER,
    klass        TEXT,             -- cex | dex_pool | router | mev_bot | market_maker | whale_candidate | retail | contract_other
    klass_reason TEXT,
    cluster_id   TEXT,
    deep_fetched INTEGER DEFAULT 0,
    meta_fetched INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_addr_klass ON addresses(klass);

-- Nhan dia chi, nhieu nguon cung ton tai (csv | blockscout | builtin).
CREATE TABLE IF NOT EXISTS labels (
    addr   TEXT NOT NULL,
    label  TEXT NOT NULL,
    source TEXT NOT NULL,
    PRIMARY KEY (addr, source, label)
);
CREATE INDEX IF NOT EXISTS ix_lab_addr ON labels(addr);

-- Nen 1 phut. ts = thoi diem mo nen (epoch giay, boi so cua 60).
CREATE TABLE IF NOT EXISTS prices (
    ts     INTEGER PRIMARY KEY,
    open   REAL, high REAL, low REAL, close REAL, volume REAL,
    src    TEXT
);

-- Ket qua buoc ledger: moi transfer da duoc dich sang hanh dong mua/ban.
CREATE TABLE IF NOT EXISTS trades (
    tx_hash    TEXT NOT NULL,
    log_index  INTEGER NOT NULL,
    wallet     TEXT NOT NULL,
    ts         INTEGER,
    block_number INTEGER,
    side       TEXT,       -- BUY | SELL | WITHDRAW | DEPOSIT | INTERNAL | OTC_IN | OTC_OUT
    amount     REAL,
    price      REAL,
    usd        REAL,
    conf       REAL,       -- do tin cay cua suy luan (0..1)
    counterparty TEXT,
    cp_klass   TEXT,
    PRIMARY KEY (tx_hash, log_index, wallet)
);
CREATE INDEX IF NOT EXISTS ix_trade_wallet ON trades(wallet, ts);

-- Ket qua buoc score: 1 dong / vi.
CREATE TABLE IF NOT EXISTS wallet_stats (
    wallet        TEXT PRIMARY KEY,
    klass         TEXT,
    name          TEXT,
    cluster_id    TEXT,
    n_trades      INTEGER,
    n_buys        INTEGER,
    n_sells       INTEGER,
    uni_bought    REAL,
    uni_sold      REAL,
    net_uni       REAL,
    gross_uni     REAL,
    balance_uni   REAL,
    avg_cost      REAL,
    realized_pnl  REAL,
    unrealized_pnl REAL,
    total_pnl     REAL,
    roi           REAL,
    buy_pctile    REAL,
    sell_pctile   REAL,
    edge          REAL,
    round_trips   INTEGER,
    win_rate      REAL,
    fwd_24h       REAL,
    fwd_72h       REAL,
    fwd_7d        REAL,
    top_prox_hours REAL,
    top_prox_ratio REAL,
    bottom_prox_ratio REAL,
    first_ts      INTEGER,
    last_ts       INTEGER,
    basis_conf    REAL,
    copy_score    REAL,
    mm_score      REAL,
    score_json    TEXT       -- breakdown tung thanh phan + phat, de bao cao giai thich duoc
);
CREATE INDEX IF NOT EXISTS ix_ws_score ON wallet_stats(copy_score DESC);

-- Con tro resume cho tung loai fetch.
CREATE TABLE IF NOT EXISTS fetch_log (
    kind TEXT NOT NULL,
    key  TEXT NOT NULL,
    ts   INTEGER,
    PRIMARY KEY (kind, key)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- Block ma ca 3 tang lay du lieu deu that bai. Bao cao ra, KHONG am tham bo qua.
CREATE TABLE IF NOT EXISTS truncated_blocks (
    block_number INTEGER PRIMARY KEY,
    noted_ts     INTEGER
);

-- Chong gui trung canh bao khi monitor restart.
CREATE TABLE IF NOT EXISTS alerts_sent (
    tx_hash   TEXT NOT NULL,
    log_index INTEGER NOT NULL,
    wallet    TEXT NOT NULL,
    sent_ts   INTEGER,
    PRIMARY KEY (tx_hash, log_index, wallet)
);
"""


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path else config.DATA_DIR / "whale.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=60)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


# ---------- meta / fetch_log ----------

def meta_get(conn, key: str, default: Any = None) -> Any:
    row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def meta_set(conn, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def fetch_done(conn, kind: str, key: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM fetch_log WHERE kind=? AND key=?", (kind, str(key))
    ).fetchone() is not None


def mark_fetched(conn, kind: str, key: str, ts: int | None = None) -> None:
    import time
    conn.execute(
        "INSERT OR REPLACE INTO fetch_log(kind,key,ts) VALUES(?,?,?)",
        (kind, str(key), ts if ts is not None else int(time.time())),
    )


# ---------- ghi hang loat ----------

def upsert_transfers(conn, rows: Iterable[Sequence]) -> int:
    """rows: (tx_hash, log_index, block_number, ts, from, to, raw_value, amount, src)"""
    cur = conn.executemany(
        "INSERT INTO transfers(tx_hash,log_index,block_number,ts,from_addr,to_addr,raw_value,amount,src)"
        " VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(tx_hash,log_index) DO UPDATE SET"
        " ts=COALESCE(excluded.ts, transfers.ts)",
        list(rows),
    )
    return cur.rowcount


def upsert_blocks(conn, rows: Iterable[Sequence]) -> None:
    conn.executemany("INSERT OR REPLACE INTO blocks(number,ts) VALUES(?,?)", list(rows))


def upsert_prices(conn, rows: Iterable[Sequence]) -> None:
    conn.executemany(
        "INSERT OR REPLACE INTO prices(ts,open,high,low,close,volume,src) VALUES(?,?,?,?,?,?,?)",
        list(rows),
    )


def touch_addresses(conn, addrs: Iterable[str]) -> None:
    conn.executemany(
        "INSERT OR IGNORE INTO addresses(addr) VALUES(?)",
        [(a.lower(),) for a in addrs],
    )


def add_label(conn, addr: str, label: str, source: str) -> None:
    label = (label or "").strip()
    if not label:
        return
    conn.execute(
        "INSERT OR IGNORE INTO labels(addr,label,source) VALUES(?,?,?)",
        (addr.lower(), label, source),
    )


def counts(conn) -> dict:
    def one(sql: str) -> int:
        return conn.execute(sql).fetchone()[0]

    return {
        "transfers": one("SELECT COUNT(*) FROM transfers"),
        "csv_rows": one("SELECT COUNT(*) FROM csv_transfers"),
        "addresses": one("SELECT COUNT(*) FROM addresses"),
        "labels": one("SELECT COUNT(DISTINCT addr) FROM labels"),
        "blocks": one("SELECT COUNT(*) FROM blocks"),
        "prices": one("SELECT COUNT(*) FROM prices"),
        "trades": one("SELECT COUNT(*) FROM trades"),
        "scored": one("SELECT COUNT(*) FROM wallet_stats"),
    }
