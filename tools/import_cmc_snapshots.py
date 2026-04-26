"""
import_cmc_snapshots.py: One-time import of cmc_historical_snapshots.csv into daily_top.

Reads the historical CMC snapshot CSV and bulk-inserts all rows with rank <= 50
into the daily_top table in marketdata.db.

Run once:
    conda activate quant
    python data/tools/import_cmc_snapshots.py
"""

import sqlite3
import pandas as pd
from pathlib import Path

CSV_PATH = Path(__file__).resolve().parents[1] / "cmc_snapshots" / "cmc_historical_snapshots.csv"
DB_PATH  = Path(__file__).resolve().parents[1] / "marketdata.db"


def main():
    print(f"Reading {CSV_PATH.name} ...")
    df = pd.read_csv(CSV_PATH)
    print(f"  {len(df):,} rows, columns: {list(df.columns)}")

    df = df[df["rank"] <= 50].copy()
    print(f"  {len(df):,} rows after filtering rank <= 50")

    df["snapshot_date"]      = df["snapshot_date"].astype(str)
    df["rank"]               = pd.to_numeric(df["rank"],               errors="coerce").astype("Int64")
    df["symbol"]             = df["symbol"].astype(str)
    df["name"]               = df["name"].astype(str)
    df["market_cap"]         = pd.to_numeric(df["market_cap"],         errors="coerce")
    df["price"]              = pd.to_numeric(df["price"],              errors="coerce")
    df["circulating_supply"] = pd.to_numeric(df["circulating_supply"], errors="coerce")

    df = df.dropna(subset=["snapshot_date", "rank"])

    rows = list(df[["snapshot_date", "rank", "symbol", "name",
                    "market_cap", "price", "circulating_supply"]].itertuples(index=False, name=None))

    print(f"Inserting {len(rows):,} rows into daily_top ...")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.executemany(
        """
        INSERT OR IGNORE INTO daily_top
            (snapshot_date, rank, symbol, name, market_cap, price, circulating_supply)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()

    count = conn.execute("SELECT COUNT(*) FROM daily_top").fetchone()[0]
    conn.close()

    print(f"Done. daily_top now has {count:,} rows.")


if __name__ == "__main__":
    main()
