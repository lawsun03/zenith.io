"""Rank sessions by how unusual they were across features.compute_all_features.

Each feature is converted to "how extreme is this value versus this
instrument's own recent history" via a trailing percentile rank of its
absolute value (so a feature that can be positive or negative, like
overnight_follow_through, counts an extreme move in either direction as
unusual). The per-feature unusualness scores are then averaged with equal
weight, ignoring nulls — the same "blend, don't select" instinct as
CLAUDE.md rule 3 applied one layer earlier: no single feature is more
important than another by construction, and nothing here sorts by one
feature and calls it the answer.
"""
from __future__ import annotations

import numpy as np
import polars as pl

from research.anomaly.features import FEATURE_COLUMNS

TRAILING_WINDOW_SESSIONS = 60
MIN_TRAILING_SESSIONS = 20
MIN_FEATURES_FOR_SCORE = 5


def _trailing_percentile_rank(values: np.ndarray, window: int, min_periods: int) -> np.ndarray:
    """out[i] = fraction of the trailing `window` values (including i) that
    are <= values[i], or NaN if fewer than `min_periods` valid values are
    available (not enough history to say what's "unusual" yet) or values[i]
    itself is NaN.

    A plain Python loop rather than a vectorised trick: at session
    granularity (thousands of rows, not bars) this runs in well under a
    second, and it's the most direct way to state "rank against the
    trailing window" without risking a subtly wrong vectorised rewrite.
    """
    n = len(values)
    out = np.full(n, np.nan)
    for i in range(n):
        lo = max(0, i - window + 1)
        window_vals = values[lo : i + 1]
        valid = window_vals[~np.isnan(window_vals)]
        if np.isnan(values[i]) or len(valid) < min_periods:
            continue
        out[i] = float((valid <= values[i]).sum()) / len(valid)
    return out


def add_trailing_percentiles(
    features: pl.DataFrame,
    *,
    window: int = TRAILING_WINDOW_SESSIONS,
    min_periods: int = MIN_TRAILING_SESSIONS,
) -> pl.DataFrame:
    """Add `<feature>_pct` (trailing percentile rank of |feature|, in
    [0, 1]) for every column in FEATURE_COLUMNS, computed independently
    per instrument.
    """
    parts = []
    for instrument in features["instrument"].unique(maintain_order=True).to_list():
        sub = features.filter(pl.col("instrument") == instrument).sort("session_date")
        pct_columns = {}
        for col in FEATURE_COLUMNS:
            basis = sub[col].abs().to_numpy()
            pct = _trailing_percentile_rank(basis, window, min_periods)
            # NaN (not enough trailing history, or the feature itself was
            # null) must become a Polars null, not a float NaN — NaN is a
            # valid, non-null float in Polars and would otherwise sail
            # through is_not_null() and mean_horizontal() uncaught.
            pct_columns[f"{col}_pct"] = pl.Series(pct).fill_nan(None)
        parts.append(sub.with_columns(**pct_columns))
    return pl.concat(parts).sort(["instrument", "session_date"])


def rank_anomalies(
    features: pl.DataFrame,
    *,
    window: int = TRAILING_WINDOW_SESSIONS,
    min_periods: int = MIN_TRAILING_SESSIONS,
    min_features: int = MIN_FEATURES_FOR_SCORE,
) -> pl.DataFrame:
    """Add per-feature unusualness, `anomaly_score`, and
    `anomaly_score_n_features`, and sort descending by anomaly_score.

    `anomaly_score` is null (and therefore sorts last) when fewer than
    `min_features` features had enough trailing history to be scored —
    CLAUDE.md rule 12: a score computed from too little context is a
    failure to score, not a quiet low score.
    """
    ranked = add_trailing_percentiles(features, window=window, min_periods=min_periods)

    unusual_cols = []
    for col in FEATURE_COLUMNS:
        unusual_col = f"{col}_unusual"
        ranked = ranked.with_columns((2 * (pl.col(f"{col}_pct") - 0.5).abs()).alias(unusual_col))
        unusual_cols.append(unusual_col)

    ranked = ranked.with_columns(
        pl.mean_horizontal(unusual_cols).alias("_raw_score"),
        pl.sum_horizontal([pl.col(c).is_not_null() for c in unusual_cols]).alias(
            "anomaly_score_n_features"
        ),
    ).with_columns(
        pl.when(pl.col("anomaly_score_n_features") >= min_features)
        .then(pl.col("_raw_score"))
        .otherwise(None)
        .alias("anomaly_score")
    ).drop("_raw_score")

    return ranked.sort("anomaly_score", descending=True, nulls_last=True)
