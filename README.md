# Market Data Pipeline

A **crypto market database built to eliminate survivorship bias and selection bias from downstream backtests**, plus the tools to build it from scratch. This repo produces `marketdata.db`, a SQLite file containing historical market cap rankings, token metadata, and daily OHLCV bars going back to 2009 for the oldest tokens.

**This is NOT a trading tool.** It is a data pipeline. Other repos like a RSPS System read from this database to run backtests and generate signals. This repo is the **source** of that data: it records which coins were actually in the top 50 by market cap on each historical date (including coins that later crashed, delisted, or disappeared), so the RSPS can use it to eliminate survivorship bias and selection bias from its own backtests.

Shoutout to:
- **Ghe (Kung Fu Panda)**: for maintaining the `dietmarb01/tvdatafeed` fork, a code-corrected version of the upstream `rongardF/tvdatafeed` that makes reliable OHLCV fetching possible. See [his post on the topic](https://app.jointherealworld.com/chat/01GGDHGV32QWPG7FJ3N39K4FME/01GMPMB1XXDR569ZHAQB5R6G9C/01JS6EJ257V2W3CBDHCWM5SQ85).

---

## Why This Exists

If you backtest a momentum strategy on today's top 20 coins, the results will look amazing. That is because those coins are in the top 20 precisely because they went up. You are testing on winners and calling it a strategy.

This is called **survivorship bias**. The fix is to test on the coins that were actually in the top 50 on each historical date, including coins that have since crashed, been delisted, or disappeared entirely. A coin that was #5 in 2019 but no longer exists today still appears in this database for 2019.

The close cousin is **selection bias**: how you pick which coins to test on in the first place. A hand-rolled list of coins you know and like biases the sample toward your own intuition, not toward any neutral definition of "what was eligible at the time". The same fix works here: let the market pick the sample. Whoever was in the top 50 by market cap on a given date is in, no cherry picking.

That is what this pipeline builds. It pulls daily top 50 snapshots from CoinMarketCap going back to 2018, checks which of those tokens have tradeable data on TradingView, and downloads their full OHLCV history. Using this database as the sample source is what lets the RSPS eliminate both biases from its own backtests.

---

## What's in the Database

The database has 4 tables:

| Table | What it stores |
|-------|---------------|
| `daily_top50` | Daily snapshot of the top 50 coins by market cap (from CoinMarketCap), going back to 2018. Think of it as a daily leaderboard: which coins were #1, #2, ... #50 on each day. |
| `symbol_metadata` | Which coins are available on TradingView and what exchange they trade on (CRYPTO: vs INDEX:). |
| `ohlcv` | Daily OHLCV bars (Open, High, Low, Close, Volume) for every coin that ever appeared in the top 50. Same data you would see on a TradingView daily chart. |
| `market_totals` | Daily OHLCV for CRYPTOCAP:TOTALES, the total crypto market cap. Same as the TOTALES chart on TradingView. Populated by an external script, not the pipeline in this repo. |

### Why only CRYPTO: exchange tokens?

We only include tokens available on the CRYPTO: exchange (plus BTCUSD and ETHUSD on INDEX:). CRYPTO: is TradingView's aggregated feed. Tokens only appear there if they are listed on enough major exchanges with sufficient liquidity to aggregate meaningfully. If a token only shows up as BINANCE:XYZUSDT but not CRYPTO:XYZUSD, it tells you:

- **Narrow exchange presence**: essentially only traded on one or very few venues
- **Lower liquidity**: not enough cross exchange volume to warrant aggregation
- **Higher manipulation risk**: thin order books, one dominant exchange controlling the price
- **Higher delisting risk**: these tokens come and go, CRYPTO: tokens tend to be more established

This acts as a built in quality filter for the database.

---

## Project Structure

Don't worry about understanding every file. This section is just a reference. To run the whole pipeline at once, run `setup.bat` (Windows) or `setup.sh` (macOS / Linux). To run it step by step, see the "How to Rebuild the Database from Scratch" section further down. The `[step N]` markers in the tree tell you where each script fits in that flow.

```
marketdata/
│
├── marketdata.db                    The database itself (tracked in git so you inherit current build state)
├── setup.bat                        [RUN on Windows] One-shot setup (env + packages + optional full DB rebuild)
├── setup.sh                         [RUN on macOS / Linux] Same as setup.bat
├── filters.py                       Rules for excluding stablecoins, wrapped tokens, etc.
├── rebrands_list.py                 Maps old ticker names to new ones (e.g. VEN to VET)
├── requirements.txt                 Python package list
│
├── setup/
│   └── init_db.py                   [step 1] Creates or verifies the 4 database tables
│
├── cmc_snapshots/                   Daily top 50 ingestion
│   ├── cmc_config.py                CoinMarketCap API settings (imported, not run directly)
│   ├── cmc_fetcher.py               [step 2, option B] Fetches daily top 50 from the CoinMarketCap API
│   ├── cg_fetcher.py                [step 2, option C] Same idea from CoinGecko (alternative source)
│   ├── check_tv_availability.py     [step 3] Checks which coins exist on TradingView
│   └── cmc_historical_snapshots.zip Bundled historical CMC snapshot CSV (extract in place before use)
│
├── tradingview/
│   └── ohlcv_backfill.py            [step 4] Downloads full price history from TradingView
│
├── historical_csv_data/             TradingView CSV exports for tokens beyond the 5000-bar limit (gitignored)
│
└── tools/
    ├── import_cmc_snapshots.py      [step 2, option A] Imports the bundled CMC CSV into the database
    ├── import_csv_backfill.py       [step 5, optional] Imports TradingView CSV exports for capped tokens
    └── daily_universe.py            Quick check: "what were the top 20 coins on date X?"
```

---

## Setup (Do This Once)

### Quick start: `setup.bat` (Windows) or `setup.sh` (macOS / Linux)

From the project root, run the one that matches your OS:

```powershell
# Windows
setup.bat
```

```bash
# macOS / Linux
chmod +x setup.sh   # first time only
./setup.sh
```

Both scripts do the same thing. They're idempotent and safe to re-run any time: every step either does nothing (if the work is already done) or picks up where the last run left off. Pass `/yes` (Windows) or `--yes` (macOS / Linux) to auto-approve every prompt, or `/help` / `--help` for the full flag list.

Both scripts run these 5 stages in order:

1. **Environment.** If a conda env or venv is already active, it uses that. Otherwise it shows an interactive menu to pick conda, venv, or system Python (or auto-creates a `.venv` under `/yes`). You can skip the menu entirely by passing `/create-conda <name>` to go straight to creating that conda env, or `/create-venv <path>` to go straight to creating a venv at that path.
2. **Python interpreter.** Reports the resolved Python version, absolute interpreter path, and the active env. Sanity check that the previous stage produced a usable Python.
3. **Packages.** Runs `pip install -r requirements.txt`. Pip skips already-satisfied packages, so subsequent runs are fast.
4. **Database.** If `marketdata.db` exists, runs `setup/init_db.py` to verify schemas. If it's missing, prints a summary of the 4-step ingestion pipeline, warns that steps 3 and 4 are rate-limited and can take many hours (both resumable), and asks to confirm. On `y` it: creates the empty DB file, runs `init_db.py` to build the schema, extracts `cmc_historical_snapshots.zip` if only the archive is present, fills `daily_top50` from the CSV (or falls back to the CoinMarketCap API), probes TradingView for every symbol, and downloads daily OHLCV for each confirmed symbol.
5. **Smoke test.** Imports `filters.should_exclude` and `rebrands_list.REBRANDS` to confirm the interpreter, packages, and top-level modules all load cleanly.

Finally it prints a cheat sheet of the individual script commands you'd run for updates or partial re-ingestion later.

If you prefer to do it manually, follow the two steps below (and then the Rebuild section further down, if `marketdata.db` is missing).

### Step 1: Install Python

Python 3.10 or higher is needed. Both **conda** and **venv** are supported (pick whichever you already use). The goal is just to have an isolated environment active before installing packages.

**Option A: conda** (Anaconda or Miniconda)

```powershell
conda create -n quant python=3.11
conda activate quant
```

This creates an isolated conda env named `quant`. Reactivate it (`conda activate quant`) every new terminal session before running anything.

**Option B: venv** (Python standard library, no conda needed)

```powershell
# Windows
python -m venv .venv
.\.venv\Scripts\activate
```

```bash
# macOS / Linux
python3 -m venv .venv
source .venv/bin/activate
```

Either option leaves you with an isolated environment. `setup.bat` / `setup.sh` pick venv by default under `/yes` if you prefer to skip this step and let the script handle it.

### Step 2: Install Python Packages

From the project root folder, run:

```powershell
pip install -r requirements.txt
```

This installs all the libraries the pipeline needs (pandas, requests, python-dotenv, tvDatafeed).

**If you see errors**, make sure you activated your conda env or venv first.

**Note on tvDatafeed.** The TradingView data scripts (`cmc_snapshots/check_tv_availability.py` and `tradingview/ohlcv_backfill.py`) use the `tvDatafeed` package, a third party library for downloading historical OHLCV from TradingView's websocket API (the fork used here is provided by Ghe (Kung Fu Panda), as mentioned above). This is already included in `requirements.txt`, so if you ran the command above it is already installed. If you need to install it separately for any reason, the command is:

```
pip install git+https://github.com/dietmarb01/tvdatafeed.git
```


---

## How to Rebuild the Database from Scratch

If `marketdata.db` gets deleted or corrupted, follow these steps **in order**. `setup.bat` / `setup.sh` automate all of them when the DB file is missing.

### Step 1: Create the empty database

`init_db.py` is intentionally paranoid: it refuses to run unless `marketdata.db` already exists. That safety keeps it from silently creating a phantom DB in the wrong directory. So start by creating an empty file and then run the script to build the schema:

```powershell
type nul > marketdata.db
python setup/init_db.py
```

This leaves you with `marketdata.db` containing all 4 empty tables (ohlcv, market_totals, symbol_metadata, daily_top50).

### Step 2: Fill in historical market cap rankings

This step populates the `daily_top50` table. You have three options depending on what you have available.

**Option A: From the bundled CSV (fastest)**

The repo ships with `cmc_snapshots/cmc_historical_snapshots.zip`. Extract it in place (so the CSV lands right next to the zip, not in a subfolder), then run the importer:

```powershell
cd cmc_snapshots
powershell Expand-Archive -Path cmc_historical_snapshots.zip -DestinationPath .
cd ..
python tools/import_cmc_snapshots.py
```

This bulk loads every daily top 50 snapshot from 2018 onward into the `daily_top50` table. The CSV must sit at `cmc_snapshots/cmc_historical_snapshots.csv`, both `import_cmc_snapshots.py` and `check_tv_availability.py` hardcode that path.

**Option B: From the CoinMarketCap API (slower, no CSV needed)**

```powershell
python cmc_snapshots/cmc_fetcher.py
```

This fetches one day at a time from the CoinMarketCap API. It will take a while if you are fetching years of data, but it is **resumable**. You can stop and restart it.

**Option C: From CoinGecko (alternative)**

```powershell
python cmc_snapshots/cg_fetcher.py
```

Same idea, different data source. Requires a CoinGecko API key in a `.env` file.

### Step 3: Check which coins are on TradingView

```powershell
python cmc_snapshots/check_tv_availability.py
```

This goes through every coin that ever appeared in the top 50 and checks if TradingView has price data for it. Results are saved in the `symbol_metadata` table.

This is **resumable**: if you stop it, it picks up where it left off (tracked in `tv_check_done.txt`). Delete that file to start over.

### Step 4: Download price history

```powershell
python tradingview/ohlcv_backfill.py
```

This downloads daily OHLCV bars from TradingView for every coin confirmed in Step 3. It fetches up to 5000 bars per coin (roughly 13+ years of daily data).

This is also **resumable**: progress is saved in `backfill_checkpoint.txt`. Delete that file to start over.

This step takes the longest. It fetches one coin at a time with delays between requests to avoid getting rate limited by TradingView.

### Step 5: Import extended history for capped tokens (optional)

Some tokens have more than 5000 days of history, which is TradingView's API limit. The backfill script logs these to `tradingview/max_bars_tokens.txt` and creates a `historical_csv_data/` folder.

To fill in the older data:

1. Open each token's chart on TradingView (daily timeframe)
2. Export the full CSV using the chart export button
3. Drop the CSV files into `historical_csv_data/`
4. Run:

```powershell
python tools/import_csv_backfill.py
```

Tokens with a matching CSV are imported and removed from the txt file. Tokens without a CSV are left for later. When all tokens have been imported, the txt file is deleted.

### Done

After these 5 steps, `marketdata.db` is fully populated and ready for use by other repos.

---

## Filters and Rebrands

Two files in the root handle data cleanup. They are meant to be imported by your RSPS system (or any downstream strategy repo), so the same exclusion and rebrand rules apply everywhere that consumes this database.

**filters.py**: Defines which coins to exclude from the universe. This covers several categories:

| Category | Examples | Why excluded |
|----------|----------|-------------|
| Stablecoins | USDT, USDC, DAI, BUSD | Pegged to fiat, no independent price action |
| Wrapped BTC/ETH | WBTC, STETH, CBETH | Mirror the underlying asset, not independent |
| Liquid staked tokens | MSOL, JITOSOL, SAVAX | Same as above, staking derivatives |
| Exchange tokens | BNB, OKB, FTT, LEO | Platform tokens, not pure crypto assets |
| Wrapped L1 tokens | WMATIC, WAVAX, WBNB | Wrapped versions of existing L1 tokens |
| Gold pegged | XAUT, PAXG | Commodity pegged, not crypto |

You would not want any of these in a momentum strategy. They either track something else or have artificial price stability.

**rebrands_list.py**: Some coins changed their ticker over the years. For example, VeChain went from VEN to VET, IOTA went from MIOTA to IOTA, and Nano went from NANO to XNO. This mapping ensures historical data lines up correctly so a coin is not double counted under its old and new name.

---

## Contact

DM or tag **shshs21** in IM💎 section.
