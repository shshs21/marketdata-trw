"""
Check which tokens exist on TradingView, write hits to symbol_metadata.

Candidate tokens come from one of two sources:

  1. update.py passes the exact set of symbols cmc_fetcher just touched
     (the `candidates` parameter). This is the normal path and avoids any
     daily_top scan. Step 2 probes only the symbols Step 1 just saw.

  2. Run standalone (`python check_tv_availability.py`) falls back to a
     full distinct scan of daily_top so the script still works as a
     repair or audit tool without orchestration.

Resumable via tv_check_queue.txt with one JSON object per line. On entry
the queue is loaded and merged with fresh candidates so an interrupted
run plus newly arrived tokens both get picked up. Each token is removed
from the queue once probed. The file is deleted when empty.

SKIP_KNOWN = True  (default)
    Only probe symbols not yet in symbol_metadata. Fast for daily updates.

SKIP_KNOWN = False
    Probe every candidate, including already known ones. Symbols that no
    longer return data on TV are REMOVED from symbol_metadata. Use to
    audit or clean the table periodically.
"""

from tvDatafeed import TvDatafeed, Interval
from pathlib import Path
import json
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

DB_PATH    = Path(__file__).resolve().parents[1] / "marketdata.db"
QUEUE_FILE = Path(__file__).resolve().parent / "tv_check_queue.txt"

BASE_DELAY  = 1.0
MAX_DELAY   = 30.0
MAX_RETRIES = 3
RETRY_DELAY = 2.0

# Set False to re-audit all and remove stale entries.
SKIP_KNOWN = True

# Symbols that trade on INDEX exchange rather than CRYPTO.
INDEX_SYMBOLS = {"BTCUSD", "ETHUSD"}


def get_exchange(tv_symbol: str) -> str:
    return "INDEX" if tv_symbol in INDEX_SYMBOLS else "CRYPTO"


def load_queue() -> list[dict]:
    if not QUEUE_FILE.exists():
        return []
    out = []
    for line in QUEUE_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"[check_tv] WARNING: skipping malformed queue line: {line!r}")
    return out


def save_queue(items: list[dict]) -> None:
    if not items:
        QUEUE_FILE.unlink(missing_ok=True)
        return
    QUEUE_FILE.write_text(
        "\n".join(json.dumps(it, sort_keys=True) for it in items) + "\n",
        encoding="utf-8",
    )


def remove_from_queue(tv_symbol: str) -> None:
    remaining = [it for it in load_queue() if it.get("tv_symbol") != tv_symbol]
    save_queue(remaining)


def get_candidates_from_db(conn: sqlite3.Connection) -> list[dict]:
    """Standalone fallback. Returns every distinct (symbol, name) pair in
    daily_top, used when this script is run directly without an upstream
    Step 1 supplying its seen set."""
    rows = conn.execute(
        """
        SELECT DISTINCT symbol, name FROM daily_top
        ORDER BY symbol ASC
        """
    ).fetchall()
    return [{"symbol": r[0], "name": r[1]} for r in rows]


def candidates_from_seen(
    conn: sqlite3.Connection, seen: set[str]
) -> list[dict]:
    """Resolve a set of raw symbols from cmc_fetcher into (symbol, name)
    rows by looking up the most recent name in daily_top. Symbols not
    present in daily_top fall back to a None name."""
    if not seen:
        return []
    placeholders = ",".join("?" * len(seen))
    rows = conn.execute(
        f"""
        SELECT symbol, name FROM (
            SELECT symbol, name,
                   ROW_NUMBER() OVER (PARTITION BY symbol ORDER BY snapshot_date DESC) AS rn
            FROM daily_top
            WHERE symbol IN ({placeholders})
        )
        WHERE rn = 1
        """,
        tuple(seen),
    ).fetchall()
    found = {r[0]: r[1] for r in rows}
    return [{"symbol": s, "name": found.get(s)} for s in sorted(seen)]


def get_known_symbols(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT symbol FROM symbol_metadata").fetchall()
    return {r[0] for r in rows}


def check_symbol(tv, tv_symbol: str, exchange: str) -> bool:
    """Probe TradingView for one bar. Retries only on connection drops,
    not on clean 'symbol not found' responses."""
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
            break
        if _tv_log_handler.has_connection_drop() and attempt < MAX_RETRIES:
            print(f"  Connection drop, retrying {tv_symbol} in {RETRY_DELAY:.0f}s... ({attempt}/{MAX_RETRIES})")
            time.sleep(RETRY_DELAY)
        else:
            break

    return False


def main(candidates: set[str] | None = None):
    """Probe TradingView for tokens.

    Parameters
    ----------
    candidates
        Set of raw CMC symbols that Step 1 just touched. When provided,
        this is the exact universe Step 2 considers, no daily_top scan
        happens. When None, falls back to scanning every distinct symbol
        in daily_top.
    """
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")

    pending = load_queue()
    if pending:
        print(f"[check_tv] Resuming: {len(pending)} pending from queue file")

    if candidates is not None:
        fresh = candidates_from_seen(conn, candidates)
        print(f"[check_tv] Symbols touched by Step 1: {len(fresh)}")
    else:
        fresh = get_candidates_from_db(conn)
        print(f"[check_tv] Distinct tokens in daily_top (standalone): {len(fresh)}")

    known = get_known_symbols(conn)
    print(f"[check_tv] Already in symbol_metadata: {len(known)}")

    queued_symbols = {it["tv_symbol"] for it in pending}
    excluded_count = 0
    fresh_added = 0

    for row in fresh:
        clean = (row["symbol"] or "").lstrip("$").strip()
        if not clean:
            continue
        rebranded = REBRANDS.get(clean, clean)
        if should_exclude(clean, row.get("name")) or should_exclude(rebranded):
            excluded_count += 1
            continue
        tv_symbol = f"{rebranded}USD"
        if tv_symbol in queued_symbols:
            continue
        if SKIP_KNOWN and tv_symbol in known:
            continue
        pending.append({
            "tv_symbol":       tv_symbol,
            "exchange":        get_exchange(tv_symbol),
            "previous_symbol": clean if rebranded != clean else None,
        })
        queued_symbols.add(tv_symbol)
        fresh_added += 1

    if fresh_added:
        print(f"[check_tv] Fresh additions to queue: {fresh_added}")
    print(f"[check_tv] Excluded by filters:    {excluded_count}")
    save_queue(pending)

    if not pending:
        print("[check_tv] Nothing to probe.")
        conn.close()
        return

    print(f"[check_tv] Total to probe: {len(pending)}")

    tv = TvDatafeed()
    delay = BASE_DELAY
    consecutive_failures = 0
    found_count = 0
    not_found_count = 0
    removed_count = 0

    for i, row in enumerate(list(pending), 1):
        sym_label = f"{row['exchange']}:{row['tv_symbol']}"
        print(f"[{i}/{len(pending)}] {sym_label:30s} ...", end="", flush=True)
        found = check_symbol(tv, row["tv_symbol"], row["exchange"])

        status = "YES" if found else "no"
        print(f"\r[{i}/{len(pending)}] {sym_label:30s} -> {status}")

        if found:
            conn.execute(
                """
                INSERT OR IGNORE INTO symbol_metadata
                    (symbol, exchange, is_active, previous_symbol, is_filtered)
                VALUES (?, ?, 1, ?, 0)
                """,
                (row["tv_symbol"], row["exchange"], row["previous_symbol"]),
            )
            conn.commit()
            found_count += 1
            consecutive_failures = 0
            delay = max(BASE_DELAY, delay * 0.9)
        else:
            consecutive_failures += 1
            not_found_count += 1
            if not SKIP_KNOWN:
                # Audit mode: drop stale rows that no longer resolve on TV.
                result = conn.execute(
                    "DELETE FROM symbol_metadata WHERE symbol = ?",
                    (row["tv_symbol"],),
                )
                if result.rowcount:
                    conn.commit()
                    removed_count += 1
                    print(f"  -> removed from symbol_metadata")
            if consecutive_failures >= 5:
                delay = min(delay * 2, MAX_DELAY)
                print(f"  (increasing delay to {delay:.1f}s)")
                consecutive_failures = 0

        # A "no" means TV doesn't have it. Genuine transient errors raise
        # from check_symbol and bypass this branch, leaving the entry queued.
        remove_from_queue(row["tv_symbol"])

        time.sleep(delay)

    conn.close()

    print(f"\nDone!")
    print(f"  Found on TV (written to DB): {found_count}")
    print(f"  NOT on TV:                   {not_found_count}")
    if not SKIP_KNOWN:
        print(f"  Removed from DB (stale):     {removed_count}")
    print(f"  Excluded by filters:         {excluded_count}")


if __name__ == "__main__":
    main()
