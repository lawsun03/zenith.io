"""
Strategy detector tests.

Each module gets a handcrafted bar sequence designed to trigger or
NOT trigger its specific behavior. Composer tests stitch the modules
together end-to-end on a synthetic /MGC scenario.

Convention for hand-built bars:
  - 1-minute bars at base_ts + i minutes.
  - Prices in /MGC range (~$2,400) for realism in stop/target math.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.broker.events import Bar
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer
from app.strategy.displacement import (
    DisplacementConfig,
    DisplacementDetector,
    FairValueGap,
)
from app.strategy.killzone import (
    Killzone,
    default_killzones,
    in_killzone,
    london_open,
    ny_am,
    ny_pm,
)
from app.strategy.liquidity import (
    LiquidityConfig,
    LiquidityTracker,
    SweepEvent,
)

ET = ZoneInfo("America/New_York")


def bar(
    ts: datetime,
    o: str, h: str, l: str, c: str,
    instrument: str = "MGC",
    timeframe: str = "1min",
    volume: int = 100,
) -> Bar:
    return Bar(
        instrument=instrument,
        timeframe=timeframe,
        ts=ts,
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=volume,
    )


def et_ts(date_y_m_d: tuple[int, int, int], h: int, m: int) -> datetime:
    """Build an ET-zoned datetime."""
    y, mo, d = date_y_m_d
    return datetime(y, mo, d, h, m, tzinfo=ET)


# =====================================================================
# Killzone tests
# =====================================================================

class TestKillzone:

    def test_ny_am_window(self):
        """8:30 ET = NY AM. 11:01 ET = outside."""
        ts_in = et_ts((2026, 5, 11), 9, 30)
        ts_out = et_ts((2026, 5, 11), 11, 1)
        zones = default_killzones()
        assert in_killzone(ts_in, zones) is not None
        assert in_killzone(ts_in, zones).name == "NY AM"
        assert in_killzone(ts_out, zones) is None

    def test_end_is_exclusive(self):
        """A bar at exactly 11:00 ET is OUT of NY AM (window is exclusive end)."""
        ts = et_ts((2026, 5, 11), 11, 0)
        assert in_killzone(ts, default_killzones()) is None

    def test_start_is_inclusive(self):
        """A bar at 8:30:00 ET is IN NY AM."""
        ts = et_ts((2026, 5, 11), 8, 30)
        zone = in_killzone(ts, default_killzones())
        assert zone is not None
        assert zone.name == "NY AM"

    def test_dst_transition_handled_correctly(self):
        """
        Critical: 9:30 ET in summer (EDT, UTC-4) and 9:30 ET in winter
        (EST, UTC-5) are different UTC times. Both must be NY AM.
        """
        # March 2026: EDT
        summer = datetime(2026, 6, 15, 9, 30, tzinfo=ET)
        # December 2026: EST
        winter = datetime(2026, 12, 15, 9, 30, tzinfo=ET)
        zones = default_killzones()
        assert in_killzone(summer, zones).name == "NY AM"
        assert in_killzone(winter, zones).name == "NY AM"

    def test_naive_datetime_rejected(self):
        """A naive datetime would silently default to UTC and break DST."""
        naive = datetime(2026, 5, 11, 9, 30)  # no tzinfo
        with pytest.raises(ValueError):
            in_killzone(naive, default_killzones())

    def test_outside_all_zones(self):
        """Asia session 22:00 ET — not a killzone we trade."""
        ts = et_ts((2026, 5, 11), 22, 0)
        assert in_killzone(ts, default_killzones()) is None


# =====================================================================
# Liquidity tracker tests
# =====================================================================

def make_session_bars(prices: list[tuple[str, str, str, str]]) -> list[Bar]:
    """Build a sequence of 1-minute bars from (o,h,l,c) tuples."""
    base = datetime(2026, 5, 11, 13, 30, tzinfo=timezone.utc)
    return [
        bar(base + timedelta(minutes=i), o, h, l, c)
        for i, (o, h, l, c) in enumerate(prices)
    ]


class TestLiquidityTracker:

    def test_swing_high_confirmation_lags_by_lookback(self):
        """
        A swing high at bar N must NOT appear in `swings` until bar
        N + lookback has been processed. This is a fundamental
        constraint: you cannot identify a swing in real time.
        """
        tracker = LiquidityTracker(LiquidityConfig(swing_lookback=2))
        # Bars: descending, peak at index 3, then descending again.
        prices = [
            ("2400", "2400.5", "2399.8", "2400.2"),
            ("2400.2", "2401", "2400", "2400.8"),
            ("2400.8", "2402", "2400.5", "2401.5"),
            ("2401.5", "2403", "2401.2", "2402.5"),  # <-- peak (idx 3)
            ("2402.5", "2402", "2401", "2401.2"),
            ("2401.2", "2401.5", "2400", "2400.5"),
        ]
        bars = make_session_bars(prices)

        # Process bars one by one; track when the swing first appears.
        first_seen_at = None
        for i, b in enumerate(bars):
            tracker.on_bar(b)
            highs = [s for s in tracker.swings if s.kind == "high"]
            if highs and first_seen_at is None:
                first_seen_at = i
                assert highs[0].price == Decimal("2403")

        # Swing at idx 3, lookback 2 → confirmed at idx 5 at earliest.
        assert first_seen_at is not None
        assert first_seen_at >= 5

    def test_pattern_b_one_bar_sweep(self):
        """
        Build a swing high, then a single bar that pierces it and
        closes back below — should emit a Pattern B sweep on that bar.
        """
        cfg = LiquidityConfig(swing_lookback=2, min_penetration=Decimal("0.20"))
        tracker = LiquidityTracker(cfg)
        prices = [
            ("2400", "2400.5", "2399.8", "2400.2"),
            ("2400.2", "2401", "2400", "2400.8"),
            ("2400.8", "2403", "2400.5", "2401.5"),  # swing high candidate
            ("2401.5", "2402", "2401", "2401.2"),
            ("2401.2", "2401.5", "2400", "2400.5"),  # confirms swing high
            # Now a Pattern B bar: pierces 2403 by 0.50 (> 0.20), closes at 2401.
            ("2400.5", "2403.5", "2400", "2401"),
        ]
        bars = make_session_bars(prices)

        events_total: list[SweepEvent] = []
        for b in bars:
            events_total.extend(tracker.on_bar(b))

        b_sweeps = [e for e in events_total if e.pattern == "B_one_bar"]
        assert len(b_sweeps) == 1
        assert b_sweeps[0].side == "high"
        assert b_sweeps[0].swept_swing.price == Decimal("2403")
        assert b_sweeps[0].sweep_extreme == Decimal("2403.5")

    def test_pattern_a_multi_bar_sweep(self):
        """
        Tag a swing high but close above (no Pattern B). Within window,
        a later bar closes back below the swing → Pattern A emits.
        """
        cfg = LiquidityConfig(
            swing_lookback=2,
            min_penetration=Decimal("0.20"),
            multi_bar_window=4,
        )
        tracker = LiquidityTracker(cfg)
        prices = [
            ("2400", "2400.5", "2399.8", "2400.2"),
            ("2400.2", "2401", "2400", "2400.8"),
            ("2400.8", "2403", "2400.5", "2401.5"),  # swing high candidate
            ("2401.5", "2402", "2401", "2401.2"),
            ("2401.2", "2401.5", "2400", "2400.5"),  # confirms swing high
            # Tag bar: pierces 2403 by 0.50, but closes ABOVE 2403 (no Pattern B).
            ("2400.5", "2403.5", "2400.5", "2403.2"),
            # Bar 7: still above 2403.
            ("2403.2", "2403.4", "2402.8", "2403.1"),
            # Bar 8: closes BELOW 2403 → Pattern A emit.
            ("2403.1", "2403.2", "2402", "2402.5"),
        ]
        bars = make_session_bars(prices)

        events_total: list[SweepEvent] = []
        for b in bars:
            events_total.extend(tracker.on_bar(b))

        a_sweeps = [e for e in events_total if e.pattern == "A_multi_bar"]
        assert len(a_sweeps) == 1
        assert a_sweeps[0].side == "high"
        assert a_sweeps[0].swept_swing.price == Decimal("2403")

    def test_pattern_b_sweep_carries_sweep_bar(self):
        """The grader's fib extension needs the manipulation bar's range —
        a Pattern B event must carry the bar that pierced and closed back."""
        cfg = LiquidityConfig(swing_lookback=2, min_penetration=Decimal("0.20"))
        tracker = LiquidityTracker(cfg)
        prices = [
            ("2400", "2400.5", "2399.8", "2400.2"),
            ("2400.2", "2401", "2400", "2400.8"),
            ("2400.8", "2403", "2400.5", "2401.5"),  # swing high candidate
            ("2401.5", "2402", "2401", "2401.2"),
            ("2401.2", "2401.5", "2400", "2400.5"),  # confirms swing high
            ("2400.5", "2403.5", "2400", "2401"),    # sweep bar
        ]
        events: list[SweepEvent] = []
        for b in make_session_bars(prices):
            events.extend(tracker.on_bar(b))

        assert len(events) == 1
        assert events[0].sweep_bar.high == Decimal("2403.5")
        assert events[0].sweep_bar.low == Decimal("2400")

    def test_pattern_a_sweep_bar_is_the_extreme_setting_bar(self):
        """For a multi-bar sweep, the manipulation bar is the one that set
        the final extreme — not the tag bar, not the close-back bar."""
        cfg = LiquidityConfig(
            swing_lookback=2,
            min_penetration=Decimal("0.20"),
            multi_bar_window=4,
        )
        tracker = LiquidityTracker(cfg)
        prices = [
            ("2400", "2400.5", "2399.8", "2400.2"),
            ("2400.2", "2401", "2400", "2400.8"),
            ("2400.8", "2403", "2400.5", "2401.5"),  # swing high candidate
            ("2401.5", "2402", "2401", "2401.2"),
            ("2401.2", "2401.5", "2400", "2400.5"),  # confirms swing high
            # Tag bar: pierces 2403, closes above.
            ("2400.5", "2403.5", "2400.5", "2403.2"),
            # Pushes the extreme higher — this is the manipulation bar.
            ("2403.2", "2404.1", "2403.0", "2403.3"),
            # Close-back bar → Pattern A emit.
            ("2403.3", "2403.4", "2402", "2402.5"),
        ]
        events: list[SweepEvent] = []
        for b in make_session_bars(prices):
            events.extend(tracker.on_bar(b))

        a_sweeps = [e for e in events if e.pattern == "A_multi_bar"]
        assert len(a_sweeps) == 1
        assert a_sweeps[0].sweep_extreme == Decimal("2404.1")
        assert a_sweeps[0].sweep_bar.high == Decimal("2404.1")
        assert a_sweeps[0].sweep_bar.low == Decimal("2403.0")

    def test_pattern_a_expires_without_emitting(self):
        """
        Tag a swing, then never close back within the window. No event.
        """
        cfg = LiquidityConfig(
            swing_lookback=2,
            min_penetration=Decimal("0.20"),
            multi_bar_window=2,  # tight window
        )
        tracker = LiquidityTracker(cfg)
        prices = [
            ("2400", "2400.5", "2399.8", "2400.2"),
            ("2400.2", "2401", "2400", "2400.8"),
            ("2400.8", "2403", "2400.5", "2401.5"),
            ("2401.5", "2402", "2401", "2401.2"),
            ("2401.2", "2401.5", "2400", "2400.5"),
            # Tag, no close-back.
            ("2400.5", "2403.5", "2400.5", "2403.2"),
            # Stays above for the duration of the window.
            ("2403.2", "2404", "2403.1", "2403.5"),
            ("2403.5", "2404.5", "2403.4", "2404"),
            # Now closes back, but window has expired.
            ("2404", "2404", "2402", "2402.5"),
        ]
        bars = make_session_bars(prices)

        events_total: list[SweepEvent] = []
        for b in bars:
            events_total.extend(tracker.on_bar(b))

        a_sweeps = [e for e in events_total if e.pattern == "A_multi_bar"]
        assert len(a_sweeps) == 0

    def test_low_sweep_mirrors_high_sweep_logic(self):
        """Swing low + Pattern B sweep on the low side."""
        cfg = LiquidityConfig(swing_lookback=2, min_penetration=Decimal("0.20"))
        tracker = LiquidityTracker(cfg)
        prices = [
            ("2402", "2402.5", "2401.5", "2402"),
            ("2402", "2402.2", "2401.2", "2401.5"),
            ("2401.5", "2401.8", "2399", "2400.5"),  # swing low candidate
            ("2400.5", "2401.5", "2400.2", "2401"),
            ("2401", "2402", "2400.5", "2401.5"),    # confirms swing low
            # Pattern B: pierces 2399 down by 0.5, closes back above.
            ("2401.5", "2401.8", "2398.5", "2401"),
        ]
        bars = make_session_bars(prices)

        events_total: list[SweepEvent] = []
        for b in bars:
            events_total.extend(tracker.on_bar(b))

        b_sweeps = [e for e in events_total if e.pattern == "B_one_bar"]
        assert len(b_sweeps) == 1
        assert b_sweeps[0].side == "low"
        assert b_sweeps[0].swept_swing.price == Decimal("2399")


# =====================================================================
# Displacement detector tests
# =====================================================================

class TestDisplacement:

    def test_no_event_before_atr_warmup(self):
        """First atr_period bars cannot produce displacement events."""
        det = DisplacementDetector(DisplacementConfig(atr_period=14))
        events = []
        for i in range(13):
            b = bar(
                datetime(2026, 5, 11, 13, 30, tzinfo=timezone.utc) + timedelta(minutes=i),
                "2400", "2400.5", "2399.5", "2400",
            )
            ev = det.on_bar(b)
            if ev:
                events.append(ev)
        assert events == []
        assert det.atr is None

    def test_strong_bullish_displacement_with_fvg(self):
        """
        Quiet bars to warm ATR, then a prior bearish FVG is formed,
        followed by a big bullish displacement bar that closes above
        the FVG's high → iFVG inversion fires with fvg = prior bearish FVG.
        """
        cfg = DisplacementConfig(
            atr_period=5,
            body_atr_multiple=Decimal("1.5"),
            min_body_to_range_ratio=Decimal("0.6"),
            min_absolute_body=Decimal("1.0"),
        )
        det = DisplacementDetector(cfg)

        ts0 = datetime(2026, 5, 11, 13, 30, tzinfo=timezone.utc)
        # 5 quiet bars: range ~$0.50 each, ATR will end around 0.50.
        for i in range(5):
            det.on_bar(bar(
                ts0 + timedelta(minutes=i),
                "2400", "2400.5", "2399.8", "2400.1",
            ))

        # Form a bearish FVG [2399.6, 2399.9] before the displacement:
        #   b1 low=2399.9, b3 high=2399.6 < b1.low → bearish FVG [2399.6, 2399.9]
        det.on_bar(bar(ts0 + timedelta(minutes=5), "2400", "2400.3", "2399.9", "2400.1"))  # b1
        det.on_bar(bar(ts0 + timedelta(minutes=6), "2400.1", "2400.2", "2399.5", "2399.7"))
        det.on_bar(bar(ts0 + timedelta(minutes=7), "2399.7", "2399.6", "2398.8", "2399.0"))  # b3: high=2399.6 < 2399.9 → FVG

        # b1 of the displacement window.
        det.on_bar(bar(ts0 + timedelta(minutes=8), "2400", "2400.3", "2399.9", "2400.1"))

        # Displacement bar (b2) — big bullish: open 2400.2, close 2402.5.
        # body=2.3, range=2.6, ratio=0.88 (>0.6). close=2402.5 > fvg.high=2399.9 → iFVG inversion.
        det.on_bar(bar(ts0 + timedelta(minutes=9), "2400.2", "2402.6", "2400", "2402.5"))

        # b3: gaps up, no further FVG needed (inversion already evaluated on b2).
        ev = det.on_bar(bar(ts0 + timedelta(minutes=10), "2402.5", "2403", "2401", "2402.8"))

        assert ev is not None
        assert ev.side == "bullish"
        assert ev.fvg is not None
        # iFVG is the prior bearish FVG that b2 closed through.
        assert ev.fvg.side == "bearish"
        assert ev.fvg.low == Decimal("2399.6")
        assert ev.fvg.high == Decimal("2399.9")

    def test_displacement_without_fvg_when_bars_overlap(self):
        """
        Big body but bar3.low <= bar1.high → no FVG. Event still fires
        but fvg=None (composer will reject for entry).
        """
        cfg = DisplacementConfig(
            atr_period=5,
            body_atr_multiple=Decimal("1.5"),
            min_absolute_body=Decimal("1.0"),
        )
        det = DisplacementDetector(cfg)
        ts0 = datetime(2026, 5, 11, 13, 30, tzinfo=timezone.utc)
        for i in range(5):
            det.on_bar(bar(
                ts0 + timedelta(minutes=i),
                "2400", "2400.5", "2399.8", "2400.1",
            ))
        det.on_bar(bar(ts0 + timedelta(minutes=5), "2400", "2400.5", "2399.9", "2400.2"))
        det.on_bar(bar(ts0 + timedelta(minutes=6), "2400.2", "2402.5", "2400", "2402.4"))
        # Bar 3 low = 2400.4 (below bar1.high 2400.5) → no FVG.
        ev = det.on_bar(bar(ts0 + timedelta(minutes=7), "2402.4", "2402.8", "2400.4", "2402.6"))
        assert ev is not None
        assert ev.side == "bullish"
        assert ev.fvg is None

    def test_small_body_does_not_trigger(self):
        """Bar with body < threshold is not displacement, even if range is big."""
        cfg = DisplacementConfig(
            atr_period=5,
            body_atr_multiple=Decimal("1.5"),
            min_body_to_range_ratio=Decimal("0.6"),
            min_absolute_body=Decimal("1.0"),
        )
        det = DisplacementDetector(cfg)
        ts0 = datetime(2026, 5, 11, 13, 30, tzinfo=timezone.utc)
        for i in range(5):
            det.on_bar(bar(
                ts0 + timedelta(minutes=i),
                "2400", "2400.5", "2399.8", "2400.1",
            ))
        det.on_bar(bar(ts0 + timedelta(minutes=5), "2400", "2400.5", "2399.9", "2400.2"))
        # Big-range bar but mostly wick: body 0.2, range 3.0 → ratio 0.067.
        det.on_bar(bar(ts0 + timedelta(minutes=6), "2400.2", "2403", "2400", "2400.4"))
        ev = det.on_bar(bar(ts0 + timedelta(minutes=7), "2400.4", "2400.6", "2400.2", "2400.5"))
        assert ev is None


# =====================================================================
# Composer tests — sweep + displacement → signal
# =====================================================================

def in_ny_am(minute_offset: int) -> datetime:
    """Build a UTC ts that falls inside NY AM (8:30–11:00 ET)."""
    base_et = datetime(2026, 5, 11, 9, 0, tzinfo=ET)
    return (base_et + timedelta(minutes=minute_offset)).astimezone(timezone.utc)


class TestComposer:

    def test_high_sweep_then_bearish_displacement_emits_short(self):
        """
        End-to-end: feed a hand-crafted 1m /MGC sequence that has
          - a prior bullish FVG (for iFVG inversion)
          - a confirmed swing high
          - a Pattern B sweep of that high
          - a bearish displacement bar whose close is below the prior bullish FVG
        Expect a SHORT signal via iFVG inversion.
        """
        liquidity = LiquidityTracker(LiquidityConfig(
            swing_lookback=2, min_penetration=Decimal("0.20"),
        ))
        displacement = DisplacementDetector(DisplacementConfig(
            atr_period=5,
            body_atr_multiple=Decimal("1.0"),
            min_body_to_range_ratio=Decimal("0.5"),
            min_absolute_body=Decimal("0.5"),
        ))
        composer = SweepDisplacementComposer(ComposerConfig(
            instrument="MGC",
            displacement_window_bars=10,
        ))

        # Pre-warm bars (outside killzone is fine for warming detectors —
        # the composer ignores sweeps outside killzone but ATR keeps
        # accumulating regardless).
        warmup = [
            ("2400", "2400.4", "2399.6", "2400.1"),
            ("2400.1", "2400.5", "2399.8", "2400.2"),
            ("2400.2", "2400.6", "2399.9", "2400.3"),
            ("2400.3", "2400.7", "2400", "2400.4"),
            ("2400.4", "2400.8", "2400.1", "2400.5"),
        ]
        # 3 bars forming a bullish FVG [2399.0, 2399.5].
        # The bearish displacement bar (further below) closes at 2398.5 < 2399.0.
        # All intervening bars have low > 2399.0 so the FVG is not mitigated.
        fvg_seed = [
            ("2399.5", "2399.0", "2398.5", "2398.8"),   # b1: high=2399.0
            ("2398.8", "2399.2", "2398.7", "2399.0"),   # middle
            ("2399.0", "2400.2", "2399.5", "2400.0"),   # b3: low=2399.5 > b1.high=2399.0 → FVG [2399.0, 2399.5]
        ]
        # In-killzone bars: build swing high, sweep it, then displace down.
        action = [
            ("2400.5", "2401", "2400.3", "2400.8"),   # idx 8
            ("2400.8", "2403", "2400.5", "2402.5"),   # idx 9 SWING HIGH candidate
            ("2402.5", "2402.8", "2401.5", "2401.8"), # idx 10
            ("2401.8", "2402.5", "2401", "2401.5"),   # idx 11 — confirms swing high
            # Pattern B sweep on idx 12: pierces 2403, closes at 2401.5.
            ("2401.5", "2403.5", "2401", "2401.5"),   # idx 12 SWEEP
            # Bar that becomes b1 of displacement window:
            ("2401.5", "2401.7", "2400.8", "2401"),   # idx 13
            # Bearish displacement bar (b2): big down body.
            # close=2398.5 < fvg.low=2400.2 (most recent bullish FVG) → iFVG inversion.
            ("2401", "2401.2", "2398.4", "2398.5"),   # idx 14 DISPLACE
            # b3: gaps down.
            ("2398.5", "2398.3", "2397", "2397.5"),   # idx 15
        ]

        signals: list[Signal] = []

        for i, (o, h, l, c) in enumerate(warmup + fvg_seed + action):
            ts = in_ny_am(i)
            b = bar(ts, o, h, l, c)
            sweeps = liquidity.on_bar(b)
            disp = displacement.on_bar(b)
            for s in sweeps:
                composer.on_sweep(b, s)
            if disp is not None:
                sig = composer.on_displacement(b, disp)
                if sig:
                    signals.append(sig)
            composer.on_bar_close(b)

        assert len(signals) == 1, (
            f"Expected exactly 1 signal, got {len(signals)}"
        )
        sig = signals[0]
        assert sig.side == "short"
        assert sig.killzone == "NY AM"
        # Stop should be just above the sweep extreme (2403.5 + buffer).
        assert sig.stop > Decimal("2403.5")
        # Entry below stop. Target below entry.
        assert sig.entry < sig.stop
        assert sig.target < sig.entry
        # Manipulation context for the grader's fib extension:
        # sweep bar idx 12 = high 2403.5, low 2401 → range 2.5.
        assert sig.sweep_bar_range == Decimal("2.5")

    def test_no_signal_outside_killzone(self):
        """
        Same setup as above but timestamps outside any killzone →
        composer ignores the sweep and no signal forms.
        """
        liquidity = LiquidityTracker(LiquidityConfig(
            swing_lookback=2, min_penetration=Decimal("0.20"),
        ))
        displacement = DisplacementDetector(DisplacementConfig(
            atr_period=5,
            body_atr_multiple=Decimal("1.0"),
            min_body_to_range_ratio=Decimal("0.5"),
            min_absolute_body=Decimal("0.5"),
        ))
        composer = SweepDisplacementComposer(ComposerConfig(instrument="MGC"))

        # Use 22:00 ET — Asia session, outside our zones.
        base_asia = datetime(2026, 5, 11, 22, 0, tzinfo=ET)

        all_bars = [
            ("2400", "2400.4", "2399.6", "2400.1"),
            ("2400.1", "2400.5", "2399.8", "2400.2"),
            ("2400.2", "2400.6", "2399.9", "2400.3"),
            ("2400.3", "2400.7", "2400", "2400.4"),
            ("2400.4", "2400.8", "2400.1", "2400.5"),
            ("2400.5", "2401", "2400.3", "2400.8"),
            ("2400.8", "2403", "2400.5", "2402.5"),
            ("2402.5", "2402.8", "2401.5", "2401.8"),
            ("2401.8", "2402.5", "2401", "2401.5"),
            ("2401.5", "2403.5", "2401", "2401.5"),
            ("2401.5", "2401.7", "2400.8", "2401"),
            ("2401", "2401.2", "2398.4", "2398.5"),
            ("2398.5", "2398.3", "2397", "2397.5"),
        ]

        signals: list[Signal] = []
        for i, (o, h, l, c) in enumerate(all_bars):
            ts = (base_asia + timedelta(minutes=i)).astimezone(timezone.utc)
            b = bar(ts, o, h, l, c)
            sweeps = liquidity.on_bar(b)
            disp = displacement.on_bar(b)
            for s in sweeps:
                composer.on_sweep(b, s)
            if disp is not None:
                sig = composer.on_displacement(b, disp)
                if sig:
                    signals.append(sig)
            composer.on_bar_close(b)

        assert signals == []

    def test_displacement_in_wrong_direction_does_not_signal(self):
        """
        High sweep wants BEARISH displacement. If a BULLISH displacement
        comes instead (continuation, not reversal), no signal.
        """
        composer = SweepDisplacementComposer(ComposerConfig(instrument="MGC"))

        # Manually build a sweep + bullish displacement.
        from app.strategy.liquidity import Swing
        ts1 = in_ny_am(1)
        b_sweep = bar(ts1, "2401", "2403.5", "2401", "2401.5")
        sweep = SweepEvent(
            side="high",
            swept_swing=Swing(
                kind="high", price=Decimal("2403"),
                bar_ts=ts1, confirmed_ts=ts1,
            ),
            pattern="B_one_bar",
            sweep_extreme=Decimal("2403.5"),
            completed_at=ts1,
            sweep_bar=b_sweep,
        )
        composer.on_sweep(b_sweep, sweep)
        composer.on_bar_close(b_sweep)

        # Bullish displacement (wrong direction for a high sweep).
        from app.strategy.displacement import DisplacementEvent, FairValueGap
        ts2 = in_ny_am(2)
        b_disp = bar(ts2, "2401.5", "2404", "2401.4", "2403.8")
        wrong_event = DisplacementEvent(
            side="bullish",
            displacement_bar=b_disp,
            body_size=Decimal("2.3"),
            atr_at_event=Decimal("0.5"),
            body_to_atr=Decimal("4.6"),
            fvg=FairValueGap(
                side="bullish",
                low=Decimal("2402"),
                high=Decimal("2403"),
                created_at=ts2,
            ),
        )
        signal = composer.on_displacement(b_disp, wrong_event)
        assert signal is None

    def test_sweep_expires_after_window(self):
        """
        Sweep happens, then no displacement for `displacement_window_bars`.
        After expiry, the sweep is forgotten — a later displacement
        does NOT pair with it.
        """
        composer = SweepDisplacementComposer(ComposerConfig(
            instrument="MGC",
            displacement_window_bars=3,
        ))

        from app.strategy.liquidity import Swing
        ts0 = in_ny_am(0)
        b_sweep = bar(ts0, "2401", "2403.5", "2401", "2401.5")
        sweep = SweepEvent(
            side="high",
            swept_swing=Swing(
                kind="high", price=Decimal("2403"),
                bar_ts=ts0, confirmed_ts=ts0,
            ),
            pattern="B_one_bar",
            sweep_extreme=Decimal("2403.5"),
            completed_at=ts0,
            sweep_bar=b_sweep,
        )
        composer.on_sweep(b_sweep, sweep)
        composer.on_bar_close(b_sweep)

        # 3 quiet bars. After the third on_bar_close, sweep should expire.
        for i in range(1, 4):
            b = bar(in_ny_am(i), "2401", "2401.2", "2400.8", "2401")
            composer.on_bar_close(b)

        assert composer.awaiting == []

        # Now a perfect bearish displacement — but the sweep is gone.
        from app.strategy.displacement import DisplacementEvent, FairValueGap
        ts_late = in_ny_am(5)
        b_late = bar(ts_late, "2401", "2401.2", "2398.4", "2398.5")
        late_event = DisplacementEvent(
            side="bearish",
            displacement_bar=b_late,
            body_size=Decimal("2.5"),
            atr_at_event=Decimal("0.5"),
            body_to_atr=Decimal("5.0"),
            fvg=FairValueGap(
                side="bearish",
                low=Decimal("2399"), high=Decimal("2400.8"),
                created_at=ts_late,
            ),
        )
        signal = composer.on_displacement(b_late, late_event)
        assert signal is None
