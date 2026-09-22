"""Contract-spec pricing invariants.

These guard the most dangerous silent bug in a multi-instrument bot: a tick/point
value that disagrees between the paper-broker P&L path and the risk-sizing path.
If they disagree, backtest P&L and live position sizing use different dollar math
for the same instrument — and nothing crashes, the numbers are just wrong.

WHY this matters (Rule 5 / Rule 9): MBT (Micro Bitcoin) was added with three
mutually-inconsistent numbers — TICK_VALUE 0.10, ticks_per_point 20, and no
_POINT_VALUE entry at all — which made paper P&L 20x too high and broke risk
sizing. This test fails if any instrument's $/point disagrees across the two modules.
"""
from __future__ import annotations

from decimal import Decimal

import pytest

from app.sim.paper import PaperBroker, TICK_VALUE, TICK_SIZE, _tick_value
from app.sim.pricing import _point_value, _POINT_VALUE


# Instruments priced by BOTH modules must agree on dollars-per-point.
# paper: $/point = tick_value ($/tick) × ticks_per_point (ticks/point)
# pricing: $/point = _point_value directly
_SHARED = ["MGC", "MNQ", "MES", "MCL", "MBT"]


@pytest.mark.parametrize("inst", _SHARED)
def test_dollars_per_point_agrees_across_modules(inst):
    paper_per_point = _tick_value(inst) * PaperBroker._ticks_per_point(inst)
    assert paper_per_point == _point_value(inst), (
        f"{inst}: paper $/point {paper_per_point} != pricing _point_value "
        f"{_point_value(inst)} — tick/point maps are inconsistent"
    )


@pytest.mark.parametrize("inst", _SHARED)
def test_tick_value_consistent_with_tick_size_and_point_value(inst):
    # tick_value ($/tick) must equal $/point × tick_size (price units/tick).
    assert _tick_value(inst) == _point_value(inst) * TICK_SIZE[inst], (
        f"{inst}: tick_value {_tick_value(inst)} != point_value×tick_size "
        f"{_point_value(inst) * TICK_SIZE[inst]}"
    )


def test_mbt_known_dollar_values():
    # CME Micro Bitcoin: 0.1 BTC/contract. A $1 move in BTC price = $0.10/contract;
    # min tick = $5.00/BTC = $0.50/contract. A $1,000 BTC move on 1 contract = $100.
    assert _point_value("MBT") == Decimal("0.10")
    assert _tick_value("MBT") == Decimal("0.50")
    assert _point_value("MBT") * Decimal("1000") == Decimal("100.00")


def test_existing_instruments_unchanged_regression():
    # Anchor the known-good instruments so the MBT fix can't perturb them.
    assert _point_value("MGC") == Decimal("10")
    assert _point_value("MNQ") == Decimal("2")
    assert _point_value("MES") == Decimal("5")
    assert _point_value("MCL") == Decimal("100")
