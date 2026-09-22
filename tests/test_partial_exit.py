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

from app.sim.pricing import PartialPlan, _partial_plan
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
    engine.phase = None
    engine._phase_day = None
    engine._flatten_task = None
    engine._flattened_today = None
    return engine


def _make_bar(
    ts: datetime,
    o: str, h: str, l: str, c: str,
    instrument: str = "MGC",
) -> "Bar":
    from app.sim.events import Bar
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

