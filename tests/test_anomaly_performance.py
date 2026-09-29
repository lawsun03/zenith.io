"""Acceptance test: the anomaly pass produces a ranked, labelled table for
three instruments in under 60 seconds (docs/research-loop/PHASE-PROMPTS.md
phase 3 / README.md phase table row 3).

research.data.loader's own load-from-warm-cache performance is already
covered by tests/test_research_loader_performance.py (<30s for the same
16-year, 3-instrument corpus); this test focuses on what phase 3 adds on
top of that — feature computation, ranking, and (budget-capped, mocked)
labelling — over an equivalently-sized synthetic corpus, built directly in
memory rather than round-tripped through a Parquet cache since the loader
itself isn't what's under test here.

The synthetic corpus approximates America/New_York as a constant UTC-5
offset (ignoring real DST transitions) purely to keep timestamp generation
a single vectorised Polars expression — for a performance stress test what
matters is a realistic row count and a non-trivial ranked/labelled output,
not minute-perfect session boundaries on DST-affected days (those are
exercised precisely in tests/test_anomaly_features.py and
tests/test_anomaly_sessions.py instead).
"""
from __future__ import annotations

import time
from datetime import timedelta

import polars as pl
import pytest

from research.anomaly.grok_client import LabelResult
from research.anomaly.labels import RegimeLabel
from research.anomaly.pipeline import build_anomaly_table, label_top_decile
from research.anomaly.spend import SpendLedger
from research.data.folds import load_folds

# The acceptance criterion is three instruments; the pool has since grown (SI), so
# this is pinned here rather than read from research.data.instruments.ROOTS.
ACCEPTANCE_ROOTS = ("NQ", "ES", "GC")
RTH_MINUTES = 390  # 09:30 - 16:00 ET
ET_UTC_OFFSET_HOURS = 5  # constant approximation of America/New_York, see module docstring


def _synthetic_bars(root: str, base_price: float) -> pl.DataFrame:
    folds = load_folds()
    start, end = folds.corpus_start, folds.holdout_start - timedelta(days=1)

    all_dates = pl.date_range(start, end, "1d", eager=True)
    session_dates = all_dates.filter(all_dates.dt.weekday() < 6)
    minutes = pl.arange(0, RTH_MINUTES, eager=True)
    grid = pl.DataFrame({"session_date": session_dates}).join(
        pl.DataFrame({"minute": minutes}), how="cross"
    )

    n = len(grid)
    idx = pl.int_range(0, n, eager=True)
    close = base_price + (idx % 97).cast(pl.Float64) * 0.3 - ((idx % 41).cast(pl.Float64) * 0.2)

    return grid.select(
        (
            pl.col("session_date").cast(pl.Datetime)
            + pl.duration(hours=9, minutes=30 + ET_UTC_OFFSET_HOURS * 60)
            + pl.duration(minutes=pl.col("minute"))
        )
        .dt.replace_time_zone("UTC")
        .alias("ts"),
        (close - 0.1).alias("open"),
        (close + 0.6).alias("high"),
        (close - 0.6).alias("low"),
        close.alias("close"),
        ((idx % 800) + 1).cast(pl.UInt64).alias("volume"),
        pl.lit(root).alias("instrument"),
    )


def _instant_label_fn(instrument, session_date, feature_summary):
    return LabelResult(
        label=RegimeLabel(category="none_identified", description="synthetic", confidence=0.0, sources=()),
        cost_usd=0.0,
    )


@pytest.fixture(scope="module")
def synthetic_corpus() -> pl.DataFrame:
    base_prices = {"NQ": 15000.0, "ES": 4500.0, "GC": 2000.0}
    frames = [_synthetic_bars(root, base_prices[root]) for root in ACCEPTANCE_ROOTS]
    return pl.concat(frames)


def test_anomaly_pass_completes_for_three_instruments_in_under_60_seconds(synthetic_corpus, tmp_path):
    assert synthetic_corpus["instrument"].n_unique() == 3
    assert len(synthetic_corpus) > 1_000_000, "fixture should be a meaningful multi-instrument stress test"

    start = time.perf_counter()
    table = build_anomaly_table(synthetic_corpus)
    spend = SpendLedger.load(tmp_path / "spend.json")  # cost is $0 throughout, cap is never approached
    run = label_top_decile(table, _instant_label_fn, spend, top_fraction=0.10, cap=150.0)
    elapsed = time.perf_counter() - start

    assert run.table.height > 0
    for root in ACCEPTANCE_ROOTS:
        assert (run.table["instrument"] == root).any()
    assert run.labelled > 0
    assert run.table.filter(pl.col("regime_label").is_not_null()).height == run.labelled

    assert elapsed < 60.0, f"anomaly pass took {elapsed:.2f}s, over the 60s acceptance budget"
