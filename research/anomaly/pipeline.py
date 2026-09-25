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
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from pathlib import Path

import polars as pl

from research.anomaly.features import FEATURE_COLUMNS, compute_all_features
from research.anomaly.label_store import append_label, load_stored_labels
from research.anomaly.grok_client import LabelFetchError, LabelFetchFatalError, LabelFn
from research.anomaly.ranking import rank_anomalies
from research.anomaly.spend import GROK_BACKFILL_CAP_USD, GrokBudgetExceeded, SpendLedger

log = logging.getLogger(__name__)

# CLAUDE.md rule 12 (fail loud): a run that's failing every call is a
# broken integration, not a sequence of unlucky sessions — stop burning
# through the rest of the batch once this many calls in a row have failed.
MAX_CONSECUTIVE_LABEL_FAILURES = 5

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
    aborted: bool = False
    abort_reason: str | None = None
    interrupted: bool = False
    abandoned: int = 0  # in-flight calls given up on after a second Ctrl-C


def _feature_summary(row: dict) -> dict[str, float]:
    return {col: row[col] for col in FEATURE_COLUMNS if row.get(col) is not None}


def label_top_decile(
    table: pl.DataFrame,
    label_fn: LabelFn,
    spend: SpendLedger,
    *,
    top_fraction: float = 0.10,
    cap: float = GROK_BACKFILL_CAP_USD,
    concurrency: int = 1,
    labels_path: Path | None = None,
) -> LabellingRun:
    """Label every not-yet-labelled session in the top `top_fraction`, up to
    `concurrency` calls in flight at once.

    Durability (the reason this is more than a loop): each label is charged
    to `spend` (persisted per call) and, if `labels_path` is given, appended
    to that file and fsync'd before the next result is handled, so an
    interrupted or crashed run loses nothing already paid for. All of that
    bookkeeping happens in this (the calling) thread as results complete —
    only `label_fn` itself runs in worker threads — so no locks are needed
    and failure/spend logic sees results one at a time.

    Stops asking Grok about further sessions the moment a call would push
    cumulative spend past `cap` — CLAUDE.md rule 12: surfaced loudly (an
    ERROR log line and `LabellingRun.budget_exhausted = True`), not a
    silent stop, and NOT a raised exception: a spent Grok budget stops
    labelling, not the whole nightly job. With concurrency, calls already
    in flight when the cap trips are drained and their labels discarded
    like any other over-cap call, so real spend can exceed the recorded
    total by up to `concurrency - 1` calls.

    A single session's label call failing to parse or fetch (LabelFetchError)
    is logged loudly (HTTP status + a response-body excerpt when the
    failure came from one) and skipped. CLAUDE.md rule 12 cuts both ways:
    MAX_CONSECUTIVE_LABEL_FAILURES failures in a row (in completion order)
    means the integration itself is broken, so the pass aborts. A
    LabelFetchFatalError (401/403/404/410 — never succeeds on retry)
    aborts on the first occurrence. After an abort or budget stop no new
    call is started; calls already in flight are drained so any that
    succeeded are still saved.

    KeyboardInterrupt: caught here. Queued-but-unstarted calls are
    cancelled, in-flight ones are waited for (their labels are already
    paid for) and saved, and the run returns with `interrupted=True` and
    every label so far applied to the returned table. A second Ctrl-C
    during that wait abandons the in-flight calls (`abandoned` says how
    many) — their spend is not recorded.
    """
    if concurrency < 1:
        raise ValueError(f"concurrency must be >= 1, got {concurrency}")

    rows = list(sessions_needing_labels(table, top_fraction=top_fraction).iter_rows(named=True))
    updates: list[dict] = []
    skipped = 0
    budget_exhausted = False
    aborted = False
    abort_reason: str | None = None
    interrupted = False
    abandoned = 0
    consecutive_failures = 0
    stop = False  # no new calls once True (abort / budget)

    def handle(row: dict, fut: Future) -> None:
        nonlocal skipped, budget_exhausted, aborted, abort_reason, consecutive_failures, stop
        instrument, session_date = row["instrument"], row["session_date"]
        try:
            result = fut.result()
        except LabelFetchFatalError as exc:
            skipped += 1
            if not aborted:
                abort_reason = (
                    f"fatal error (status={exc.status_code}) fetching a label for {instrument} "
                    f"{session_date}, aborting the rest of this pass: {exc}"
                    + (f" — body: {exc.body_excerpt}" if exc.body_excerpt else "")
                )
                log.error(abort_reason)
                aborted = stop = True
            return
        except LabelFetchError as exc:
            log.warning(
                "regime label fetch failed for %s %s (status=%s) — skipped: %s%s",
                instrument, session_date, exc.status_code, exc,
                f" — body: {exc.body_excerpt}" if exc.body_excerpt else "",
            )
            skipped += 1
            consecutive_failures += 1
            if consecutive_failures >= MAX_CONSECUTIVE_LABEL_FAILURES and not aborted:
                abort_reason = (
                    f"{consecutive_failures} consecutive label-fetch failures — aborting the "
                    "rest of this pass rather than continuing to burn through every session"
                )
                log.error(abort_reason)
                aborted = stop = True
            return
        consecutive_failures = 0

        try:
            spend.charge(result.cost_usd, cap=cap, context=f"{instrument} {session_date}")
        except GrokBudgetExceeded as exc:
            if not budget_exhausted:
                log.error("grok backfill budget exhausted, stopping labelling early: %s", exc)
            budget_exhausted = stop = True
            return

        log.info(
            "regime label: %s %s -> %s (confidence=%.2f)",
            instrument, session_date, result.label.category, result.label.confidence,
        )
        update = {
            "instrument": instrument,
            "session_date": session_date,
            "regime_label": result.label.category,
            "regime_label_confidence": result.label.confidence,
            "regime_description": result.label.description,
            "regime_sources": list(result.label.sources),
            "labelled_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
        if labels_path is not None:
            append_label(labels_path, update)
        updates.append(update)

    pool = ThreadPoolExecutor(max_workers=concurrency)
    pending: dict[Future, dict] = {}
    remaining = iter(rows)
    try:
        while True:
            while not stop and len(pending) < concurrency:
                row = next(remaining, None)
                if row is None:
                    break
                fut = pool.submit(label_fn, row["instrument"], row["session_date"], _feature_summary(row))
                pending[fut] = row
            if not pending:
                break
            done, _ = wait(pending, return_when=FIRST_COMPLETED)
            for fut in done:
                handle(pending.pop(fut), fut)
    except KeyboardInterrupt:
        interrupted = True
        for fut in list(pending):
            if fut.cancel():
                del pending[fut]
        log.warning(
            "interrupted — waiting for %d in-flight call(s) so their labels are saved "
            "(Ctrl-C again to abandon them)", len(pending),
        )
        try:
            for fut, row in list(pending.items()):
                wait([fut])
                del pending[fut]
                try:
                    handle(row, fut)
                except KeyboardInterrupt:
                    pass  # a worker that itself raised KeyboardInterrupt; nothing to save
        except KeyboardInterrupt:
            abandoned = len(pending)
            log.error("abandoned %d in-flight call(s) — their spend was not recorded", abandoned)
    finally:
        pool.shutdown(wait=not abandoned, cancel_futures=True)

    updated_table = _apply_label_updates(table, updates)
    return LabellingRun(
        table=updated_table,
        labelled=len(updates),
        skipped=skipped,
        spend_usd=spend.total_usd,
        budget_exhausted=budget_exhausted,
        aborted=aborted,
        abort_reason=abort_reason,
        interrupted=interrupted,
        abandoned=abandoned,
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
    concurrency: int = 1,
    labels_path: Path | None = None,
) -> LabellingRun:
    """One full pass: recompute features/ranking from `bars`, carry forward
    any labels already on `existing_table` AND in `labels_path` (labels a
    previous, interrupted run paid for but never got into a saved table),
    then label whatever's newly in the top decile. `labels_path` is merged
    again at the end, so the returned table reflects every label on disk.
    This is both the one-time backfill (existing_table=None) and the nightly
    incremental job (existing_table=the last run's table).
    """
    table = build_anomaly_table(bars)
    if existing_table is not None:
        table = _apply_label_updates(
            table,
            existing_table.filter(pl.col("regime_label").is_not_null()).to_dicts(),
        )
    if labels_path is not None:
        table = _apply_label_updates(table, load_stored_labels(labels_path))
    run = label_top_decile(
        table, label_fn, spend, top_fraction=top_fraction, cap=cap,
        concurrency=concurrency, labels_path=labels_path,
    )
    if labels_path is not None:
        run.table = _apply_label_updates(run.table, load_stored_labels(labels_path))
    return run
