from pathlib import Path

MARKETDATA_DB = str(Path(__file__).resolve().parents[1] / "marketdata.db")

CMC_TABLE = "daily_top"
TOP_N = 100

START_DATE = "2018-01-01"
REQUEST_SLEEP_SECONDS = 5

CMC_API_URL = "https://api.coinmarketcap.com/data-api/v3/cryptocurrency/listings/historical"

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "application/json",
}
