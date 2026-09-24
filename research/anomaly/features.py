"""Per-session, per-instrument anomaly features (Polars, no model).

Six features from docs/research-loop/PHASE-PROMPTS.md phase 3, plus one
justified addition:

  realised_vol        sum of squared 1-min log returns within RTH — the
                       trailing-percentile basis for "how volatile was
                       today vs its own recent history".
  opening_range_ratio first-30-minute range vs the PRIOR session's full
                       RTH range.
  sweep_excursion_atr how far price swept beyond the prior session's RTH
                       high/low before closing back inside it, normalised
                       by a trailing average daily range (ATR proxy).
  overnight_follow_through
                       whether the RTH session extended or reversed the
                       overnight (prior RTH close -> this RTH open) move,
                       signed so continuation and reversal are both
                       captured (magnitude, not just direction).
  volume_profile_skew volume-weighted skewness of RTH close price — a
                       volume profile that is top- or bottom-heavy inside
                       the day's range.
  gap_size_atr         overnight gap (this RTH open vs prior RTH close),
                       normalised by the same ATR proxy.
  gap_fill_rate        fraction of that gap retraced during RTH.
  trend_efficiency     JUSTIFIED ADDITION (Kaufman's efficiency ratio):
                       |net RTH move| / (sum of |bar-to-bar moves|), in
                       [0, 1]. Cheap, and it captures something none of
                       the above do — whether the session moved directly
                       (trend day) or churned (chop day) — which the
                       realised-vol magnitude alone can't distinguish
                       (a choppy day and a trend day can have identical
                       realised_vol).

All features are pure functions of a session's own bars plus the
immediately preceding session's RTH high/low/close — no lookahead within
a session, and only ever one session back across sessions (CLAUDE.md rule
12 / gate-9 spirit: predicates never read data they couldn't have had at
decision time; the anomaly pass is looser than an IR predicate since it
runs after the fact for classification, but "no lookahead beyond what's
already closed" is kept anyway, since floor-to-day-N-close is what a
same-day-close labelling process could reproduce).
"""
from __future__ import annotations

import polars as pl

from research.anomaly.sessions import opening_range_mask, rth_mask, with_session_date

ATR_LOOKBACK_SESSIONS = 14

_EPS = 1e-12


def _rth_session_agg(bars_with_session: pl.DataFrame) -> pl.DataFrame:
    """One row per (instrument, session_date): RTH open/high/low/close/volume
    plus the realised-vol and volume-profile-skew features, which both need
    the bar-level RTH rows to compute.
    """
    rth = bars_with_session.filter(rth_mask()).sort(["instrument", "session_date", "local_ts"])

    rth = rth.with_columns(
        (pl.col("close").log() - pl.col("close").log().shift(1).over(["instrument", "session_date"]))
        .alias("_log_ret")
    )

    # Volume-weighted skew of RTH close price, computed via window
    # expressions rather than a self-join (Polars `.over()` broadcasts the
    # per-group aggregate back onto every row in the group).
    vol_sum = pl.col("volume").sum().over(["instrument", "session_date"])
    weight = pl.col("volume") / (vol_sum + _EPS)
    wmean = (weight * pl.col("close")).sum().over(["instrument", "session_date"])
    rth = rth.with_columns((pl.col("close") - wmean).alias("_dev"))
    wvar = (weight * pl.col("_dev") ** 2).sum().over(["instrument", "session_date"])
    wm3 = (weight * pl.col("_dev") ** 3).sum().over(["instrument", "session_date"])
    rth = rth.with_columns((wm3 / (wvar ** 1.5 + _EPS)).alias("_skew"))

    session = rth.group_by(["instrument", "session_date"]).agg(
        pl.col("open").first().alias("rth_open"),
        pl.col("high").max().alias("rth_high"),
        pl.col("low").min().alias("rth_low"),
        pl.col("close").last().alias("rth_close"),
        pl.col("volume").sum().alias("rth_volume"),
        (pl.col("_log_ret") ** 2).sum().alias("realised_vol"),
        pl.col("_skew").first().alias("volume_profile_skew"),
    )

    orb = (
        bars_with_session.filter(opening_range_mask())
        .group_by(["instrument", "session_date"])
        .agg(
            pl.col("high").max().alias("or_high"),
            pl.col("low").min().alias("or_low"),
        )
    )
    return session.join(orb, on=["instrument", "session_date"], how="left")


def _compute_session_features(bars_with_session: pl.DataFrame) -> pl.DataFrame:
    """Per (instrument, session_date) feature table, minus trend_efficiency
    (see `_trend_efficiency` — split out because it needs its own pass over
    the bar-level RTH rows before they're collapsed to one row per session).

    `bars_with_session` is research.anomaly.sessions.with_session_date()
    applied to the concatenated output of research.data.loader.load_bars
    across instruments.
    """
    session = _rth_session_agg(bars_with_session).sort(["instrument", "session_date"])

    prior_high = pl.col("rth_high").shift(1).over("instrument")
    prior_low = pl.col("rth_low").shift(1).over("instrument")
    prior_close = pl.col("rth_close").shift(1).over("instrument")
    prior_range = prior_high - prior_low

    atr_proxy = (
        (pl.col("rth_high") - pl.col("rth_low"))
        .shift(1)
        .over("instrument")
        .rolling_mean(window_size=ATR_LOOKBACK_SESSIONS, min_samples=ATR_LOOKBACK_SESSIONS)
        .over("instrument")
    )

    session = session.with_columns(
        prior_high.alias("_prior_high"),
        prior_low.alias("_prior_low"),
        prior_close.alias("_prior_close"),
        prior_range.alias("_prior_range"),
        atr_proxy.alias("_atr_proxy"),
    )

    excursion_high = (pl.col("rth_high") - pl.col("_prior_high")).clip(lower_bound=0)
    excursion_low = (pl.col("_prior_low") - pl.col("rth_low")).clip(lower_bound=0)
    reversed_inside = (pl.col("rth_close") <= pl.col("_prior_high")) & (
        pl.col("rth_close") >= pl.col("_prior_low")
    )
    raw_excursion = pl.when(reversed_inside).then(
        pl.max_horizontal(excursion_high, excursion_low)
    ).otherwise(0.0)

    overnight_move = pl.col("rth_open") - pl.col("_prior_close")
    rth_move = pl.col("rth_close") - pl.col("rth_open")

    gap = pl.col("rth_open") - pl.col("_prior_close")
    gap_up = gap > 0
    filled_up = (pl.col("rth_open") - pl.col("rth_low")).clip(upper_bound=gap.abs())
    filled_down = (pl.col("rth_high") - pl.col("rth_open")).clip(upper_bound=gap.abs())
    filled = pl.when(gap_up).then(filled_up).otherwise(filled_down)

    session = session.with_columns(
        (raw_excursion / (pl.col("_atr_proxy") + _EPS)).alias("sweep_excursion_atr"),
        ((pl.col("or_high") - pl.col("or_low")) / (pl.col("_prior_range") + _EPS)).alias(
            "opening_range_ratio"
        ),
        (
            pl.when(overnight_move.abs() > _EPS)
            .then(overnight_move.sign() * rth_move / (overnight_move.abs() + _EPS))
            .otherwise(None)
        ).alias("overnight_follow_through"),
        (gap / (pl.col("_atr_proxy") + _EPS)).alias("gap_size_atr"),
        (
            pl.when(gap.abs() > _EPS).then(filled / gap.abs()).otherwise(None)
        ).alias("gap_fill_rate"),
    )

    return session.drop(
        ["_prior_high", "_prior_low", "_prior_close", "_prior_range", "_atr_proxy", "or_high", "or_low"]
    )


def _trend_efficiency(bars_with_session: pl.DataFrame) -> pl.DataFrame:
    """Kaufman efficiency ratio per (instrument, session_date): |net RTH
    move| / sum(|bar-to-bar move|). Split out from `compute_session_features`
    because it needs the bar-level path length, computed on the RTH-filtered
    frame before it's collapsed to one row per session.
    """
    rth = bars_with_session.filter(rth_mask()).sort(["instrument", "session_date", "local_ts"])
    rth = rth.with_columns(
        (pl.col("close") - pl.col("close").shift(1).over(["instrument", "session_date"]))
        .abs()
        .alias("_step")
    )
    agg = rth.group_by(["instrument", "session_date"]).agg(
        pl.col("open").first().alias("_open"),
        pl.col("close").last().alias("_close"),
        pl.col("_step").sum().alias("_path_length"),
    )
    return agg.with_columns(
        (
            (pl.col("_close") - pl.col("_open")).abs() / (pl.col("_path_length") + _EPS)
        ).alias("trend_efficiency")
    ).select(["instrument", "session_date", "trend_efficiency"])


def compute_all_features(bars: pl.DataFrame) -> pl.DataFrame:
    """Full per (instrument, session_date) feature table.

    `bars` is the concatenated output of research.data.loader.load_bars
    across instruments (columns: ts, open, high, low, close, volume,
    contract, instrument). Returns one row per session with the RTH OHLCV
    and every column in FEATURE_COLUMNS — ranking.py adds the
    trailing-percentile and anomaly-score columns on top of this.
    """
    bars_with_session = with_session_date(bars)
    features = _compute_session_features(bars_with_session)
    trend = _trend_efficiency(bars_with_session)
    return features.join(trend, on=["instrument", "session_date"], how="left").sort(
        ["instrument", "session_date"]
    )


FEATURE_COLUMNS: tuple[str, ...] = (
    "realised_vol",
    "opening_range_ratio",
    "sweep_excursion_atr",
    "overnight_follow_through",
    "volume_profile_skew",
    "gap_size_atr",
    "gap_fill_rate",
    "trend_efficiency",
)
