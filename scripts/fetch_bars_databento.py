#!/usr/bin/env python3
"""
Fetch Databento 1-min OHLCV bars and append to a local CSV.

Called by the bot's /api/databento/fetch endpoint:
    python scripts/fetch_bars_databento.py \
        --symbol GC.c.0 \
        --start 2024-01-01 \
        --end 2026-05-30 \
        --out bars/bars_MGC.csv \
        [--estimate-only]

With --estimate-only: prints "$X.XXXX" to stdout and exits without fetching.

Incremental: reads the last timestamp already in --out and only fetches the
new range. Appends to the existing file so you never pay for data twice.

Output format (matches load_bars_csv expectations):
    ts,open,high,low,close,volume
    2024-01-02T18:00:00+00:00,2063.2000,2064.8000,2062.1000,2063.5000,42

Setup:
    pip install databento
    Set DATABENTO_API_KEY in .env (get key at https://databento.com/portal)
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DATASET = "GLBX.MDP3"
PRICE_SCALE = 1_000_000_000  # Databento stores prices as int64 × 1e-9 USD


def _last_csv_date(csv_path: str) -> str | None:
    """Return YYYY-MM-DD of the last bar in the CSV, or None if absent/empty."""
    p = Path(csv_path)
    if not p.exists():
        return None
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None
            chunk = min(512, size)
            f.seek(-chunk, 2)
            tail = f.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            ts = line.split(",")[0].strip()
            if len(ts) >= 10:
                return ts[:10]
        return None
    except Exception:
        return None


def _first_csv_date(csv_path: str) -> str | None:
    """Return YYYY-MM-DD of the first data bar in the CSV, or None if absent/empty."""
    p = Path(csv_path)
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                ts = line.split(",")[0].strip()
                # Skip header row
                if ts.lower() in ("ts", "timestamp", "datetime"):
                    continue
                if len(ts) >= 10:
                    return ts[:10]
        return None
    except Exception:
        return None


def _incremental_start(cached_through: str | None, requested_start: str, requested_end: str) -> str | None:
    """
    Return the fetch start date given what's already cached.
    Returns None if the CSV already covers through requested_end (full cache hit).
    Does NOT handle backfill — call _needs_backfill separately.
    """
    if cached_through is None or cached_through < requested_start:
        return requested_start
    next_day = (date.fromisoformat(cached_through) + timedelta(days=1)).isoformat()
    if next_day >= requested_end:
        return None  # full cache hit (Databento end is exclusive)
    return next_day


def _needs_backfill(csv_path: str, requested_start: str) -> bool:
    """True if the CSV exists but its earliest bar is after requested_start."""
    first = _first_csv_date(csv_path)
    return first is not None and first > requested_start


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Databento 1-min bars")
    parser.add_argument("--symbol", required=True, help="Databento symbol, e.g. GC.c.0")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    parser.add_argument("--out", required=True, help="Path to output CSV (appended)")
    parser.add_argument("--estimate-only", action="store_true", help="Print cost estimate and exit")
    args = parser.parse_args()

    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        print("DATABENTO_API_KEY not set in environment", file=sys.stderr)
        sys.exit(1)

    try:
        import databento as db
    except ImportError:
        print("databento package not installed — run: pip install databento", file=sys.stderr)
        sys.exit(1)

    client = db.Historical(api_key)

    # Find what's already cached so we only fetch the new range.
    cached_through = _last_csv_date(args.out)
    backfill = _needs_backfill(args.out, args.start)

    # If historical data is missing before what's in the file, re-fetch the full
    # range from scratch (overwrite). Otherwise use incremental append.
    if backfill:
        fetch_start = args.start
    else:
        fetch_start = _incremental_start(cached_through, args.start, args.end)

    if args.estimate_only:
        if fetch_start is None:
            # Fully cached — no cost.
            print(f"$0.0000")
            print(f"[estimate] fully cached through {cached_through}, nothing to fetch", flush=True)
            return
        # Append explicit UTC midnight so Databento treats end as start-of-day,
        # not end-of-day — bare dates are ambiguous and trigger 422 when end=today.
        end_ts = args.end if "T" in args.end else f"{args.end}T00:00:00+00:00"
        cost = client.metadata.get_cost(
            dataset=DATASET,
            symbols=[args.symbol],
            schema="ohlcv-1m",
            stype_in="continuous",
            start=fetch_start,
            end=end_ts,
        )
        print(f"${cost:.4f}")
        suffix = ""
        if backfill:
            suffix = f" (overwrite: file starts {_first_csv_date(args.out)}, need history from {args.start})"
        elif cached_through:
            suffix = f" (cached through {cached_through})"
        print(
            f"[estimate] ohlcv-1m {args.symbol} {fetch_start}->{args.end}: ${cost:.4f}{suffix}",
            flush=True,
        )
        return

    if fetch_start is None:
        print(f"[databento] fully cached through {cached_through}, skipping fetch", flush=True)
        return

    if backfill:
        print(
            f"[databento] backfill: file starts {_first_csv_date(args.out)}, "
            f"re-fetching full range {args.start}->{args.end} ...",
            flush=True,
        )
    else:
        print(f"[databento] fetching {args.symbol} {fetch_start}->{args.end} ...", flush=True)

    end_ts = args.end if "T" in args.end else f"{args.end}T00:00:00+00:00"
    data = client.timeseries.get_range(
        dataset=DATASET,
        symbols=[args.symbol],
        schema="ohlcv-1m",
        stype_in="continuous",
        start=fetch_start,
        end=end_ts,
    )

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # Overwrite if backfilling historical data; otherwise append.
    open_mode = "w" if backfill else "a"
    write_header = backfill or not out_path.exists() or out_path.stat().st_size == 0

    rows_written = 0
    with out_path.open(open_mode, newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if write_header:
            writer.writerow(["ts", "open", "high", "low", "close", "volume"])
        for record in data:
            # ts_event is nanoseconds since POSIX epoch (UTC bar close time).
            ts_dt = datetime.fromtimestamp(record.ts_event / 1_000_000_000, tz=timezone.utc)
            ts = ts_dt.isoformat()
            # Prices are stored as int64 in units of 1e-9 USD (nano-dollars).
            o = record.open  / PRICE_SCALE
            h = record.high  / PRICE_SCALE
            lo = record.low  / PRICE_SCALE
            c = record.close / PRICE_SCALE
            writer.writerow([ts, f"{o:.4f}", f"{h:.4f}", f"{lo:.4f}", f"{c:.4f}", record.volume])
            rows_written += 1

    print(
        f"[databento] wrote {rows_written} bars to {args.out} "
        f"(fetch range {fetch_start}->{args.end})",
        flush=True,
    )


if __name__ == "__main__":
    main()
