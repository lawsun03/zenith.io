#!/usr/bin/env python3
"""
Fetch Databento `trades` (tick prints with aggressor side) and aggregate them to
a per-minute order-flow sidecar CSV (buy/sell volume, delta, running CVD).

Sibling of fetch_bars_databento.py — same dataset (GLBX.MDP3), same continuous
symbols (GC.c.0 / ES.c.0 / NQ.c.0), but the `trades` schema carries the
aggressor side that OHLCV bars throw away. The output aligns 1:1 by minute with
bars_<SYMBOL>.csv so the backtest can join delta onto each bar.

    python scripts/fetch_trades_databento.py \
        --symbol GC.c.0 \
        --start 2024-01-01 \
        --end 2026-05-30 \
        --out orderflow/of_MGC.csv \
        [--estimate-only]

With --estimate-only: prints "$X.XXXX" to stdout and exits without fetching.

Output format (matches app.strategy.order_flow.CSV_HEADER):
    ts,buy_vol,sell_vol,delta,cvd,total_vol
    2024-01-02T18:00:00+00:00,120,80,40,40,205

NOTE: `trades` is a heavier schema than `ohlcv-1m` — always run --estimate-only
first. CVD is cumulative *within a fetched batch*; the backtest recomputes the
running total across the full series on load, so incremental appends are safe.

Setup: pip install databento; set DATABENTO_API_KEY (https://databento.com/portal)
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

# Reuse the aggregation + CSV writer so the delta/CVD math has one definition.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app.strategy.order_flow import aggregate_trades, write_csv  # noqa: E402

DATASET = "GLBX.MDP3"
SCHEMA = "trades"
PRICE_SCALE = 1_000_000_000  # int64 × 1e-9 USD (unused for delta, kept for parity)


def _last_csv_date(csv_path: str) -> str | None:
    """YYYY-MM-DD of the last row in the sidecar CSV, or None if absent/empty."""
    p = Path(csv_path)
    if not p.exists():
        return None
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size == 0:
                return None
            f.seek(-min(512, size), 2)
            tail = f.read().decode("utf-8", errors="replace")
        for line in reversed(tail.splitlines()):
            line = line.strip()
            if not line:
                continue
            ts = line.split(",")[0].strip()
            if len(ts) >= 10 and ts.lower() not in ("ts", "timestamp"):
                return ts[:10]
        return None
    except Exception:
        return None


def _fetch_start(cached_through: str | None, requested_start: str) -> str:
    """Fetch from the day after what's cached, or from requested_start if empty."""
    if cached_through is None or cached_through < requested_start:
        return requested_start
    from datetime import date, timedelta
    return (date.fromisoformat(cached_through) + timedelta(days=1)).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Databento trades → order-flow CSV")
    parser.add_argument("--symbol", required=True, help="Databento symbol, e.g. GC.c.0")
    parser.add_argument("--start", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end", required=True, help="YYYY-MM-DD (exclusive)")
    parser.add_argument("--out", required=True, help="Path to order-flow CSV (appended)")
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
    cached_through = _last_csv_date(args.out)
    fetch_start = _fetch_start(cached_through, args.start)
    end_ts = args.end if "T" in args.end else f"{args.end}T00:00:00+00:00"

    if fetch_start >= args.end:
        print(f"[databento] fully cached through {cached_through}, nothing to fetch", flush=True)
        return

    if args.estimate_only:
        cost = client.metadata.get_cost(
            dataset=DATASET, symbols=[args.symbol], schema=SCHEMA,
            stype_in="continuous", start=fetch_start, end=end_ts,
        )
        suffix = f" (cached through {cached_through})" if cached_through else ""
        print(f"${cost:.4f}")
        print(f"[estimate] trades {args.symbol} {fetch_start}->{args.end}: ${cost:.4f}{suffix}",
              flush=True)
        return

    print(f"[databento] fetching trades {args.symbol} {fetch_start}->{args.end} ...", flush=True)
    data = client.timeseries.get_range(
        dataset=DATASET, symbols=[args.symbol], schema=SCHEMA,
        stype_in="continuous", start=fetch_start, end=end_ts,
    )

    def _prints():
        for record in data:
            ts = datetime.fromtimestamp(record.ts_event / 1_000_000_000, tz=timezone.utc)
            # record.side is the aggressor code 'A'/'B'/'N'; record.size is contracts.
            yield ts, int(record.size), str(record.side)

    of_bars = aggregate_trades(_prints())
    n = write_csv(of_bars, args.out, append=cached_through is not None)
    print(f"[databento] wrote {n} order-flow minutes to {args.out} "
          f"(fetch range {fetch_start}->{args.end})", flush=True)


if __name__ == "__main__":
    main()
