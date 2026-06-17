"""B92 — news_straddle LIVE resting-OCO path: broker order method + scheduler.

Defining-behavior tests (TDD, written before implementation). These encode WHY
the live path must behave a certain way (Rule 9):

  * place_oco_stop_entries must place TWO resting stop ENTRY orders (buy-stop
    ABOVE the range, sell-stop BELOW), register each in _pending_brackets with
    the correct stop/target offsets so the proven _place_bracket_after_fill path
    attaches a TIGHT stop at the broken boundary + a tp_r target, and link the
    two as OCO siblings.
  * When one leg fills, the resting sibling must be cancelled (OCO) and removed
    from _pending_brackets so it can NEVER open an opposite position.
  * The scheduler must lock the 15-min pre-range, arm one OCO straddle per event,
    place it at range±offset, and never re-arm a fired/skipped event.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.broker.events import Bar, Fill
from app.broker.topstepx import TopstepXBroker
from app.broker.pricing import SIDE_BUY, SIDE_SELL


UTC = timezone.utc


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
    def __init__(self):
        self.calls = []
        # Numeric ids — production order ids are numeric (_cancel_order does int()).
        self.stop_seq = iter(["8001", "8002", "8003", "8004"])

    async def place_stop_order(self, instrument_id, side, size, price, account_id):
        oid = next(self.stop_seq)
        self.calls.append(("stop", {"oid": oid, "side": side, "size": size, "price": price}))
        return FakeResp(oid)

    async def place_limit_order(self, instrument_id, side, size, price, account_id):
        self.calls.append(("limit", {"side": side, "size": size, "price": price}))
        return FakeResp("L1")

    async def cancel_order(self, order_id):
        self.calls.append(("cancel", {"order_id": order_id}))
        return FakeResp(order_id)


class FakeSuite:
    def __init__(self):
        self.orders = FakeOrders()
        self.instrument_id = "CON.F.US.MNQ.M26"


def _broker_with_stub():
    b = TopstepXBroker()
    b._suite = FakeSuite()
    b._instruments = ["MNQ"]
    return b


# ----------------------------- broker OCO entry -----------------------------

def test_place_oco_stop_entries_places_two_stop_orders():
    b = _broker_with_stub()
    # range [100, 110], offset 15 (60t * 0.25). buy_stop=125, sell_stop=85.
    buy_id, sell_id = asyncio.run(
        b.place_oco_stop_entries(
            "MNQ", Decimal("125"), Decimal("85"),
            stop_r=Decimal("15"), tp_r=Decimal("3"), size=2,
        )
    )
    stops = [c for c in b._suite.orders.calls if c[0] == "stop"]
    assert len(stops) == 2
    sides = {round(c[1]["price"]): c[1]["side"] for c in stops}
    # buy-stop ABOVE the range fills LONG (side BUY); sell-stop BELOW fills SHORT.
    assert sides[125] == SIDE_BUY
    assert sides[85] == SIDE_SELL
    assert buy_id == "8001" and sell_id == "8002"


def test_oco_entry_registers_brackets_with_tight_stop_and_target():
    b = _broker_with_stub()
    buy_id, sell_id = asyncio.run(
        b.place_oco_stop_entries(
            "MNQ", Decimal("125"), Decimal("85"),
            stop_r=Decimal("15"), tp_r=Decimal("3"), size=2,
        )
    )
    # Long leg: entry 125, stop = broken boundary 110 (offset below), target = 125+45.
    bl = b._pending_brackets[buy_id]
    assert bl["stop_offset"] == Decimal("-15")        # tight: stop = entry - R
    assert bl["target_offset"] == Decimal("45")       # 3R
    assert bl["entry_side"] == "long"
    assert bl["partial_r"] == Decimal("0")            # straddle takes full target
    # Short leg: entry 85, stop = 100 (offset above), target = 85-45.
    bs = b._pending_brackets[sell_id]
    assert bs["stop_offset"] == Decimal("15")
    assert bs["target_offset"] == Decimal("-45")
    assert bs["entry_side"] == "short"


def test_oco_entry_links_siblings():
    b = _broker_with_stub()
    buy_id, sell_id = asyncio.run(
        b.place_oco_stop_entries(
            "MNQ", Decimal("125"), Decimal("85"),
            stop_r=Decimal("15"), tp_r=Decimal("3"), size=2,
        )
    )
    assert b._oco_entry_siblings[buy_id] == sell_id
    assert b._oco_entry_siblings[sell_id] == buy_id


def test_oco_sibling_cancelled_on_fill():
    b = _broker_with_stub()
    buy_id, sell_id = asyncio.run(
        b.place_oco_stop_entries(
            "MNQ", Decimal("125"), Decimal("85"),
            stop_r=Decimal("15"), tp_r=Decimal("3"), size=2,
        )
    )
    b._suite.orders.calls.clear()
    # Buy leg fills → sibling sell-stop must be cancelled and de-registered so it
    # can never open an opposite position.
    async def _drive():
        b._cancel_oco_sibling(buy_id)
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        await asyncio.gather(*pending, return_exceptions=True)
    asyncio.run(_drive())
    cancels = [c for c in b._suite.orders.calls if c[0] == "cancel"]
    assert len(cancels) == 1
    # _cancel_order coerces to int (production ids are numeric).
    assert str(cancels[0][1]["order_id"]) == sell_id
    assert sell_id not in b._pending_brackets
    assert buy_id not in b._oco_entry_siblings
    assert sell_id not in b._oco_entry_siblings


def test_oco_sibling_noop_for_non_oco_order():
    b = _broker_with_stub()
    # An ordinary (non-OCO) entry order id must be a harmless no-op.
    b._cancel_oco_sibling("not-an-oco-order")
    assert b._suite.orders.calls == []


def test_filled_leg_brackets_at_tight_stop_and_3r_target():
    """End-to-end money math: a buy leg filling at 125 must attach a stop at the
    broken boundary (110) and a 3R target (170) via the proven bracket path."""
    b = _broker_with_stub()
    buy_id, _ = asyncio.run(
        b.place_oco_stop_entries(
            "MNQ", Decimal("125"), Decimal("85"),
            stop_r=Decimal("15"), tp_r=Decimal("3"), size=2,
        )
    )
    bracket = b._pending_brackets.pop(buy_id)
    bracket["fill_price"] = Decimal("125")   # filled at the buy-stop level
    b._suite.orders.calls.clear()
    _run(b._place_bracket_after_fill(bracket))
    stop = next(c for c in b._suite.orders.calls if c[0] == "stop")
    target = next(c for c in b._suite.orders.calls if c[0] == "limit")
    assert stop[1]["price"] == 110.0       # tight stop = fill - R (broken range high)
    assert target[1]["price"] == 170.0     # 3R = fill + 3*15


# ------------------------------- scheduler ----------------------------------

from app.notifications.news_straddle_scheduler import NewsStraddleScheduler


class FakeBroker:
    def __init__(self):
        self.oco_calls = []

    async def place_oco_stop_entries(self, instrument, buy_stop, sell_stop, *, stop_r, tp_r, size):
        self.oco_calls.append(
            {"instrument": instrument, "buy_stop": buy_stop, "sell_stop": sell_stop,
             "stop_r": stop_r, "tp_r": tp_r, "size": size}
        )
        return ("BUY_OID", "SELL_OID")


def _bar(ts, high, low):
    return Bar(ts=ts, instrument="MNQ", timeframe="1min",
               open=Decimal(str(low)), high=Decimal(str(high)),
               low=Decimal(str(low)), close=Decimal(str(low)), volume=100)


def _sched(broker, event_ts, **kw):
    return NewsStraddleScheduler(
        broker,
        instrument="MNQ",
        event_times=[event_ts],
        offset_ticks=60,
        tp_r=Decimal("3.0"),
        tick=Decimal("0.25"),
        size=2,
        arm_lead_seconds=120,
        range_minutes=15,
        min_range_bars=5,
        **kw,
    )


def test_scheduler_arms_oco_from_pre_range():
    broker = FakeBroker()
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    sched = _sched(broker, event)
    arm_ts = event - timedelta(seconds=120)
    # Fill the 15-min window before arm_ts with 1-min bars; high spans 100..110.
    base = arm_ts - timedelta(minutes=15)
    for i in range(15):
        ts = base + timedelta(minutes=i)
        asyncio.run(sched.on_bar(_bar(ts, 100 + i % 11, 100 - i % 11)))
    ev = sched._events[0]
    asyncio.run(sched._arm_event(ev))
    assert len(broker.oco_calls) == 1
    call = broker.oco_calls[0]
    # offset = 60t * 0.25 = 15. high over window = 110, low = 90.
    assert call["buy_stop"] == Decimal("110") + Decimal("15")
    assert call["sell_stop"] == Decimal("90") - Decimal("15")
    assert call["stop_r"] == Decimal("15")
    assert call["tp_r"] == Decimal("3.0")
    assert call["size"] == 2
    assert ev.status == "armed"
    assert ev.buy_id == "BUY_OID" and ev.sell_id == "SELL_OID"


def test_scheduler_skips_when_too_few_bars():
    broker = FakeBroker()
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    sched = _sched(broker, event)
    arm_ts = event - timedelta(seconds=120)
    base = arm_ts - timedelta(minutes=15)
    for i in range(3):  # only 3 bars < min_range_bars(5)
        asyncio.run(sched.on_bar(_bar(base + timedelta(minutes=i), 105, 95)))
    ev = sched._events[0]
    asyncio.run(sched._arm_event(ev))
    assert broker.oco_calls == []
    assert ev.status == "skipped"


def test_scheduler_does_not_rearm_fired_event():
    broker = FakeBroker()
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    sched = _sched(broker, event)
    arm_ts = event - timedelta(seconds=120)
    base = arm_ts - timedelta(minutes=15)
    for i in range(15):
        asyncio.run(sched.on_bar(_bar(base + timedelta(minutes=i), 105, 95)))
    ev = sched._events[0]
    asyncio.run(sched._arm_event(ev))
    asyncio.run(sched._arm_event(ev))  # second call must be a no-op
    assert len(broker.oco_calls) == 1


def test_scheduler_state_shape():
    broker = FakeBroker()
    event = datetime(2026, 6, 11, 12, 30, tzinfo=UTC)
    sched = _sched(broker, event)
    st = sched.state()
    assert "events" in st and isinstance(st["events"], list)
    assert st["events"][0]["status"] == "pending"


# ------------------------------- config gate --------------------------------

def test_live_path_is_default_off():
    from app.bot_config import StrategyParams
    s = StrategyParams()
    assert s.news_straddle_live_enabled is False


def test_straddle_preflight_config_defaults():
    from app.bot_config import StrategyParams
    s = StrategyParams()
    assert s.news_straddle_preflight_lead_seconds == 300
    assert s.news_straddle_retry_interval_seconds == 60
