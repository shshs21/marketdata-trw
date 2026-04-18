"""
Check which CMC snapshot tokens exist on TradingView.

Reads unique symbols from cmc_historical_snapshots.csv, applies filters
and rebrands, then probes TradingView for each symbol with a 1-bar fetch.

Tokens that pass filters AND exist on TV are written into the
symbol_metadata table in marketdata.db.

Resumable: tv_check_done.txt stores the last checked symbol.
On restart the script skips ahead to that point and continues.
Delete the file to start fresh.

SKIP_KNOWN = True  (default)
    Only probe symbols not yet in symbol_metadata. Fast for incremental runs.

SKIP_KNOWN = False
    Probe every symbol, including already-known ones.
    Symbols that no longer return data on TV are REMOVED from symbol_metadata.
    Use this to audit/clean the table periodically.
"""

from tvDatafeed import TvDatafeed, Interval
from pathlib import Path
import pandas as pd
import sqlite3
import logging
import time
import sys


class _LogCapture(logging.Handler):
    """Captures log messages from tvDatafeed.main during a single fetch."""
    def __init__(self):
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record):
        self.messages.append(record.getMessage())

    def clear(self):
        self.messages = []

    def has_timeout(self) -> bool:
        return any("Connection timed out" in m for m in self.messages)

    def has_connection_drop(self) -> bool:
        return any("Connection to remote host was lost" in m for m in self.messages)


_tv_log_handler = _LogCapture()
logging.getLogger("tvDatafeed.main").addHandler(_tv_log_handler)

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from filters import should_exclude
from rebrands_list import REBRANDS

CSV_IN     = Path(__file__).resolve().parent / "cmc_historical_snapshots.csv"
DB_PATH    = Path(__file__).resolve().parents[1] / "marketdata.db"
CHECKPOINT = Path(__file__).resolve().parent / "tv_check_done.txt"

BASE_DELAY  = 12.0
MAX_DELAY   = 120.0
MAX_RETRIES = 3
RETRY_DELAY = 5.0

# Set False to re-audit all and remove stale entries.
SKIP_KNOWN = True

# Symbols that trade on INDEX exchange rather than CRYPTO.
INDEX_SYMBOLS = {"BTCUSD", "ETHUSD"}


def get_exchange(tv_symbol: str) -> str:
    return "INDEX" if tv_symbol in INDEX_SYMBOLS else "CRYPTO"


def check_symbol(tv, tv_symbol: str, exchange: str) -> bool:
    """Try fetching 1 bar. Returns True if the symbol exists on TV.
    Retries only on connection drops, not on clean 'symbol not found' responses.
    """
    for attempt in range(1, MAX_RETRIES + 1):
        _tv_log_handler.clear()
        try:
            df = tv.get_hist(
                symbol=tv_symbol,
                exchange=exchange,
                interval=Interval.in_daily,
                n_bars=1,
            )
            if df is not None and not df.empty:
                return True
        except Exception:
            pass

        if _tv_log_handler.has_timeout():
            # "Connection timed out" means symbol not found, no point retrying.
            break
        if _tv_log_handler.has_connection_drop() and attempt < MAX_RETRIES:
            print(f"  Connection drop, retrying {tv_symbol} in {RETRY_DELAY:.0f}s... ({attempt}/{MAX_RETRIES})")
            time.sleep(RETRY_DELAY)
        else:
            break

    return False


def load_checkpoint() -> str | None:
    """Load the last checked TV symbol from checkpoint file."""
    if not CHECKPOINT.exists():
        return None
    text = CHECKPOINT.read_text().strip()
    return text if text else None


def save_checkpoint(tv_symbol: str):
    """Overwrite checkpoint with the last checked symbol."""
    CHECKPOINT.write_text(tv_symbol)


def get_known_symbols(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT symbol FROM symbol_metadata").fetchall()
    return {r[0] for r in rows}


def main():
    last_checked = load_checkpoint()
    if last_checked:
        print(f"Checkpoint: resuming after {last_checked}")

    df = pd.read_csv(CSV_IN)
    raw_symbols = sorted(df["symbol"].dropna().unique())
    print(f"Unique symbols in CMC CSV: {len(raw_symbols)}")

    full_list = []
    excluded_count = 0

    for sym in raw_symbols:
        clean = sym.lstrip("$").strip()
        if not clean:
            continue

        rebranded = REBRANDS.get(clean, clean)

        if should_exclude(clean) or should_exclude(rebranded):
            excluded_count += 1
            continue

        tv_symbol = f"{rebranded}USD"
        exchange = get_exchange(tv_symbol)
        previous = clean if rebranded != clean else None

        full_list.append({
            "symbol": tv_symbol,
            "exchange": exchange,
            "previous_symbol": previous,
        })

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")

    if SKIP_KNOWN:
        known = get_known_symbols(conn)
        before = len(full_list)
        full_list = [r for r in full_list if r["symbol"] not in known]
        print(f"Skipping {before - len(full_list)} already known symbols (SKIP_KNOWN=True)")

    if last_checked:
        skip_to = None
        for idx, row in enumerate(full_list):
            if row["symbol"] == last_checked:
                skip_to = idx + 1
                break

        if skip_to is not None:
            skipped = full_list[:skip_to]
            full_list = full_list[skip_to:]
            print(f"Skipped {len(skipped)} symbols (checkpoint)")
        else:
            print(f"Warning: checkpoint '{last_checked}' not found in remaining list, starting from beginning")

    print(f"Excluded by filters:    {excluded_count}")
    print(f"Symbols to check on TV: {len(full_list)}")

    if not full_list:
        print("Nothing to do.")
        conn.close()
        return

    tv = TvDatafeed()
    delay = BASE_DELAY
    consecutive_failures = 0
    found_count = 0
    not_found_count = 0
    removed_count = 0

    for i, row in enumerate(full_list, 1):
        sym_label = f"{row['exchange']}:{row['symbol']}"
        print(f"[{i}/{len(full_list)}] {sym_label:30s} ...", end="", flush=True)
        found = check_symbol(tv, row["symbol"], row["exchange"])

        status = "YES" if found else "no"
        print(f"\r[{i}/{len(full_list)}] {sym_label:30s} -> {status}")

        if found:
            conn.execute(
                """
                INSERT OR IGNORE INTO symbol_metadata
                    (symbol, exchange, is_active, previous_symbol, is_filtered)
                VALUES (?, ?, 1, ?, 0)
                """,
                (row["symbol"], row["exchange"], row["previous_symbol"]),
            )
            conn.commit()
            found_count += 1
            consecutive_failures = 0
            delay = max(BASE_DELAY, delay * 0.9)
        else:
            consecutive_failures += 1
            not_found_count += 1
            if not SKIP_KNOWN:
                # Full audit mode removes stale entries from symbol_metadata.
                result = conn.execute(
                    "DELETE FROM symbol_metadata WHERE symbol = ?",
                    (row["symbol"],),
                )
                if result.rowcount:
                    conn.commit()
                    removed_count += 1
                    print(f"  -> removed from symbol_metadata")
            if consecutive_failures >= 5:
                delay = min(delay * 2, MAX_DELAY)
                print(f"  (increasing delay to {delay:.1f}s)")
                consecutive_failures = 0

        save_checkpoint(row["symbol"])
        time.sleep(delay)

    conn.close()

    print(f"\nDone!")
    print(f"  Found on TV (written to DB): {found_count}")
    print(f"  NOT on TV:                   {not_found_count}")
    if not SKIP_KNOWN:
        print(f"  Removed from DB (stale):     {removed_count}")
    print(f"  Excluded by filters:         {excluded_count}")
    print(f"  Total CMC symbols:           {len(raw_symbols)}")


if __name__ == "__main__":
    main()
