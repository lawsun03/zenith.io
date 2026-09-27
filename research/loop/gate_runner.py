"""Wires research.gates.pipeline into the hypothesis loop
(docs/research-loop/PHASE-PROMPTS.md Phase 6b): backtests a candidate
across NQ/ES/GC/SI on the frozen folds, builds the two CandidateInputs
(macro-release sessions included and excluded), and records the full
gate 0-9 battery to the ledger.

Design notes on real forks this module resolves (see the header comment
of each function for the mechanics):

- CandidateInputs carries ONE point_value/tick_value/combine_point_value,
  but CLAUDE.md domain invariant 2 pools trades across NQ, ES and GC.
  Sharpe-based gates (1, 3, 4, 5, 6, 7) are scale-invariant (R-multiples),
  so pooling native trades is exact. Gates 2 and 8 are dollar-denominated,
  so every pooled trade is rescaled by ITS OWN instrument's point_value
  before being handed to CandidateInputs — entry/stop/target/pnl_points
  all scale by the same per-trade constant, which leaves every R-multiple
  (and therefore every Sharpe-based gate) byte-for-byte unchanged while
  making `point_value=1` yield the true pooled dollar edge for gate 2.
  Gate 8 reuses the same dollar-scaled trades with `combine_point_value`
  set per trade to ITS instrument's micro/full point-value ratio. The ratio
  is not uniform (NQ/ES/GC micros are 1/10, silver's SIL is 1/5), so a
  single scalar would mis-cost gate 8. Each micro is found through the
  account file's `underlying` field — never by name (silver's micro is
  SIL, not MSI) — and a missing micro raises rather than guessing.

- Gate 3's sweep_surface is reconstructed from the ledger's persisted
  `param_grid` (field-path -> swept values) applied to the base IR as a
  Cartesian product, since individual phase-5 variant IR documents are
  never persisted — only the grid description and a variant count are.
  A combination that fails IR schema validation is dropped from the
  surface, the same way research.loop.variants drops one Kimi proposes.

- Macro-release session filtering reads data/news_events.csv
  (app.strategy.event_times, preserved from the removed live bot)
  directly — deterministic and model-free, never the anomaly pipeline's
  Grok regime labels (CLAUDE.md rule 5: gate pass/fail is arithmetic, a
  model never decides it).

- Gate 2 requires a commission figure cost-model.md deliberately never
  hardcodes ("verify against your broker's current schedule"). Callers
  get a documented, clearly-labeled placeholder default
  (`DEFAULT_COMMISSION_PER_SIDE`) they should override once verified.

- Gate 6's sr_variance_across_trials needs the PER-TRADE (non-annualized)
  OOS Sharpe of past ledger trials — a number `sharpe_oos` never stored,
  since it's annualized. This module writes it to a new ledger column,
  `hypotheses.sharpe_oos_per_trade` (docs/research-loop/ledger.sql,
  research/ledger/db.py's migration), rather than reconstructing it from
  a stored trades_per_year (which also isn't stored) — see
  `research.ledger.api.per_trade_oos_sharpes`.

- Which sr_variance_across_trials SOURCE (empirical vs. null) was used,
  and its value, is logged at INFO alongside the gate 6 result rather
  than given its own ledger column — the same "full auditability without
  a schema migration" tradeoff research.loop.cycle's own docstring
  already makes for per-stage provenance.
"""
from __future__ import annotations

import itertools
import logging
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from sqlite3 import Connection
from typing import Sequence
from zoneinfo import ZoneInfo

from app.sim.events import Bar
from app.strategy.event_times import load_event_times
from research.gates import cost
from research.gates.ensemble import build_ensemble
from research.gates.metrics import r_multiples, sharpe_from_trades
from research.gates.pipeline import CandidateInputs, CandidateOutcome, evaluate_candidate
from research.gates.walk_forward import FoldResult
from research.ir.engine import IRBacktestEngine, Trade
from research.ir.schema import validate as validate_ir
from research.ir.sizing import size_trades
from research.ledger.api import (
    insert_ensemble, per_trade_oos_sharpes, record_candidate_scores, record_gate_results,
)
from research.stats.deflated_sharpe import empirical_sr_variance, null_sr_variance

log = logging.getLogger(__name__)

# cost-model.md deliberately gives no concrete commission figure. This is a
# documented placeholder — verify against your broker's current schedule
# before trusting a gate-2 result, exactly as cost-model.md instructs.
DEFAULT_COMMISSION_PER_SIDE = Decimal("0.62")

DEFAULT_EVENTS_PATH = "data/news_events.csv"
DEFAULT_EVENT_TYPES: tuple[str, ...] = ("CPI", "PPI", "FOMC")

# Below this many recorded per-trial per-trade OOS Sharpes, gate 6 uses the
# null-hypothesis variance stand-in instead of measuring the real spread
# (research.stats.deflated_sharpe.null_sr_variance's own docstring: "a
# principled stand-in ... while too few trials have been recorded").
EMPIRICAL_SR_VARIANCE_MIN_TRIALS = 20


# ---------------------------------------------------------------------
# Macro-release session filtering (deterministic, model-free)
# ---------------------------------------------------------------------

def macro_release_session_dates(
    session_tz: str, *,
    events_path: str = DEFAULT_EVENTS_PATH,
    event_types: Sequence[str] = DEFAULT_EVENT_TYPES,
) -> set[date]:
    """Calendar dates, in `session_tz`, that contain any of `event_types`.
    Empty set (not an error) when the events file is missing or has no
    matching rows — load_event_times already degrades that way."""
    tz = ZoneInfo(session_tz)
    dates: set[date] = set()
    for event_type in event_types:
        for ts in load_event_times(events_path, event_type):
            dates.add(ts.astimezone(tz).date())
    return dates


def _filter_release_sessions(bars: Sequence[Bar], excluded_dates: set[date], tz: ZoneInfo) -> list[Bar]:
    if not excluded_dates:
        return list(bars)
    return [b for b in bars if b.ts.astimezone(tz).date() not in excluded_dates]


def _bars_in_range(bars: Sequence[Bar], start: date, end: date, tz: ZoneInfo) -> list[Bar]:
    return [b for b in bars if start <= b.ts.astimezone(tz).date() < end]


# ---------------------------------------------------------------------
# Gate 3 — reconstruct the sweep surface from the persisted param_grid
# ---------------------------------------------------------------------

def _apply_param_path(ir_doc: dict, path: str, value: float) -> dict:
    """Deep-copies `ir_doc` and sets the dot-path field (e.g. "stop.multiple")
    to `value` — the exact field-path convention variants_v1.md's prompt
    contract specifies for param_grid keys."""
    doc = deepcopy(ir_doc)
    *parents, leaf = path.split(".")
    node = doc
    for key in parents:
        node = node[key]
    node[leaf] = value
    return doc


def variant_irs_from_param_grid(
    base_ir: dict, param_grid: dict[str, Sequence[float]],
) -> tuple[list[Sequence[float]], dict[tuple[float, ...], dict]]:
    """The Cartesian product of param_grid's swept values applied to
    `base_ir`, one axis per field path (sorted for a stable, reproducible
    axis order). A combination that fails IR schema validation is dropped —
    the same treatment research.loop.variants gives a variant Kimi proposes
    that doesn't validate.

    Returns (axes, {axis_point: ir_doc}), axes in the same field order as
    each axis_point tuple.
    """
    fields = sorted(param_grid)
    axes = [sorted(param_grid[f]) for f in fields]
    by_point: dict[tuple[float, ...], dict] = {}
    for point in itertools.product(*axes):
        doc = base_ir
        for field, value in zip(fields, point):
            doc = _apply_param_path(doc, field, value)
        if not validate_ir(doc):
            by_point[point] = doc
    return axes, by_point


# ---------------------------------------------------------------------
# Backtesting — research.ir.engine across NQ/ES/GC, pooled
# ---------------------------------------------------------------------

def _run_engine_trades(ir_doc: dict, instrument: str, bars: Sequence[Bar], tick_size: Decimal) -> list[Trade]:
    return IRBacktestEngine(ir_doc, instrument, tick_size=tick_size).run(bars)


def _pooled_trades(
    ir_doc: dict, bars_by_instrument: dict[str, Sequence[Bar]], account: dict,
    *, start: date, end: date, tz: ZoneInfo,
) -> list[Trade]:
    """Every trade from every instrument the IR document trades
    (CLAUDE.md domain invariant 2: always NQ+ES+GC pooled — never
    per-instrument), sliced to [start, end) and sorted by entry time."""
    trades: list[Trade] = []
    for instrument in ir_doc["instruments"]:
        bars = _bars_in_range(bars_by_instrument[instrument], start, end, tz)
        tick_size = Decimal(account["instruments"][instrument]["tick_size"])
        trades.extend(_run_engine_trades(ir_doc, instrument, bars, tick_size))
    trades.sort(key=lambda t: t.entry_ts)
    return trades


def _dollar_scale_trades(trades: Sequence[Trade], point_value_by_instrument: dict[str, Decimal]) -> list[Trade]:
    """Rescales every trade by ITS OWN instrument's point_value. entry/stop/
    target/pnl_points all scale by the same per-trade constant, so every
    R-multiple (pnl_points / abs(entry - stop)) is exactly unchanged —
    Sharpe, skew, kurtosis and everything downstream of r_multiples() reads
    identically off the scaled trades as the native ones. What changes is
    that pnl_points is now genuinely dollar-denominated, so
    mean_edge_dollars_per_trade(trades, point_value=1) returns the true
    pooled dollar edge across instruments of very different point values."""
    out = []
    for t in trades:
        pv = point_value_by_instrument[t.instrument]
        out.append(replace(
            t,
            entry_price=t.entry_price * pv, stop_price=t.stop_price * pv,
            target_price=t.target_price * pv, pnl_points=t.pnl_points * pv,
        ))
    return out


def _weighted_tick_value(trades: Sequence[Trade], tick_value_by_instrument: dict[str, Decimal]) -> Decimal:
    """Trade-count-weighted average tick_value across the instruments
    actually present in `trades` — gate 2's round_turn_cost is a single
    scalar compared once against the pooled mean edge, so this is the
    representative tick_value that comparison uses."""
    if not trades:
        return Decimal("0")
    total = sum((tick_value_by_instrument[t.instrument] for t in trades), Decimal("0"))
    return total / len(trades)


def _micro_symbol(account: dict, instrument: str) -> str:
    """The account-file micro whose `underlying` is `instrument`. Raises if
    there is not exactly one, rather than guessing a spelling."""
    matches = [
        sym for sym, spec in account["instruments"].items()
        if spec.get("underlying") == instrument
    ]
    if len(matches) != 1:
        raise ValueError(f"expected exactly one micro with underlying {instrument!r}, found {matches}")
    return matches[0]


def _micro_full_ratio(account: dict, instrument: str) -> Decimal:
    full = Decimal(account["instruments"][instrument]["point_value"])
    micro = Decimal(account["instruments"][_micro_symbol(account, instrument)]["point_value"])
    return micro / full


def _size_contracts_pooled(
    native_trades: Sequence[Trade], ir_doc: dict, account: dict, *, years_span: float,
) -> list[int]:
    """MICRO contract counts (research.ir.sizing, vol-targeted per
    CandidateInputs' own doc: gate 8 sizes to micros while every other gate
    runs the rule on full-size), aligned index-for-index with
    `native_trades`' pooled order. Each instrument's own trades_per_year is
    that instrument's trade count over the SAME years_span the candidate's
    overall trades_per_year uses — passing the raw per-instrument trade
    count instead (implicitly "one year" of data) would overstate trading
    frequency by roughly years_span x and undersize every contract count."""
    sizing_cfg = ir_doc["sizing"]
    contracts_by_id: dict[int, int] = {}
    for instrument in ir_doc["instruments"]:
        subset = [(i, t) for i, t in enumerate(native_trades) if t.instrument == instrument]
        if not subset:
            continue
        indices, instrument_trades = zip(*subset)
        instrument_trades_per_year = len(instrument_trades) / years_span if years_span > 0 else 0
        sized = size_trades(
            list(instrument_trades), Decimal(str(sizing_cfg["vol_target_annual"])), account,
            _micro_symbol(account, instrument), max_contracts=sizing_cfg.get("max_contracts"),
            trades_per_year=instrument_trades_per_year,
        )
        for idx, c in zip(indices, sized):
            contracts_by_id[idx] = c
    return [contracts_by_id[i] for i in range(len(native_trades))]


def _safe_sharpe(trades: Sequence[Trade], trades_per_year: float) -> float:
    """0.0 (no measurable edge) instead of raising on <2 trades or zero
    variance — a walk-forward FIT/TEST sub-window is often too small to
    estimate a Sharpe ratio at all, and walk_forward.fold_decay already
    treats a non-positive fit_sharpe as total decay, so 0.0 here degrades
    correctly rather than crashing the whole candidate evaluation."""
    if len(trades) < 2:
        return 0.0
    try:
        return sharpe_from_trades(trades, trades_per_year, annualize=True)
    except ValueError:
        return 0.0


def _r_multiples_sum(trades: Sequence[Trade]) -> float:
    if not trades:
        return 0.0
    return sum(r_multiples(trades))


def _fold_result(
    ir_doc: dict, bars_by_instrument: dict[str, Sequence[Bar]], account: dict,
    fold, *, tz: ZoneInfo, trades_per_year: float,
) -> FoldResult:
    fit_trades = _pooled_trades(ir_doc, bars_by_instrument, account,
                                 start=fold.fit_start, end=fold.fit_end, tz=tz)
    oos_trades = _pooled_trades(ir_doc, bars_by_instrument, account,
                                 start=fold.test_start, end=fold.test_end, tz=tz)
    return FoldResult(
        fit_sharpe=_safe_sharpe(fit_trades, trades_per_year),
        oos_return=_r_multiples_sum(oos_trades),
        oos_sharpe=_safe_sharpe(oos_trades, trades_per_year),
    )


def _sweep_surface(
    ir_doc: dict, param_grid: dict[str, Sequence[float]],
    bars_by_instrument: dict[str, Sequence[Bar]], account: dict,
    *, start: date, end: date, tz: ZoneInfo, trades_per_year: float,
) -> tuple[list[Sequence[float]], dict[tuple[float, ...], float]]:
    axes, variants = variant_irs_from_param_grid(ir_doc, param_grid)
    surface: dict[tuple[float, ...], float] = {}
    for point, variant_doc in variants.items():
        trades = _pooled_trades(variant_doc, bars_by_instrument, account, start=start, end=end, tz=tz)
        surface[point] = _safe_sharpe(trades, trades_per_year)
    return axes, surface


# ---------------------------------------------------------------------
# Build one release-variant's CandidateInputs
# ---------------------------------------------------------------------

def build_candidate_inputs(
    ir_doc: dict,
    param_grid: dict[str, Sequence[float]],
    bars_by_instrument: dict[str, Sequence[Bar]],
    account: dict,
    folds: Sequence,
    *,
    exclude_dates: set[date],
    corpus_start: date,
    corpus_end: date,
    commission_and_fees_per_side: Decimal,
    condition: cost.Condition,
    discretionary_entry: bool,
    combine_seed: int | None,
    hypothesis_id: str | None = None,
) -> CandidateInputs:
    tz = ZoneInfo(ir_doc["session"]["tz"])
    filtered = {
        instrument: _filter_release_sessions(bars, exclude_dates, tz)
        for instrument, bars in bars_by_instrument.items()
    }

    native_trades = _pooled_trades(ir_doc, filtered, account, start=corpus_start, end=corpus_end, tz=tz)

    years_span = float((corpus_end - corpus_start).days) / 365.25
    trades_per_year = (len(native_trades) / years_span) if years_span > 0 else 0.0

    point_value_by_instrument = {
        i: Decimal(account["instruments"][i]["point_value"]) for i in ir_doc["instruments"]
    }
    tick_value_by_instrument = {
        i: Decimal(account["instruments"][i]["tick_value"]) for i in ir_doc["instruments"]
    }

    dollar_trades = _dollar_scale_trades(native_trades, point_value_by_instrument)
    tick_value = _weighted_tick_value(native_trades, tick_value_by_instrument)
    ratio_by_instrument = {i: _micro_full_ratio(account, i) for i in ir_doc["instruments"]}
    combine_point_value = [ratio_by_instrument[t.instrument] for t in native_trades]
    contracts = _size_contracts_pooled(native_trades, ir_doc, account, years_span=years_span)

    axes, sweep_surface = _sweep_surface(
        ir_doc, param_grid, filtered, account,
        start=corpus_start, end=corpus_end, tz=tz, trades_per_year=trades_per_year,
    )

    fold_results = [
        _fold_result(ir_doc, filtered, account, fold, tz=tz, trades_per_year=trades_per_year)
        for fold in folds
    ]

    return CandidateInputs(
        ir_doc=ir_doc,
        trades=dollar_trades,
        point_value=Decimal("1"),
        sample_start=corpus_start,
        sample_end=corpus_end,
        trades_per_year=trades_per_year,
        tick_value=tick_value,
        commission_and_fees_per_side=commission_and_fees_per_side,
        condition=condition,
        discretionary_entry=discretionary_entry,
        sweep_surface=sweep_surface,
        sweep_axes=axes,
        fold_results=fold_results,
        account=account,
        contracts=contracts,
        combine_point_value=combine_point_value,
        combine_seed=combine_seed,
        hypothesis_id=hypothesis_id,
    )


# ---------------------------------------------------------------------
# The one integration point: evaluate a ledger hypothesis end to end
# ---------------------------------------------------------------------

@dataclass(frozen=True)
class HypothesisEvaluation:
    outcome: CandidateOutcome
    daily_returns_with_releases: list[float]


def evaluate_hypothesis(
    conn: Connection,
    hypothesis_id: str,
    ir_doc: dict,
    param_grid: dict[str, Sequence[float]],
    *,
    bars_by_instrument: dict[str, Sequence[Bar]],
    account: dict,
    folds: Sequence,
    corpus_start: date,
    corpus_end: date,
    n_trials: float,
    years: float,
    loop_pbo: float | None = None,
    sr_variance_across_trials: float,
    sr_variance_source: str,
    commission_and_fees_per_side: Decimal = DEFAULT_COMMISSION_PER_SIDE,
    condition: cost.Condition = "market_normal",
    discretionary_entry: bool = True,
    combine_seed: int | None = None,
    events_path: str = DEFAULT_EVENTS_PATH,
    event_types: Sequence[str] = DEFAULT_EVENT_TYPES,
    ensemble_family: str | None = None,
) -> HypothesisEvaluation:
    """Backtest `hypothesis_id`'s IR twice (macro-release sessions included
    and excluded), run it through gates 0-9, and record everything to the
    ledger. `n_trials`/`years`/`loop_pbo`/`sr_variance_across_trials` are
    loop-state properties the caller computes ONCE per cycle, not per
    candidate (docs/research-loop/PHASE-PROMPTS.md phase 6b).

    A survivor (cleared every gate) joins a NEW single-member equal-weighted
    ensemble — this system has no existing mechanism that groups distinct
    ledger rows into a shared strategy family (research.gates.ensemble's
    `family` is caller-supplied, and nothing upstream of this function ever
    assigns one), so `ensemble_family` defaults to the hypothesis's own id.
    Flagging this for Lawrence, the same way research.loop.cycle's own
    docstring flags its own scope decisions: if hypotheses should be
    clustered into shared families before ensembling, that clustering logic
    doesn't exist yet and is out of this function's scope.
    """
    log.info(
        "gate 6 sr_variance_across_trials source=%s value=%.6g (hypothesis=%s)",
        sr_variance_source, sr_variance_across_trials, hypothesis_id,
    )

    release_dates = macro_release_session_dates(
        ir_doc["session"]["tz"], events_path=events_path, event_types=event_types,
    )

    common_kwargs = dict(
        ir_doc=ir_doc, param_grid=param_grid, bars_by_instrument=bars_by_instrument,
        account=account, folds=folds, corpus_start=corpus_start, corpus_end=corpus_end,
        commission_and_fees_per_side=commission_and_fees_per_side, condition=condition,
        discretionary_entry=discretionary_entry, combine_seed=combine_seed,
        hypothesis_id=hypothesis_id,
    )
    inputs_with = build_candidate_inputs(exclude_dates=set(), **common_kwargs)
    inputs_without = build_candidate_inputs(exclude_dates=release_dates, **common_kwargs)

    outcome = evaluate_candidate(
        conn, inputs_with, inputs_without,
        n_trials=n_trials, years=years, loop_pbo=loop_pbo,
        sr_variance_across_trials=sr_variance_across_trials,
    )

    record_gate_results(
        conn, hypothesis_id,
        [{"gate": r.gate, "passed": r.passed, "measured": r.measured, "threshold": r.threshold}
         for r in outcome.gate_results],
    )

    by_gate = {r.gate: r for r in outcome.gate_results}
    fit_sharpes_with = [f.fit_sharpe for f in inputs_with.fold_results]
    sharpe_is = sum(fit_sharpes_with) / len(fit_sharpes_with) if fit_sharpes_with else None
    sharpe_per_trade = min(
        (v for v in (
            _safe_sharpe_annualized_false(inputs_with),
            _safe_sharpe_annualized_false(inputs_without),
        ) if v is not None),
        default=None,
    )

    record_candidate_scores(
        conn, hypothesis_id,
        sharpe_is=sharpe_is,
        sharpe_oos=outcome.sharpe_recorded,
        sharpe_oos_per_trade=sharpe_per_trade,
        sharpe_decay=by_gate[4].measured,
        sharpe_deflated=by_gate[6].measured,
        sr_cutoff_applied=by_gate[5].threshold,
        sharpe_with_releases=outcome.sharpe_with_releases,
        sharpe_without_releases=outcome.sharpe_without_releases,
        combine_payout_prob=outcome.combine_payout_prob,
        outcome="rejected",
    )

    if outcome.cleared_all_gates:
        family = ensemble_family or hypothesis_id
        ensemble = build_ensemble(family, [(hypothesis_id, ir_doc)])
        insert_ensemble(conn, ensemble, sharpe_oos=outcome.sharpe_recorded,
                         combine_payout_prob=outcome.combine_payout_prob)

    daily_returns = daily_r_returns(inputs_with.trades, corpus_start, corpus_end)
    return HypothesisEvaluation(outcome=outcome, daily_returns_with_releases=daily_returns)


def _safe_sharpe_annualized_false(inputs: CandidateInputs) -> float | None:
    if len(inputs.trades) < 2:
        return None
    try:
        return sharpe_from_trades(inputs.trades, inputs.trades_per_year, annualize=False)
    except ValueError:
        return None


def resolve_sr_variance_across_trials(conn: Connection, now) -> tuple[float, str]:
    """The value AND source (research.loop.cycle-style provenance flagging)
    for gate 6's sr_variance_across_trials: empirical_sr_variance over every
    recorded hypotheses.sharpe_oos_per_trade once at least
    EMPIRICAL_SR_VARIANCE_MIN_TRIALS exist, null_sr_variance before that.

    STATED ASSUMPTION (CLAUDE.md rule 1), flagged for Lawrence: research.
    stats.deflated_sharpe's own docstring frames null_sr_variance(n_obs) as
    the sampling variance of ONE candidate's own SR estimate (n_obs = that
    candidate's trade count) used as a stand-in for the across-trials
    spread. But PHASE-PROMPTS.md phase 6b is explicit that
    sr_variance_across_trials is computed ONCE PER LOOP STATE, not per
    candidate — before any candidate has been backtested, so no
    candidate's own trade count is available yet at this call site. This
    function resolves that tension by treating n_obs as the number of
    PAST TRIALS recorded (the loop-level quantity that actually exists at
    this point), not any one candidate's trade count. Once
    EMPIRICAL_SR_VARIANCE_MIN_TRIALS trials exist this is moot — the
    empirical branch measures the real cross-trial spread directly.
    """
    sharpes = per_trade_oos_sharpes(conn, now)
    if len(sharpes) >= EMPIRICAL_SR_VARIANCE_MIN_TRIALS:
        return empirical_sr_variance(sharpes), "empirical"
    n_obs = max(len(sharpes), 2)
    return null_sr_variance(n_obs), "null"


# ---------------------------------------------------------------------
# Loop-level PBO (CSCV over this cycle's own candidates)
#
# STATED ASSUMPTION, flagged for Lawrence: "all recorded candidates"
# (PHASE-PROMPTS.md phase 6b) would need every PAST candidate's daily OOS
# return series, and only summary statistics (Sharpe, etc.) are persisted
# to the ledger — not a day-by-day return series a later cycle could
# replay CSCV against. This computes PBO over the candidates backtested
# WITHIN THE CURRENT CYCLE instead: honest given what's actually
# reconstructable from the ledger, but narrower than a full-history CSCV.
# research.loop.cycle accumulates each candidate's daily return series as
# it evaluates them and calls compute_loop_pbo once at least 2 exist.
# ---------------------------------------------------------------------

def daily_r_returns(trades: Sequence[Trade], start: date, end: date) -> "list[float]":
    """One value per calendar day in [start, end): that day's R-multiples
    summed, 0.0 for a day with no entries. The fixed-length, calendar-
    aligned series research.stats.cscv needs to compare variants that
    don't necessarily trade on the same days."""
    n_days = (end - start).days
    out = [0.0] * n_days
    if not trades:
        return out
    rs = r_multiples(trades)
    for t, r in zip(trades, rs):
        idx = (t.entry_ts.date() - start).days
        if 0 <= idx < n_days:
            out[idx] += r
    return out


def compute_loop_pbo(
    daily_returns_by_candidate: Sequence[Sequence[float]], *, n_splits: int = 16,
) -> float | None:
    """None with fewer than 2 candidates (CSCV needs at least 2 variants to
    rank against each other) or too few aligned days to form `n_splits`
    equal blocks. Trims every series to the same, largest length divisible
    by n_splits — CSCV requires equal-length blocks (research.stats.cscv)."""
    if len(daily_returns_by_candidate) < 2:
        return None
    import numpy as np

    from research.stats.cscv import probability_of_backtest_overfitting

    min_len = min(len(s) for s in daily_returns_by_candidate)
    usable_len = (min_len // n_splits) * n_splits
    if usable_len < n_splits:
        return None
    matrix = np.array([s[:usable_len] for s in daily_returns_by_candidate]).T
    return probability_of_backtest_overfitting(matrix, n_splits=n_splits)
