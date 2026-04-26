"""
ohlcv_backfill.py: Fetch OHLCV history for every token that ever appeared
in the historical top-N universe (daily_top table).

Pulls the full set of unique tokens that ever ranked <= TOP_N on any date,
then fetches their complete available history from TradingView.

Incremental: already-stored bars are not re-fetched.
Resumable:   backfill_checkpoint.txt stores the last completed symbol.
             Delete it to start over.
"""

import sys
import sqlite3
import time
import pandas as pd
from pathlib import Path
from datetime import datetime, timezone

from tvDatafeed import TvDatafeed, Interval

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "cmc_snapshots"))
from filters import should_exclude
from rebrands_list import REBRANDS
from cmc_config import TOP_N  # type: ignore[reportMissingImports]  # sys.path set above

DB_PATH        = Path(__file__).resolve().parents[1] / "marketdata.db"
MAX_BARS_FILE  = Path(__file__).resolve().parent / "max_bars_tokens.txt"
CHECKPOINT     = Path(__file__).resolve().parent / "backfill_checkpoint.txt"
CSV_DIR        = Path(__file__).resolve().parent.parent / "historical_csv_data"

MAX_BARS    = 5000
BASE_DELAY  = 5.0
MAX_DELAY   = 30.0
MAX_RETRIES = 3
RETRY_DELAY = 15.0

INDEX_SYMBOLS = {"BTCUSD", "ETHUSD"}


def get_exchange(tv_symbol: str) -> str:
    return "INDEX" if tv_symbol in INDEX_SYMBOLS else "CRYPTO"


def load_set(path: Path) -> set[str]:
    if not path.exists():
        return set()
    return {line.strip() for line in path.read_text().splitlines() if line.strip()}


def append_line(path: Path, value: str):
    with open(path, "a") as f:
        f.write(value + "\n")


def load_checkpoint() -> str | None:
    if not CHECKPOINT.exists():
        return None
    text = CHECKPOINT.read_text().strip()
    return text if text else None


def save_checkpoint(symbol: str):
    CHECKPOINT.write_text(symbol)


def get_ever_top_n(conn: sqlite3.Connection, top_n: int) -> list[tuple[str, str]]:
    """
    Return all (tv_symbol, exchange) pairs that ever ranked <= top_n in
    daily_top, after applying filters and rebrands.
    Cross-checked against symbol_metadata (TV-confirmed symbols only).
    """
    rows = conn.execute(
        """
        SELECT DISTINCT symbol, name
        FROM daily_top
        WHERE rank <= ?
        ORDER BY symbol
        """,
        (top_n,),
    ).fetchall()

    known = {r[0] for r in conn.execute("SELECT symbol FROM symbol_metadata").fetchall()}

    result = []
    seen   = set()
    for symbol, name in rows:
        clean     = symbol.lstrip("$").strip()
        rebranded = REBRANDS.get(clean, clean)
        if should_exclude(clean, name) or should_exclude(rebranded):
            continue
        tv_symbol = f"{rebranded}USD"
        if tv_symbol not in known or tv_symbol in seen:
            continue
        seen.add(tv_symbol)
        result.append((tv_symbol, get_exchange(tv_symbol)))

    return result


def get_last_date(conn: sqlite3.Connection, symbol: str) -> str | None:
    row = conn.execute(
        "SELECT MAX(ts) FROM ohlcv WHERE symbol = ?", (symbol,)
    ).fetchone()
    return row[0] if row and row[0] else None


def fetch_ohlcv(tv, symbol: str, exchange: str) -> pd.DataFrame | None:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            df = tv.get_hist(
                symbol=symbol,
                exchange=exchange,
                interval=Interval.in_daily,
                n_bars=MAX_BARS,
            )
            if df is not None and not df.empty:
                return df
        except Exception as e:
            print(f"  Error fetching {symbol} (attempt {attempt}/{MAX_RETRIES}): {e}")
        if attempt < MAX_RETRIES:
            print(f"  Retrying {symbol} in {RETRY_DELAY:.0f}s...")
            time.sleep(RETRY_DELAY)
        else:
            print(f"  {symbol}: failed after {MAX_RETRIES} attempts")
    return None


def main():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")

    universe = get_ever_top_n(conn, TOP_N)
    print(f"[backfill] Tokens that ever ranked <= {TOP_N} (TV-confirmed): {len(universe)}")

    last_done = load_checkpoint()
    if last_done:
        symbols_done = {s for s, _ in universe[:next(
            (i for i, (s, _) in enumerate(universe) if s == last_done), -1
        ) + 1]}
        start_idx = next((i for i, (s, _) in enumerate(universe) if s == last_done), -1) + 1
        print(f"[backfill] Resuming after '{last_done}', skipping {start_idx} symbols")
        universe = universe[start_idx:]
    else:
        print("[backfill] No checkpoint, starting from beginning")

    if not universe:
        print("[backfill] Nothing to do.")
        conn.close()
        return

    max_bars_known = load_set(MAX_BARS_FILE)
    tv             = TvDatafeed()
    today_utc      = datetime.now(timezone.utc).date()
    delay          = BASE_DELAY

    for idx, (symbol, exchange) in enumerate(universe, 1):
        total = idx + (len(universe) - len(universe))
        last_date = get_last_date(conn, symbol)

        if last_date:
            print(f"[{idx}/{len(universe)}] {symbol}: last bar {last_date}, incremental fetch")
        else:
            print(f"[{idx}/{len(universe)}] {symbol}: no history, fetching full {MAX_BARS} bars")

        df = fetch_ohlcv(tv, symbol, exchange)

        if df is None:
            print(f"  {symbol}: no data returned, skipping")
            save_checkpoint(symbol)
            time.sleep(delay)
            continue

        # Only log bar-cap hits when DB history isn't already past the cap.
        if len(df) == MAX_BARS and symbol not in max_bars_known:
            existing = conn.execute(
                "SELECT COUNT(*) FROM ohlcv WHERE symbol = ?", (symbol,)
            ).fetchone()[0]
            if existing <= MAX_BARS:
                CSV_DIR.mkdir(exist_ok=True)
                append_line(MAX_BARS_FILE, symbol)
                max_bars_known.add(symbol)
                print(f"  {symbol}: hit {MAX_BARS}-bar cap, added to {MAX_BARS_FILE.name}")

        df = df.rename(columns=str.lower)
        df.index = pd.to_datetime(df.index, utc=True)

        # Drop today's incomplete bar.
        df = df[df.index.date < today_utc]

        # Only insert rows newer than last stored date.
        if last_date:
            df = df[df.index.date.astype(str) > last_date]

        if df.empty:
            print(f"  {symbol}: already up to date")
            save_checkpoint(symbol)
            time.sleep(delay)
            continue

        rows = [
            (
                exchange,
                symbol,
                str(row_idx.date()),
                float(row["open"]),
                float(row["high"]),
                float(row["low"]),
                float(row["close"]),
                float(row["volume"]) if pd.notna(row.get("volume")) else None,
            )
            for row_idx, row in df.iterrows()
        ]

        conn.executemany(
            """
            INSERT OR IGNORE INTO ohlcv
                (exchange, symbol, ts, open, high, low, close, volume)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()
        print(f"  {symbol}: inserted {len(rows)} new bars")

        save_checkpoint(symbol)
        delay = max(BASE_DELAY, delay * 0.9)
        time.sleep(delay)

    conn.close()
    print("\n[backfill] Done.")


if __name__ == "__main__":
    main()
