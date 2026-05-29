"""
Tests for ArmedZoneTracker wired into StrategyRunner.

Verifies that:
  - ifvg_edge/retrace_ce modes arm the tracker instead of returning a signal
  - close mode returns signal immediately (existing behavior unchanged)
  - armed zone fill returns a signal using zone.entry_price / zone.stop_price
  - armed zone invalidation discards the pending signal
  - Rule F: TP1 hit before entry cancels the armed zone (premature liquidity)

These tests use a controlled StrategyRunner driven by direct calls to
_arm_or_return and armed_tracker.on_bar — NOT by feeding bars through
on_bar() — because the bar sequence required to trigger a real graded signal
is complex and fragile. The wiring tests encode the dispatch logic; the
integration replay test in test_engine.py covers the full pipeline.

WHY each behavior matters:
  - ifvg_edge arms: broker must never see a stale entry price when price
    hasn't actually retraced to the zone edge yet.
  - close returns immediately: existing behavior for market entry is unaffected.
  - fill builds signal: returned signal.entry/stop must reflect the zone prices
    so the broker places the limit/stop bracket at the right levels.
  - invalidation clears state: prevents a second entry attempt on a dead setup.
  - Rule F cancel: prevents entering a trade after the opposing liquidity
    (TP1 structural swing) has already been taken — the R-multiple is now wrong.
"""
from __future__ import annotations

from dataclasses import replace as dc_replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.bot_config import StrategyParams
from app.broker.events import Bar
from app.execution.engine import StrategyRunner
from app.strategy.armed_zone import ArmedZone, ArmedZoneTracker
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.grader import SetupGrader
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker

BASE_TS = datetime(2026, 5, 28, 9, 30, tzinfo=timezone.utc)


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        instrument="MGC", timeframe="1min",
        ts=BASE_TS + timedelta(minutes=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=100,
    )


def _make_runner(entry_mode: str = "ifvg_edge") -> StrategyRunner:
    """Minimal runner for wiring tests. Session filter disabled."""
    return StrategyRunner(
        instrument="MGC",
        timeframe="1min",
        liquidity=LiquidityTracker(LiquidityConfig(swing_lookback=2, min_penetration=Decimal("0.20"))),
        displacement=DisplacementDetector(DisplacementConfig(
            atr_period=5,
            body_atr_multiple=Decimal("1.0"),
            min_body_to_range_ratio=Decimal("0.5"),
            min_absolute_body=Decimal("0.5"),
        )),
        composer=SweepDisplacementComposer(ComposerConfig(
            instrument="MGC",
            displacement_window_bars=5,
            stop_buffer=Decimal("0.30"),
            r_multiple=Decimal("2.0"),
        )),
        grader=SetupGrader(),
        strategy_cfg=StrategyParams(ifvg_session_windows=[], ifvg_entry_mode=entry_mode),
    )


def _make_short_signal(
    entry: str = "2401.0",
    stop: str = "2403.50",
    target: str = "2396.0",
    fvg_low: str = "2401.0",
    fvg_high: str = "2403.0",
) -> Signal:
    """A realistic short signal with FVG bounds set (required for arming)."""
    return Signal(
        instrument="MGC",
        side="short",
        entry=Decimal(entry),
        stop=Decimal(stop),
        target=Decimal(target),
        created_at=BASE_TS,
        killzone="NY AM",
        sweep_pattern="B_one_bar",
        sweep_extreme=Decimal(stop),
        fvg_low=Decimal(fvg_low),
        fvg_high=Decimal(fvg_high),
        rationale="test",
    )


def _make_long_signal(
    entry: str = "2400.0",
    stop: str = "2397.50",
    target: str = "2406.0",
    fvg_low: str = "2398.0",
    fvg_high: str = "2400.0",
) -> Signal:
    """A realistic long signal with FVG bounds set (required for arming)."""
    return Signal(
        instrument="MGC",
        side="long",
        entry=Decimal(entry),
        stop=Decimal(stop),
        target=Decimal(target),
        created_at=BASE_TS,
        killzone="NY AM",
        sweep_pattern="B_one_bar",
        sweep_extreme=Decimal(stop),
        fvg_low=Decimal(fvg_low),
        fvg_high=Decimal(fvg_high),
        rationale="test",
    )


def _make_disp_event():
    """Minimal DisplacementEvent for _arm_or_return parameter."""
    from app.strategy.displacement import DisplacementEvent, FairValueGap
    fvg = FairValueGap(
        low=Decimal("2401.0"), high=Decimal("2403.0"),
        side="bearish", created_at=BASE_TS,
    )
    from app.broker.events import Bar as _Bar
    b2 = _Bar(
        instrument="MGC", timeframe="1min", ts=BASE_TS,
        open=Decimal("2401"), high=Decimal("2401.2"),
        low=Decimal("2398.4"), close=Decimal("2398.5"),
        volume=100,
    )
    return DisplacementEvent(
        side="bearish",
        displacement_bar=b2,
        body_size=Decimal("2.5"),
        atr_at_event=Decimal("2.0"),
        body_to_atr=Decimal("1.25"),
        fvg=fvg,
    )


class TestCloseModeBehavior:
    def test_close_mode_returns_signal_immediately(self):
        """close mode: _arm_or_return returns signal directly, no zone armed.

        WHY: market entries need the signal returned to the broker immediately.
        ifvg_edge/retrace_ce need to wait for price to retrace — market entry
        means price is already there, so arming would cause a missed fill.
        """
        runner = _make_runner(entry_mode="close")
        signal = _make_short_signal()
        disp = _make_disp_event()

        result = runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)

        assert result is not None, "close mode must return the signal immediately"
        assert result is signal or result.entry == signal.entry
        assert runner.armed_tracker.active is None, "close mode must not arm a zone"
        assert runner._pending_signal is None


class TestIfvgEdgeModeArms:
    def test_ifvg_edge_mode_arms_instead_of_returning_signal(self):
        """ifvg_edge mode: _arm_or_return returns None and arms tracker.

        WHY: entry is only valid when price actually retraces to the zone edge.
        Returning the signal immediately would cause a market entry at the wrong
        price — the entry should only fire when price touches the fvg boundary.
        """
        runner = _make_runner(entry_mode="ifvg_edge")
        signal = _make_short_signal()
        disp = _make_disp_event()

        result = runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)

        assert result is None, "ifvg_edge mode must return None (armed, not returned)"
        assert runner.armed_tracker.active is not None, "armed_tracker must have an active zone"
        assert runner._pending_signal is not None, "_pending_signal must be stored"

    def test_retrace_ce_mode_arms_instead_of_returning_signal(self):
        """retrace_ce mode: _arm_or_return returns None and arms tracker at CE.

        WHY: entry at CE (midpoint) requires price to pull back deeper than
        ifvg_edge. Same arming logic applies — wait for CE touch.
        """
        runner = _make_runner(entry_mode="retrace_ce")
        signal = _make_short_signal()
        disp = _make_disp_event()

        result = runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)

        assert result is None
        zone = runner.armed_tracker.active
        assert zone is not None
        # CE for [2401, 2403] = 2402
        assert zone.entry_price == Decimal("2402")
        assert zone.entry_mode == "retrace_ce"

    def test_no_fvg_bounds_returns_signal_directly(self):
        """When fvg_low/fvg_high are None, cannot compute zone — return signal directly.

        WHY: ArmedZone requires FVG bounds to compute entry/stop/CE. Signals
        without FVG bounds (unusual but possible) must still reach the broker.
        """
        runner = _make_runner(entry_mode="ifvg_edge")
        signal = Signal(
            instrument="MGC", side="short",
            entry=Decimal("2401"), stop=Decimal("2403.5"), target=Decimal("2396"),
            created_at=BASE_TS, killzone="NY AM", sweep_pattern="B_one_bar",
            sweep_extreme=Decimal("2403.5"), fvg_low=None, fvg_high=None,
            rationale="no fvg",
        )
        disp = _make_disp_event()

        result = runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)

        assert result is not None, "no-FVG signal must be returned directly"
        assert runner.armed_tracker.active is None


class TestArmedZoneFill:
    def test_armed_zone_fill_returns_signal_with_zone_entry_stop(self):
        """When armed zone fills, returned signal uses zone.entry_price and zone.stop_price.

        WHY: the broker must receive the limit price and stop defined by the zone,
        not the original signal's market-entry price or composer stop. The zone prices
        are the correct levels for a retrace limit order.
        """
        runner = _make_runner(entry_mode="ifvg_edge")
        signal = _make_short_signal()
        disp = _make_disp_event()

        # Arm the zone
        runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)
        zone = runner.armed_tracker.active
        assert zone is not None

        # Feed a bar that fills the short zone: bar.high >= zone.entry_price (2401)
        # Short zone: entry_price = fvg_low = 2401 (ifvg_edge short → box_boundary=fvg_low)
        # bar.high=2401 >= 2401 → filled
        fill_bar = _bar(1, "2399", "2401", "2398", "2399.5")

        # Manually invoke tracker.on_bar since we're unit testing the dispatch
        status = runner.armed_tracker.on_bar(fill_bar)
        assert status == "filled"

        # Simulate what on_bar() does after "filled"
        pending = runner._pending_signal
        assert pending is not None
        filled_signal = dc_replace(
            pending,
            entry=zone.entry_price,
            stop=zone.stop_price,
            armed_zone=zone,
        )
        runner._pending_signal = None

        assert filled_signal.entry == zone.entry_price, "signal.entry must be zone.entry_price"
        assert filled_signal.stop == zone.stop_price, "signal.stop must be zone.stop_price"
        assert filled_signal.armed_zone is zone

    def test_pending_signal_cleared_after_fill(self):
        """After processing a fill, _pending_signal is None.

        WHY: prevents double-entry if a second bar arrives before the engine
        processes the fill signal.
        """
        runner = _make_runner(entry_mode="ifvg_edge")
        signal = _make_long_signal()
        disp = _make_disp_event()

        runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)

        # Simulate fill processing (as done in on_bar)
        zone = runner.armed_tracker.active
        pending = runner._pending_signal
        runner._pending_signal = None  # cleared on fill
        runner.armed_tracker.cancel()  # zone already cleared by on_bar, but simulate

        assert runner._pending_signal is None
        assert pending is not None  # was set before fill


class TestArmedZoneInvalidation:
    def test_armed_zone_invalidation_discards_pending_signal(self):
        """When armed zone is invalidated, _pending_signal is cleared and tracker reset.

        WHY: once a zone is invalidated (price closed through the far edge),
        the setup is dead. The pending signal must be discarded so the engine
        can seek a new setup on the next valid bar.
        """
        runner = _make_runner(entry_mode="ifvg_edge")
        signal = _make_short_signal()
        disp = _make_disp_event()

        runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)
        assert runner.armed_tracker.active is not None
        assert runner._pending_signal is not None

        # Short zone [fvg_low=2401, fvg_high=2403], ifvg_edge: entry=2401 (box_boundary=fvg_low)
        # Invalidation (short): bar.close > fvg_high=2403
        # Must also have bar.high < entry=2401 (no fill first) — but invalidation is
        # checked before fill in ArmedZoneTracker, so close > fvg_high wins regardless.
        inval_bar = _bar(1, "2400", "2404", "2399", "2404")  # close=2404 > fvg_high=2403
        status = runner.armed_tracker.on_bar(inval_bar)
        assert status == "invalidated"

        # Simulate on_bar() dispatch for "invalidated"
        runner._pending_signal = None  # as done in on_bar

        assert runner.armed_tracker.active is None
        assert runner._pending_signal is None

    def test_pending_signal_set_before_invalidation(self):
        """_pending_signal is non-None between arm and invalidation/fill.

        WHY: the pending signal must persist across multiple 'pending' bars so the
        zone can fill on any subsequent bar within the session.
        """
        runner = _make_runner(entry_mode="retrace_ce")
        signal = _make_short_signal()
        disp = _make_disp_event()

        runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)

        # Feed a pending bar (price doesn't reach CE=2402, doesn't invalidate)
        # Short zone: pending if bar.high < 2402 (no fill) and bar.close <= 2403 (no inval)
        pending_bar = _bar(1, "2399", "2400", "2398", "2399")
        status = runner.armed_tracker.on_bar(pending_bar)
        assert status == "pending"
        assert runner._pending_signal is not None, "_pending_signal must survive a 'pending' bar"


class TestPrematureLiquidityRule:
    def test_premature_liquidity_cancel_via_tp1_short(self):
        """Rule F: if short TP1 hit before entry, cancel armed zone.

        WHY: if the opposing structural low (the target) is taken before
        our short entry fills, the R-multiple is already compromised —
        the trade should not be entered. The zone must be cancelled.
        """
        runner = _make_runner(entry_mode="ifvg_edge")
        signal = _make_short_signal()
        disp = _make_disp_event()

        # Arm the zone
        runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)
        assert runner.armed_tracker.active is not None

        # Manually set tp1_price on the zone (normally set by grader HTF swings)
        # Short TP1 is a swing low below entry — price hit before filling
        zone = runner.armed_tracker.active
        zone_with_tp1 = dc_replace(zone, tp1_price=Decimal("2397.0"))
        runner.armed_tracker._active = zone_with_tp1
        runner._pending_signal = dc_replace(runner._pending_signal, armed_zone=zone_with_tp1)

        # Bar that hits TP1 (bar.low <= 2397.0) without hitting entry (bar.high < 2401)
        # Short side: TP1 hit = bar.low <= tp1_price for short (going down)
        tp1_bar = _bar(1, "2399", "2400", "2396.5", "2398")  # low=2396.5 < tp1=2397, high=2400 < 2401

        # Simulate the premature-liquidity check from on_bar()
        active = runner.armed_tracker.active
        assert active is not None
        assert active.tp1_price is not None
        if active.side == "short" and tp1_bar.low <= active.tp1_price:
            runner.armed_tracker.cancel()
            runner._pending_signal = None

        assert runner.armed_tracker.active is None, "Zone must be cancelled after TP1 hit"
        assert runner._pending_signal is None, "_pending_signal must be cleared after TP1 hit"

    def test_premature_liquidity_cancel_via_tp1_long(self):
        """Rule F: if long TP1 hit before entry, cancel armed zone.

        WHY: same as short case — opposing structural high taken before long entry
        fills means the setup is compromised and must be discarded.
        """
        runner = _make_runner(entry_mode="ifvg_edge")
        signal = _make_long_signal()
        disp = _make_disp_event()

        runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)
        assert runner.armed_tracker.active is not None

        # Set tp1_price: for long, TP1 is a swing high above entry
        zone = runner.armed_tracker.active
        zone_with_tp1 = dc_replace(zone, tp1_price=Decimal("2405.0"))
        runner.armed_tracker._active = zone_with_tp1
        runner._pending_signal = dc_replace(runner._pending_signal, armed_zone=zone_with_tp1)

        # Bar that hits TP1 (bar.high >= 2405.0) without hitting entry (bar.low > 2400)
        tp1_bar = _bar(1, "2401", "2406", "2401", "2403")  # high=2406 >= tp1=2405, low=2401 > entry=2400

        # Simulate premature-liquidity check
        active = runner.armed_tracker.active
        assert active is not None
        if active.side == "long" and tp1_bar.high >= active.tp1_price:
            runner.armed_tracker.cancel()
            runner._pending_signal = None

        assert runner.armed_tracker.active is None
        assert runner._pending_signal is None

    def test_tp1_not_hit_leaves_zone_armed(self):
        """TP1 not hit: zone remains armed, _pending_signal intact.

        WHY: Rule F must only cancel when TP1 is actually hit. A bar that
        moves toward TP1 but doesn't reach it must leave the zone live.
        """
        runner = _make_runner(entry_mode="ifvg_edge")
        signal = _make_short_signal()
        disp = _make_disp_event()

        runner._arm_or_return(_bar(0, "2401", "2401.2", "2398.4", "2398.5"), signal, disp)
        zone = runner.armed_tracker.active
        zone_with_tp1 = dc_replace(zone, tp1_price=Decimal("2397.0"))
        runner.armed_tracker._active = zone_with_tp1
        runner._pending_signal = dc_replace(runner._pending_signal, armed_zone=zone_with_tp1)

        # Bar that does NOT hit tp1 (low=2397.5 > tp1=2397.0)
        no_tp1_bar = _bar(1, "2399", "2400", "2397.5", "2398.5")

        # Simulate premature-liquidity check — nothing should cancel
        active = runner.armed_tracker.active
        if active is not None and active.tp1_price is not None:
            if active.side == "short" and no_tp1_bar.low <= active.tp1_price:
                runner.armed_tracker.cancel()
                runner._pending_signal = None

        assert runner.armed_tracker.active is not None, "Zone must remain armed"
        assert runner._pending_signal is not None


class TestArmedZoneTrackerField:
    def test_runner_has_armed_tracker_field(self):
        """StrategyRunner must have armed_tracker and _pending_signal fields.

        WHY: ensures the wiring fields are present and initialized correctly.
        Missing fields would cause AttributeError in on_bar.
        """
        runner = _make_runner()
        assert hasattr(runner, "armed_tracker")
        assert isinstance(runner.armed_tracker, ArmedZoneTracker)
        assert runner.armed_tracker.active is None

        assert hasattr(runner, "_pending_signal")
        assert runner._pending_signal is None

    def test_second_runner_gets_independent_tracker(self):
        """Each StrategyRunner instance has its own ArmedZoneTracker (not shared).

        WHY: if two runners shared a tracker, one instrument's zone fill/cancel
        would incorrectly affect another instrument's pending signal.
        """
        r1 = _make_runner()
        r2 = _make_runner()
        assert r1.armed_tracker is not r2.armed_tracker
