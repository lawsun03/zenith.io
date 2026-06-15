"""
Fetch historical bars from the ProjectX API and write them to CSV files
that paper-mode replay can consume.

Single symbol:
    python scripts/fetch_bars.py --symbol MGC --days 30 --interval 1

Multiple symbols (saves bars/bars_MGC.csv, bars/bars_MNQ.csv, ...):
    python scripts/fetch_bars.py --symbol MGC,MNQ,ES --days 365

The --out flag overrides the output path for single-symbol fetches only.
For multi-symbol, files are always saved as bars/bars_{SYMBOL}.csv.

CSV columns: timestamp,open,high,low,close,volume
Timestamps are UTC ISO-8601.
Credentials are read from .env or the environment directly.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _load_dotenv(env_path: Path) -> None:
    if not env_path.exists():
        return
    with env_path.open() as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, val = line.partition("=")
            os.environ.setdefault(key.strip(), val.strip().strip('"').strip("'"))


async def _fetch_one(client, symbol: str, days: int, interval: int, out: Path) -> int:
    import polars as pl  # type: ignore

    print(f"Fetching {days}d of {interval}-min {symbol} bars (paginating)...", flush=True)

    all_rows: list[dict] = []
    end_time = datetime.now(timezone.utc)
    chunk_days = 13  # stay under the ~20K bar cap per call

    target_start = end_time - timedelta(days=days)

    while end_time > target_start:
        start_time = max(end_time - timedelta(days=chunk_days), target_start)
        df = await client.get_bars(
            symbol,
            interval=interval,
            unit=2,
            start_time=start_time,
            end_time=end_time,
        )
        if df is None or len(df) == 0:
            print(f"  WARNING: empty chunk {start_time.date()} -> {end_time.date()}, skipping", flush=True)
            end_time = start_time  # advance the window past the empty chunk
            continue

        df = df.with_columns(
            pl.col("timestamp")
            .dt.convert_time_zone("UTC")
            .dt.to_string("%Y-%m-%dT%H:%M:%S+00:00")
        )
        rows = df.select(["timestamp", "open", "high", "low", "close", "volume"]).to_dicts()
        all_rows.extend(rows)
        print(f"  chunk {start_time.date()} -> {end_time.date()}: {len(rows)} bars", flush=True)
        end_time = start_time

    if not all_rows:
        print(f"ERROR: no bars returned for {symbol}", file=sys.stderr)
        return 0

    # Dedupe and sort ascending by timestamp string (ISO 8601 sorts correctly).
    seen: set[str] = set()
    deduped = []
    for r in all_rows:
        if r["timestamp"] not in seen:
            seen.add(r["timestamp"])
            deduped.append(r)
    deduped.sort(key=lambda r: r["timestamp"])

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(deduped)

    print(f"  -> {len(deduped)} bars total -> {out}")
    return len(deduped)


async def _run(symbols: list[str], days: int, interval: int, out: Path | None) -> None:
    from project_x_py import ProjectX  # type: ignore

    async with ProjectX.from_env() as client:
        await client.authenticate()
        for symbol in symbols:
            dest = out if (out and len(symbols) == 1) else Path("bars") / f"bars_{symbol}.csv"
            await _fetch_one(client, symbol, days, interval, dest)


def main() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    _load_dotenv(repo_root / ".env")

    parser = argparse.ArgumentParser(description="Fetch historical bars from ProjectX API")
    parser.add_argument("--symbol", default="MGC",
                        help="Symbol or comma-separated list (e.g. MGC,MNQ,ES). Default: MGC")
    parser.add_argument("--days",     type=int, default=30, help="Days of history (default: 30)")
    parser.add_argument("--interval", type=int, default=1,  help="Bar interval in minutes (default: 1)")
    parser.add_argument("--out", default=None,
                        help="Output path for single-symbol fetch (default: bars/bars_{SYMBOL}.csv)")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]
    out = Path(args.out) if args.out else None
    asyncio.run(_run(symbols, args.days, args.interval, out))


if __name__ == "__main__":
    main()
