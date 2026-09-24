"""Weighted drill-queue sampling tests.

CLAUDE.md rule 9: a test that can't fail when business logic changes is
wrong. These assert the actual bias the spec asks for (toward previously-
wrong decisions and near-misses) statistically, with a seeded RNG for
determinism, rather than just checking that sample_queue returns
something of the right length.
"""
from __future__ import annotations

import random
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from research.ir.engine import DecisionPoint
from research.trainer.sampling import (
    Candidate, build_candidate_pool, sample_queue, weight_of,
)

BASE_TS = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)


def _point(i: int, *, fired: bool = False, near_miss: bool = False) -> DecisionPoint:
    return DecisionPoint(
        ts=BASE_TS + timedelta(minutes=i), fired=fired, near_miss=near_miss,
        side="long" if fired else None,
        entry_price=Decimal("100") if fired else None,
        stop_price=Decimal("99") if fired else None,
        target_price=Decimal("102") if fired else None,
    )


def _candidate(i: int, **kw) -> Candidate:
    return Candidate(hypothesis_id="H1", instrument="NQ", regime_label=None, point=_point(i, **kw))


def test_weight_of_multiplies_wrong_before_and_near_miss() -> None:
    baseline = _candidate(0)
    wrong_before = _candidate(1)
    near_miss = _candidate(2, near_miss=True)
    both = _candidate(3, near_miss=True)

    assert weight_of(baseline, previously_wrong=set()) == Decimal("1")
    assert weight_of(wrong_before, previously_wrong={wrong_before.key}) == Decimal("3")
    assert weight_of(near_miss, previously_wrong=set()) == Decimal("2")
    assert weight_of(both, previously_wrong={both.key}) == Decimal("6")


def test_previously_wrong_decision_is_sampled_more_often_than_baseline() -> None:
    """3x weight -> roughly 3x the draw frequency across many independent
    single-pick trials (drawing from the same two-item pool each time, one
    weighted, one not)."""
    wrong_before = _candidate(0)
    baseline = _candidate(1)
    pool = [wrong_before, baseline]
    previously_wrong = {wrong_before.key}

    wrong_before_picks = 0
    trials = 4000
    for seed in range(trials):
        rng = random.Random(seed)
        picked = sample_queue(pool, 1, previously_wrong=previously_wrong, rng=rng)[0]
        if picked.key == wrong_before.key:
            wrong_before_picks += 1

    fraction = wrong_before_picks / trials
    # expected 0.75 (weight 3 vs 1) — generous band for RNG noise
    assert 0.68 < fraction < 0.82, fraction


def test_near_miss_is_sampled_more_often_than_a_quiet_point() -> None:
    near_miss = _candidate(0, near_miss=True)
    quiet = _candidate(1)
    pool = [near_miss, quiet]

    near_miss_picks = 0
    trials = 4000
    for seed in range(trials):
        rng = random.Random(seed)
        picked = sample_queue(pool, 1, previously_wrong=set(), rng=rng)[0]
        if picked.key == near_miss.key:
            near_miss_picks += 1

    fraction = near_miss_picks / trials
    # expected 0.667 (weight 2 vs 1)
    assert 0.58 < fraction < 0.75, fraction


def test_sample_queue_never_repeats_a_candidate() -> None:
    pool = [_candidate(i) for i in range(10)]
    queue = sample_queue(pool, 10, previously_wrong=set(), rng=random.Random(1))
    assert len(queue) == 10
    assert len({c.key for c in queue}) == 10


def test_sample_queue_caps_at_pool_size() -> None:
    pool = [_candidate(i) for i in range(3)]
    queue = sample_queue(pool, 100, previously_wrong=set(), rng=random.Random(1))
    assert len(queue) == 3


def test_build_candidate_pool_keeps_every_loud_point_but_caps_quiet_ones() -> None:
    """A uniform sample over every bar would be almost all no-trade
    sessions — the spec's own complaint. Fired/near-miss points are never
    dropped; quiet points are capped."""
    day_key = ("H1", "NQ", date(2026, 1, 6))
    group = (
        [_candidate(i, fired=True) for i in range(2)]
        + [_candidate(i, near_miss=True) for i in range(2, 4)]
        + [_candidate(i) for i in range(4, 20)]  # 16 quiet bars
    )
    pool = build_candidate_pool({day_key: group}, rng=random.Random(1))

    loud = [c for c in pool if c.point.fired or c.point.near_miss]
    quiet = [c for c in pool if not c.point.fired and not c.point.near_miss]
    assert len(loud) == 4  # all 2 fired + 2 near-miss kept
    assert len(quiet) == 3  # capped at QUIET_BARS_PER_SESSION_DAY
