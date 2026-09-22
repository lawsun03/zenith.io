"""Acceptance test: loading 16 years of 1-minute bars for all three
instruments completes in under 30 seconds from warm cache.

Real ingested data isn't available in this environment, so this builds a
synthetic warm Parquet cache at a representative scale (the full accessible
corpus window from the frozen folds, ~1000 bars/session) and times only the
load_bars() calls — fixture generation happens before the clock starts.
"""
from __future__ import annotations

import time
from datetime import date, datetime, timedelta
from pathlib import Path

import polars as pl
import pytest

from research.data.folds import load_folds
from research.data.instruments import ROOTS
from research.data.loader import load_bars

BARS_PER_SESSION = 1000


def _session_dates(start: date, end: date) -> list[date]:
    dates = []
    d = start
    one_day = timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:
            dates.append(d)
        d += one_day
    return dates


def _write_synthetic_corpus(continuous_root: Path, root: str, start: date, end: date) -> int:
    sessions = _session_dates(start, end)
    sessions_df = pl.DataFrame({"session_date": sessions})
    minutes_df = pl.DataFrame({"minute": pl.arange(0, BARS_PER_SESSION, eager=True)})
    grid = sessions_df.join(minutes_df, how="cross")
    n = len(grid)

    grid = grid.with_columns(
        (pl.col("session_date").cast(pl.Datetime) + pl.duration(minutes=pl.col("minute"))).alias("ts")
    )
    idx = pl.arange(0, n, eager=True)
    close = 100.0 + (idx % 1000).cast(pl.Float64) * 0.1
    df = grid.select(
        "ts",
        (close - 0.05).alias("open"),
        (close + 0.1).alias("high"),
        (close - 0.1).alias("low"),
        close.alias("close"),
        ((idx % 3000) + 1).cast(pl.UInt64).alias("volume"),
        pl.lit(f"{root}SYN").alias("contract"),
    )

    out_dir = continuous_root / root / "corpus"
    out_dir.mkdir(parents=True, exist_ok=True)
    df.write_parquet(out_dir / "back_adjusted.parquet")
    return n


@pytest.fixture(scope="module")
def warm_cache(tmp_path_factory):
    continuous_root = tmp_path_factory.mktemp("warm_cache")
    folds = load_folds()
    total_rows = 0
    for root in ROOTS:
        total_rows += _write_synthetic_corpus(continuous_root, root, folds.corpus_start, folds.holdout_start - timedelta(days=1))
    return continuous_root, folds, total_rows


def test_loading_full_corpus_for_all_three_instruments_is_fast_from_warm_cache(warm_cache):
    continuous_root, folds, total_rows = warm_cache
    assert total_rows > 5_000_000, "fixture should be a meaningful multi-million-row stress test"

    start = time.perf_counter()
    frames = [
        load_bars(root, folds.corpus_start, folds.holdout_start - timedelta(days=1), "back_adjusted",
                  continuous_root=continuous_root)
        for root in ROOTS
    ]
    elapsed = time.perf_counter() - start

    for f, root in zip(frames, ROOTS):
        assert len(f) > 0
        assert set(f["instrument"].to_list()) == {root}

    assert elapsed < 30.0, f"warm-cache load took {elapsed:.2f}s, over the 30s acceptance budget"
