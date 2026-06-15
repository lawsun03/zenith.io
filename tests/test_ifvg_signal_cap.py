"""
B23 defining-behavior tests: iFVG daily signal cap.

The cap resets at each ET calendar-day boundary. Signals are suppressed
once `daily_signal_cap` signals have been emitted on the current ET day.
"""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.broker.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing, SweepEvent

# 14:00 UTC = 10:00 ET (NY AM killzone — inside trading window)
_DAY1_BASE = datetime(2026, 1, 5, 14, 0, tzinfo=timezone.utc)   # Monday
_DAY2_BASE = datetime(2026, 1, 6, 14, 0, tzinfo=timezone.utc)   # Tuesday


def _bar(ts: datetime) -> Bar:
    return Bar(
        instrument="MNQ", timeframe="5min", ts=ts,
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("97"), close=Decimal("101"),
        volume=500,
    )


def _disp() -> DisplacementEvent:
    """Bullish displacement with a valid bearish FVG (sweep_extreme=97)."""
    fvg = FairValueGap(
        side="bearish",
        low=Decimal("98.50"),
        high=Decimal("99.70"),
        created_at=_DAY1_BASE,
    )
    return DisplacementEvent(
        side="bullish",
        displacement_bar=_bar(_DAY1_BASE),
        body_size=Decimal("1.0"),
        atr_at_event=Decimal("5.0"),
        body_to_atr=Decimal("0.2"),
        fvg=fvg,
    )


def _low_sweep(ts: datetime) -> SweepEvent:
    return SweepEvent(
        side="low",
        swept_swing=Swing(
            kind="low",
            price=Decimal("97.5"),
            bar_ts=ts - timedelta(minutes=5),
            confirmed_ts=ts - timedelta(minutes=2),
        ),
        pattern="B_one_bar",
        sweep_extreme=Decimal("97.0"),
        completed_at=ts,
        sweep_bar=_bar(ts),
    )


def _composer(cap: int) -> SweepDisplacementComposer:
    cfg = ComposerConfig(
        instrument="MNQ",
        trend_ema_period=0,
        daily_signal_cap=cap,
        stop_buffer=Decimal("0.30"),
    )
    return SweepDisplacementComposer(cfg)


def _emit_signal(composer: SweepDisplacementComposer, ts: datetime) -> object:
    """Arm a sweep then fire a displacement on the same bar timestamp."""
    bar = _bar(ts)
    composer.on_sweep(bar, _low_sweep(ts))
    return composer.on_displacement(bar, _disp())


class TestDailySignalCap:

    def test_cap1_first_fires_second_suppressed(self):
        """cap=1: first signal of the day fires; second identical setup returns None."""
        composer = _composer(cap=1)

        sig1 = _emit_signal(composer, _DAY1_BASE)
        assert sig1 is not None, "First signal should fire (cap=1, count=0)"

        sig2 = _emit_signal(composer, _DAY1_BASE + timedelta(hours=1))
        assert sig2 is None, "Second signal should be suppressed (cap=1 reached)"

    def test_cap2_first_and_second_fire_third_suppressed(self):
        """cap=2: first two signals fire; third returns None."""
        composer = _composer(cap=2)

        sig1 = _emit_signal(composer, _DAY1_BASE)
        assert sig1 is not None, "First signal should fire"

        sig2 = _emit_signal(composer, _DAY1_BASE + timedelta(hours=1))
        assert sig2 is not None, "Second signal should fire (cap=2, count=1)"

        sig3 = _emit_signal(composer, _DAY1_BASE + timedelta(hours=2))
        assert sig3 is None, "Third signal should be suppressed (cap=2 reached)"

    def test_cap0_unlimited_signals(self):
        """cap=0 (default off): all signals fire regardless of day count.
        Use 10-min spacing to stay inside NY AM killzone (08:30-11:00 ET;
        _DAY1_BASE=14:00UTC=10:00ET, so 4 × 10min stays within the window).
        """
        composer = _composer(cap=0)

        for i in range(4):
            sig = _emit_signal(composer, _DAY1_BASE + timedelta(minutes=10 * i))
            assert sig is not None, f"Signal {i+1} should fire (cap=0, unlimited)"

    def test_day_reset_counter_clears_at_new_et_day(self):
        """cap=1: first signal fires day 1; same setup fires again on day 2 (counter reset)."""
        composer = _composer(cap=1)

        sig_day1 = _emit_signal(composer, _DAY1_BASE)
        assert sig_day1 is not None, "Day-1 signal should fire"

        # Same day: capped
        sig_day1_extra = _emit_signal(composer, _DAY1_BASE + timedelta(hours=1))
        assert sig_day1_extra is None, "Same day: second signal suppressed"

        # New ET day: counter resets
        sig_day2 = _emit_signal(composer, _DAY2_BASE)
        assert sig_day2 is not None, "Day-2 first signal should fire (counter reset)"
