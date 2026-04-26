#!/usr/bin/env bash
# setup.sh: Idempotent environment setup for the marketdata pipeline. Run "./setup.sh --help" for options.

set -u

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

AUTO_YES=0
CREATE_CONDA=""
CREATE_VENV=""

usage() {
    cat <<EOF

Usage: ./setup.sh [options]
  --yes                       Skip confirmation prompts (defaults to venv at .venv if no env active)
  --create-conda <name>       Create and activate a conda env named <name> (python=3.11)
  --create-venv <path>        Create and activate a venv at <path>
  --help, -h                  Show this help

If neither --create-conda nor --create-venv is passed, the script:
  1. Uses any already-active env (conda, venv, etc), OR
  2. If no env is active, prompts you to pick conda / venv / system Python.
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes)          AUTO_YES=1; shift ;;
        --create-conda) CREATE_CONDA="$2"; shift 2 ;;
        --create-venv)  CREATE_VENV="$2"; shift 2 ;;
        --help|-h)      usage; exit 0 ;;
        *)              echo "Unknown argument: $1"; echo "Run ./setup.sh --help for usage."; exit 1 ;;
    esac
done

if [[ -n "$CREATE_CONDA" && -n "$CREATE_VENV" ]]; then
    echo "ERROR: pass at most one of --create-conda or --create-venv."
    exit 1
fi


echo
echo "=== Step 1: Environment ==="

# Picks the conda activation machinery and creates the env if needed.
create_conda_env() {
    echo "  Target: conda env \"$CREATE_CONDA\""
    if ! command -v conda >/dev/null 2>&1; then
        echo "  ERROR: conda not on PATH. Install Miniconda/Anaconda first, or use --create-venv."
        exit 1
    fi
    # conda activate inside a script needs conda.sh sourced first.
    # shellcheck disable=SC1091
    source "$(conda info --base)/etc/profile.d/conda.sh"
    if conda env list | awk '{print $1}' | grep -Fxq "$CREATE_CONDA"; then
        echo "  Env already exists."
    else
        echo "  Env does not exist. Creating with python=3.11 ..."
        conda create -n "$CREATE_CONDA" python=3.11 -y || { echo "  ERROR: conda create failed."; exit 1; }
    fi
    echo "  Activating ..."
    conda activate "$CREATE_CONDA" || { echo "  ERROR: conda activate failed."; exit 1; }
}

create_venv_env() {
    echo "  Target: venv at \"$CREATE_VENV\""
    local PY
    if command -v python3 >/dev/null 2>&1; then
        PY="python3"
    elif command -v python >/dev/null 2>&1; then
        PY="python"
    else
        echo "  ERROR: python not on PATH. Install Python first or use --create-conda."
        exit 1
    fi
    if [[ -f "$CREATE_VENV/bin/activate" ]]; then
        echo "  Venv already exists."
    else
        echo "  Creating ..."
        "$PY" -m venv "$CREATE_VENV" || { echo "  ERROR: python -m venv failed."; exit 1; }
    fi
    echo "  Activating ..."
    # shellcheck disable=SC1091
    source "$CREATE_VENV/bin/activate" || { echo "  ERROR: venv activate failed."; exit 1; }
}

# Explicit flags win.
if [[ -n "$CREATE_CONDA" ]]; then
    create_conda_env
elif [[ -n "$CREATE_VENV" ]]; then
    create_venv_env
# Already-active env wins next.
elif [[ -n "${CONDA_DEFAULT_ENV:-}" ]]; then
    echo "  Using already-active conda env: $CONDA_DEFAULT_ENV"
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
    echo "  Using already-active venv: $VIRTUAL_ENV"
else
    # No env active and no flag. Prompt or auto-default.
    echo "  No Python env is currently active."
    if [[ "$AUTO_YES" -eq 1 ]]; then
        echo "  --yes given, defaulting to venv at .venv"
        CREATE_VENV=".venv"
        create_venv_env
    else
        echo
        echo "  Choose how to proceed:"
        echo "    [1] Create + use a new conda env"
        echo "    [2] Create + use a new venv in this repo"
        echo "    [3] Use the current system Python (not recommended)"
        echo "    [4] Cancel"
        read -r -p "  Choice [1-4]: " CHOICE
        case "$CHOICE" in
            1)  read -r -p "  Conda env name [quant]: " CONDA_NAME
                CREATE_CONDA="${CONDA_NAME:-quant}"
                create_conda_env
                ;;
            2)  read -r -p "  Venv path [.venv]: " VENV_PATH
                CREATE_VENV="${VENV_PATH:-.venv}"
                create_venv_env
                ;;
            3)  echo "  Proceeding with system Python."
                ;;
            *)  echo "  Cancelled."; exit 0 ;;
        esac
    fi
fi


echo
echo "=== Step 2: Python interpreter ==="
if command -v python >/dev/null 2>&1; then
    PY_CMD="python"
elif command -v python3 >/dev/null 2>&1; then
    PY_CMD="python3"
else
    echo "  ERROR: python not on PATH after env resolution."
    exit 1
fi
echo "  version:     $("$PY_CMD" --version 2>&1)"
echo "  interpreter: $("$PY_CMD" -c 'import sys; print(sys.executable)')"
if [[ -n "${CONDA_DEFAULT_ENV:-}" ]]; then
    echo "  active env:  conda :: $CONDA_DEFAULT_ENV"
elif [[ -n "${VIRTUAL_ENV:-}" ]]; then
    echo "  active env:  venv :: $VIRTUAL_ENV"
else
    echo "  active env:  (none detected, using system Python or another tool)"
fi


echo
echo "=== Step 3: Python packages ==="
echo "  Running: python -m pip install -r requirements.txt"
echo "  (pip skips packages that are already satisfied at the required version)"
"$PY_CMD" -m pip install -r "$REPO_ROOT/requirements.txt" || { echo "  ERROR: pip install failed."; exit 1; }


echo
echo "=== Step 4: Database ==="
if [[ -f "$REPO_ROOT/marketdata.db" ]]; then
    echo "  marketdata.db already exists, verifying schema ..."
    "$PY_CMD" "$REPO_ROOT/setup/init_db.py" || { echo "  ERROR: schema verification failed."; exit 1; }
else
    echo "  marketdata.db not found."
    echo
    echo "  This repo builds a survivorship-bias-free crypto market database."
    echo "  Choosing to rebuild from scratch will run the full ingestion pipeline:"
    echo "    1. setup/init_db.py                           Create the 4 empty tables."
    echo "    2. tools/import_cmc_snapshots.py      OR"
    echo "       cmc_snapshots/cmc_fetcher.py               Fill daily_top (CSV is fast, API is slow)."
    echo "    3. cmc_snapshots/check_tv_availability.py     Probe TradingView for every unique symbol."
    echo "    4. tradingview/ohlcv_backfill.py              Download daily OHLCV for each confirmed symbol."
    echo
    echo "  Step 3 and Step 4 are rate-limited and can take many hours end-to-end."
    echo "  Both are resumable, stop with ctrl+c and re-run later to continue from checkpoint."
    echo
    if [[ "$AUTO_YES" -eq 1 ]]; then
        REPLY="y"
    else
        read -r -p "  Run the full rebuild now? [y/N] " REPLY
    fi
    if [[ ! "$REPLY" =~ ^[Yy]$ ]]; then
        echo "  Skipped. Run the scripts above manually when ready, or see README."
    else
        echo
        echo "  Creating empty marketdata.db ..."
        touch "$REPO_ROOT/marketdata.db" || { echo "  ERROR: could not create marketdata.db."; exit 1; }

        echo
        echo "  [1/4] setup/init_db.py"
        "$PY_CMD" "$REPO_ROOT/setup/init_db.py" || { echo "  ERROR: init_db.py failed."; exit 1; }

        # Prefer the CSV source. Offer to unzip cmc_historical_snapshots.zip if only the archive is present.
        if [[ ! -f "$REPO_ROOT/cmc_snapshots/cmc_historical_snapshots.csv" ]]; then
            if [[ -f "$REPO_ROOT/cmc_snapshots/cmc_historical_snapshots.zip" ]]; then
                echo
                echo "  cmc_historical_snapshots.csv is missing, but cmc_historical_snapshots.zip is present."
                echo "  The CSV must sit next to the zip at cmc_snapshots/cmc_historical_snapshots.csv."
                if [[ "$AUTO_YES" -eq 1 ]]; then
                    EXTRACT="y"
                else
                    read -r -p "  Extract it into cmc_snapshots/ now? [Y/n] " EXTRACT
                fi
                if [[ ! "$EXTRACT" =~ ^[Nn]$ ]]; then
                    echo "  Extracting cmc_historical_snapshots.zip ..."
                    if command -v unzip >/dev/null 2>&1; then
                        unzip -o "$REPO_ROOT/cmc_snapshots/cmc_historical_snapshots.zip" -d "$REPO_ROOT/cmc_snapshots" || { echo "  ERROR: extraction failed."; exit 1; }
                    else
                        # Fallback: use Python's zipfile module, always available.
                        "$PY_CMD" -m zipfile -e "$REPO_ROOT/cmc_snapshots/cmc_historical_snapshots.zip" "$REPO_ROOT/cmc_snapshots" || { echo "  ERROR: extraction failed."; exit 1; }
                    fi
                fi
            fi
        fi

        if [[ -f "$REPO_ROOT/cmc_snapshots/cmc_historical_snapshots.csv" ]]; then
            echo
            echo "  [2/4] tools/import_cmc_snapshots.py  (CSV source)"
            "$PY_CMD" "$REPO_ROOT/tools/import_cmc_snapshots.py" || { echo "  ERROR: import_cmc_snapshots.py failed."; exit 1; }
        else
            echo
            echo "  No CSV available, falling back to the CoinMarketCap API."
            echo "  [2/4] cmc_snapshots/cmc_fetcher.py  (CoinMarketCap API, slow)"
            "$PY_CMD" "$REPO_ROOT/cmc_snapshots/cmc_fetcher.py" || { echo "  ERROR: cmc_fetcher.py failed."; exit 1; }
        fi

        echo
        echo "  [3/4] cmc_snapshots/check_tv_availability.py  (long, resumable)"
        "$PY_CMD" "$REPO_ROOT/cmc_snapshots/check_tv_availability.py" || { echo "  ERROR: check_tv_availability.py failed."; exit 1; }

        echo
        echo "  [4/4] tradingview/ohlcv_backfill.py  (long, resumable)"
        "$PY_CMD" "$REPO_ROOT/tradingview/ohlcv_backfill.py" || { echo "  ERROR: ohlcv_backfill.py failed."; exit 1; }

        echo
        echo "  Rebuild complete. Tokens capped at TradingView's 5000-bar limit (if any)"
        echo "  are listed in tradingview/max_bars_tokens.txt. To backfill their older"
        echo "  history, export CSVs from TradingView into historical_csv_data/ and run"
        echo "  tools/import_csv_backfill.py."
    fi
fi


echo
echo "=== Step 5: Smoke test ==="
PYTHONPATH="$REPO_ROOT" "$PY_CMD" -c "from filters import should_exclude; from rebrands_list import REBRANDS; print('  imports OK, rebrands registered:', len(REBRANDS))" || { echo "  ERROR: smoke test failed."; exit 1; }


echo
echo "=== DONE ==="
echo "Next steps:"
echo "    python setup/init_db.py                           (create or verify DB schema)"
echo "    python tools/import_cmc_snapshots.py              (bulk import historical CMC CSV)"
echo "    python cmc_snapshots/cmc_fetcher.py               (fetch daily top 50 from CoinMarketCap)"
echo "    python cmc_snapshots/cg_fetcher.py                (alternative daily source, CoinGecko)"
echo "    python cmc_snapshots/check_tv_availability.py     (check TradingView listing per symbol)"
echo "    python tradingview/ohlcv_backfill.py              (download OHLCV history)"
echo "    python tools/import_csv_backfill.py               (import TradingView CSV exports)"
echo
