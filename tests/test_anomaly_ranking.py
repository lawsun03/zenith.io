"""Trailing-percentile and anomaly-score behavior.

Rule 12 (fail loud) applied to scoring: a session without enough trailing
history must get a null anomaly_score, not a score computed from
whatever partial context happens to be available.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from research.anomaly.features import FEATURE_COLUMNS
from research.anomaly.ranking import (
    MIN_FEATURES_FOR_SCORE,
    MIN_TRAILING_SESSIONS,
    TRAILING_WINDOW_SESSIONS,
    _trailing_percentile_rank,
    rank_anomalies,
)


def test_trailing_percentile_rank_of_the_max_in_window_is_one():
    values = np.array([1.0, 2.0, 3.0, 10.0])
    out = _trailing_percentile_rank(values, window=4, min_periods=2)
    assert out[-1] == pytest.approx(1.0)


def test_trailing_percentile_rank_of_the_min_in_window_is_the_smallest_fraction():
    values = np.array([5.0, 4.0, 3.0, 1.0])
    out = _trailing_percentile_rank(values, window=4, min_periods=2)
    assert out[-1] == pytest.approx(0.25)  # only itself is <= itself, out of 4


def test_trailing_percentile_rank_is_nan_below_min_periods():
    values = np.array([1.0, 2.0])
    out = _trailing_percentile_rank(values, window=10, min_periods=5)
    assert np.isnan(out).all()


def test_trailing_percentile_rank_ignores_values_outside_the_window():
    # A huge outlier 10 sessions ago must not affect today's rank once it
    # has fallen out of a 3-session window.
    values = np.array([100.0, 1.0, 1.0, 1.0, 1.0])
    out = _trailing_percentile_rank(values, window=3, min_periods=3)
    assert out[-1] == pytest.approx(1.0)  # window is [1,1,1] at the end


def _baseline_row(rng: np.random.Generator, instrument: str, d: date, **overrides) -> dict:
    # Small random noise, not a repeating value — a repeating baseline
    # value would trivially hit its own trailing max (percentile 1.0)
    # every time it recurs, which would make ties at the top meaningless.
    row = {"instrument": instrument, "session_date": d}
    row.update({col: float(rng.uniform(0, 1)) for col in FEATURE_COLUMNS})
    row.update(overrides)
    return row


def _build_features(n_sessions: int, *, last_row_overrides: dict) -> pl.DataFrame:
    rng = np.random.default_rng(0)
    base = date(2024, 1, 2)
    rows = [_baseline_row(rng, "NQ", base + timedelta(days=i)) for i in range(n_sessions - 1)]
    rows.append(_baseline_row(rng, "NQ", base + timedelta(days=n_sessions - 1), **last_row_overrides))
    return pl.DataFrame(rows)


def test_anomaly_score_is_null_without_enough_trailing_history():
    # MIN_TRAILING_SESSIONS - 1 sessions total: even the last row can't see
    # a full min_periods window behind it yet.
    features = _build_features(MIN_TRAILING_SESSIONS - 1, last_row_overrides={})
    ranked = rank_anomalies(features)
    last = ranked.filter(pl.col("session_date") == ranked["session_date"].max())
    assert last["anomaly_score_n_features"][0] == 0
    assert last["anomaly_score"][0] is None


def test_extreme_session_ranks_first_once_it_has_full_trailing_history():
    n = TRAILING_WINDOW_SESSIONS + MIN_TRAILING_SESSIONS + 1
    # Every feature at once, far outside the [0, 1) random baseline —
    # no baseline row can coincidentally match this on all 8 features at
    # once, so this is the unique highest-scoring session.
    outlier = {col: 100.0 for col in FEATURE_COLUMNS}
    features = _build_features(n, last_row_overrides=outlier)
    ranked = rank_anomalies(features)
    assert ranked["anomaly_score"][0] == pytest.approx(1.0)
    assert ranked["session_date"][0] == features["session_date"].max()
    assert ranked["anomaly_score_n_features"][0] == len(FEATURE_COLUMNS)


def test_score_requires_min_features_even_if_a_few_are_extreme():
    n = TRAILING_WINDOW_SESSIONS + MIN_TRAILING_SESSIONS + 1
    # Extreme on only a minority of features (the rest nulled out) must
    # not be enough to produce a score at all.
    partial = {col: 100.0 for col in FEATURE_COLUMNS[: MIN_FEATURES_FOR_SCORE - 1]}
    for col in FEATURE_COLUMNS[MIN_FEATURES_FOR_SCORE - 1 :]:
        partial[col] = None
    features = _build_features(n, last_row_overrides=partial)
    ranked = rank_anomalies(features)
    last = ranked.filter(pl.col("session_date") == features["session_date"].max())
    assert last["anomaly_score"][0] is None
