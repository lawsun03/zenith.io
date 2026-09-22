"""Holdout enforcement (CLAUDE.md domain invariant 6): no code under
research/ may ever read the sealed holdout.

Two layers, both tested: the fast application-level date-range guard in
load_bars(), and — the real backstop — that a filesystem-permission-sealed
directory genuinely cannot be read, proven by pointing research/ code's own
disk-touching function directly at one.
"""
from __future__ import annotations

import stat
from datetime import datetime, timedelta

import polars as pl
import pytest

from research.data.folds import load_folds
from research.data.loader import HoldoutAccessError, _read_corpus_parquet, load_bars


def test_load_bars_raises_before_touching_disk_for_a_range_inside_the_holdout(tmp_path):
    folds = load_folds()
    empty_root = tmp_path / "nonexistent_cache"  # no files anywhere under here

    with pytest.raises(HoldoutAccessError):
        load_bars(
            "GC",
            folds.holdout_start,
            folds.holdout_start + timedelta(days=1),
            "back_adjusted",
            continuous_root=empty_root,
        )
    # If it had tried to read a file it would have raised FileNotFoundError
    # instead — HoldoutAccessError proves the guard fired first.


def test_load_bars_raises_for_a_range_that_only_partially_overlaps_the_holdout(tmp_path):
    folds = load_folds()
    empty_root = tmp_path / "nonexistent_cache"

    with pytest.raises(HoldoutAccessError):
        load_bars(
            "GC",
            folds.holdout_start - timedelta(days=5),
            folds.holdout_start + timedelta(days=5),
            "back_adjusted",
            continuous_root=empty_root,
        )


def test_load_bars_does_not_raise_for_a_range_entirely_inside_the_corpus(tmp_path):
    folds = load_folds()
    corpus_dir = tmp_path / "GC" / "corpus"
    corpus_dir.mkdir(parents=True)
    day1 = datetime.combine(folds.corpus_start, datetime.min.time())
    df = pl.DataFrame({
        "ts": pl.datetime_range(
            day1,
            day1 + timedelta(days=1),
            interval="1d",
            eager=True,
        ),
        "open": [100.0, 101.0],
        "high": [100.5, 101.5],
        "low": [99.5, 100.5],
        "close": [100.2, 101.2],
        "volume": [1000, 1100],
        "contract": ["GCZ19", "GCZ19"],
    })
    df.write_parquet(corpus_dir / "back_adjusted.parquet")

    result = load_bars("GC", folds.corpus_start, date_two_years_after(folds.corpus_start), "back_adjusted", continuous_root=tmp_path)
    assert set(result["instrument"].to_list()) == {"GC"}


def date_two_years_after(d):
    return d.replace(year=d.year + 2)


def test_sealed_holdout_directory_cannot_be_physically_read(tmp_path):
    """The real enforcement: a filesystem-permission-sealed directory raises
    PermissionError when research/ code's own read function is pointed at
    it directly — proving the seal works independent of any application
    guard."""
    holdout_dir = tmp_path / "GC" / "holdout"
    holdout_dir.mkdir(parents=True)
    df = pl.DataFrame({
        "ts": pl.datetime_range(
            datetime(2025, 6, 1),
            datetime(2025, 6, 2),
            interval="1d",
            eager=True,
        ),
        "open": [1.0, 2.0], "high": [1.0, 2.0], "low": [1.0, 2.0],
        "close": [1.0, 2.0], "volume": [1, 1], "contract": ["x", "x"],
    })
    parquet_path = holdout_dir / "back_adjusted.parquet"
    df.write_parquet(parquet_path)

    holdout_dir.chmod(0o000)
    try:
        with pytest.raises(PermissionError):
            _read_corpus_parquet(parquet_path, load_folds().holdout_start, load_folds().holdout_end)
    finally:
        holdout_dir.chmod(stat.S_IRWXU)  # restore so pytest can clean up tmp_path
