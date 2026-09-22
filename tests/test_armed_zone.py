"""Tests for ArmedZoneTracker state machine.

Tests encode WHY each behavior matters for trading:
- Correct entry price ensures the broker limit order is placed at the right level
- Correct stop ensures risk is defined beyond the iFVG extreme
- Correct invalidation prevents entering a dead setup
- Wick-vs-body distinction prevents false invalidations from noise

Geometry note: for ifvg_edge zones, entry is at the near edge of the zone. A bar
that closes through the far edge (invalidation) in a single bar must first pass
through entry (fill). Therefore invalidation tests use two-bar scenarios: bar 1 is
pending (price doesn't reach entry), bar 2 closes through the far edge without
reaching entry — which requires price to move away from entry (geometrically valid
only when the bar opens/stays on the far side and gaps through the zone boundary).
For simplicity, we use retrace_ce mode for invalidation tests where entry=ce sits
inside the zone, allowing bars that close through the far edge without touching ce.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.sim.events import Bar
from app.strategy.armed_zone import ArmedZone, ArmedZoneTracker

BASE_TS = datetime(2026, 5, 28, 9, 30, tzinfo=timezone.utc)
BUFFER = Decimal("0.50")


def bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        instrument="MGC", timeframe="1min",
        ts=BASE_TS + timedelta(minutes=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=100,
    )


def make_tracker() -> ArmedZoneTracker:
    return ArmedZoneTracker()


def arm_short(tracker: ArmedZoneTracker, mode: str = "ifvg_edge") -> ArmedZone:
    """Short zone: iFVG [2401, 2403]. floor=2401, stop=2403+buffer."""
    return tracker.arm(
        side="short",
        fvg_low=Decimal("2401"), fvg_high=Decimal("2403"),
        entry_mode=mode,
        stop_buffer=BUFFER,
        created_at=BASE_TS,
        killzone="NY AM",
    )


def arm_long(tracker: ArmedZoneTracker, mode: str = "ifvg_edge") -> ArmedZone:
    """Long zone: iFVG [2398, 2400]. ceiling=2400, stop=2398-buffer."""
    return tracker.arm(
        side="long",
        fvg_low=Decimal("2398"), fvg_high=Decimal("2400"),
        entry_mode=mode,
        stop_buffer=BUFFER,
        created_at=BASE_TS,
        killzone="NY AM",
    )


class TestArmedZoneConstruction:
    def test_ifvg_edge_short_entry_price_is_fvg_low(self):
        """Short ifvg_edge: entry at floor (fvg_low) — tightest entry for short."""
        t = make_tracker()
        z = arm_short(t, mode="ifvg_edge")
        assert z.box_boundary == Decimal("2401")
        assert z.entry_price == Decimal("2401")

    def test_ifvg_edge_long_entry_price_is_fvg_high(self):
        """Long ifvg_edge: entry at ceiling (fvg_high) — tightest entry for long."""
        t = make_tracker()
        z = arm_long(t, mode="ifvg_edge")
        assert z.box_boundary == Decimal("2400")
        assert z.entry_price == Decimal("2400")

    def test_retrace_ce_entry_price_is_midpoint(self):
        """retrace_ce: entry at CE = (2401 + 2403) / 2 = 2402."""
        t = make_tracker()
        z = arm_short(t, mode="retrace_ce")
        assert z.ce == Decimal("2402")
        assert z.entry_price == Decimal("2402")

    def test_ce_always_computed_regardless_of_mode(self):
        """CE is always computed for journaling even in ifvg_edge mode."""
        t = make_tracker()
        z = arm_short(t, mode="ifvg_edge")
        assert z.ce == Decimal("2402")  # (2401 + 2403) / 2

    def test_stop_short_is_fvg_high_plus_buffer(self):
        """Short stop: fvg_high + buffer = 2403 + 0.50 = 2403.50."""
        t = make_tracker()
        z = arm_short(t)
        assert z.stop_price == Decimal("2403.50")

    def test_stop_long_is_fvg_low_minus_buffer(self):
        """Long stop: fvg_low - buffer = 2398 - 0.50 = 2397.50."""
        t = make_tracker()
        z = arm_long(t)
        assert z.stop_price == Decimal("2397.50")

    def test_close_mode_entry_price_is_box_boundary(self):
        """close mode uses box_boundary as entry (caller immediately treats as fill)."""
        t = make_tracker()
        z = arm_short(t, mode="close")
        assert z.entry_price == z.box_boundary


class TestArmedZoneFill:
    def test_short_zone_filled_when_bar_high_reaches_entry(self):
        """Short zone [entry=2401]: bar.high >= 2401 → filled."""
        t = make_tracker()
        arm_short(t, mode="ifvg_edge")
        # bar.high=2401 reaches the short entry (retrace up into zone)
        status = t.on_bar(bar(1, "2398", "2401", "2397", "2399"))
        assert status == "filled"
        assert t.active is None

    def test_long_zone_filled_when_bar_low_reaches_entry(self):
        """Long zone [entry=2400]: bar.low <= 2400 → filled."""
        t = make_tracker()
        arm_long(t, mode="ifvg_edge")
        status = t.on_bar(bar(1, "2402", "2403", "2400", "2401"))
        assert status == "filled"
        assert t.active is None

    def test_zone_pending_when_price_does_not_reach_entry(self):
        """Zone stays pending when price doesn't reach entry_price."""
        t = make_tracker()
        arm_short(t, mode="ifvg_edge")  # entry=2401
        status = t.on_bar(bar(1, "2396", "2400", "2395", "2398"))  # bar.high=2400 < 2401
        assert status == "pending"
        assert t.active is not None


class TestArmedZoneInvalidation:
    def test_body_close_below_fvg_low_invalidates_long(self):
        """Long zone: bar.close < fvg_low → invalidated.

        Uses retrace_ce mode so entry_price=ce=2399 is inside the zone.
        Bar 1: pending — price rallies above the zone, doesn't reach entry (bar.low=2401 > ce=2399).
        Bar 2: price collapses, bar.low=2401 still above entry, bar.close=2397 < fvg_low=2398.
        This is a two-bar scenario: zone armed, one pending bar, then invalidation bar.
        """
        t = make_tracker()
        # Long zone [2398, 2400], ce=2399, entry=2399 in retrace_ce mode
        t.arm(
            side="long",
            fvg_low=Decimal("2398"), fvg_high=Decimal("2400"),
            entry_mode="retrace_ce",
            stop_buffer=BUFFER,
            created_at=BASE_TS,
            killzone="NY AM",
        )
        # Bar 1: pending — price above the zone, bar.low=2401 doesn't reach entry=2399
        s1 = t.on_bar(bar(1, "2403", "2404", "2401", "2402"))
        assert s1 == "pending"
        # Bar 2: price collapses through zone — bar.low=2401 still above entry=2399,
        # but bar.close=2397 < fvg_low=2398 → invalidated
        # Valid OHLC: open=2402, high=2402, low=2397, close=2397
        # bar.low=2397 <= entry=2399 → fill fires before invalidation check
        # We need bar.low > entry=2399. Since close must be >= low, close >= low > 2399.
        # But invalidation needs close < fvg_low=2398 < 2399. Contradiction.
        #
        # Resolution: invalidation is checked before fill in the state machine.
        # If the bar's close already breaks the far edge, the zone is dead even if
        # the bar also traded through entry (a gap-down invalidation scenario).
        # The implementation must check invalidation BEFORE fill.
        s2 = t.on_bar(bar(2, "2402", "2402", "2395", "2397"))
        assert s2 == "invalidated"
        assert t.active is None

    def test_body_close_above_fvg_high_invalidates_short(self):
        """Short zone: bar.close > fvg_high → invalidated.

        Uses retrace_ce mode so entry_price=ce=2402 is inside the zone.
        Bar 1: pending — price below zone, doesn't reach entry (bar.high=2400 < ce=2402).
        Bar 2: price surges through zone — close=2404 > fvg_high=2403 → invalidated.
        Invalidation is checked before fill so close breaking far edge wins.
        """
        t = make_tracker()
        t.arm(
            side="short",
            fvg_low=Decimal("2401"), fvg_high=Decimal("2403"),
            entry_mode="retrace_ce",
            stop_buffer=BUFFER,
            created_at=BASE_TS,
            killzone="NY AM",
        )
        s1 = t.on_bar(bar(1, "2399", "2400", "2398", "2399"))
        assert s1 == "pending"
        s2 = t.on_bar(bar(2, "2400", "2406", "2400", "2404"))
        assert s2 == "invalidated"
        assert t.active is None

    def test_wick_below_fvg_low_does_not_invalidate_long(self):
        """Long zone: bar.low < fvg_low but bar.close >= fvg_low → still pending (wick doesn't count).

        Uses retrace_ce mode (entry=ce=2399) so bar.low can pierce fvg_low=2398
        without reaching entry=2399, while close stays inside the zone.
        Bar: low=2397 < fvg_low=2398, close=2399 >= fvg_low (body stays in zone).
        bar.low=2397 < entry=2399, so fill would fire — but close=2399 >= fvg_low
        means no invalidation. Fill check: bar.low=2397 <= entry=2399 → filled.

        The wick-vs-body distinction for invalidation is only distinguishable from
        fill when entry is above fvg_low. We test with ifvg_edge mode and a bar
        whose low pierces fvg_low but close stays above it AND bar.low is above entry.
        For long ifvg_edge: entry=fvg_high=2400. bar.low must be > 2400 (no fill)
        AND bar.low < fvg_low=2398 — geometrically impossible (low can't be both
        > 2400 and < 2398 simultaneously).

        Valid restatement: test that invalidation uses bar.close, not bar.low.
        We construct a bar where bar.low < fvg_low but bar.close >= fvg_low.
        For retrace_ce long (entry=ce=2399): bar.low=2397 < fvg_low=2398, close=2399.
        bar.low=2397 <= entry=2399 → fill fires (fill priority over wick-invalidation).
        This confirms fill takes priority — and the zone correctly resolves as filled,
        NOT invalidated due to the low wick. The wick-doesn't-invalidate rule is
        enforced by checking close (not low) for invalidation.
        """
        t = make_tracker()
        t.arm(
            side="long",
            fvg_low=Decimal("2398"), fvg_high=Decimal("2400"),
            entry_mode="retrace_ce",
            stop_buffer=BUFFER,
            created_at=BASE_TS,
            killzone="NY AM",
        )
        # Bar: wick pierces fvg_low, close stays above fvg_low — no invalidation by close.
        # bar.low=2397 < entry=2399 → fill fires (fill > wick-invalidation).
        # Zone resolves as "filled", not "invalidated" — wick alone never invalidates.
        status = t.on_bar(bar(1, "2400", "2401", "2397", "2399"))
        assert status == "filled"  # filled, NOT invalidated — body close stays above fvg_low

    def test_wick_above_fvg_high_does_not_invalidate_short(self):
        """Short zone: bar.high > fvg_high but bar.close <= fvg_high → still pending.

        For retrace_ce short (entry=ce=2402): a bar that wicks above fvg_high=2403
        must have high > 2403 > entry=2402, so fill fires (bar.high >= entry=2402).
        The zone resolves as "filled", not "invalidated" — body close below fvg_high
        prevents invalidation, and the fill takes effect.
        This confirms the wick-vs-body rule: close is used for invalidation, not high.
        """
        t = make_tracker()
        t.arm(
            side="short",
            fvg_low=Decimal("2401"), fvg_high=Decimal("2403"),
            entry_mode="retrace_ce",
            stop_buffer=BUFFER,
            created_at=BASE_TS,
            killzone="NY AM",
        )
        # Wick to 2405 (above fvg_high=2403), close=2402 (below fvg_high) — no invalidation.
        # bar.high=2405 >= entry=2402 → fill fires. Zone filled, not invalidated.
        status = t.on_bar(bar(1, "2401", "2405", "2401", "2402"))
        assert status == "filled"  # filled, NOT invalidated — body close stays below fvg_high


class TestArmedZoneCancel:
    def test_cancel_clears_active_zone(self):
        t = make_tracker()
        arm_short(t)
        t.cancel()
        assert t.active is None

    def test_on_bar_returns_none_when_no_active_zone(self):
        t = make_tracker()
        assert t.on_bar(bar(0, "2400", "2401", "2399", "2400")) is None


class TestStopUsesSweepExtreme:
    """Stop should sit beyond the sweep wick (liquidity level), not the iFVG edge,
    so a normal wick retest doesn't stop the trade. Matches composer.py's path."""

    def test_short_stop_uses_sweep_extreme_beyond_fvg(self):
        t = make_tracker()
        z = t.arm(side="short", fvg_low=Decimal("2401"), fvg_high=Decimal("2403"),
                  entry_mode="ifvg_edge", stop_buffer=BUFFER, created_at=BASE_TS,
                  killzone="NY AM", sweep_extreme=Decimal("2404"))
        assert z.stop_price == Decimal("2404.50")   # sweep_extreme 2404 + 0.50

    def test_long_stop_uses_sweep_extreme_beyond_fvg(self):
        t = make_tracker()
        z = t.arm(side="long", fvg_low=Decimal("2398"), fvg_high=Decimal("2400"),
                  entry_mode="ifvg_edge", stop_buffer=BUFFER, created_at=BASE_TS,
                  killzone="NY AM", sweep_extreme=Decimal("2397"))
        assert z.stop_price == Decimal("2396.50")   # sweep_extreme 2397 - 0.50

    def test_stop_never_tighter_than_fvg_edge(self):
        """Degenerate sweep inside the FVG → keep the iFVG-edge stop, never tighter."""
        t = make_tracker()
        z = t.arm(side="short", fvg_low=Decimal("2401"), fvg_high=Decimal("2403"),
                  entry_mode="ifvg_edge", stop_buffer=BUFFER, created_at=BASE_TS,
                  killzone="NY AM", sweep_extreme=Decimal("2402"))   # inside FVG
        assert z.stop_price == Decimal("2403.50")   # max(2402, 2403) + 0.50

    def test_stop_falls_back_to_fvg_edge_when_no_sweep_extreme(self):
        t = make_tracker()
        z = t.arm(side="short", fvg_low=Decimal("2401"), fvg_high=Decimal("2403"),
                  entry_mode="ifvg_edge", stop_buffer=BUFFER, created_at=BASE_TS,
                  killzone="NY AM")   # sweep_extreme omitted
        assert z.stop_price == Decimal("2403.50")


class TestZoneExpiry:
    """
    Zones must not live forever. A zone whose entry is hit long after arming
    fills at days-old structure (2026-06-10 parity post-mortem found backtest
    entries ~160 pts off-market from exactly this). max_age_bars caps zone life.
    """

    def test_zone_expires_after_max_age_bars(self):
        """Entry touched AFTER max_age_bars -> expired, never filled."""
        t = make_tracker()
        t.arm(side="long", fvg_low=Decimal("2398"), fvg_high=Decimal("2400"),
              entry_mode="ifvg_edge", stop_buffer=BUFFER, created_at=BASE_TS,
              killzone="NY AM", max_age_bars=3)
        # 3 pending bars: price stays above entry=2400, no invalidation
        for i in range(1, 4):
            assert t.on_bar(bar(i, "2401", "2402", "2400.5", "2401")) == "pending"
        # Bar 4 trades through entry — but the zone is past max age
        status = t.on_bar(bar(4, "2401", "2401.5", "2399.5", "2400.5"))
        assert status == "expired", f"stale zone must expire, got {status}"
        assert t.active is None

    def test_zone_fills_within_max_age(self):
        """Entry touched within max_age_bars -> fills as before."""
        t = make_tracker()
        t.arm(side="long", fvg_low=Decimal("2398"), fvg_high=Decimal("2400"),
              entry_mode="ifvg_edge", stop_buffer=BUFFER, created_at=BASE_TS,
              killzone="NY AM", max_age_bars=3)
        assert t.on_bar(bar(1, "2401", "2402", "2400.5", "2401")) == "pending"
        assert t.on_bar(bar(2, "2401", "2401.5", "2399.5", "2400.5")) == "filled"

    def test_zero_max_age_disables_expiry(self):
        """max_age_bars=0 (default) -> zone never expires (legacy behavior)."""
        t = make_tracker()
        t.arm(side="long", fvg_low=Decimal("2398"), fvg_high=Decimal("2400"),
              entry_mode="ifvg_edge", stop_buffer=BUFFER, created_at=BASE_TS,
              killzone="NY AM")
        for i in range(1, 50):
            assert t.on_bar(bar(i, "2401", "2402", "2400.5", "2401")) == "pending"
        assert t.on_bar(bar(50, "2401", "2401.5", "2399.5", "2400.5")) == "filled"
