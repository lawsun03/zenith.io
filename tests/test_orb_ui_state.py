"""ORB Rule-13 UI state (B9) — defining-behavior tests for ORBDetector.state().

state() must reflect OR range and fired count so the live dashboard can show
what the detector is currently seeing without side-effects on trading.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.sim.events import Bar
from app.strategy.orb import ORBConfig, ORBDetector

ET = ZoneInfo("America/New_York")


def _bar(h: int, m: int, *, hi: str, lo: str, c: str, day: int = 2) -> Bar:
    return Bar(
        instrument="MNQ", timeframe="5min",
        ts=datetime(2025, 1, day, h, m, tzinfo=ET),
        open=Decimal(c), high=Decimal(hi), low=Decimal(lo),
        close=Decimal(c), volume=100,
    )


def _det() -> ORBDetector:
    return ORBDetector(ORBConfig(
        instrument="MNQ",
        range_minutes=15,
        r_multiple=Decimal("2.0"),
    ))


class TestORBStateBeforeRange:
    def test_state_empty_before_any_bars(self):
        det = _det()
        s = det.state()
        assert s["or_established"] is False
        assert s["or_high"] is None
        assert s["or_low"] is None
        assert s["fired"] == 0


class TestORBStateRangeBuilding:
    def test_state_still_empty_during_range_window(self):
        det = _det()
        # Bars at 09:30 and 09:35 are inside the 09:30+15min window → range not yet committed
        det.on_bar(_bar(9, 30, hi="100.0", lo="98.0", c="99.0"))
        det.on_bar(_bar(9, 35, hi="101.0", lo="97.0", c="99.5"))
        s = det.state()
        # Range is still accumulating; no bar has confirmed it yet from outside
        assert s["or_established"] is False
        assert s["fired"] == 0

    def test_state_established_after_range_window_closes(self):
        det = _det()
        # Feed three bars inside the 09:30+15min window (09:30, 09:35, 09:40 land at et < end=09:45)
        det.on_bar(_bar(9, 30, hi="100.0", lo="98.0", c="99.0"))
        det.on_bar(_bar(9, 35, hi="101.0", lo="97.0", c="100.0"))
        det.on_bar(_bar(9, 40, hi="101.5", lo="97.5", c="100.5"))
        # First post-range bar (09:45) that doesn't break out
        det.on_bar(_bar(9, 45, hi="101.5", lo="98.0", c="100.0"))
        s = det.state()
        assert s["or_established"] is True
        assert s["or_high"] == "101.5"
        assert s["or_low"] == "97.0"
        assert s["fired"] == 0


class TestORBStateFired:
    def test_state_fired_increments_on_breakout(self):
        det = _det()
        det.on_bar(_bar(9, 30, hi="100.0", lo="98.0", c="99.0"))
        det.on_bar(_bar(9, 35, hi="101.0", lo="97.0", c="100.0"))
        det.on_bar(_bar(9, 40, hi="101.5", lo="97.5", c="100.5"))
        # Breakout bar: close above or_high (101.5)
        sig = det.on_bar(_bar(9, 45, hi="103.0", lo="100.0", c="102.0"))
        assert sig is not None
        s = det.state()
        assert s["fired"] == 1
        assert s["or_established"] is True

    def test_state_fired_stays_1_after_max_exceeded(self):
        det = _det()
        det.on_bar(_bar(9, 30, hi="100.0", lo="98.0", c="99.0"))
        det.on_bar(_bar(9, 35, hi="101.0", lo="97.0", c="100.0"))
        det.on_bar(_bar(9, 40, hi="101.5", lo="97.5", c="100.5"))
        det.on_bar(_bar(9, 45, hi="103.0", lo="100.0", c="102.0"))  # breakout → fired=1
        det.on_bar(_bar(9, 50, hi="104.0", lo="101.0", c="103.0"))  # max_trades=1, no second signal
        s = det.state()
        assert s["fired"] == 1


class TestORBStateDayReset:
    def test_state_resets_on_new_day(self):
        det = _det()
        # Day 2: feed and fire a signal
        det.on_bar(_bar(9, 30, hi="100.0", lo="98.0", c="99.0", day=2))
        det.on_bar(_bar(9, 35, hi="101.0", lo="97.0", c="100.0", day=2))
        det.on_bar(_bar(9, 40, hi="101.5", lo="97.5", c="100.5", day=2))
        det.on_bar(_bar(9, 45, hi="103.0", lo="100.0", c="102.0", day=2))
        assert det.state()["fired"] == 1
        # Day 3: first bar resets state
        det.on_bar(_bar(9, 0, hi="102.0", lo="100.0", c="101.0", day=3))
        s = det.state()
        assert s["fired"] == 0
        assert s["or_established"] is False
