"""
Fetch historical bars from the ProjectX API and write them to CSV files
that paper-mode replay can consume.

Single symbol:
    python scripts/fetch_bars.py --symbol MGC --days 30 --interval 1

Multiple symbols (saves bars_MGC.csv, bars_MNQ.csv, ...):
    python scripts/fetch_bars.py --symbol MGC,MNQ,ES --days 365

The --out flag overrides the output path for single-symbol fetches only.
For multi-symbol, files are always saved as bars_{SYMBOL}.csv.

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

    print(f"Fetching {days}d of {interval}-min {symbol} bars...", flush=True)
    df = await client.get_bars(symbol, days=days, interval=interval, unit=2)

    if df is None or len(df) == 0:
        print(f"ERROR: no bars returned for {symbol}", file=sys.stderr)
        return 0

    df = df.with_columns(
        pl.col("timestamp").dt.convert_time_zone("UTC").dt.to_string("%Y-%m-%dT%H:%M:%S+00:00")
    )
    rows = df.select(["timestamp", "open", "high", "low", "close", "volume"]).to_dicts()

    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["timestamp", "open", "high", "low", "close", "volume"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"  -> {len(rows)} bars -> {out}")
    return len(rows)


async def _run(symbols: list[str], days: int, interval: int, out: Path | None) -> None:
    from project_x_py import ProjectX  # type: ignore

    async with ProjectX.from_env() as client:
        await client.authenticate()
        for symbol in symbols:
            dest = out if (out and len(symbols) == 1) else Path(f"bars_{symbol}.csv")
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
                        help="Output path for single-symbol fetch (default: bars_{SYMBOL}.csv)")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbol.split(",") if s.strip()]
    out = Path(args.out) if args.out else None
    asyncio.run(_run(symbols, args.days, args.interval, out))


if __name__ == "__main__":
    main()
