import sqlite3
import time
import requests
import pandas as pd
from datetime import datetime, timedelta

from cmc_config import (
    START_DATE,
    REQUEST_SLEEP_SECONDS,
    CMC_API_URL,
    HEADERS,
    MARKETDATA_DB,
    CMC_TABLE,
    TOP_N,
)


def get_dates_to_fetch():
    """Return chronologically ordered dates that need a fetch.

    Two sources, unioned:
      1. Existing snapshot_date rows where COUNT(*) < TOP_N. Refetching
         is non destructive because of INSERT OR IGNORE on the
         (snapshot_date, rank) primary key. Existing rows stay, missing
         ranks get filled in.
      2. Every date from MAX(snapshot_date)+1 to today, or from
         START_DATE to today on a fresh DB.

    Sorted oldest first so progress moves forward in time on resume.
    """
    today = datetime.utcnow().date()
    start = datetime.strptime(START_DATE, "%Y-%m-%d").date()

    with sqlite3.connect(MARKETDATA_DB) as conn:
        underpop_rows = conn.execute(
            f"""
            SELECT snapshot_date FROM {CMC_TABLE}
            GROUP BY snapshot_date
            HAVING COUNT(*) < ?
            """,
            (TOP_N,),
        ).fetchall()
        underpopulated = {
            datetime.strptime(r[0], "%Y-%m-%d").date() for r in underpop_rows
        }

        max_row = conn.execute(
            f"SELECT MAX(snapshot_date) FROM {CMC_TABLE}"
        ).fetchone()

    latest = (
        datetime.strptime(max_row[0], "%Y-%m-%d").date()
        if max_row and max_row[0] else None
    )

    new_dates: set = set()
    cursor = (latest + timedelta(days=1)) if latest else start
    while cursor <= today:
        new_dates.add(cursor)
        cursor += timedelta(days=1)

    return sorted(underpopulated | new_dates)


def fetch_snapshot_json(date):
    params = {
        "date": date.strftime("%Y-%m-%d"),
        "limit": TOP_N,
        "start": 1,
        "sortBy": "market_cap",
        "sortType": "desc",
        "convert": "USD",
    }

    r = requests.get(
        CMC_API_URL,
        headers=HEADERS,
        params=params,
        timeout=15,
    )

    if r.status_code != 200:
        print(f"[WARN] {date} -> HTTP {r.status_code}")
        return []

    try:
        data = r.json()
    except Exception:
        print(f"[WARN] {date} -> invalid JSON")
        return []

    payload = data.get("data") if isinstance(data, dict) else None

    if isinstance(payload, dict):
        listings = (
            payload.get("cryptoCurrencyList")
            or payload.get("listings")
            or []
        )
    elif isinstance(payload, list):
        listings = payload
    else:
        listings = []

    if not listings:
        return []

    rows = []
    for item in listings:
        try:
            quotes = item.get("quotes", [])
            usd_quote = quotes[0] if quotes else {}

            rows.append({
                "snapshot_date": date.isoformat(),
                "rank": int(item.get("cmcRank")),
                "name": item.get("name"),
                "symbol": item.get("symbol"),
                "market_cap": usd_quote.get("marketCap"),
                "price": usd_quote.get("price"),
                "circulating_supply": item.get("circulatingSupply"),
            })
        except Exception:
            continue

    return rows


def update_snapshots() -> set[str]:
    """Fetch every date from get_dates_to_fetch(), insert rows into
    daily_top, and return the union of every raw symbol seen across all
    responses this run. The returned set drives check_tv_availability's
    candidate universe.
    """
    dates = get_dates_to_fetch()
    print(f"Need to fetch {len(dates)} days")

    seen_symbols: set[str] = set()

    for d in dates:
        print(f"-> {d}")
        rows = fetch_snapshot_json(d)
        print(f"    parsed rows: {len(rows)}")

        if not rows:
            time.sleep(REQUEST_SLEEP_SECONDS)
            continue

        for r in rows:
            sym = r.get("symbol")
            if sym:
                seen_symbols.add(sym)

        new_df = pd.DataFrame(rows)

        with sqlite3.connect(MARKETDATA_DB) as conn:
            new_df = new_df.drop_duplicates(
                subset=["snapshot_date", "symbol"]
            )

            conn.executemany(
                f"""
                INSERT OR IGNORE INTO {CMC_TABLE}
                (snapshot_date, rank, name, symbol, market_cap, price, circulating_supply)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                new_df.itertuples(index=False, name=None)
            )

        print(f"    saved snapshot for {d}")

        time.sleep(REQUEST_SLEEP_SECONDS)

    print(f"[cmc_fetcher] Touched {len(seen_symbols)} unique symbols this run")
    return seen_symbols


if __name__ == "__main__":
    update_snapshots()
