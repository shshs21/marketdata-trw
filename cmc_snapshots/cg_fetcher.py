"""
Daily top-N snapshot fetcher using CoinGecko.

Fetches today's top tokens by market cap from CoinGecko and writes
them into the daily_top table in marketdata.db.

Run daily to keep the table up to date.
"""

import os
import requests
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv

from cmc_config import CMC_TABLE, TOP_N

load_dotenv()

DB_PATH = Path(__file__).resolve().parents[1] / "marketdata.db"
CG_API_KEY = os.getenv("COINGECKO_API_KEY")
LIMIT = TOP_N


def get_today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def already_stored(conn: sqlite3.Connection, date: str) -> bool:
    row = conn.execute(
        f"SELECT COUNT(*) FROM {CMC_TABLE} WHERE snapshot_date = ?", (date,)
    ).fetchone()
    return row[0] > 0


def fetch_top50(date: str) -> list:
    if not CG_API_KEY:
        raise RuntimeError("COINGECKO_API_KEY not set in environment")

    r = requests.get(
        "https://api.coingecko.com/api/v3/coins/markets",
        headers={"x-cg-demo-api-key": CG_API_KEY},
        params={
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": LIMIT,
            "page": 1,
            "sparkline": "false",
        },
        timeout=30,
    )
    r.raise_for_status()
    data = r.json()

    rows = []
    for item in data:
        rows.append((
            date,
            item["market_cap_rank"],
            item["symbol"].upper(),
            item["name"],
            item.get("market_cap"),
            item.get("current_price"),
            item.get("circulating_supply"),
        ))
    return rows


def main():
    today = get_today()
    print(f"Fetching top {LIMIT} for {today}...")

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA journal_mode=WAL;")

    if already_stored(conn, today):
        print(f"Already stored for {today}, nothing to do.")
        conn.close()
        return

    rows = fetch_top50(today)

    conn.executemany(
        f"""
        INSERT OR IGNORE INTO {CMC_TABLE}
            (snapshot_date, rank, symbol, name, market_cap, price, circulating_supply)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()

    print(f"Done. Inserted {len(rows)} rows for {today}")
    for r in rows[:5]:
        print(f"  #{r[1]:2d}  {r[2]:10s}  ${r[4]:,.0f}")
    if len(rows) > 5:
        print(f"  ... and {len(rows) - 5} more")


if __name__ == "__main__":
    main()
