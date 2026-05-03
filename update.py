"""
Incremental update of marketdata.db. Runs four idempotent, resumable steps
in order: cmc_fetcher, check_tv_availability, ohlcv_backfill, totals_sync.
A failure in one step prints its traceback and continues to the next.
Exit code is non-zero if any step failed.

cmc_fetcher returns the set of raw symbols it touched this run. That set
is passed straight to check_tv_availability as its candidate universe.
"""

import sys
import time
import traceback
from pathlib import Path

# Force UTF-8 stdout/stderr so non-ASCII token names from CMC (accents, CJK,
# emoji) don't crash the run under Windows' default cp1252 console codec.
sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

REPO_ROOT = Path(__file__).resolve().parent

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "cmc_snapshots"))
sys.path.insert(0, str(REPO_ROOT / "tradingview"))

# Import up front so a missing dependency fails before any step does real work.
import cmc_fetcher
import check_tv_availability
import ohlcv_backfill
import totals_sync


def _banner(text: str) -> None:
    bar = "=" * 70
    print(f"\n{bar}\n{text.center(70)}\n{bar}")


def _write_timing_file(
    timings: list[tuple[str, float]],
    total: float,
    failures: list[str],
) -> None:
    path = REPO_ROOT / "last_update_timing.txt"
    width = max((len(name) for name, _ in timings), default=len("Total")) + 1
    run_iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    lines = [f"Run: {run_iso}"]
    for name, elapsed in timings:
        lines.append(f"{name.ljust(width)}: {elapsed:.1f}s")
    lines.append("----")
    lines.append(f"{'Total'.ljust(width)}: {total:.1f}s")
    lines.append(f"FAILED steps: {', '.join(failures) if failures else 'none'}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    failures: list[str] = []
    timings: list[tuple[str, float]] = []
    overall_t0 = time.time()

    seen_symbols: set[str] = set()

    def run_step1():
        nonlocal seen_symbols
        seen_symbols = cmc_fetcher.update_snapshots()

    def run_step2():
        check_tv_availability.main(candidates=seen_symbols)

    steps = [
        ("cmc_fetcher",           run_step1),
        ("check_tv_availability", run_step2),
        ("ohlcv_backfill",        ohlcv_backfill.main),
        ("totals_sync",           totals_sync.main),
    ]

    for i, (name, fn) in enumerate(steps, 1):
        _banner(f"Step {i}/{len(steps)}: {name}")
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
            timings.append((name, time.time() - t0))
        else:
            elapsed = time.time() - t0
            timings.append((name, elapsed))
            print(f"\n[update] {name} done in {elapsed:.1f}s")

    total_elapsed = time.time() - overall_t0
    _write_timing_file(timings, total_elapsed, failures)

    _banner("Summary")
    print(f"Total elapsed: {total_elapsed:.1f}s")
    if failures:
        print(f"FAILED steps: {', '.join(failures)}")
        return 1
    print("All steps completed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
