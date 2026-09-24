"""Orchestration: bars -> feature table -> ranked table -> labelled table.

The full recompute (features + ranking) is cheap and deterministic Polars
over the corpus (README's phase-3 acceptance test: under 60 seconds for
three instruments), so both the one-time backfill and the nightly
incremental job re-run it in full every time — CLAUDE.md rule 2:
re-deriving a small table from scratch is simpler than maintaining
incremental-update logic for it. What genuinely must be incremental is
Grok labelling, because it costs real money: `label_top_decile` only calls
`label_fn` for top-decile sessions that don't already carry a label.
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from research.anomaly.features import FEATURE_COLUMNS, compute_all_features
from research.anomaly.grok_client import LabelFetchError, LabelFn
from research.anomaly.ranking import rank_anomalies
from research.anomaly.spend import GROK_BACKFILL_CAP_USD, GrokBudgetExceeded, SpendLedger

log = logging.getLogger(__name__)

_REGIME_COLUMNS = (
    "regime_label",
    "regime_label_confidence",
    "regime_description",
    "regime_sources",
    "labelled_at",
)

_REGIME_SCHEMA = {
    "regime_label": pl.Utf8,
    "regime_label_confidence": pl.Float64,
    "regime_description": pl.Utf8,
    "regime_sources": pl.List(pl.Utf8),
    "labelled_at": pl.Utf8,
}


def _ensure_regime_columns(table: pl.DataFrame) -> pl.DataFrame:
    missing = [c for c in _REGIME_COLUMNS if c not in table.columns]
    if not missing:
        return table
    return table.with_columns(
        [pl.lit(None, dtype=_REGIME_SCHEMA[c]).alias(c) for c in missing]
    )


def build_anomaly_table(bars: pl.DataFrame) -> pl.DataFrame:
    """bars (concatenated across instruments) -> ranked, unlabelled table."""
    features = compute_all_features(bars)
    ranked = rank_anomalies(features)
    return _ensure_regime_columns(ranked)


def sessions_needing_labels(table: pl.DataFrame, *, top_fraction: float = 0.10) -> pl.DataFrame:
    """Top `top_fraction` of scored sessions that don't have a regime_label yet.

    This is what makes the nightly job incremental: re-running it after a
    backfill only asks Grok about sessions newly promoted into the top
    decile since the last run.
    """
    table = _ensure_regime_columns(table)
    valid = table.filter(pl.col("anomaly_score").is_not_null())
    if valid.is_empty() or top_fraction <= 0:
        return valid.head(0)
    n = max(1, math.ceil(len(valid) * top_fraction))
    candidates = valid.sort("anomaly_score", descending=True).head(n)
    return candidates.filter(pl.col("regime_label").is_null())


@dataclass
class LabellingRun:
    table: pl.DataFrame
    labelled: int
    skipped: int
    spend_usd: float
    budget_exhausted: bool = False


def _feature_summary(row: dict) -> dict[str, float]:
    return {col: row[col] for col in FEATURE_COLUMNS if row.get(col) is not None}


def label_top_decile(
    table: pl.DataFrame,
    label_fn: LabelFn,
    spend: SpendLedger,
    *,
    top_fraction: float = 0.10,
    cap: float = GROK_BACKFILL_CAP_USD,
) -> LabellingRun:
    """Label every not-yet-labelled session in the top `top_fraction`.

    Stops asking Grok about further sessions the moment a call would push
    cumulative spend past `cap` — CLAUDE.md rule 12: surfaced loudly (an
    ERROR log line and `LabellingRun.budget_exhausted = True`), not a
    silent stop, but NOT a raised exception either: a spent Grok budget
    stops labelling, not the whole nightly job — the feature/ranking table
    still needs to build and save regardless (README: "that pause is
    intended behaviour" is exactly this kind of halt, same spirit as the
    annual hypothesis cap, applied to money instead of trial count). Every
    label successfully obtained before the cap was hit is still applied and
    returned.

    A single session's label call failing to parse or fetch (LabelFetchError)
    is logged loudly and skipped — it does not abort labelling the rest of
    the batch, but it is never silently treated as "none_identified".
    """
    to_label = sessions_needing_labels(table, top_fraction=top_fraction)
    updates: list[dict] = []
    skipped = 0
    budget_exhausted = False

    for row in to_label.iter_rows(named=True):
        instrument, session_date = row["instrument"], row["session_date"]
        try:
            result = label_fn(instrument, session_date, _feature_summary(row))
        except LabelFetchError:
            log.warning("regime label fetch failed for %s %s — skipped", instrument, session_date)
            skipped += 1
            continue

        try:
            spend.charge(result.cost_usd, cap=cap, context=f"{instrument} {session_date}")
        except GrokBudgetExceeded as exc:
            log.error("grok backfill budget exhausted, stopping labelling early: %s", exc)
            budget_exhausted = True
            break

        log.info(
            "regime label: %s %s -> %s (confidence=%.2f)",
            instrument,
            session_date,
            result.label.category,
            result.label.confidence,
        )
        updates.append(
            {
                "instrument": instrument,
                "session_date": session_date,
                "regime_label": result.label.category,
                "regime_label_confidence": result.label.confidence,
                "regime_description": result.label.description,
                "regime_sources": list(result.label.sources),
                "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

    updated_table = _apply_label_updates(table, updates)
    return LabellingRun(
        table=updated_table,
        labelled=len(updates),
        skipped=skipped,
        spend_usd=spend.total_usd,
        budget_exhausted=budget_exhausted,
    )


def _apply_label_updates(table: pl.DataFrame, updates: list[dict]) -> pl.DataFrame:
    """Merge `updates` (each a dict with instrument, session_date, and the
    five regime columns) into `table`. Any other keys present in the dicts
    are ignored — callers must pre-select down to just these columns.
    """
    if not updates:
        return table
    updates_df = pl.DataFrame(
        [{k: u[k] for k in ("instrument", "session_date", *_REGIME_COLUMNS)} for u in updates],
        schema_overrides=_REGIME_SCHEMA,
    )
    joined = table.join(updates_df, on=["instrument", "session_date"], how="left", suffix="_new")
    coalesced = [
        pl.coalesce([pl.col(f"{c}_new"), pl.col(c)]).alias(c) for c in _REGIME_COLUMNS
    ]
    return joined.with_columns(coalesced).drop([f"{c}_new" for c in _REGIME_COLUMNS])


def load_table(path: Path) -> pl.DataFrame | None:
    return pl.read_parquet(path) if path.exists() else None


def save_table(table: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    table.write_parquet(path)


def run_pass(
    bars: pl.DataFrame,
    *,
    existing_table: pl.DataFrame | None,
    label_fn: LabelFn,
    spend: SpendLedger,
    top_fraction: float = 0.10,
    cap: float = GROK_BACKFILL_CAP_USD,
) -> LabellingRun:
    """One full pass: recompute features/ranking from `bars`, carry forward
    any labels already on `existing_table`, then label whatever's newly in
    the top decile. This is both the one-time backfill (existing_table=None)
    and the nightly incremental job (existing_table=the last run's table).
    """
    table = build_anomaly_table(bars)
    if existing_table is not None:
        table = _apply_label_updates(
            table,
            existing_table.filter(pl.col("regime_label").is_not_null()).to_dicts(),
        )
    return label_top_decile(table, label_fn, spend, top_fraction=top_fraction, cap=cap)
