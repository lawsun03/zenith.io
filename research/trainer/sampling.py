"""Weighted drill-queue sampling (docs/research-loop/PHASE-PROMPTS.md
Phase 6, requirement #3): bias toward decision points the trainee got
wrong before, and toward near-miss setups the strategy declines — while
keeping a light, non-dominant sample of quiet no-trade bars so the queue
isn't "almost all no-trade sessions" (the spec's own complaint about
uniform sampling).
"""
from __future__ import annotations

import random
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Sequence

from research.ir.engine import DecisionPoint
from research.ledger.api import isoformat_utc

QUIET_BARS_PER_SESSION_DAY = 3
WRONG_BEFORE_WEIGHT = Decimal("3")
NEAR_MISS_WEIGHT = Decimal("2")
BASE_WEIGHT = Decimal("1")

# (hypothesis_id, instrument, decision_ts.isoformat())
DecisionKey = tuple[str, str, str]


@dataclass(frozen=True)
class Candidate:
    """One decision point available to drill, tagged with the ensemble
    member and instrument it came from — the identity a trainee's past
    answer (drill_decisions) is looked up by."""

    hypothesis_id: str
    instrument: str
    regime_label: str | None
    point: DecisionPoint

    @property
    def key(self) -> DecisionKey:
        # isoformat_utc, not point.ts.isoformat() — must match exactly what
        # research.ledger.api.insert_drill_decision/previously_wrong_decisions
        # store, or "gotten wrong before" would silently never match.
        return (self.hypothesis_id, self.instrument, isoformat_utc(self.point.ts))


def build_candidate_pool(
    candidates_by_day: dict[tuple[str, str, date], list[Candidate]],
    *, rng: random.Random,
) -> list[Candidate]:
    """`candidates_by_day` groups already-extracted decision points (see
    research/trainer/decision_points.py) by (hypothesis_id, instrument,
    session_date in ET). Every fired or near-miss point is always kept;
    quiet points (fired=False, near_miss=False) are subsampled to at most
    QUIET_BARS_PER_SESSION_DAY per group."""
    pool: list[Candidate] = []
    for group in candidates_by_day.values():
        loud = [c for c in group if c.point.fired or c.point.near_miss]
        quiet = [c for c in group if not c.point.fired and not c.point.near_miss]
        pool.extend(loud)
        pool.extend(rng.sample(quiet, min(QUIET_BARS_PER_SESSION_DAY, len(quiet))))
    return pool


def weight_of(candidate: Candidate, *, previously_wrong: set[DecisionKey]) -> Decimal:
    w = BASE_WEIGHT
    if candidate.key in previously_wrong:
        w *= WRONG_BEFORE_WEIGHT
    if candidate.point.near_miss:
        w *= NEAR_MISS_WEIGHT
    return w


def sample_queue(
    pool: Sequence[Candidate], n: int, *,
    previously_wrong: set[DecisionKey],
    rng: random.Random,
) -> list[Candidate]:
    """Weighted sample without replacement, size min(n, len(pool))."""
    remaining = list(pool)
    weights = [float(weight_of(c, previously_wrong=previously_wrong)) for c in remaining]
    queue: list[Candidate] = []
    for _ in range(min(n, len(remaining))):
        idx = rng.choices(range(len(remaining)), weights=weights, k=1)[0]
        queue.append(remaining.pop(idx))
        weights.pop(idx)
    return queue
