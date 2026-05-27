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


from app.broker.topstepx import TopstepXBroker


def test_broker_stores_partial_profit_r():
    b = TopstepXBroker(partial_profit_r=Decimal("1.5"))
    assert b.partial_profit_r == Decimal("1.5")


def test_broker_default_partial_disabled():
    assert TopstepXBroker().partial_profit_r == Decimal("0")


import asyncio
from app.broker.events import Fill


class FakeResp:
    def __init__(self, order_id, success=True):
        self.orderId = order_id
        self.success = success


class FakeOrders:
    """Records SDK order calls and returns deterministic ids."""
    def __init__(self):
        self.calls = []          # list of (method, kwargs)
        self.stop_seq = iter(["STOP1", "STOP2"])
        self.limit_seq = iter(["PART1", "TGT1", "TGT2"])
        self.modify_ok = True
        self.next_modify_returns = None  # override list for failure tests

    async def place_stop_order(self, instrument_id, side, size, price, account_id):
        oid = next(self.stop_seq)
        self.calls.append(("stop", {"oid": oid, "size": size, "price": price}))
        return FakeResp(oid, success=oid is not None)

    async def place_limit_order(self, instrument_id, side, size, price, account_id):
        oid = next(self.limit_seq)
        self.calls.append(("limit", {"oid": oid, "size": size, "price": price}))
        return FakeResp(oid)

    async def modify_order(self, order_id, limit_price=None, stop_price=None, size=None):
        self.calls.append(("modify", {"order_id": order_id, "stop_price": stop_price, "size": size}))
        if self.next_modify_returns is not None:
            return self.next_modify_returns.pop(0)
        return self.modify_ok

    async def cancel_order(self, order_id):
        self.calls.append(("cancel", {"order_id": order_id}))
        return FakeResp(order_id)

    async def place_market_order(self, contract_id, side, size):
        self.calls.append(("market", {"size": size, "side": side}))
        return FakeResp("FLAT1")


class FakeSuite:
    def __init__(self):
        self.orders = FakeOrders()
        self.instrument_id = "CON.F.US.MGC.M26"


def _broker_with_stub(partial_r="1.5"):
    b = TopstepXBroker(partial_profit_r=Decimal(partial_r))
    b._suite = FakeSuite()
    b._instruments = ["MGC"]
    return b


def test_place_partial_legs_size4_places_three_orders():
    b = _broker_with_stub()
    bracket = {
        "fill_price": Decimal("100"), "stop_offset": Decimal("-1"),
        "target_offset": Decimal("5"), "close_sdk_side": 1, "size": 4,
        "account_id": 1, "partial_r": Decimal("1.5"), "entry_side": "long",
        "instrument": "MGC",
    }
    asyncio.run(b._place_partial_bracket_after_fill(bracket))
    methods = [c[0] for c in b._suite.orders.calls]
    assert methods.count("stop") == 1
    assert methods.count("limit") == 2  # partial + final target
    assert "STOP1" in b._exit_groups and "PART1" in b._exit_groups and "TGT1" in b._exit_groups
    g = b._exit_groups["STOP1"]
    assert g["partial_size"] == 2 and g["remaining_size"] == 2
    assert g["be_price"] == Decimal("100")
