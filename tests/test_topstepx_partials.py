"""Tests for live partial-profit / break-even logic in TopstepXBroker."""
from decimal import Decimal

import pytest

from app.broker.topstepx import _partial_plan


def test_partial_plan_disabled_returns_none():
    assert _partial_plan(Decimal("100"), Decimal("99"), 2, Decimal("0")) is None


def test_partial_plan_long_half_at_1_5r():
    # entry 100, stop 99 → R=1. 1.5R partial at 101.5. size 4 → half=2, remaining=2.
    plan = _partial_plan(Decimal("100"), Decimal("99"), 4, Decimal("1.5"))
    assert plan.partial_price == Decimal("101.5")
    assert plan.partial_size == 2
    assert plan.remaining_size == 2
    assert plan.be_price == Decimal("100")


def test_partial_plan_short_half_at_1_5r():
    # entry 100, stop 101 → R=1. short 1.5R partial BELOW at 98.5.
    plan = _partial_plan(Decimal("100"), Decimal("101"), 4, Decimal("1.5"))
    assert plan.partial_price == Decimal("98.5")
    assert plan.partial_size == 2
    assert plan.remaining_size == 2


def test_partial_plan_size_one_no_scaleout():
    # 1 lot → partial_size 0, remaining 1, but a partial_price (BE trigger) still set.
    plan = _partial_plan(Decimal("100"), Decimal("99"), 1, Decimal("1.5"))
    assert plan.partial_size == 0
    assert plan.remaining_size == 1
    assert plan.partial_price == Decimal("101.5")
    assert plan.be_price == Decimal("100")


def test_partial_plan_odd_size_floors_half():
    # size 3 → half=1, remaining=2.
    plan = _partial_plan(Decimal("100"), Decimal("99"), 3, Decimal("1.5"))
    assert plan.partial_size == 1
    assert plan.remaining_size == 2
