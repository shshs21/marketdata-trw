import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parents[1] / "marketdata.db"

if not DB_PATH.exists():
    print(f"ERROR: {DB_PATH} does not exist.")
    print("init_db.py only verifies schema on an existing DB, it will not create one.")
    sys.exit(1)

conn = sqlite3.connect(DB_PATH)

conn.execute("""
CREATE TABLE IF NOT EXISTS ohlcv (
    exchange TEXT NOT NULL,
    symbol   TEXT NOT NULL,
    ts       TEXT NOT NULL,     -- YYYY-MM-DD (UTC date)
    open     REAL,
    high     REAL,
    low      REAL,
    close    REAL,
    volume   REAL,
    PRIMARY KEY (exchange, symbol, ts)
);
""")

conn.execute("""
CREATE INDEX IF NOT EXISTS idx_ohlcv_exchange_symbol_ts
ON ohlcv(exchange, symbol, ts);
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS market_totals (
    symbol TEXT NOT NULL,       -- e.g. CRYPTOCAP:TOTALES
    date   TEXT NOT NULL,       -- YYYY-MM-DD (UTC)
    open   REAL,
    high   REAL,
    low    REAL,
    close  REAL,
    volume REAL,
    PRIMARY KEY (symbol, date)
);
""")

conn.execute("""
CREATE INDEX IF NOT EXISTS idx_market_totals_symbol_date
ON market_totals(symbol, date);
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS symbol_metadata (
    symbol    TEXT PRIMARY KEY,
    exchange  TEXT,
    is_active INTEGER DEFAULT 1,
    previous_symbol TEXT,                -- legacy base (MIOTA, VEN, NANO, ...)
    is_filtered     INTEGER DEFAULT 0,   -- excluded from research universe
    is_in_engine_universe INTEGER DEFAULT 0
);
""")

conn.execute("""
CREATE TABLE IF NOT EXISTS daily_top (
    snapshot_date      TEXT    NOT NULL,   -- YYYY-MM-DD (UTC)
    rank               INTEGER NOT NULL,
    symbol             TEXT    NOT NULL,
    name               TEXT,
    market_cap         REAL,
    price              REAL,
    circulating_supply REAL,
    PRIMARY KEY (snapshot_date, rank)
);
""")

conn.execute("""
CREATE INDEX IF NOT EXISTS idx_daily_top_date
ON daily_top(snapshot_date);
""")

conn.commit()
conn.close()

print("Database initialized and verified: marketdata.db")
print("Tables: ohlcv, market_totals, symbol_metadata, daily_top")
