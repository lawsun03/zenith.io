"""B17 — ORB long-only funded benchmark.

Defining-behavior tests:
1. orb_long_only=True: bearish breakout (bar.close < or_low) returns None.
2. orb_long_only=True: bullish breakout (bar.close > or_high) returns Signal(side="long").
3. orb_long_only=False (default): bearish breakout returns Signal(side="short") — unchanged behavior.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.broker.events import Bar
from app.strategy.orb import ORBConfig, ORBDetector

ET = ZoneInfo("America/New_York")


def bar(h, m, o, hi, lo, c, day=4):
    return Bar(instrument="MNQ", timeframe="5min",
               ts=datetime(2026, 3, day, h, m, tzinfo=ET),
               open=Decimal(o), high=Decimal(hi), low=Decimal(lo),
               close=Decimal(c), volume=100)


def feed_range(det, day=4):
    """Feed 3 bars to build OR = 21000-21020."""
    det.on_bar(bar(9, 30, "21005", "21015", "21000", "21010", day))
    det.on_bar(bar(9, 35, "21010", "21020", "21005", "21018", day))
    det.on_bar(bar(9, 40, "21018", "21019", "21008", "21012", day))


class TestORBLongOnly:
    def test_short_breakout_suppressed_when_long_only(self):
        """orb_long_only=True: bar breaking below OR low returns None."""
        cfg = ORBConfig(instrument="MNQ", long_only=True)
        det = ORBDetector(cfg)
        feed_range(det)
        sig = det.on_bar(bar(9, 45, "21010", "21015", "20990", "20985"))
        assert sig is None

    def test_long_breakout_passes_when_long_only(self):
        """orb_long_only=True: bar breaking above OR high still fires a long signal."""
        cfg = ORBConfig(instrument="MNQ", long_only=True)
        det = ORBDetector(cfg)
        feed_range(det)
        sig = det.on_bar(bar(9, 45, "21015", "21035", "21010", "21030"))
        assert sig is not None
        assert sig.side == "long"

    def test_short_breakout_fires_when_long_only_false(self):
        """orb_long_only=False (default): bearish breakout returns a short signal."""
        cfg = ORBConfig(instrument="MNQ", long_only=False)
        det = ORBDetector(cfg)
        feed_range(det)
        sig = det.on_bar(bar(9, 45, "21010", "21015", "20990", "20985"))
        assert sig is not None
        assert sig.side == "short"

    def test_wiring_in_build_runner(self):
        """runner.py passes orb_long_only into ORBConfig correctly."""
        from app.backtest.runner import BacktestConfig, _build_runner
        from app.bot_config import StrategyParams
        from app.strategy.orb import ORBRunner

        s = StrategyParams(engine="orb", orb_r_multiple=Decimal("2.5"),
                           orb_long_only=True)
        cfg = BacktestConfig(instrument="MNQ", bars=iter([]),
                             timeframe="5min", strategy_params=s)
        runner = _build_runner(cfg)
        assert isinstance(runner, ORBRunner)
        assert runner.detector.config.long_only is True
