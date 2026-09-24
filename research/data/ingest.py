"""Databento GLBX.MDP3 ingestion: per-contract 1-minute bars, cached to
Parquet, never re-fetching a date range already on disk.

`fetch_fn` is injected rather than this module importing `databento`
directly — keeps every test offline and keeps this module ignorant of
whether it's talking to the real API or a fixture. `make_databento_fetch_fn`
below is the one adapter that actually calls the SDK; nothing else here does.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable

import polars as pl

FetchFn = Callable[[str, date, date], pl.DataFrame]

BAR_SCHEMA = {
    "ts": pl.Datetime(time_unit="ns", time_zone="UTC"),
    "open": pl.Float64,
    "high": pl.Float64,
    "low": pl.Float64,
    "close": pl.Float64,
    "volume": pl.UInt64,
}


def _empty_bars() -> pl.DataFrame:
    return pl.DataFrame(schema=BAR_SCHEMA)


def _missing_ranges(
    coverage: tuple[date, date] | None,
    start: date,
    end: date,
) -> list[tuple[date, date]]:
    """Sub-ranges of [start, end) not already covered on disk.

    Coverage is tracked as a single contiguous [min, max] interval, matching
    the existing scripts/fetch_bars_databento.py convention — gaps inside an
    already-fetched range aren't expected or handled.
    """
    if coverage is None:
        return [(start, end)] if start < end else []

    cov_start, cov_end = coverage
    ranges: list[tuple[date, date]] = []
    if start < cov_start:
        ranges.append((start, cov_start))
    day_after_cov = cov_end + timedelta(days=1)
    if end > day_after_cov:
        ranges.append((max(day_after_cov, start), end))
    return ranges


def fetch_contract(
    fetch_fn: FetchFn,
    raw_dir: Path,
    root: str,
    raw_symbol: str,
    start: date,
    end: date,
    *,
    cache_key: str | None = None,
) -> pl.DataFrame:
    """Ensure `raw_dir/root/cache_key.parquet` covers [start, end), fetching
    only what's missing (via `raw_symbol`, the string actually sent to
    `fetch_fn`), and return the full cached frame.

    `cache_key` names the cache file when it must differ from `raw_symbol`.
    This matters because Databento's GLBX.MDP3 raw_symbol spells a futures
    contract with a SINGLE-digit year (research/data/contracts.py — e.g.
    "GCZ3" for December Gold, verified against the live API), which repeats
    every decade: 2013 and 2023 both spell "GCZ3", disambiguated only by
    the request's date window, never by the string itself. A caller
    fetching a range spanning more than one decade MUST pass a
    decade-unique `cache_key` (e.g. the 4-digit-year form) — otherwise two
    unrelated contracts silently merge into one cache file, sorted
    together by timestamp with no error. Defaults to `raw_symbol` for
    callers who know their range can't cross a decade boundary.
    """
    key = cache_key if cache_key is not None else raw_symbol
    path = raw_dir / root / f"{key}.parquet"

    existing = pl.read_parquet(path) if path.exists() else None
    coverage = None
    if existing is not None and not existing.is_empty():
        coverage = (existing["ts"].dt.date().min(), existing["ts"].dt.date().max())

    missing = _missing_ranges(coverage, start, end)
    if not missing:
        return existing if existing is not None else _empty_bars()

    fetched = [fetch_fn(raw_symbol, s, e) for s, e in missing]
    fetched = [f for f in fetched if not f.is_empty()]

    parts = ([existing] if existing is not None and not existing.is_empty() else []) + fetched
    if not parts:
        return _empty_bars()

    combined = pl.concat(parts).unique(subset=["ts"]).sort("ts")
    path.parent.mkdir(parents=True, exist_ok=True)
    combined.write_parquet(path)
    return combined


def make_databento_fetch_fn(api_key: str, dataset: str) -> FetchFn:
    """Adapter to the real Databento SDK. Not exercised by tests — those
    inject a fake FetchFn. Requires `pip install databento` and a funded
    DATABENTO_API_KEY; pulling the full corpus costs real money (see
    docs/research-loop/cost-model.md) and is a deliberate, human-run action.
    """
    import databento as db

    client = db.Historical(api_key)
    PRICE_SCALE = 1_000_000_000  # int64 prices are nano-dollars

    def _fetch(raw_symbol: str, start: date, end: date) -> pl.DataFrame:
        records: Iterable = client.timeseries.get_range(
            dataset=dataset,
            symbols=[raw_symbol],
            schema="ohlcv-1m",
            stype_in="raw_symbol",
            start=start.isoformat(),
            end=end.isoformat(),
        )
        rows = [
            {
                "ts": r.ts_event,
                "open": r.open / PRICE_SCALE,
                "high": r.high / PRICE_SCALE,
                "low": r.low / PRICE_SCALE,
                "close": r.close / PRICE_SCALE,
                "volume": r.volume,
            }
            for r in records
        ]
        if not rows:
            return _empty_bars()
        return pl.DataFrame(rows).with_columns(
            pl.from_epoch(pl.col("ts"), time_unit="ns").dt.replace_time_zone("UTC")
        )

    return _fetch
