"""The four-stage autonomous hypothesis loop
(docs/research-loop/README.md, docs/research-loop/PHASE-PROMPTS.md phase 5).

Orchestration only — every stage (triage, generate_hypothesis,
adversarial_review, enumerate_variants) is a pure function of an injected
ChatFn plus domain data, so this module contains no model-specific logic
and no prompt text. Swapping which model backs a stage is an edit in
research/loop/config.py, not a change here.

Ledger provenance — a deliberate, flagged design decision: `hypotheses`
(docs/research-loop/ledger.sql) has exactly ONE model_name/model_version/
prompt_hash/temperature per row. That schema was built in phase 1, before
this four-stage loop existed, so it has no column for "which model
triaged this" or "which model enumerated its variants." This module
resolves that by recording stage b's (hypothesis-generation / Astra's)
provenance in those columns — ir_hash/ir_json are stage b's output, and
those are exactly the columns ledger.sql documents as "which model,
exactly [produced this]". `mechanism` is stage b's and `falsifier` is
stage c's, exactly as ledger.sql's own column comments name them. Every
stage's provenance, including triage's and variant enumeration's (which
have no ledger column at all), is still logged at INFO level in each
stage module — full auditability without a schema migration. Flagging
this for Lawrence: if per-stage ledger provenance is wanted, `hypotheses`
needs new columns; leaving that out was a scope decision for this phase,
not an oversight.

Trial-budget accounting — also flagged: a hypothesis that FAILS
adversarial review is still appended to the ledger, with
outcome='rejected' (append_hypothesis's own default), param_grid={} and
n_variants_swept=1 — it consumes an annual trial-budget slot the moment
Astra produces a complete, schema-valid IR document, before review has
run. This is the conservative reading of "30-50 hypotheses per year"
(README): the cap throttles distinct ideas seriously drafted, not just
ideas that reached a backtest, on the theory that a generator iterating
against its own reviewer is itself a form of multiple comparisons worth
capping.

Gate 0-9 evaluation (research.gates.pipeline, via research.loop.
gate_runner) now runs immediately after each hypothesis is appended
(PHASE-PROMPTS.md phase 6b — "wire it in"), using that SAME row's IR and
param_grid; it is still a step run against a row this cycle has already
written, not folded into append_hypothesis itself. A hypothesis that
failed review (verdict.survives is False) is backtested and gated the
same as one that survived — the gates are the thing that actually decides
promotion; adversarial review is a cheap upstream filter, not a
substitute for it.
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Sequence

from app.sim.events import Bar
from research.data.folds import load_folds
from research.gates import cost
from research.ledger.api import append_hypothesis, trial_count_at
from research.loop.gate_runner import (
    DEFAULT_COMMISSION_PER_SIDE,
    DEFAULT_EVENT_TYPES,
    DEFAULT_EVENTS_PATH,
    compute_loop_pbo,
    evaluate_hypothesis,
    resolve_sr_variance_across_trials,
)
from research.loop.hypothesis import (
    HypothesisInvalidIRError,
    HypothesisParseError,
    generate_hypothesis,
)
from research.loop.providers import ChatFn
from research.loop.review import ReviewParseError, adversarial_review
from research.loop.triage import triage_anomalies
from research.loop.variants import VariantParseError, enumerate_variants
from research.stats.budget import ANNUAL_HYPOTHESIS_CAP, TrialBudgetExceeded

log = logging.getLogger(__name__)


@dataclass
class CycleResult:
    hypothesis_ids: list[str] = field(default_factory=list)
    n_triaged: int = 0
    n_survived_review: int = 0
    n_review_rejected: int = 0
    n_generation_failed: int = 0
    budget_exhausted: bool = False
    # Gate 0-9 evaluation (phase 6b) — separate from review survival above:
    # a hypothesis is backtested and gated regardless of verdict.survives.
    n_gate_evaluated: int = 0
    n_cleared_all_gates: int = 0
    n_gate_evaluation_failed: int = 0


def run_cycle(
    conn: sqlite3.Connection,
    anomaly_rows: list[dict],
    *,
    feature_columns: Sequence[str],
    triage_chat_fn: ChatFn,
    hypothesis_chat_fn: ChatFn,
    review_chat_fn: ChatFn,
    variants_chat_fn: ChatFn,
    hypothesis_model_name: str,
    hypothesis_temperature: float,
    data_range: str,
    bars_by_instrument: dict[str, Sequence[Bar]],
    account: dict,
    max_shortlist: int = 5,
    max_variants: int = 12,
    cap: int = ANNUAL_HYPOTHESIS_CAP,
    now: datetime | None = None,
    commission_and_fees_per_side: Decimal = DEFAULT_COMMISSION_PER_SIDE,
    condition: cost.Condition = "market_normal",
    discretionary_entry: bool = True,
    combine_seed: int | None = None,
    events_path: str = DEFAULT_EVENTS_PATH,
    event_types: Sequence[str] = DEFAULT_EVENT_TYPES,
) -> CycleResult:
    """One full unattended cycle: triage -> per shortlisted anomaly,
    generate -> review -> (if it survives) enumerate variants -> append to
    the ledger -> backtest and gate 0-9 it (research.loop.gate_runner),
    regardless of review verdict. Stops the moment the annual trial cap is
    hit (TrialBudgetExceeded from append_hypothesis) rather than raising
    past it — CLAUDE.md rule 6: "that pause is intended behaviour."

    `bars_by_instrument` and `account` are the same real-data/account-config
    inputs every candidate this cycle is backtested against — loaded once
    by the caller (scripts/run_hypothesis_loop.py), not by this module,
    matching how the chat_fns are already injected rather than constructed
    here.

    years and sr_variance_across_trials are loop-state properties computed
    ONCE below, before the per-candidate loop (docs/research-loop/
    PHASE-PROMPTS.md phase 6b) — not re-fetched per candidate: they're
    properties of the whole corpus/search, stable regardless of how many
    candidates this cycle itself appends.

    n_trials is a stated exception to "once per loop state," flagged for
    Lawrence: trial_count_at(conn, now) called ONCE before this cycle's own
    candidates exist would read n_trials=0 for the very first hypothesis
    ever appended to a fresh ledger — and research.stats.cutoff_table.
    sr_cutoff rejects n_trials < 1 outright (a candidate is, at minimum,
    trial number 1 the moment its own row is appended). So n_trials is
    re-read via trial_count_at immediately after each append_hypothesis
    call instead, which is always >= that candidate's own n_variants_swept
    and therefore always valid — the literal "once per loop state" reading
    is mathematically impossible for the first trial of a cycle, not just
    a style choice.

    loop_pbo only exists once at least 2 candidates in THIS cycle have
    been gate-evaluated (research.loop.gate_runner.compute_loop_pbo's own
    docstring: this is CSCV over the candidates backtested within the
    current cycle, not a full-history replay — no day-by-day return series
    survives for past candidates, only their summary statistics), so it
    starts None and is recomputed after each candidate.
    """
    now = now or datetime.now(timezone.utc)
    folds = load_folds()
    corpus_start, corpus_end = folds.corpus_start, folds.holdout_start
    years = (corpus_end - corpus_start).days / 365.25
    sr_variance_across_trials, sr_variance_source = resolve_sr_variance_across_trials(conn, now)
    loop_pbo: float | None = None
    daily_returns_this_cycle: list[list[float]] = []
    result = CycleResult()

    triage_result = triage_anomalies(
        triage_chat_fn, anomaly_rows, feature_columns=feature_columns, max_shortlist=max_shortlist,
    )
    result.n_triaged = len(triage_result.shortlist)

    for anomaly in triage_result.shortlist:
        try:
            draft = generate_hypothesis(
                hypothesis_chat_fn, anomaly,
                model_name=hypothesis_model_name, temperature=hypothesis_temperature,
            )
        except (HypothesisParseError, HypothesisInvalidIRError) as exc:
            log.warning("hypothesis generation failed for %s: %s", anomaly.anomaly_id, exc)
            result.n_generation_failed += 1
            continue

        try:
            verdict = adversarial_review(
                review_chat_fn,
                mechanism=draft.mechanism,
                instruments=draft.ir["instruments"],
                session_start=draft.ir["session"]["start"],
                session_end=draft.ir["session"]["end"],
                session_tz=draft.ir["session"]["tz"],
            )
        except ReviewParseError as exc:
            log.warning("adversarial review failed for %s: %s", anomaly.anomaly_id, exc)
            result.n_generation_failed += 1
            continue

        if verdict.survives:
            try:
                variant_set = enumerate_variants(
                    variants_chat_fn, mechanism=draft.mechanism, base_ir=draft.ir,
                    max_variants=max_variants,
                )
                param_grid, n_variants = variant_set.param_grid, variant_set.n_variants_swept
            except VariantParseError as exc:
                log.warning(
                    "variant enumeration failed for %s, recording as a single trial: %s",
                    anomaly.anomaly_id, exc,
                )
                param_grid, n_variants = {}, 1
        else:
            param_grid, n_variants = {}, 1

        try:
            hyp_id = append_hypothesis(
                conn,
                ir=draft.ir,
                mechanism=draft.mechanism,
                falsifier=verdict.falsifier,
                model_name=draft.model_name,
                model_version=draft.model_version,
                prompt_hash=draft.prompt_hash,
                temperature=draft.temperature,
                data_range=data_range,
                param_grid=param_grid,
                n_variants_swept=n_variants,
                trial_count_at_test=trial_count_at(conn, now),
                origin_anomaly_id=anomaly.anomaly_id,
                regime_label=anomaly.regime_label,
                regime_label_confidence=anomaly.regime_label_confidence,
                created_at=now,
                cap=cap,
            )
        except TrialBudgetExceeded as exc:
            log.error("annual hypothesis cap reached mid-cycle, stopping: %s", exc)
            result.budget_exhausted = True
            break

        result.hypothesis_ids.append(hyp_id)
        if verdict.survives:
            result.n_survived_review += 1
        else:
            result.n_review_rejected += 1

        try:
            n_trials = trial_count_at(conn, now)  # see run_cycle's docstring on why this is per-candidate
            evaluation = evaluate_hypothesis(
                conn, hyp_id, draft.ir, param_grid,
                bars_by_instrument=bars_by_instrument, account=account,
                folds=folds.walk_forward_folds, corpus_start=corpus_start, corpus_end=corpus_end,
                n_trials=n_trials, years=years, loop_pbo=loop_pbo,
                sr_variance_across_trials=sr_variance_across_trials,
                sr_variance_source=sr_variance_source,
                commission_and_fees_per_side=commission_and_fees_per_side, condition=condition,
                discretionary_entry=discretionary_entry, combine_seed=combine_seed,
                events_path=events_path, event_types=event_types,
            )
        except Exception:
            log.error("gate evaluation failed for %s", hyp_id, exc_info=True)
            result.n_gate_evaluation_failed += 1
        else:
            result.n_gate_evaluated += 1
            if evaluation.outcome.cleared_all_gates:
                result.n_cleared_all_gates += 1
            daily_returns_this_cycle.append(evaluation.daily_returns_with_releases)
            loop_pbo = compute_loop_pbo(daily_returns_this_cycle)

    log.info(
        "cycle complete: %d triaged, %d logged to the ledger (%d survived review, %d rejected "
        "at review), %d generation/review failures, %d gate-evaluated (%d cleared every gate, "
        "%d gate-evaluation failures), budget_exhausted=%s",
        result.n_triaged, len(result.hypothesis_ids), result.n_survived_review,
        result.n_review_rejected, result.n_generation_failed, result.n_gate_evaluated,
        result.n_cleared_all_gates, result.n_gate_evaluation_failed, result.budget_exhausted,
    )
    return result
