"""
daily_universe.py: Build a time-varying universe from daily_top.

Reads the daily_top table, applies filters (stables/wrappers),
re-ranks by market_cap per date, and returns the top-N eligible symbols.

Useful for inspection and standalone use. rsps/data.py contains the
equivalent logic integrated with OHLCV loading.
"""

import sqlite3
import pandas as pd
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from filters import should_exclude
from rebrands_list import REBRANDS

DB_PATH = Path(__file__).resolve().parents[1] / "marketdata.db"
TABLE   = "daily_top"


def load_daily_universe(
    top_n: int = 20,
    start_date: str = "2018-01-01",
    end_date: str | None = None,
    db_path: Path | str = DB_PATH,
) -> dict[str, set[str]]:
    """
    Return a dict mapping snapshot_date (YYYY-MM-DD str) to a set of clean symbol strings
    representing the top-N eligible tokens on that date.

    Filters applied:
      * Stablecoins, BTC/ETH wrappers (via filters.should_exclude)
      * Rebrands resolved (via rebrands_list.REBRANDS)

    Parameters
    ----------
    top_n : int
        Number of tokens per date after filtering.
    start_date, end_date : str
        Inclusive date range.
    db_path : path-like
        Override DB path.

    Returns
    -------
    dict[str, set[str]]
        {date_str: {clean_symbol, ...}}
    """
    db_path = Path(db_path)
    if end_date is None:
        end_date = pd.Timestamp.utcnow().strftime("%Y-%m-%d")

    conn = sqlite3.connect(db_path)
    snap = pd.read_sql(
        f"SELECT snapshot_date, rank, symbol, name, market_cap FROM {TABLE} "
        "WHERE snapshot_date >= ? AND snapshot_date <= ? ORDER BY snapshot_date, rank",
        conn, params=[start_date, end_date],
    )
    conn.close()

    if snap.empty:
        return {}

    snap["clean"]    = snap["symbol"].str.lstrip("$").str.strip()
    snap["rebranded"] = snap["clean"].map(lambda s: REBRANDS.get(s, s))

    mask_exclude = snap.apply(
        lambda r: should_exclude(r["clean"], r["name"]) or should_exclude(r["rebranded"]),
        axis=1,
    )
    snap = snap[~mask_exclude].copy()

    snap["market_cap"] = pd.to_numeric(snap["market_cap"], errors="coerce")
    snap = snap.sort_values(["snapshot_date", "market_cap"], ascending=[True, False])
    snap["eff_rank"] = snap.groupby("snapshot_date").cumcount() + 1
    snap = snap[snap["eff_rank"] <= top_n]

    universe: dict[str, set[str]] = {}
    for date, group in snap.groupby("snapshot_date"):
        universe[str(date)] = set(group["rebranded"].tolist())

    return universe


if __name__ == "__main__":
    u = load_daily_universe(top_n=20)
    dates = sorted(u.keys())
    print(f"Loaded universe for {len(dates)} dates ({dates[0]} to {dates[-1]})")
    print(f"Latest top-20: {sorted(u[dates[-1]])}")
