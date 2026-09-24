"""Divergence tracking (PHASE-PROMPTS.md Phase 6, requirement 4): what
fraction of the strategy's real trades the trainee actually took, and how
the trainee's own hypothetical trades would have played out next to the
strategy's, in R-multiples.

Informational only. This module is computed strictly AFTER a session's
records are scored (research.trainer.scoring) and its output is never fed
back into fidelity_score — see that module's docstring for why. Reusing
the same rule here is safe precisely because nothing here can loop back
into the grade.

R-multiples, not dollar P&L: account size never enters rule design
(CLAUDE.md domain invariant 4). A trade's outcome is expressed relative to
its own risk (entry-to-stop distance), which is the only quantity both the
strategy and a trainee's hand-picked stop share.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Sequence

from app.sim.events import Bar
from research.gates.ensemble import Ensemble
from research.trainer.sessions import DecisionRecord


@dataclass(frozen=True)
class DivergenceResult:
    trades_available: int          # decisions where the IR itself fired
    trades_taken: int              # of those, how many the trainee also called a setup
    fraction_taken: Decimal
    strategy_equity_r: list[Decimal]   # cumulative R, one point per IR trade, in queue order
    trainee_equity_r: list[Decimal]    # cumulative R, one point per trainee trade, in queue order
    # Per-decision, non-cumulative R, keyed by index into the `records` list
    # passed to compute_divergence — lets a caller (the API layer, writing
    # drill_decisions rows) attach one shadow outcome per decision without
    # re-deriving which records were "taken" itself.
    strategy_r_by_record_index: dict[int, Decimal]
    trainee_r_by_record_index: dict[int, Decimal]


def _touch_outcome(
    bars_after: Sequence[Bar], side: str, entry: Decimal, stop: Decimal, target: Decimal,
) -> Decimal:
    """Walk forward until stop or target is touched; pessimistic tie-break
    on a whipsaw bar (matches research.ir.engine._check_exit). 0R if
    neither touches within the bars given — approximates a flat session-end
    exit, which is close enough for an informational comparison."""
    risk = abs(entry - stop)
    if risk == 0:
        return Decimal("0")
    reward = abs(target - entry)
    r_reward = reward / risk
    for bar in bars_after:
        stop_hit = (side == "long" and bar.low <= stop) or (side == "short" and bar.high >= stop)
        target_hit = (side == "long" and bar.high >= target) or (side == "short" and bar.low <= target)
        if stop_hit:
            return Decimal("-1")
        if target_hit:
            return r_reward
    return Decimal("0")


def _target_multiple_for(ir_doc: dict) -> Decimal:
    target_cfg = ir_doc.get("target")
    if not target_cfg:
        return Decimal("2.0")
    return Decimal(str(target_cfg["multiple"]))


def _bars_after(bars: Sequence[Bar], ts) -> tuple[Bar | None, list[Bar]]:
    """The bar at exactly `ts` (the decision bar, for its close) and every
    bar strictly after it — never anything at or before `ts` is looked at
    for the outcome walk, which would be lookahead of a different kind
    (reusing information the trainee's answer already had)."""
    for i, bar in enumerate(bars):
        if bar.ts == ts:
            return bar, list(bars[i + 1:])
    return None, []


def compute_divergence(
    ensemble: Ensemble,
    records: list[DecisionRecord],
    bars_by_instrument: dict[str, list[Bar]],
) -> DivergenceResult:
    members_by_id = {m.hypothesis_id: m for m in ensemble.members}

    fired_records = [r for r in records if r.candidate.point.fired]
    taken_records = [r for r in fired_records if r.answer.is_setup]
    fraction_taken = (
        Decimal(len(taken_records)) / Decimal(len(fired_records))
        if fired_records else Decimal("0")
    )

    strategy_equity: list[Decimal] = []
    strategy_r_by_index: dict[int, Decimal] = {}
    running = Decimal("0")
    for idx, r in enumerate(records):
        point = r.candidate.point
        if not point.fired:
            continue
        bars = bars_by_instrument.get(r.candidate.instrument, [])
        _, bars_after = _bars_after(bars, point.ts)
        r_outcome = _touch_outcome(
            bars_after, point.side, point.entry_price, point.stop_price, point.target_price,
        )
        running += r_outcome
        strategy_equity.append(running)
        strategy_r_by_index[idx] = r_outcome

    trainee_equity: list[Decimal] = []
    trainee_r_by_index: dict[int, Decimal] = {}
    running = Decimal("0")
    for idx, r in enumerate(records):
        answer = r.answer
        if not (answer.is_setup and answer.direction is not None and answer.stop_price is not None):
            continue
        member = members_by_id.get(r.candidate.hypothesis_id)
        if member is None:
            continue
        bars = bars_by_instrument.get(r.candidate.instrument, [])
        entry_bar, bars_after = _bars_after(bars, r.candidate.point.ts)
        if entry_bar is None:
            continue
        entry = entry_bar.close
        risk = abs(entry - answer.stop_price)
        if risk == 0:
            continue
        target_multiple = _target_multiple_for(member.ir_doc)
        reward = target_multiple * risk
        target = entry + reward if answer.direction == "long" else entry - reward
        r_outcome = _touch_outcome(bars_after, answer.direction, entry, answer.stop_price, target)
        running += r_outcome
        trainee_equity.append(running)
        trainee_r_by_index[idx] = r_outcome

    return DivergenceResult(
        trades_available=len(fired_records),
        trades_taken=len(taken_records),
        fraction_taken=fraction_taken,
        strategy_equity_r=strategy_equity,
        trainee_equity_r=trainee_equity,
        strategy_r_by_record_index=strategy_r_by_index,
        trainee_r_by_record_index=trainee_r_by_index,
    )
