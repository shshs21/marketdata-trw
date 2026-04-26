"""
Incremental update of marketdata.db. Runs the four idempotent, resumable
pipeline steps in order: cmc_fetcher, check_tv_availability, ohlcv_backfill,
totals_sync. A failure in one step prints its traceback and continues to the
next; exit code is non-zero if any step failed.
"""

import sys
import time
import traceback
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "cmc_snapshots"))
sys.path.insert(0, str(REPO_ROOT / "tradingview"))

# Import up front so a missing dependency fails before any step does real work.
import cmc_fetcher
import check_tv_availability
import ohlcv_backfill
import totals_sync


STEPS = [
    ("cmc_fetcher",           cmc_fetcher.update_snapshots),
    ("check_tv_availability", check_tv_availability.main),
    ("ohlcv_backfill",        ohlcv_backfill.main),
    ("totals_sync",           totals_sync.main),
]


def _banner(text: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n{text.center(70)}\n{bar}")


def main() -> int:
    failures: list[str] = []
    overall_t0 = time.time()

    for i, (name, fn) in enumerate(STEPS, 1):
        _banner(f"Step {i}/{len(STEPS)}: {name}")
        t0 = time.time()
        try:
            fn()
        except KeyboardInterrupt:
            print(f"\n[update] Interrupted during {name}, exiting.")
            return 130
        except Exception as e:
            print(f"\n[update] {name} FAILED: {e.__class__.__name__}: {e}")
            traceback.print_exc()
            failures.append(name)
        else:
            print(f"\n[update] {name} done in {time.time() - t0:.1f}s")

    _banner("Summary")
    print(f"Total elapsed: {time.time() - overall_t0:.1f}s")
    if failures:
        print(f"FAILED steps: {', '.join(failures)}")
        return 1
    print("All steps completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
