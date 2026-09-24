"""The gate pipeline orchestrator (docs/research-loop/gates.md).

Runs gates 0-9 in order, cheapest-first, short-circuiting on the first
failure — a gate after the first failure is recorded as `unreached`
(types.unreached), never skipped silently (CLAUDE.md rule 12).

`CandidateInputs` bundles one release-variant's already-computed backtest
artifacts (a trade sequence, a parameter-sweep surface, walk-forward fold
results, ...). This module does not run a backtest itself — gates.md's
gate battery is a pure function of those artifacts, and producing them (by
walking real bars through research.ir.engine across NQ/ES/GC and the frozen
folds) is the caller's job, exactly as research/ir/engine.py takes bars and
research/ir/sizing.py takes a trade sequence rather than either one reaching
into research/data/loader.py itself. `evaluate_candidate` is the one
function that runs the FULL gate 0-9 x {with-releases, without-releases}
battery and combines it per macro_releases.combine_dual — this is "every
candidate runs twice ... recorded as ONE trial" (gates.md, "Macro
releases").
"""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

from research.gates import (
    ceiling, combine, cost, frequency, ir_validity, plateau, sharpe_floor,
    significance, teachability,
)
from research.gates.macro_releases import combine_dual
from research.gates.metrics import frequency_stats, mean_edge_dollars_per_trade, sharpe_from_trades, skew_and_kurtosis
from research.gates.types import GATE_ORDER, GateResult, unreached
from research.gates.walk_forward import MAX_SHARPE_DECAY, FoldResult
from research.gates.walk_forward import evaluate as evaluate_walk_forward
from research.ir.engine import Trade
from research.stats.cutoff_table import sr_cutoff


@dataclass(frozen=True)
class CandidateInputs:
    ir_doc: dict
    trades: Sequence[Trade]                 # this release-variant's OOS trade sequence
    point_value: Decimal                    # $ per 1.0 of price movement, 1 contract
    sample_start: date
    sample_end: date
    trades_per_year: float

    # gate 2 — tick_value is the $ per single tick (cost-model.md's slippage
    # unit), distinct from point_value (there are point_value/tick_size
    # ticks per point; NQ: point_value $20, tick_value $5 at tick_size 0.25).
    tick_value: Decimal
    commission_and_fees_per_side: Decimal
    condition: cost.Condition
    discretionary_entry: bool

    # gate 3
    sweep_surface: dict[tuple[float, ...], float]
    sweep_axes: Sequence[Sequence[float]]

    # gate 4
    fold_results: Sequence[FoldResult]

    # gate 6
    skew: float | None = None
    kurtosis: float | None = None

    # gate 8 — the only fields that touch account/sizing (CLAUDE.md domain
    # invariant 4). `combine_point_value` defaults to `point_value` but is a
    # separate field on purpose: research runs the RULE on full-size NQ/ES/GC
    # (this module's other gates), while execution sizes to MICROS to fit a
    # combine's much tighter drawdown (docs/research-loop/README.md,
    # "Instruments" — "the sizing layer makes this translation; the rule
    # never knows"). `contracts` is a count of whatever instrument
    # `combine_point_value` prices — micros in production.
    account: dict | None = None
    contracts: Sequence[int] | None = None
    combine_point_value: Decimal | None = None
    combine_n_paths: int | None = None   # overrides account["simulation"]["monte_carlo_paths"]
    combine_seed: int | None = None      # deterministic Monte Carlo for tests

    # gate 0 — set when this candidate's own row already exists in the
    # ledger (research.loop.gate_runner runs the full battery AFTER
    # append_hypothesis, so the duplicate-hash check must exclude the
    # candidate's own just-inserted row, not just any matching hash).
    hypothesis_id: str | None = None


def gate_threshold(gate: int, inputs: CandidateInputs, *, n_trials: float, years: float) -> float | None:
    """The threshold that WOULD apply to `gate`, computable without running
    it — every threshold in this pipeline depends only on static config
    (n_trials, years, the account file), never on the candidate's own
    backtest — which is what lets an unreached gate still record a
    threshold (CLAUDE.md rule 12: a gate result of "never reached" is not
    the same as "no information")."""
    if gate == 0:
        return ir_validity.THRESHOLD
    if gate == 1:
        return frequency.WEEKS_MEETING_FLOOR_MIN
    if gate == 2:
        return cost.COST_HEADROOM_MIN
    if gate == 3:
        return plateau.PLATEAU_MIN_NEIGHBOR_RATIO
    if gate == 4:
        return MAX_SHARPE_DECAY
    if gate == 5:
        return sr_cutoff(n_trials, years)
    if gate == 6:
        return significance.DSR_MIN_PROBABILITY
    if gate == 7:
        return ceiling.SHARPE_CEILING
    if gate == 8:
        account = inputs.account or {}
        return account.get("simulation", {}).get(
            "pass_threshold_payout_prob", combine.COMBINE_PAYOUT_PROB_MIN_DEFAULT
        )
    if gate == 9:
        return teachability.THRESHOLD
    raise ValueError(f"unknown gate {gate}")


def _evaluate_one(
    gate: int, inputs: CandidateInputs, conn: sqlite3.Connection,
    *, n_trials: float, years: float, loop_pbo: float | None,
    sr_variance_across_trials: float | None = None,
) -> GateResult:
    if gate == 0:
        return ir_validity.evaluate(conn, inputs.ir_doc, exclude_hypothesis_id=inputs.hypothesis_id)

    if gate == 1:
        stats = frequency_stats(inputs.trades, inputs.sample_start, inputs.sample_end)
        return frequency.evaluate(stats)

    if gate == 2:
        edge = mean_edge_dollars_per_trade(inputs.trades, inputs.point_value)
        rt_cost = cost.round_turn_cost(
            condition=inputs.condition,
            tick_value=inputs.tick_value,
            commission_and_fees_per_side=inputs.commission_and_fees_per_side,
            discretionary_entry=inputs.discretionary_entry,
        )
        return cost.evaluate(edge, rt_cost)

    if gate == 3:
        return plateau.evaluate(inputs.sweep_surface, inputs.sweep_axes)

    if gate == 4:
        return evaluate_walk_forward(inputs.fold_results)

    if gate == 5:
        sharpe_oos = sharpe_from_trades(inputs.trades, inputs.trades_per_year, annualize=True)
        return sharpe_floor.evaluate(sharpe_oos, n_trials, years)

    if gate == 6:
        sharpe_per_trade = sharpe_from_trades(inputs.trades, inputs.trades_per_year, annualize=False)
        if inputs.skew is None or inputs.kurtosis is None:
            skew, kurtosis = skew_and_kurtosis(inputs.trades)
        else:
            skew, kurtosis = inputs.skew, inputs.kurtosis
        return significance.evaluate(
            sharpe_per_trade, int(n_trials), skew, kurtosis, len(inputs.trades),
            sr_variance_across_trials=sr_variance_across_trials, loop_pbo=loop_pbo,
        )

    if gate == 7:
        sharpe_oos = sharpe_from_trades(inputs.trades, inputs.trades_per_year, annualize=True)
        return ceiling.evaluate(sharpe_oos)

    if gate == 8:
        if inputs.account is None or inputs.contracts is None:
            raise ValueError("gate 8 requires `account` and `contracts` on CandidateInputs")
        combine_trades = combine.trades_to_combine_trades(
            inputs.trades, inputs.contracts, inputs.combine_point_value or inputs.point_value,
        )
        rng = random.Random(inputs.combine_seed) if inputs.combine_seed is not None else None
        payout_prob = combine.run_combine_simulation(
            combine_trades, inputs.account, n_paths=inputs.combine_n_paths, rng=rng,
        )
        return combine.evaluate(payout_prob, inputs.account)

    if gate == 9:
        return teachability.evaluate(inputs.ir_doc)

    raise ValueError(f"unknown gate {gate}")


def evaluate_variant(
    inputs: CandidateInputs, conn: sqlite3.Connection,
    *, n_trials: float, years: float, loop_pbo: float | None = None,
    sr_variance_across_trials: float | None = None,
) -> list[GateResult]:
    """Runs gates 0-9 against one release-variant, short-circuiting on the
    first failure. Always returns exactly len(GATE_ORDER) results."""
    results: list[GateResult] = []
    failed = False
    for gate in GATE_ORDER:
        if failed:
            results.append(unreached(gate, gate_threshold(gate, inputs, n_trials=n_trials, years=years)))
            continue
        result = _evaluate_one(
            gate, inputs, conn, n_trials=n_trials, years=years, loop_pbo=loop_pbo,
            sr_variance_across_trials=sr_variance_across_trials,
        )
        results.append(result)
        if not result.passed:
            failed = True
    return results


@dataclass(frozen=True)
class CandidateOutcome:
    gate_results: list[GateResult]
    first_failed_gate: int | None
    sharpe_with_releases: float | None
    sharpe_without_releases: float | None
    sharpe_recorded: float | None       # min() of the two — gates.md "Macro releases"
    combine_payout_prob: float | None
    cleared_all_gates: bool


def evaluate_candidate(
    conn: sqlite3.Connection,
    inputs_with_releases: CandidateInputs,
    inputs_without_releases: CandidateInputs,
    *, n_trials: float, years: float, loop_pbo: float | None = None,
    sr_variance_across_trials: float | None = None,
) -> CandidateOutcome:
    results_with = evaluate_variant(
        inputs_with_releases, conn, n_trials=n_trials, years=years, loop_pbo=loop_pbo,
        sr_variance_across_trials=sr_variance_across_trials,
    )
    results_without = evaluate_variant(
        inputs_without_releases, conn, n_trials=n_trials, years=years, loop_pbo=loop_pbo,
        sr_variance_across_trials=sr_variance_across_trials,
    )
    combined = combine_dual(results_with, results_without)

    failed_gates = [r.gate for r in combined if not r.passed]
    first_failed_gate = min(failed_gates) if failed_gates else None

    by_gate = {r.gate: r for r in combined}

    return CandidateOutcome(
        gate_results=combined,
        first_failed_gate=first_failed_gate,
        sharpe_with_releases=results_with[5].measured,
        sharpe_without_releases=results_without[5].measured,
        sharpe_recorded=by_gate[5].measured,       # the combined (worse-of-two) gate 5 result
        combine_payout_prob=by_gate[8].measured,
        cleared_all_gates=first_failed_gate is None,
    )
