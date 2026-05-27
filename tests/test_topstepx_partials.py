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


def _run(coro):
    """Run a coroutine, then drain create_task'd work (OCO cancels) it scheduled."""
    async def _wrap():
        await coro
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
    asyncio.run(_wrap())


class FakeResp:
    def __init__(self, order_id, success=True):
        self.orderId = order_id
        self.success = success


class FakeOrders:
    """Records SDK order calls and returns deterministic ids."""
    def __init__(self):
        self.calls = []          # list of (method, kwargs)
        # Numeric ids (production order ids are numeric; _cancel_order does int()).
        self.stop_seq = iter(["9001", "9002"])
        # Placement order in _place_partial_bracket_after_fill: final target FIRST,
        # then the partial leg. So 9101 = final target, 9102 = partial.
        self.limit_seq = iter(["9101", "9102", "9103"])
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
    assert "9001" in b._exit_groups and "9102" in b._exit_groups and "9101" in b._exit_groups
    g = b._exit_groups["9001"]
    assert g["partial_size"] == 2 and g["remaining_size"] == 2
    assert g["be_price"] == Decimal("100")


def _exit_fill(order_id, price, instrument="CON.F.US.MGC.M26", side="short", size=2):
    from datetime import datetime, timezone
    return Fill(
        ts=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        instrument=instrument, side=side, fill_price=Decimal(str(price)),
        size=size, is_entry=False, realized_pnl_delta=Decimal("0"),
        contracts_delta=-size, broker_order_id=order_id,
    )


def _register_group(b):
    """Place a size-4 long group so we can fire fills at its legs."""
    bracket = {
        "fill_price": Decimal("100"), "stop_offset": Decimal("-1"),
        "target_offset": Decimal("5"), "close_sdk_side": 1, "size": 4,
        "account_id": 1, "partial_r": Decimal("1.5"), "entry_side": "long",
        "instrument": "MGC",
    }
    asyncio.run(b._place_partial_bracket_after_fill(bracket))
    return b


def test_partial_fill_moves_stop_to_be_and_resizes():
    b = _register_group(_broker_with_stub())
    b._suite.orders.calls.clear()
    _run(b._handle_group_fill(_exit_fill("9102", "101.5", size=2)))
    modifies = [c for c in b._suite.orders.calls if c[0] == "modify"]
    assert len(modifies) == 1
    assert modifies[0][1]["stop_price"] == 100.0   # BE
    assert modifies[0][1]["size"] == 2             # remaining
    g = b._exit_groups["9001"]
    assert g["partial_filled"] is True
    assert "9102" not in b._exit_groups   # partial leg consumed


def test_final_target_after_partial_cancels_stop():
    b = _register_group(_broker_with_stub())
    _run(b._handle_group_fill(_exit_fill("9102", "101.5", size=2)))
    b._suite.orders.calls.clear()
    _run(b._handle_group_fill(_exit_fill("9101", "105", size=2)))
    cancels = [c for c in b._suite.orders.calls if c[0] == "cancel"]
    assert any(c[1]["order_id"] == 9001 for c in cancels)  # _cancel_order passes int()
    assert b._exit_groups == {}  # group cleared


def test_stop_before_partial_cancels_both_targets():
    b = _register_group(_broker_with_stub())
    b._suite.orders.calls.clear()
    _run(b._handle_group_fill(_exit_fill("9001", "99", side="short", size=4)))
    cancels = [c for c in b._suite.orders.calls if c[0] == "cancel"]
    assert len(cancels) == 2   # partial + final both cancelled
    assert b._exit_groups == {}


def _register_1lot(b, side="long"):
    stop_off = Decimal("-1") if side == "long" else Decimal("1")
    tgt_off = Decimal("5") if side == "long" else Decimal("-5")
    bracket = {
        "fill_price": Decimal("100"), "stop_offset": stop_off,
        "target_offset": tgt_off, "close_sdk_side": 1 if side == "long" else 0,
        "size": 1, "account_id": 1, "partial_r": Decimal("1.5"),
        "entry_side": side, "instrument": "MGC",
    }
    asyncio.run(b._place_partial_bracket_after_fill(bracket))
    return b


def test_1lot_arms_be_watch_no_partial_leg():
    b = _register_1lot(_broker_with_stub())
    methods = [c[0] for c in b._suite.orders.calls]
    assert methods.count("limit") == 1   # only the final target, no partial leg
    assert "MGC" in b._be_watches
    assert b._be_watches["MGC"]["trigger_price"] == Decimal("101.5")


def test_1lot_be_watch_moves_stop_when_price_crosses():
    b = _register_1lot(_broker_with_stub())
    b._suite.orders.calls.clear()
    # Price below trigger → no move.
    asyncio.run(b._maybe_move_stop_to_be("MGC", Decimal("101.0")))
    assert not any(c[0] == "modify" for c in b._suite.orders.calls)
    # Price crosses 101.5 → modify stop to BE, disarm.
    asyncio.run(b._maybe_move_stop_to_be("MGC", Decimal("101.5")))
    modifies = [c for c in b._suite.orders.calls if c[0] == "modify"]
    assert len(modifies) == 1 and modifies[0][1]["stop_price"] == 100.0
    assert b._be_watches["MGC"]["armed"] is False
    # Further crossings do nothing.
    asyncio.run(b._maybe_move_stop_to_be("MGC", Decimal("102")))
    assert len([c for c in b._suite.orders.calls if c[0] == "modify"]) == 1
