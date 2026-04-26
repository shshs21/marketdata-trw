@echo off
setlocal enabledelayedexpansion

REM setup.bat: Idempotent environment setup for the marketdata pipeline. Run "setup.bat /help" for options.

set "REPO_ROOT=%~dp0"

set "AUTO_YES=0"
set "CREATE_CONDA="
set "CREATE_VENV="

:parse_args
if "%~1"=="" goto :args_done
if /i "%~1"=="/yes"           ( set "AUTO_YES=1"          & shift & goto :parse_args )
if /i "%~1"=="/create-conda"  ( set "CREATE_CONDA=%~2"    & shift & shift & goto :parse_args )
if /i "%~1"=="/create-venv"   ( set "CREATE_VENV=%~2"     & shift & shift & goto :parse_args )
if /i "%~1"=="/help"          goto :help
if /i "%~1"=="/?"             goto :help
echo Unknown argument: %~1
echo Run "setup.bat /help" for usage.
exit /b 1
:help
echo.
echo Usage: setup.bat [options]
echo   /yes                       Skip confirmation prompts (defaults to venv at .venv if no env active)
echo   /create-conda ^<name^>       Create and activate a conda env named ^<name^> (python=3.11)
echo   /create-venv ^<path^>        Create and activate a venv at ^<path^>
echo   /help, /?                  Show this help
echo.
echo If neither /create-conda nor /create-venv is passed, the script:
echo   1. Uses any already-active env (conda, venv, etc), OR
echo   2. If no env is active, prompts you to pick conda / venv / system Python.
exit /b 0
:args_done


if not "%CREATE_CONDA%"=="" if not "%CREATE_VENV%"=="" (
    echo ERROR: pass at most one of /create-conda or /create-venv.
    exit /b 1
)

echo.
echo === Step 1: Environment ===

REM Explicit flags win.
if not "%CREATE_CONDA%"=="" goto :create_conda
if not "%CREATE_VENV%"==""  goto :create_venv

REM Already-active env wins next.
if defined CONDA_DEFAULT_ENV (
    echo   Using already-active conda env: %CONDA_DEFAULT_ENV%
    goto :env_report
)
if defined VIRTUAL_ENV (
    echo   Using already-active venv: %VIRTUAL_ENV%
    goto :env_report
)

REM No env active and no flag. Prompt or auto-default.
echo   No Python env is currently active.
if "%AUTO_YES%"=="1" (
    echo   /yes given, defaulting to venv at .venv
    set "CREATE_VENV=.venv"
    goto :create_venv
)
echo.
echo   Choose how to proceed:
echo     [1] Create + use a new conda env
echo     [2] Create + use a new venv in this repo
echo     [3] Use the current system Python (not recommended)
echo     [4] Cancel
set "CHOICE="
set /p "CHOICE=  Choice [1-4]: "
if "!CHOICE!"=="1" (
    set "CONDA_NAME="
    set /p "CONDA_NAME=  Conda env name [quant]: "
    if "!CONDA_NAME!"=="" set "CONDA_NAME=quant"
    set "CREATE_CONDA=!CONDA_NAME!"
    goto :create_conda
)
if "!CHOICE!"=="2" (
    set "VENV_PATH="
    set /p "VENV_PATH=  Venv path [.venv]: "
    if "!VENV_PATH!"=="" set "VENV_PATH=.venv"
    set "CREATE_VENV=!VENV_PATH!"
    goto :create_venv
)
if "!CHOICE!"=="3" (
    echo   Proceeding with system Python.
    goto :env_report
)
echo   Cancelled.
exit /b 0


:create_conda
echo   Target: conda env "%CREATE_CONDA%"
where conda >nul 2>&1
if errorlevel 1 (
    echo   ERROR: conda not on PATH. Install Anaconda or Miniconda first, or use /create-venv.
    exit /b 1
)
call conda env list | findstr /R /C:"^%CREATE_CONDA% " /C:"^%CREATE_CONDA%$" >nul
if errorlevel 1 (
    echo   Env does not exist. Creating with python=3.11 ...
    call conda create -n %CREATE_CONDA% python=3.11 -y
    if errorlevel 1 ( echo   ERROR: conda create failed. & exit /b 1 )
) else (
    echo   Env already exists.
)
echo   Activating ...
call conda activate %CREATE_CONDA%
if errorlevel 1 ( echo   ERROR: conda activate failed. & exit /b 1 )
goto :env_report


:create_venv
echo   Target: venv at "%CREATE_VENV%"
where python >nul 2>&1
if errorlevel 1 (
    echo   ERROR: python not on PATH. Install Python first or use /create-conda.
    exit /b 1
)
if exist "%CREATE_VENV%\Scripts\activate.bat" (
    echo   Venv already exists.
) else (
    echo   Creating ...
    python -m venv "%CREATE_VENV%"
    if errorlevel 1 ( echo   ERROR: python -m venv failed. & exit /b 1 )
)
echo   Activating ...
call "%CREATE_VENV%\Scripts\activate.bat"
if errorlevel 1 ( echo   ERROR: venv activate failed. & exit /b 1 )
goto :env_report


:env_report
echo.
echo === Step 2: Python interpreter ===
where python >nul 2>&1
if errorlevel 1 (
    echo   ERROR: python not on PATH after env resolution.
    exit /b 1
)
for /f "delims=" %%V in ('python --version 2^>^&1') do echo   version:     %%V
for /f "delims=" %%P in ('python -c "import sys; print(sys.executable)"') do echo   interpreter: %%P
if defined CONDA_DEFAULT_ENV (
    echo   active env:  conda :: %CONDA_DEFAULT_ENV%
) else if defined VIRTUAL_ENV (
    echo   active env:  venv :: %VIRTUAL_ENV%
) else (
    echo   active env:  ^(none detected, using system Python or another tool^)
)


echo.
echo === Step 3: Python packages ===
echo   Running: python -m pip install -r requirements.txt
echo   (pip skips packages that are already satisfied at the required version)
python -m pip install -r "%REPO_ROOT%requirements.txt"
if errorlevel 1 ( echo   ERROR: pip install failed. & exit /b 1 )


echo.
echo === Step 4: Database ===
if exist "%REPO_ROOT%marketdata.db" (
    echo   marketdata.db already exists, verifying schema ...
    python "%REPO_ROOT%setup\init_db.py"
    if errorlevel 1 ( echo   ERROR: schema verification failed. & exit /b 1 )
    goto :db_done
)

echo   marketdata.db not found.
echo.
echo   This repo builds a survivorship-bias-free crypto market database.
echo   Choosing to rebuild from scratch will run the full ingestion pipeline:
echo     1. setup\init_db.py                           Create the 4 empty tables.
echo     2. tools\import_cmc_snapshots.py      OR
echo        cmc_snapshots\cmc_fetcher.py               Fill daily_top50 (CSV is fast, API is slow).
echo     3. cmc_snapshots\check_tv_availability.py     Probe TradingView for every unique symbol.
echo     4. tradingview\ohlcv_backfill.py              Download daily OHLCV for each confirmed symbol.
echo.
echo   Step 3 and Step 4 are rate-limited and can take many hours end-to-end.
echo   Both are resumable, stop with ctrl+c and re-run later to continue from checkpoint.
echo.
if "%AUTO_YES%"=="1" (
    set "REPLY=y"
) else (
    set /p "REPLY=  Run the full rebuild now? [y/N] "
)
if /i not "!REPLY!"=="y" (
    echo   Skipped. Run the scripts above manually when ready, or see README.
    goto :db_done
)

echo.
echo   Creating empty marketdata.db ...
type nul > "%REPO_ROOT%marketdata.db"
if errorlevel 1 ( echo   ERROR: could not create marketdata.db. & exit /b 1 )

echo.
echo   [1/4] setup\init_db.py
python "%REPO_ROOT%setup\init_db.py"
if errorlevel 1 ( echo   ERROR: init_db.py failed. & exit /b 1 )

REM Prefer the CSV source. Offer to unzip cmc_historical_snapshots.zip if only the archive is present.
if not exist "%REPO_ROOT%cmc_snapshots\cmc_historical_snapshots.csv" (
    if exist "%REPO_ROOT%cmc_snapshots\cmc_historical_snapshots.zip" (
        echo.
        echo   cmc_historical_snapshots.csv is missing, but cmc_historical_snapshots.zip is present.
        echo   The CSV must sit next to the zip at cmc_snapshots\cmc_historical_snapshots.csv.
        if "%AUTO_YES%"=="1" (
            set "EXTRACT=y"
        ) else (
            set /p "EXTRACT=  Extract it into cmc_snapshots\ now? [Y/n] "
        )
        if /i not "!EXTRACT!"=="n" (
            echo   Extracting cmc_historical_snapshots.zip ...
            powershell -NoProfile -Command "Expand-Archive -Force -Path '%REPO_ROOT%cmc_snapshots\cmc_historical_snapshots.zip' -DestinationPath '%REPO_ROOT%cmc_snapshots'"
            if errorlevel 1 ( echo   ERROR: extraction failed. & exit /b 1 )
        )
    )
)

if exist "%REPO_ROOT%cmc_snapshots\cmc_historical_snapshots.csv" (
    echo.
    echo   [2/4] tools\import_cmc_snapshots.py  (CSV source)
    python "%REPO_ROOT%tools\import_cmc_snapshots.py"
    if errorlevel 1 ( echo   ERROR: import_cmc_snapshots.py failed. & exit /b 1 )
) else (
    echo.
    echo   No CSV available, falling back to the CoinMarketCap API.
    echo   [2/4] cmc_snapshots\cmc_fetcher.py  (CoinMarketCap API, slow)
    python "%REPO_ROOT%cmc_snapshots\cmc_fetcher.py"
    if errorlevel 1 ( echo   ERROR: cmc_fetcher.py failed. & exit /b 1 )
)

echo.
echo   [3/4] cmc_snapshots\check_tv_availability.py  (long, resumable)
python "%REPO_ROOT%cmc_snapshots\check_tv_availability.py"
if errorlevel 1 ( echo   ERROR: check_tv_availability.py failed. & exit /b 1 )

echo.
echo   [4/4] tradingview\ohlcv_backfill.py  (long, resumable)
python "%REPO_ROOT%tradingview\ohlcv_backfill.py"
if errorlevel 1 ( echo   ERROR: ohlcv_backfill.py failed. & exit /b 1 )

echo.
echo   Rebuild complete. Tokens capped at TradingView's 5000-bar limit (if any)
echo   are listed in tradingview\max_bars_tokens.txt. To backfill their older
echo   history, export CSVs from TradingView into historical_csv_data\ and run
echo   tools\import_csv_backfill.py.

:db_done


echo.
echo === Step 5: Smoke test ===
set "PYTHONPATH=%REPO_ROOT%;%PYTHONPATH%"
python -c "from filters import should_exclude; from rebrands_list import REBRANDS; print('  imports OK, rebrands registered:', len(REBRANDS))"
if errorlevel 1 ( echo   ERROR: smoke test failed. & exit /b 1 )


echo.
echo === DONE ===
echo Next steps:
echo     python setup\init_db.py                           (create or verify DB schema)
echo     python tools\import_cmc_snapshots.py              (bulk import historical CMC CSV)
echo     python cmc_snapshots\cmc_fetcher.py               (fetch daily top 50 from CoinMarketCap)
echo     python cmc_snapshots\cg_fetcher.py                (alternative daily source, CoinGecko)
echo     python cmc_snapshots\check_tv_availability.py     (check TradingView listing per symbol)
echo     python tradingview\ohlcv_backfill.py              (download OHLCV history)
echo     python tools\import_csv_backfill.py               (import TradingView CSV exports)
echo.

endlocal
