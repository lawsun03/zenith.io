"""B59 — Long-only sweep-reentry micro-engine.

Defining-behavior tests (from spec):
1. Engine idle / no prior ORB stop today: no signal.
2. ORB long stopped, session_low swept by >= k ATR, displacement + FVG inversion
   -> long signal at inversion bar close, stop beyond swept extreme.
3. Sweep too shallow (< k ATR) -> no signal.
4. Short setups never fire (long-only by design).
5. Only one reentry overlay per day (second arm after first signal is ignored).
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.events import Bar
from app.strategy.sweep_reentry import (
    SweepReentryComposer,
    SweepReentryConfig,
    SweepReentryDetector,
    SweepReentryRunner,
)
from app.strategy.orb import ORBConfig, ORBDetector

ET = ZoneInfo("America/New_York")


def bar(h, m, o, hi, lo, c, day=3, vol=500):
    return Bar(
        instrument="MNQ",
        timeframe="5min",
        ts=datetime(2026, 3, day, h, m, tzinfo=ET),
        open=Decimal(str(o)),
        high=Decimal(str(hi)),
        low=Decimal(str(lo)),
        close=Decimal(str(c)),
        volume=vol,
    )


def make_cfg(**kwargs):
    defaults = dict(
        instrument="MNQ",
        sweep_depth_atr=Decimal("0.25"),
        min_absolute_body=Decimal("2.0"),  # lower for test bars
        body_atr_multiple=Decimal("0.5"),  # lower for test bars
        r_multiple=Decimal("2.0"),
        stop_buffer=Decimal("0.50"),
        displacement_window_bars=10,
    )
    defaults.update(kwargs)
    return SweepReentryConfig(**defaults)


def make_det(**kwargs):
    return SweepReentryDetector(make_cfg(**kwargs))


def warm_up_det(det, day=1, n=20):
    """Feed neutral bars before 09:30 to warm up ATR (avoids None ATR at start)."""
    price = Decimal("21000")
    for i in range(n):
        # pre-session bars at 08:00-09:29
        h = 8 + i // 12
        m = (i % 12) * 5
        det.on_bar(Bar(
            instrument="MNQ", timeframe="5min",
            ts=datetime(2026, 3, day, h, m, tzinfo=ET),
            open=price, high=price + 5, low=price - 5, close=price,
            volume=200,
        ))


class TestSweepReentryIdle:
    """Test 1: No prior ORB stop → no signal."""

    def test_no_signal_without_arm(self):
        """Without arm_for_reentry(), no signal even with sweep + displacement."""
        det = make_det()
        warm_up_det(det, day=3)

        # Establish session low by feeding bars after 09:30
        det.on_bar(bar(9, 30, 21000, 21010, 20990, 21005))  # session_low = 20990
        det.on_bar(bar(9, 35, 21005, 21015, 21000, 21010))
        det.on_bar(bar(9, 40, 21010, 21020, 21005, 21018))

        # Sweep below session_low + bullish displacement (would fire if armed)
        det.on_bar(bar(10, 0, 21000, 21005, 20950, 20955))   # deep sweep, no arm
        sig = det.on_bar(bar(10, 5, 20955, 21020, 20950, 21018))  # displacement
        assert sig is None


class TestSweepReentryHappyPath:
    """Test 2: Full happy path — ORB long stops, sweep + displacement → signal."""

    def test_full_chain_fires_long_signal(self):
        det = make_det(
            sweep_depth_atr=Decimal("0.25"),
            displacement_window_bars=15,
        )
        warm_up_det(det, day=3, n=20)

        # Session bars: establish session_low ~ 20990
        det.on_bar(bar(9, 30, 21000, 21010, 20990, 21005))
        det.on_bar(bar(9, 35, 21005, 21020, 21000, 21015))
        det.on_bar(bar(9, 40, 21015, 21025, 21010, 21020))

        # ARM: simulate ORB long stopped out
        det.arm_for_reentry()

        # Sweep: bar dips well below session_low (20990 - 0.25*ATR; ATR ~10 → threshold ~20987.5)
        # Use a large sweep to be well below threshold
        sweep_bar = bar(10, 0, 21000, 21005, 20960, 20965)  # low=20960, session_low=20990, sweep=30pts
        result = det.on_bar(sweep_bar)
        assert result is None  # can't fire on sweep bar itself

        # Bar1 after sweep (small neutral bar)
        det.on_bar(bar(10, 5, 20965, 20975, 20960, 20968))  # bar1

        # Displacement bar (bar2): large bullish body from 20968 to 21015
        det.on_bar(bar(10, 10, 20968, 21020, 20965, 21015))  # bar2 displacement

        # Bar3: close above bar1.high, confirming FVG → signal fires
        sig = det.on_bar(bar(10, 15, 21015, 21025, 21010, 21020))  # bar3

        assert sig is not None, "Expected long signal after sweep + displacement + FVG"
        assert sig.side == "long"
        assert sig.entry == Decimal("21020")  # bar3 close
        assert sig.stop < Decimal("20965"), "Stop must be below sweep extreme"
        assert sig.target > sig.entry


class TestSweepTooShallow:
    """Test 3: Sweep too shallow (< k ATR) → no signal."""

    def test_shallow_sweep_no_signal(self):
        det = make_det(sweep_depth_atr=Decimal("0.25"))
        warm_up_det(det, day=3, n=20)

        # Session bars: session_low ~ 20990
        det.on_bar(bar(9, 30, 21000, 21010, 20990, 21005))
        det.on_bar(bar(9, 35, 21005, 21020, 21000, 21010))

        det.arm_for_reentry()

        # Shallow sweep: only 1pt below session_low (ATR ~10, threshold ~2.5pts)
        det.on_bar(bar(10, 0, 21000, 21005, 20988, 20990))  # low=20988 < 20990 but shallow
        det.on_bar(bar(10, 5, 20990, 21000, 20988, 20995))
        det.on_bar(bar(10, 10, 20995, 21010, 20990, 21008))
        sig = det.on_bar(bar(10, 15, 21008, 21015, 21005, 21012))

        assert sig is None, "Shallow sweep below threshold should not produce signal"


class TestLongOnlyEnforced:
    """Test 4: Only long signals are ever produced."""

    def test_no_short_signal_possible(self):
        """The detector never emits a short signal — it is structurally long-only."""
        det = make_det()
        warm_up_det(det, day=3, n=20)
        det.arm_for_reentry()

        # Feed many bars; even if a bearish displacement occurs, no signal
        det.on_bar(bar(9, 30, 21000, 21010, 20990, 21005))
        det.on_bar(bar(9, 35, 21005, 21020, 21000, 21010))

        # Bearish sweep + bearish displacement
        det.on_bar(bar(10, 0, 21010, 21060, 21005, 21055))  # sweep UP
        det.on_bar(bar(10, 5, 21055, 21058, 21000, 21005))  # bar1
        det.on_bar(bar(10, 10, 21005, 21008, 20960, 20965))  # bearish displacement bar2
        sig = det.on_bar(bar(10, 15, 20965, 20970, 20950, 20955))  # bar3

        assert sig is None or sig.side == "long"


class TestOnlyOneReentryPerDay:
    """Test 5: Only one reentry per day — second arm after signal is ignored."""

    def test_second_arm_ignored_after_signal_fires(self):
        det = make_det(displacement_window_bars=20)
        warm_up_det(det, day=3, n=20)

        # Establish session low
        det.on_bar(bar(9, 30, 21000, 21010, 20990, 21005))
        det.on_bar(bar(9, 35, 21005, 21020, 21000, 21010))
        det.on_bar(bar(9, 40, 21010, 21025, 21005, 21015))

        # First arm → signal fires
        det.arm_for_reentry()
        det.on_bar(bar(10, 0, 21000, 21005, 20960, 20965))  # sweep

        det.on_bar(bar(10, 5, 20965, 20975, 20960, 20968))   # bar1
        det.on_bar(bar(10, 10, 20968, 21020, 20965, 21015))  # bar2 displacement
        sig1 = det.on_bar(bar(10, 15, 21015, 21025, 21010, 21020))  # bar3 → signal
        assert sig1 is not None

        # Second arm (e.g. ORB stopped again) — should be ignored
        det.arm_for_reentry()

        # Another sweep + displacement — should NOT fire a second signal
        det.on_bar(bar(10, 30, 21020, 21025, 20950, 20955))   # sweep again
        det.on_bar(bar(10, 35, 20955, 20965, 20950, 20958))   # bar1
        det.on_bar(bar(10, 40, 20958, 21010, 20955, 21005))   # bar2
        sig2 = det.on_bar(bar(10, 45, 21005, 21015, 21000, 21010))

        assert sig2 is None, "Second signal on same day must be suppressed"
