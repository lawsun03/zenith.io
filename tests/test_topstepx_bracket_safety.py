"""Safety tests: a filled entry must never be left naked on the loss side.

WHY these matter: when a stop+target bracket is placed after an entry fills, if
the STOP leg is rejected the position is open with no loss protection. Before
this fix, _place_bracket_after_fill registered the OCO only `if stop_id and
target_id` (no else) and _place_partial_bracket_after_fill just logged
"UNPROTECTED" and returned — either way the position sat naked until the
reconciler healed it. These tests fail if that regression returns.

Mock limitation: SafetyOrders records calls but does not model exchange ack
latency or partial-fill races. It cannot catch a naked window that opens
*between* order placement and the failure branch — only that the failure branch
flattens as intended.
"""
from decimal import Decimal

import asyncio

from app.broker.topstepx import TopstepXBroker


class FakeResp:
    def __init__(self, order_id, success=True):
        self.orderId = order_id
        self.success = success


class SafetyOrders:
    """Configurable stub: choose whether the stop leg and each limit leg
    succeed. Order ids are numeric strings so _cancel_order's int() works."""

    def __init__(self, stop_ok=True, target_oks=(True,)):
        self.calls = []
        self.stop_ok = stop_ok
        self.target_oks = list(target_oks)
        self._limit_n = 0

    async def place_stop_order(self, instrument_id, side, size, price, account_id):
        self.calls.append(("stop", {"size": size, "price": price}))
        return FakeResp("9001", success=True) if self.stop_ok else FakeResp(None, success=False)

    async def place_limit_order(self, instrument_id, side, size, price, account_id):
        i = self._limit_n
        self._limit_n += 1
        ok = self.target_oks[i] if i < len(self.target_oks) else self.target_oks[-1]
        self.calls.append(("limit", {"size": size, "price": price, "ok": ok}))
        return FakeResp(str(9100 + i), success=True) if ok else FakeResp(None, success=False)

    async def cancel_order(self, order_id):
        self.calls.append(("cancel", {"order_id": order_id}))
        return FakeResp(order_id)

    async def place_market_order(self, *args, **kwargs):
        self.calls.append(("market", kwargs))
        return FakeResp("FLAT1")


class FakeSuite:
    def __init__(self, orders):
        self.orders = orders
        self.instrument_id = "CON.F.US.MGC.M26"


def _broker(stop_ok=True, target_oks=(True,), partial_r="0"):
    b = TopstepXBroker(partial_profit_r=Decimal(partial_r))
    b._suite = FakeSuite(SafetyOrders(stop_ok=stop_ok, target_oks=target_oks))
    b._instruments = ["MGC"]
    return b


def _flatten_recorder(b):
    flatten_calls = []

    async def fake_flatten(instr):
        flatten_calls.append(instr)
        return True

    b.flatten = fake_flatten
    return flatten_calls


def _plain_bracket(size=2):
    return {
        "fill_price": Decimal("100"), "stop_offset": Decimal("-1"),
        "target_offset": Decimal("5"), "close_sdk_side": 1, "size": size,
        "account_id": 1, "partial_r": Decimal("0"), "entry_side": "long",
        "instrument": "MGC",
    }


def _partial_bracket(size=4):
    b = _plain_bracket(size)
    b["partial_r"] = Decimal("1.5")
    return b


# --- Main bracket path -------------------------------------------------------

def test_stop_failure_flattens_and_cancels_orphan_target():
    """Stop rejected + target resting (placed via the same gather) → flatten the
    position and cancel the orphan target. Never hold a naked position."""
    b = _broker(stop_ok=False, target_oks=(True,))
    flattens = _flatten_recorder(b)
    asyncio.run(b._place_bracket_after_fill(_plain_bracket()))

    methods = [c[0] for c in b._suite.orders.calls]
    assert flattens == ["MGC"]                      # position flattened
    assert "cancel" in methods                      # orphan target cancelled
    cancelled = [c[1]["order_id"] for c in b._suite.orders.calls if c[0] == "cancel"]
    assert 9100 in cancelled                        # the resting target id (int())
    assert b._exit_pairs == {}                      # no OCO registered


def test_happy_path_registers_oco_and_does_not_flatten():
    """Both legs placed → OCO registered, position NOT flattened (regression
    guard: the fix must not disturb the normal path)."""
    b = _broker(stop_ok=True, target_oks=(True,))
    flattens = _flatten_recorder(b)
    asyncio.run(b._place_bracket_after_fill(_plain_bracket()))

    methods = [c[0] for c in b._suite.orders.calls]
    assert flattens == []                            # not flattened
    assert "cancel" not in methods                   # nothing cancelled
    assert b._exit_pairs != {}                       # OCO registered


# --- Partial bracket path ----------------------------------------------------

def test_partial_stop_failure_flattens():
    """Partial path: stop (placed first) rejected → flatten, no group registered."""
    b = _broker(stop_ok=False, target_oks=(True, True), partial_r="1.5")
    flattens = _flatten_recorder(b)
    asyncio.run(b._place_partial_bracket_after_fill(_partial_bracket()))

    assert flattens == ["MGC"]
    assert b._exit_groups == {}


def test_partial_happy_path_registers_group_and_does_not_flatten():
    """Partial path happy case → group registered, not flattened (regression guard)."""
    b = _broker(stop_ok=True, target_oks=(True, True), partial_r="1.5")
    flattens = _flatten_recorder(b)
    asyncio.run(b._place_partial_bracket_after_fill(_partial_bracket()))

    assert flattens == []
    assert b._exit_groups != {}
