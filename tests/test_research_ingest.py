"""Ingestion caching: never re-download a date range already on disk.

A fake fetch_fn stands in for the Databento client (real access needs a
funded DATABENTO_API_KEY and costs money — see docs/research-loop/cost-model.md
— so it's never called from a test). What's tested is the caching contract:
which ranges get requested, and that the Parquet cache round-trips exactly.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import polars as pl
import pytest

from research.data.ingest import fetch_contract


def _bars_for_range(symbol: str, start: date, end: date) -> pl.DataFrame:
    days = (end - start).days
    ts = [datetime.combine(start, datetime.min.time(), tzinfo=timezone.utc) + timedelta(days=i) for i in range(days)]
    return pl.DataFrame({
        "ts": ts,
        "open": [100.0 + i for i in range(days)],
        "high": [100.5 + i for i in range(days)],
        "low": [99.5 + i for i in range(days)],
        "close": [100.2 + i for i in range(days)],
        "volume": [1000 + i for i in range(days)],
    })


class RecordingFetcher:
    def __init__(self):
        self.calls: list[tuple[str, date, date]] = []

    def __call__(self, symbol: str, start: date, end: date) -> pl.DataFrame:
        self.calls.append((symbol, start, end))
        return _bars_for_range(symbol, start, end)


def test_first_fetch_requests_the_full_range_and_writes_cache(tmp_path):
    fetcher = RecordingFetcher()
    df = fetch_contract(fetcher, tmp_path, "GC", "GCZ24", date(2024, 1, 1), date(2024, 1, 11))

    assert fetcher.calls == [("GCZ24", date(2024, 1, 1), date(2024, 1, 11))]
    assert len(df) == 10
    cache_file = tmp_path / "GC" / "GCZ24.parquet"
    assert cache_file.exists()


def test_second_fetch_of_an_already_covered_range_makes_no_call(tmp_path):
    fetcher = RecordingFetcher()
    fetch_contract(fetcher, tmp_path, "GC", "GCZ24", date(2024, 1, 1), date(2024, 1, 11))
    fetcher.calls.clear()

    df = fetch_contract(fetcher, tmp_path, "GC", "GCZ24", date(2024, 1, 2), date(2024, 1, 8))

    assert fetcher.calls == [], "range already on disk must not trigger a re-fetch"
    assert len(df) == 10  # returns the full cached frame


def test_extending_the_range_only_requests_the_new_tail(tmp_path):
    fetcher = RecordingFetcher()
    fetch_contract(fetcher, tmp_path, "GC", "GCZ24", date(2024, 1, 1), date(2024, 1, 11))
    fetcher.calls.clear()

    df = fetch_contract(fetcher, tmp_path, "GC", "GCZ24", date(2024, 1, 1), date(2024, 1, 21))

    assert fetcher.calls == [("GCZ24", date(2024, 1, 11), date(2024, 1, 21))], (
        "only the missing tail should be requested, not the whole range again"
    )
    assert len(df) == 20


def test_backfilling_an_earlier_range_only_requests_the_new_head(tmp_path):
    fetcher = RecordingFetcher()
    fetch_contract(fetcher, tmp_path, "GC", "GCZ24", date(2024, 1, 11), date(2024, 1, 21))
    fetcher.calls.clear()

    df = fetch_contract(fetcher, tmp_path, "GC", "GCZ24", date(2024, 1, 1), date(2024, 1, 21))

    assert fetcher.calls == [("GCZ24", date(2024, 1, 1), date(2024, 1, 11))]
    assert len(df) == 20


def test_cached_parquet_roundtrips_values_exactly(tmp_path):
    fetcher = RecordingFetcher()
    fetch_contract(fetcher, tmp_path, "GC", "GCZ24", date(2024, 1, 1), date(2024, 1, 4))

    reloaded = pl.read_parquet(tmp_path / "GC" / "GCZ24.parquet").sort("ts")
    assert reloaded["close"].to_list() == pytest.approx([100.2, 101.2, 102.2])
    assert reloaded["volume"].to_list() == [1000, 1001, 1002]
