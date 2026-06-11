"""Tests for iFVG exit ladder — structural TP1 and breakeven.

These tests encode WHY each behavior matters for trading:
- Structural TP1 uses a real market level (HTF swing) instead of a fixed R-multiple.
  This means the partial exit respects the actual market structure rather than
  an arbitrary multiplier.
- be_after_tp1=False preserves the original stop when the trader wants to hold
  the remainder to the full target without moving risk to BE.
- The premature-liquidity cancel prevents entering a trade whose target was already
  hit before entry filled — the setup is dead.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.broker.pricing import PartialPlan, _partial_plan
from app.strategy.armed_zone import ArmedZone, ArmedZoneTracker


# ---------------------------------------------------------------------------
# _partial_plan unit tests
# ---------------------------------------------------------------------------

def test_partial_plan_uses_structural_tp1_when_provided():
    """Structural TP1 must be used verbatim — market structure, not R-math."""
    tp1 = Decimal("2402.0")
    plan = _partial_plan(
        entry_price=Decimal("2400.0"),
        stop=Decimal("2398.0"),
        size=4,
        partial_r=Decimal("0"),
        tp1_price=tp1,
        tp1_fraction=Decimal("0.5"),
    )
    assert plan is not None
    assert plan.partial_price == tp1
    assert plan.partial_size == 2   # 4 * 0.5 = 2
    assert plan.remaining_size == 2
    assert plan.be_price == Decimal("2400.0")


def test_partial_plan_tp1_fraction_applied_correctly():
    """tp1_fraction controls how much is taken off — not hardcoded to half."""
    plan = _partial_plan(
        entry_price=Decimal("2400.0"),
        stop=Decimal("2398.0"),
        size=10,
        partial_r=Decimal("0"),
        tp1_price=Decimal("2403.0"),
        tp1_fraction=Decimal("0.3"),
    )
    assert plan is not None
    assert plan.partial_size == 3   # int(10 * 0.3) = 3
    assert plan.remaining_size == 7


def test_partial_plan_falls_back_to_partial_r_when_tp1_none():
    """When no structural TP1 is provided, the R-multiple path must still work."""
    plan = _partial_plan(
        entry_price=Decimal("2400.0"),
        stop=Decimal("2398.0"),
        size=4,
        partial_r=Decimal("1.5"),
        tp1_price=None,
    )
    assert plan is not None
    # R = |2400 - 2398| = 2; long (stop below entry); partial = 2400 + 2*1.5 = 2403
    assert plan.partial_price == Decimal("2403.0")
    assert plan.partial_size == 2   # 4 // 2
    assert plan.remaining_size == 2


def test_partial_plan_returns_none_when_no_tp1_and_no_r():
    """With neither source of TP1, partials should be disabled (returns None)."""
    plan = _partial_plan(
        entry_price=Decimal("2400.0"),
        stop=Decimal("2398.0"),
        size=4,
        partial_r=Decimal("0"),
        tp1_price=None,
    )
    assert plan is None


def test_partial_plan_tp1_fraction_0_gives_zero_partial():
    """tp1_fraction=0 means close nothing at TP1 — pure BE-watch path for 1-lots."""
    plan = _partial_plan(
        entry_price=Decimal("2400.0"),
        stop=Decimal("2398.0"),
        size=2,
        partial_r=Decimal("0"),
        tp1_price=Decimal("2402.0"),
        tp1_fraction=Decimal("0"),
    )
    assert plan is not None
    assert plan.partial_size == 0
    assert plan.remaining_size == 2


def test_partial_plan_structural_tp1_wins_over_partial_r():
    """When both tp1_price and partial_r are set, structural TP1 takes precedence."""
    tp1 = Decimal("2401.5")
    plan = _partial_plan(
        entry_price=Decimal("2400.0"),
        stop=Decimal("2398.0"),
        size=4,
        partial_r=Decimal("2.0"),
        tp1_price=tp1,
        tp1_fraction=Decimal("0.5"),
    )
    assert plan is not None
    assert plan.partial_price == tp1  # structural TP1, not R-based


def test_partial_plan_short_r_based():
    """R-based path correctly inverts for short trades."""
    plan = _partial_plan(
        entry_price=Decimal("2400.0"),
        stop=Decimal("2402.0"),  # stop ABOVE entry → short
        size=4,
        partial_r=Decimal("1.5"),
        tp1_price=None,
    )
    assert plan is not None
    # R = |2400 - 2402| = 2; short; partial = 2400 - 2*1.5 = 2397
    assert plan.partial_price == Decimal("2397.0")


# ---------------------------------------------------------------------------
# TopstepXBroker _pending_brackets storage tests
# ---------------------------------------------------------------------------

def _make_broker_stub(entry_mode: str = "market"):
    """Build the minimal TopstepXBroker-like object needed to test pending_brackets."""
    from app.broker.topstepx import TopstepXBroker

    broker = object.__new__(TopstepXBroker)
    # Minimal internal state needed by place_market_bracket
    broker._connected = True
    broker._pending_brackets = {}
    broker._known_order_ids = set()
    broker._early_fills = {}
    broker.partial_profit_r = Decimal("0")
    broker.max_entry_slippage_frac = Decimal("0")
    broker.entry_mode = entry_mode
    broker._instruments = ["MGC"]
    broker._extra_suites = {}  # multi-instrument refactor: _get_suite_for() reads this
    # Fake suite + orders
    mock_resp = MagicMock()
    mock_resp.success = True
    mock_resp.orderId = "order123"
    mock_suite = MagicMock()
    mock_suite.instrument_id = "1"
    mock_suite.orders.place_market_order = AsyncMock(return_value=mock_resp)
    mock_suite.orders.place_limit_order = AsyncMock(return_value=mock_resp)
    mock_suite.client.account_info = MagicMock(id=42)
    broker._suite = mock_suite
    return broker


@pytest.mark.asyncio
async def test_tp1_price_stored_in_pending_brackets_market():
    """place_market_bracket must store tp1_price/fraction/be_after_tp1 in _pending_brackets."""
    broker = _make_broker_stub("market")
    tp1 = Decimal("2403.0")
    await broker.place_market_bracket(
        instrument="MGC",
        side="long",
        size=2,
        entry=Decimal("2400.0"),
        stop=Decimal("2398.0"),
        target=Decimal("2405.0"),
        tp1_price=tp1,
        tp1_fraction=Decimal("0.5"),
        be_after_tp1=True,
    )
    assert "order123" in broker._pending_brackets
    stored = broker._pending_brackets["order123"]
    assert stored["tp1_price"] == tp1
    assert stored["tp1_fraction"] == Decimal("0.5")
    assert stored["be_after_tp1"] is True


@pytest.mark.asyncio
async def test_tp1_price_stored_in_pending_brackets_limit():
    """place_limit_bracket must store tp1_price/fraction/be_after_tp1 in _pending_brackets."""
    broker = _make_broker_stub("limit")
    tp1 = Decimal("2397.0")
    await broker.place_limit_bracket(
        instrument="MGC",
        side="short",
        size=2,
        entry=Decimal("2400.0"),
        stop=Decimal("2402.0"),
        target=Decimal("2395.0"),
        tp1_price=tp1,
        tp1_fraction=Decimal("0.4"),
        be_after_tp1=False,
    )
    assert "order123" in broker._pending_brackets
    stored = broker._pending_brackets["order123"]
    assert stored["tp1_price"] == tp1
    assert stored["tp1_fraction"] == Decimal("0.4")
    assert stored["be_after_tp1"] is False


@pytest.mark.asyncio
async def test_be_after_tp1_false_skips_be_watch():
    """When be_after_tp1=False, no _be_watches entry should be armed for 1-lot entries.

    A 1-lot entry with a structural TP1 has partial_size=0 (fraction*1=0).
    Without the be_after_tp1 gate the broker would arm a BE watch even when
    the caller explicitly opted out of the stop move.
    """
    broker = _make_broker_stub("market")
    broker._be_watches = {}
    broker._exit_groups = {}

    # Build a bracket dict that simulates a 1-lot entry after fill
    bracket = {
        "fill_price": Decimal("2400.0"),
        "stop_offset": Decimal("-2.0"),
        "target_offset": Decimal("5.0"),
        "close_sdk_side": 1,  # SELL
        "size": 1,
        "account_id": 42,
        "instrument": "MGC",
        "entry_side": "long",
        "partial_r": Decimal("0"),
        "tp1_price": Decimal("2402.0"),
        "tp1_fraction": Decimal("0.5"),
        "be_after_tp1": False,
    }

    # Stub the order placement helpers
    broker._place_stop = AsyncMock(return_value="stop1")
    broker._place_limit = AsyncMock(return_value="target1")

    await broker._place_partial_bracket_after_fill(bracket)

    # For size=1 with fraction=0.5 → partial_size = max(0, int(1*0.5)) = 0
    # be_after_tp1=False → no _be_watches entry
    assert "MGC" not in broker._be_watches, (
        "_be_watches should be empty when be_after_tp1=False"
    )


@pytest.mark.asyncio
async def test_be_after_tp1_true_arms_be_watch_for_1lot():
    """When be_after_tp1=True, a 1-lot entry must arm the BE watch."""
    broker = _make_broker_stub("market")
    broker._be_watches = {}
    broker._exit_groups = {}

    bracket = {
        "fill_price": Decimal("2400.0"),
        "stop_offset": Decimal("-2.0"),
        "target_offset": Decimal("5.0"),
        "close_sdk_side": 1,
        "size": 1,
        "account_id": 42,
        "instrument": "MGC",
        "entry_side": "long",
        "partial_r": Decimal("0"),
        "tp1_price": Decimal("2402.0"),
        "tp1_fraction": Decimal("0.5"),
        "be_after_tp1": True,
    }

    broker._place_stop = AsyncMock(return_value="stop1")
    broker._place_limit = AsyncMock(return_value="target1")

    await broker._place_partial_bracket_after_fill(bracket)

    assert "MGC" in broker._be_watches, (
        "_be_watches must be armed when be_after_tp1=True and 1-lot entry"
    )
    watch = broker._be_watches["MGC"]
    assert watch["trigger_price"] == Decimal("2402.0")
    assert watch["armed"] is True


# ---------------------------------------------------------------------------
# ArmedZone tp1_price field
# ---------------------------------------------------------------------------

def test_armed_zone_tp1_field_stored():
    """ArmedZone dataclass must accept and store tp1_price."""
    zone = ArmedZone(
        side="long",
        fvg_low=Decimal("2398.0"),
        fvg_high=Decimal("2400.0"),
        box_boundary=Decimal("2400.0"),
        ce=Decimal("2399.0"),
        entry_mode="ifvg_edge",
        entry_price=Decimal("2400.0"),
        stop_price=Decimal("2397.7"),
        created_at=datetime(2026, 5, 28, 9, 30, tzinfo=timezone.utc),
        killzone="ny_am",
        tp1_price=Decimal("2403.5"),
    )
    assert zone.tp1_price == Decimal("2403.5")


def test_armed_zone_tp1_defaults_to_none():
    """tp1_price defaults to None — backward compatible with existing ArmedZone creation."""
    zone = ArmedZone(
        side="short",
        fvg_low=Decimal("2401.0"),
        fvg_high=Decimal("2403.0"),
        box_boundary=Decimal("2401.0"),
        ce=Decimal("2402.0"),
        entry_mode="ifvg_edge",
        entry_price=Decimal("2401.0"),
        stop_price=Decimal("2403.3"),
        created_at=datetime(2026, 5, 28, 9, 30, tzinfo=timezone.utc),
        killzone="ny_am",
    )
    assert zone.tp1_price is None


# ---------------------------------------------------------------------------
# Premature-liquidity cancel (Rule F) — engine integration
# ---------------------------------------------------------------------------

def _make_engine_with_tp1(tp1_price: Decimal, side: str, instrument: str = "MGC"):
    """Build a minimal ExecutionEngine with a live _pending_entry_tp1 entry."""
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState

    mock_broker = MagicMock()
    mock_broker.entry_mode = "limit"
    mock_broker.feed_is_healthy = MagicMock(return_value=True)
    mock_broker.cancel_all = AsyncMock(return_value=1)

    risk = RiskState(fifty_k_combine())
    engine = ExecutionEngine.__new__(ExecutionEngine)
    engine.broker = mock_broker
    engine.risk_state = risk
    engine.runners = {}
    engine._bar_router = {}
    engine.on_signal = None
    engine.contracts = 1
    engine.risk_per_trade_pct = Decimal("0")
    engine.strategy_cfg = None
    engine._on_order_placed = None
    engine._on_pre_place = None
    engine._replay_mode = True
    engine._was_locked = False
    engine._flattening = False
    engine._forming_signal_fired = {}
    engine._poll_task = None
    engine._pending_reversal = {}
    engine._reversal_flatten_active = set()
    engine._started = False
    engine.htf_bias = None
    engine.htf_levels = None
    engine._htf_warned = False
    engine._pending_entry_tp1 = {instrument: (tp1_price, side)}
    engine.flatten_enabled = False  # not under test here; disable to avoid interfering
    engine.flatten_time_ct = "15:05"
    engine.entry_cutoff_time_ct = "14:30"
    engine._flatten_task = None
    engine._flattened_today = None
    return engine


def _make_bar(
    ts: datetime,
    o: str, h: str, l: str, c: str,
    instrument: str = "MGC",
) -> "Bar":
    from app.broker.events import Bar
    return Bar(
        instrument=instrument,
        timeframe="1min",
        ts=ts,
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=100,
    )


@pytest.mark.asyncio
async def test_premature_liquidity_cancels_pending_long_entry():
    """A bar whose high crosses the TP1 must cancel the pending limit entry.

    Business logic: if price reached the opposing-side liquidity target before
    our entry filled, the setup is dead — the move we were betting on already
    happened without us, and entering now would mean chasing the end of the move.
    """
    engine = _make_engine_with_tp1(Decimal("2403.0"), "long")

    bar = _make_bar(
        ts=datetime(2026, 5, 28, 9, 31, tzinfo=timezone.utc),
        o="2400", h="2404", l="2399", c="2403",  # high >= 2403.0
    )

    await engine._handle_bar(bar)
    # cancel_all is called via create_task — drain the event loop to let it run.
    await asyncio.sleep(0)

    engine.broker.cancel_all.assert_awaited_once()
    assert "MGC" not in engine._pending_entry_tp1, (
        "_pending_entry_tp1 must be cleared after premature-liquidity cancel"
    )


@pytest.mark.asyncio
async def test_premature_liquidity_cancels_pending_short_entry():
    """A bar whose low crosses the TP1 must cancel the pending short entry."""
    engine = _make_engine_with_tp1(Decimal("2397.0"), "short")

    bar = _make_bar(
        ts=datetime(2026, 5, 28, 9, 31, tzinfo=timezone.utc),
        o="2400", h="2401", l="2396", c="2397",  # low <= 2397.0
    )

    await engine._handle_bar(bar)
    # cancel_all is called via create_task — drain the event loop to let it run.
    await asyncio.sleep(0)

    engine.broker.cancel_all.assert_awaited_once()
    assert "MGC" not in engine._pending_entry_tp1


@pytest.mark.asyncio
async def test_premature_liquidity_no_cancel_when_tp1_not_hit():
    """When price does NOT reach TP1, the pending entry stays armed."""
    engine = _make_engine_with_tp1(Decimal("2403.0"), "long")

    bar = _make_bar(
        ts=datetime(2026, 5, 28, 9, 31, tzinfo=timezone.utc),
        o="2400", h="2402", l="2399", c="2401",  # high < 2403.0
    )

    await engine._handle_bar(bar)

    engine.broker.cancel_all.assert_not_called()
    assert "MGC" in engine._pending_entry_tp1, (
        "_pending_entry_tp1 must remain when TP1 is not hit"
    )


# ---------------------------------------------------------------------------
# Partial-beyond-target guard
# ---------------------------------------------------------------------------

def _make_partial_broker_stub():
    """Broker stub wired for _place_partial_bracket_after_fill tests."""
    from app.broker.topstepx import TopstepXBroker

    broker = object.__new__(TopstepXBroker)
    broker._connected = True
    broker._pending_brackets = {}
    broker._exit_groups = {}
    broker._be_watches = {}
    broker._early_fills = {}
    broker._extra_suites = {}
    broker._fill_handlers = []
    broker.partial_profit_r = Decimal("1.5")
    broker.max_entry_slippage_frac = Decimal("0")
    broker.entry_mode = "market"
    broker._instruments = ["MNQ"]

    mock_resp = MagicMock()
    mock_resp.success = True
    mock_resp.orderId = "order_stop"
    mock_suite = MagicMock()
    mock_suite.instrument_id = "1"
    mock_suite.orders.place_stop_order = AsyncMock(return_value=mock_resp)
    mock_suite.orders.place_limit_order = AsyncMock(return_value=mock_resp)
    mock_suite.client.account_info = MagicMock(id=42)
    broker._suite = mock_suite
    return broker


# (instrument, entry, stop_offset) at each symbol's real price scale. The guard
# is pure price arithmetic, so it must hold at MGC's ~2400, MNQ's ~29000, and
# MES's ~5300 — not just the MNQ scale of the original 2026-06-08 incident.
# All cases are SHORT (matching the incident): stop above entry, target below.
_GUARD_SYMBOLS = [
    ("MGC", Decimal("2400.0"), Decimal("3.0")),
    ("MNQ", Decimal("29409.25"), Decimal("55.05")),
    ("MES", Decimal("5300.0"), Decimal("5.0")),
]


def _short_partial_bracket(instrument, entry, stop_offset, target_r):
    """Build a SHORT partial bracket with target at `target_r` * R below entry.

    target_r < 1.5 (partial_r) puts the target closer than the partial → beyond.
    target_r > 1.5 leaves the partial safely between entry and target.
    """
    return {
        "instrument": instrument,
        "fill_price": entry,
        "stop_offset": stop_offset,          # stop above entry for short
        "target_offset": -(stop_offset * target_r),  # target below entry
        "close_sdk_side": 1,
        "size": 2,
        "account_id": 42,
        "entry_side": "short",
        "partial_r": Decimal("1.5"),
        "tp1_price": None,
        "tp1_fraction": Decimal("0.5"),
        "be_after_tp1": True,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("instrument,entry,stop_offset", _GUARD_SYMBOLS)
async def test_partial_beyond_target_falls_back_to_plain_bracket(instrument, entry, stop_offset):
    """When partial_price is beyond the signal target, must use plain bracket.

    Business reason: if target_r < partial_r, the target leg fires first, cancels
    the stop and partial, and leaves partial-size contracts stranded with no
    protection. The 2026-06-08 MNQ incident: SHORT @ 29409, target at 0.72R but
    partial_r=1.5 — target fired first, orphaning 1 contract. Parametrized across
    MGC/MNQ/MES so the guard is proven at every traded symbol's price scale.
    """
    from unittest.mock import patch

    broker = _make_partial_broker_stub()
    # target at 0.72R — closer than the 1.5R partial → partial is beyond target.
    bracket = _short_partial_bracket(instrument, entry, stop_offset, Decimal("0.72"))

    plain_called = []
    async def fake_plain(b):
        plain_called.append(True)

    with patch.object(broker, "_place_bracket_after_fill", side_effect=fake_plain):
        await broker._place_partial_bracket_after_fill(bracket)

    assert plain_called, (
        f"[{instrument}] Must fall back to plain bracket when partial_price is "
        "beyond target — otherwise target fires first and orphans the partial-size "
        "contracts."
    )
    # No stop or partial/target limit orders should have been placed directly
    broker._suite.orders.place_stop_order.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("instrument,entry,stop_offset", _GUARD_SYMBOLS)
async def test_partial_within_target_uses_group_path(instrument, entry, stop_offset):
    """When partial_price is between entry and target, partial group should be used.

    Normal case: target_r > partial_r (e.g. target at 3R, partial at 1.5R).
    Parametrized across MGC/MNQ/MES — the happy path must also hold per symbol.
    """
    from unittest.mock import patch

    broker = _make_partial_broker_stub()
    # target at 3R — well beyond the 1.5R partial → group path is safe.
    bracket = _short_partial_bracket(instrument, entry, stop_offset, Decimal("3.0"))

    plain_called = []
    async def fake_plain(b):
        plain_called.append(True)

    with patch.object(broker, "_place_bracket_after_fill", side_effect=fake_plain):
        await broker._place_partial_bracket_after_fill(bracket)

    assert not plain_called, (
        f"[{instrument}] Should use group path when partial is within target range."
    )
    broker._suite.orders.place_stop_order.assert_called_once()
