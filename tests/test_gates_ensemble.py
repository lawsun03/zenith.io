"""The ensemble builder — CLAUDE.md domain invariant 3: "blend, do not
select." See tests/test_gates_no_selection.py for the static source-scan
guard against a sort-then-truncate creeping into the promotion path."""
from __future__ import annotations

import random
from decimal import Decimal

import pytest

from research.gates.ensemble import build_ensemble

SURVIVORS = [(f"h{i}", {"name": f"strategy-{i}"}) for i in range(7)]


def test_every_survivor_becomes_a_member_with_equal_weight():
    ensemble = build_ensemble("sweep-displacement", SURVIVORS)
    assert ensemble.member_count == len(SURVIVORS)
    weights = {m.weight for m in ensemble.members}
    assert weights == {Decimal(1) / Decimal(len(SURVIVORS))}
    assert {m.hypothesis_id for m in ensemble.members} == {hid for hid, _ in SURVIVORS}


def test_weights_sum_to_one():
    ensemble = build_ensemble("sweep-displacement", SURVIVORS)
    assert sum(m.weight for m in ensemble.members) == Decimal(1)


def test_membership_and_weight_are_independent_of_input_order():
    """The defining property of blending, not ranking: shuffling the
    survivors given to build_ensemble must not change who's in it or what
    weight they get — a selection step would be order/rank-sensitive,
    equal-weight blending is not."""
    shuffled = SURVIVORS[:]
    random.Random(42).shuffle(shuffled)
    a = build_ensemble("f", SURVIVORS)
    b = build_ensemble("f", shuffled)
    a_by_id = {m.hypothesis_id: m.weight for m in a.members}
    b_by_id = {m.hypothesis_id: m.weight for m in b.members}
    assert a_by_id == b_by_id


def test_empty_survivors_raises():
    with pytest.raises(ValueError):
        build_ensemble("f", [])
