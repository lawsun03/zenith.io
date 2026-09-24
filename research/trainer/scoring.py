"""Fidelity scoring.

THE RULE THE WHOLE FEATURE STANDS ON: a trainee is scored against the IR's
own answer, never against what the market did. This module contains no
reference to pnl or market outcome anywhere in its code — only in this
docstring's prose, which tests/test_trainer_scoring.py's identifier-level
check deliberately does not flag (an English sentence *about* the rule is
not the same as the rule being violated).
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

Side = str  # "long" | "short"


@dataclass(frozen=True)
class GroundTruth:
    """What the IR itself says about a decision point — straight from
    research.ir.engine.DecisionPoint. Never derived from any bar after
    decision_ts."""

    fired: bool
    side: Side | None = None
    stop_price: Decimal | None = None


@dataclass(frozen=True)
class Answer:
    """A trainee's answer to one decision point's three questions.
    direction/stop_price are expected to be None when is_setup=False —
    there was nothing to answer."""

    is_setup: bool
    direction: Side | None = None
    stop_price: Decimal | None = None


@dataclass(frozen=True)
class Score:
    """Per-question correctness. direction/stop are None ("not
    applicable") whenever the IR and the trainee didn't both call it a
    setup — grading a direction or a stop against a trade that, by the
    IR's own rules, does not exist has no ground truth to grade against."""

    correct_setup: bool
    correct_direction: bool | None
    correct_stop: bool | None


def score_decision(truth: GroundTruth, answer: Answer, *, tick_size: Decimal) -> Score:
    correct_setup = answer.is_setup == truth.fired

    true_positive = truth.fired and answer.is_setup
    correct_direction: bool | None = None
    correct_stop: bool | None = None
    if true_positive:
        correct_direction = answer.direction == truth.side
        correct_stop = (
            answer.stop_price is not None
            and truth.stop_price is not None
            and abs(answer.stop_price - truth.stop_price) <= tick_size
        )

    return Score(correct_setup=correct_setup, correct_direction=correct_direction,
                 correct_stop=correct_stop)


def fidelity_score(scores: list[Score]) -> Decimal:
    """Fraction of scored sub-questions that matched the IR. Direction and
    stop only count toward the denominator when they were applicable
    (see Score) — the DDL comment ("fraction of decisions matching the
    IR") is scored per sub-question, not per decision, since setup/
    direction/stop fail differently and each needs its own count."""
    correct = 0
    total = 0
    for s in scores:
        total += 1
        correct += int(s.correct_setup)
        if s.correct_direction is not None:
            total += 1
            correct += int(s.correct_direction)
        if s.correct_stop is not None:
            total += 1
            correct += int(s.correct_stop)
    if total == 0:
        return Decimal("0")
    return Decimal(correct) / Decimal(total)


@dataclass(frozen=True)
class SessionTally:
    """Maps directly onto drill_sessions' columns
    (docs/research-loop/ledger.sql)."""

    n_decisions: int
    setups_correctly_taken: int
    setups_missed: int
    false_positives: int
    direction_errors: int
    stop_placement_errors: int
    fidelity: Decimal


def tally_session(decisions: list[tuple[GroundTruth, Answer, Score]]) -> SessionTally:
    setups_correctly_taken = sum(1 for t, a, _ in decisions if t.fired and a.is_setup)
    setups_missed = sum(1 for t, a, _ in decisions if t.fired and not a.is_setup)
    false_positives = sum(1 for t, a, _ in decisions if not t.fired and a.is_setup)
    direction_errors = sum(1 for _, _, s in decisions if s.correct_direction is False)
    stop_placement_errors = sum(1 for _, _, s in decisions if s.correct_stop is False)
    return SessionTally(
        n_decisions=len(decisions),
        setups_correctly_taken=setups_correctly_taken,
        setups_missed=setups_missed,
        false_positives=false_positives,
        direction_errors=direction_errors,
        stop_placement_errors=stop_placement_errors,
        fidelity=fidelity_score([s for _, _, s in decisions]),
    )
