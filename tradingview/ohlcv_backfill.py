"""
ohlcv_backfill.py: Fetch OHLCV history for every token that ever appeared
in the historical top-N universe (daily_top table).

Pulls the full set of unique tokens that ever ranked <= TOP_N on any date,
then fetches their complete available history from TradingView.

Already stored bars are not refetched.
Resumable via backfill_queue.txt with one symbol per line. On entry the
queue is loaded and merged with the freshly derived universe so an
interrupted run plus newly arrived tokens both get picked up. Each symbol
is removed from the queue once processed. The file is deleted when empty.
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
QUEUE_FILE     = Path(__file__).resolve().parent / "backfill_queue.txt"
CSV_DIR        = Path(__file__).resolve().parent.parent / "historical_csv_data"

MAX_BARS    = 5000
BASE_DELAY  = 1.0
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


def load_queue() -> list[str]:
    if not QUEUE_FILE.exists():
        return []
    return [
        line.strip()
        for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def save_queue(symbols: list[str]) -> None:
    if not symbols:
        QUEUE_FILE.unlink(missing_ok=True)
        return
    QUEUE_FILE.write_text("\n".join(symbols) + "\n", encoding="utf-8")


def remove_from_queue(symbol: str) -> None:
    remaining = [s for s in load_queue() if s != symbol]
    save_queue(remaining)


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

    pending = load_queue()
    if pending:
        print(f"[backfill] Resuming: {len(pending)} pending from queue file")

    universe       = get_ever_top_n(conn, TOP_N)
    universe_map   = {sym: exch for sym, exch in universe}
    print(f"[backfill] Tokens that ever ranked <= {TOP_N} (TV-confirmed): {len(universe)}")

    pending_set = set(pending)
    fresh_added = 0
    for sym, _ in universe:
        if sym not in pending_set:
            pending.append(sym)
            pending_set.add(sym)
            fresh_added += 1

    # Drop queue entries that are no longer in the universe (rebrand, filter, fell out).
    pending = [s for s in pending if s in universe_map]
    if fresh_added:
        print(f"[backfill] Fresh additions to queue: {fresh_added}")
    save_queue(pending)

    if not pending:
        print("[backfill] Nothing to do.")
        conn.close()
        return

    print(f"[backfill] Total to process: {len(pending)}")

    max_bars_known = load_set(MAX_BARS_FILE)
    tv             = TvDatafeed()
    today_utc      = datetime.now(timezone.utc).date()
    delay          = BASE_DELAY

    # Iterate over a snapshot. remove_from_queue rewrites the file as we go.
    for idx, symbol in enumerate(list(pending), 1):
        exchange  = universe_map[symbol]
        last_date = get_last_date(conn, symbol)

        if last_date:
            print(f"[{idx}/{len(pending)}] {symbol}: last bar {last_date}, incremental fetch")
        else:
            print(f"[{idx}/{len(pending)}] {symbol}: no history, fetching full {MAX_BARS} bars")

        df = fetch_ohlcv(tv, symbol, exchange)

        if df is None:
            print(f"  {symbol}: no data returned, skipping")
            remove_from_queue(symbol)
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

        if last_date:
            df = df[df.index.date.astype(str) > last_date]

        if df.empty:
            print(f"  {symbol}: already up to date")
            remove_from_queue(symbol)
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

        remove_from_queue(symbol)
        delay = max(BASE_DELAY, delay * 0.9)
        time.sleep(delay)

    conn.close()
    print("\n[backfill] Done.")


if __name__ == "__main__":
    main()
