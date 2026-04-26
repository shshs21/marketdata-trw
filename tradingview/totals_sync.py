"""
Sync TOTALES (total crypto market cap) from TradingView into market_totals.

Mirrors the incremental pattern used by ohlcv_sync.py: fetch daily bars via
tvDatafeed, drop today's incomplete bar, INSERT OR IGNORE for new rows only.
"""

import sqlite3
import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

from tvDatafeed import TvDatafeed, Interval

DB_PATH    = Path(__file__).resolve().parents[1] / "marketdata.db"
TV_SYMBOL  = "CRYPTOCAP:TOTALES"
INTERVAL   = Interval.in_daily
MAX_BARS   = 5000

MAX_RETRIES = 3
RETRY_DELAY = 15.0


def get_last_date(conn: sqlite3.Connection, symbol: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(date) FROM market_totals WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row[0] if row and row[0] else None


def fetch_totals(tv: TvDatafeed) -> pd.DataFrame | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = tv.get_hist(
                symbol=TV_SYMBOL,
                exchange="",
                interval=INTERVAL,
                n_bars=MAX_BARS,
            )
            if df is not None and not df.empty:
                return df
        except Exception as e:
            print(f"  Error fetching TOTALES (attempt {attempt}/{MAX_RETRIES}): {e}")
        if attempt < MAX_RETRIES:
            print(f"  Retrying in {RETRY_DELAY:.0f}s...")
            time.sleep(RETRY_DELAY)
        else:
            print(f"  TOTALES: failed after {MAX_RETRIES} attempts")
    return None


def main():
    tv = TvDatafeed()

    print("[totals_sync] Fetching TOTALES from TradingView...")
    df = fetch_totals(tv)

    if df is None:
        print("[totals_sync] No data returned after retries, skipping.")
        return

    df = df.rename(columns=str.lower)
    df.index = pd.to_datetime(df.index, utc=True)

    today_utc = datetime.now(timezone.utc).date()
    df = df[df.index.date < today_utc]

    print(f"  Fetched {len(df)} bars from TradingView")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")

    last_date = get_last_date(conn, TV_SYMBOL)
    print(f"  Last stored date: {last_date or 'none'}")

    rows = []
    for ts, row in df.iterrows():
        date_str = ts.date().isoformat()
        if last_date is not None and date_str <= last_date:
            continue
        rows.append((
            TV_SYMBOL,
            date_str,
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]) if pd.notna(row["volume"]) else None,
        ))

    conn.executemany("""
        INSERT OR IGNORE INTO market_totals
        (symbol, date, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    conn.close()

    print(f"  Inserted {len(rows)} new TOTALES bars")
    print("[totals_sync] Done.")


if __name__ == "__main__":
    main()
