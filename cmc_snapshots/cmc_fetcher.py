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
)


def get_dates_to_fetch():
    today = datetime.utcnow().date()
    start = datetime.strptime(START_DATE, "%Y-%m-%d").date()

    with sqlite3.connect(MARKETDATA_DB) as conn:
        row = conn.execute(
            f"SELECT MAX(snapshot_date) FROM {CMC_TABLE}"
        ).fetchone()

    if row and row[0]:
        start = datetime.strptime(row[0], "%Y-%m-%d").date() + timedelta(days=1)

    dates = []
    while start <= today:
        dates.append(start)
        start += timedelta(days=1)

    return dates


def fetch_snapshot_json(date):
    params = {
        "date": date.strftime("%Y-%m-%d"),
        "limit": 50,
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


def update_snapshots():
    dates = get_dates_to_fetch()
    print(f"Need to fetch {len(dates)} days")

    for d in dates:
        print(f"-> {d}")
        rows = fetch_snapshot_json(d)
        print(f"    parsed rows: {len(rows)}")

        if not rows:
            time.sleep(REQUEST_SLEEP_SECONDS)
            continue

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


if __name__ == "__main__":
    update_snapshots()
