"""Regime-label lookups for the trainer's session-day filter
(PHASE-PROMPTS.md Phase 6, requirement 3: "allow filtering by the Grok
regime label so CPI sessions can be drilled as a block"). Read-only —
writing regime_label is the anomaly pipeline's job
(research/anomaly/pipeline.py), never the trainer's.
"""
from __future__ import annotations

from datetime import date

import polars as pl

from research.anomaly.paths import ANOMALY_TABLE_PATH


def available_regime_labels() -> list[str]:
    if not ANOMALY_TABLE_PATH.exists():
        return []
    df = pl.read_parquet(ANOMALY_TABLE_PATH)
    return sorted(
        df.filter(pl.col("regime_label").is_not_null())["regime_label"].unique().to_list()
    )


def session_dates_for_label(instrument: str, regime_label: str) -> set[date]:
    """ET session dates labelled `regime_label` for `instrument`. Empty set
    (not an error) when the anomaly table doesn't exist yet or nothing
    matches — a filter that narrows to nothing is a legitimate, if useless,
    drill request, not a failure."""
    if not ANOMALY_TABLE_PATH.exists():
        return set()
    df = pl.read_parquet(ANOMALY_TABLE_PATH)
    filtered = df.filter(
        (pl.col("instrument") == instrument) & (pl.col("regime_label") == regime_label)
    )
    return set(filtered["session_date"].to_list())
