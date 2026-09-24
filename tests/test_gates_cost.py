"""Gate 2 — cost survival. Acceptance test: "A strategy with 1.2x cost
headroom is rejected at gate 2" (docs/research-loop/PHASE-PROMPTS.md phase 4).
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from research.gates.cost import COST_HEADROOM_MIN, evaluate, round_turn_cost


def test_round_turn_cost_hand_computed():
    # market_normal: 1 tick/side = 2 ticks total. commission $2.50/side = $5.
    # human latency (discretionary entry): +1 tick. tick_value $5.
    # cost = 5 + 2*5 + 1*5 = 5 + 10 + 5 = 20.
    cost = round_turn_cost(
        condition="market_normal", tick_value=Decimal("5.00"),
        commission_and_fees_per_side=Decimal("2.50"),
    )
    assert cost == Decimal("20.00")


def test_resting_orders_skip_human_latency():
    cost = round_turn_cost(
        condition="market_normal", tick_value=Decimal("5.00"),
        commission_and_fees_per_side=Decimal("2.50"), discretionary_entry=False,
    )
    assert cost == Decimal("15.00")


def test_two_x_headroom_passes():
    result = evaluate(Decimal("40"), Decimal("20"))
    assert result.passed
    assert result.measured == 2.0
    assert result.threshold == COST_HEADROOM_MIN


def test_1_2x_headroom_fails():
    result = evaluate(Decimal("24"), Decimal("20"))
    assert not result.passed
    assert result.measured == pytest.approx(1.2)
