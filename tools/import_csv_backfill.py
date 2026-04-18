"""
import_csv_backfill.py: Import extended OHLCV history from TradingView CSV exports.

Tokens that hit TradingView's 5000-bar API limit during ohlcv_backfill.py are
logged to tradingview/max_bars_tokens.txt.  To get their full history you can
manually export CSVs from the TradingView chart and drop them into
historical_csv_data/ at the project root.

This script reads the txt file, looks for a matching CSV for each token,
inserts the data into the ohlcv table, and removes the token from the txt
file.  Tokens without a CSV are left for later.  When every token has been
imported the txt file is deleted.

Resumable: safe to stop and re-run at any time.
"""

import sqlite3
import pandas as pd
from pathlib import Path

ROOT          = Path(__file__).resolve().parents[1]
DB_PATH       = ROOT / "marketdata.db"
MAX_BARS_FILE = ROOT / "tradingview" / "max_bars_tokens.txt"
CSV_DIR       = ROOT / "historical_csv_data"

INDEX_SYMBOLS = {"BTCUSD", "ETHUSD"}


def get_exchange(tv_symbol: str) -> str:
    return "INDEX" if tv_symbol in INDEX_SYMBOLS else "CRYPTO"


def find_csv(symbol: str, exchange: str) -> Path | None:
    """Find a CSV matching {EXCHANGE}_{SYMBOL}, 1D_*.csv in the CSV directory."""
    if not CSV_DIR.exists():
        return None
    matches = list(CSV_DIR.glob(f"{exchange}_{symbol}, 1D_*.csv"))
    return matches[0] if matches else None


def import_csv(csv_path: Path, symbol: str, exchange: str,
               conn: sqlite3.Connection) -> int:
    """Import a TradingView CSV export into the ohlcv table. Returns rows inserted."""
    df = pd.read_csv(csv_path)

    df["ts"] = pd.to_datetime(df["time"], unit="s", utc=True).dt.strftime("%Y-%m-%d")

    vol_col = next((c for c in df.columns if c.lower() == "volume"), None)

    rows = [
        (
            exchange,
            symbol,
            row["ts"],
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row[vol_col]) if vol_col and pd.notna(row[vol_col]) else None,
        )
        for _, row in df.iterrows()
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
    return len(rows)


def main():
    if not MAX_BARS_FILE.exists():
        print("No max_bars_tokens.txt found, nothing to do.")
        return

    tokens = [line.strip() for line in MAX_BARS_FILE.read_text().splitlines()
              if line.strip()]
    if not tokens:
        print("max_bars_tokens.txt is empty, deleting it.")
        MAX_BARS_FILE.unlink()
        return

    print(f"Tokens in max_bars_tokens.txt: {len(tokens)}")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")

    remaining = []
    imported = 0

    for symbol in tokens:
        exchange = get_exchange(symbol)
        csv_path = find_csv(symbol, exchange)

        if csv_path is None:
            print(f"  {symbol}: no CSV found, skipping")
            remaining.append(symbol)
            continue

        print(f"  {symbol}: importing {csv_path.name} ...", end="", flush=True)
        count = import_csv(csv_path, symbol, exchange, conn)
        print(f" {count} rows")
        imported += 1

    conn.close()

    if remaining:
        MAX_BARS_FILE.write_text("\n".join(remaining) + "\n")
        print(f"\nDone. Imported {imported} tokens, {len(remaining)} remaining in txt file.")
    else:
        MAX_BARS_FILE.unlink()
        print(f"\nDone. Imported {imported} tokens. Deleted max_bars_tokens.txt.")


if __name__ == "__main__":
    main()
