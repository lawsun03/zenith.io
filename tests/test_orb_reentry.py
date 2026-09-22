"""B14 — ORB reentry after stop.

Defining-behavior tests:
1. Stop hit → detector re-armed → next breakout fires signal 2.
2. No re-arm when flag is off (default config preserves one-trade-per-day).
3. Target hit (no on_stop_loss call) → no reentry.
4. Two stops in one day → only the first re-arms; second leaves detector at max.
"""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.sim.events import Bar
from app.strategy.orb import ORBComposer, ORBConfig, ORBDetector

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


class TestORBReentryAfterStop:
    def test_reentry_fires_after_stop(self):
        """Stop hit on the first signal → re-armed → second breakout fires."""
        cfg = ORBConfig(instrument="MNQ", reentry_after_stop=True)
        det = ORBDetector(cfg)
        composer = ORBComposer(detector=det, reentry_after_stop=True)
        feed_range(det)
        sig1 = det.on_bar(bar(9, 45, "21015", "21030", "21014", "21028"))
        assert sig1 is not None and sig1.side == "long"
        # Simulate: stop fill lands (engine calls composer.on_stop_loss)
        composer.on_stop_loss()
        # Next bar closes back above OR high → second signal fires
        sig2 = det.on_bar(bar(10, 0, "21010", "21040", "21005", "21035"))
        assert sig2 is not None
        assert sig2.side == "long"

    def test_no_reentry_when_flag_off(self):
        """Default reentry_after_stop=False: stop call leaves detector blocked."""
        cfg = ORBConfig(instrument="MNQ", reentry_after_stop=False)
        det = ORBDetector(cfg)
        composer = ORBComposer(detector=det, reentry_after_stop=False)
        feed_range(det)
        sig1 = det.on_bar(bar(9, 45, "21015", "21030", "21014", "21028"))
        assert sig1 is not None
        composer.on_stop_loss()
        # Detector still blocked — no second signal
        sig2 = det.on_bar(bar(10, 0, "21010", "21040", "21005", "21035"))
        assert sig2 is None

    def test_no_reentry_on_target_hit(self):
        """Target fill does not call on_stop_loss → no re-arm → no second signal."""
        cfg = ORBConfig(instrument="MNQ", reentry_after_stop=True)
        det = ORBDetector(cfg)
        composer = ORBComposer(detector=det, reentry_after_stop=True)  # noqa: F841
        feed_range(det)
        sig1 = det.on_bar(bar(9, 45, "21015", "21030", "21014", "21028"))
        assert sig1 is not None
        # Target hit: engine does NOT call on_stop_loss — no re-arm
        sig2 = det.on_bar(bar(10, 0, "21010", "21040", "21005", "21035"))
        assert sig2 is None

    def test_two_stops_only_first_rearmed(self):
        """First stop re-arms (signals again); second stop does not → day capped."""
        cfg = ORBConfig(instrument="MNQ", reentry_after_stop=True)
        det = ORBDetector(cfg)
        composer = ORBComposer(detector=det, reentry_after_stop=True)
        feed_range(det)
        # First breakout
        sig1 = det.on_bar(bar(9, 45, "21015", "21030", "21014", "21028"))
        assert sig1 is not None
        # First stop → re-arm
        composer.on_stop_loss()
        # Reentry breakout (second signal)
        sig2 = det.on_bar(bar(10, 0, "21010", "21040", "21005", "21035"))
        assert sig2 is not None
        # Second stop → no second re-arm (already used)
        composer.on_stop_loss()
        # Detector at max: no third signal
        sig3 = det.on_bar(bar(10, 10, "21015", "21045", "21012", "21040"))
        assert sig3 is None


class TestORBReentryWiring:
    """Verify runner.py wires reentry_after_stop into the runner correctly."""

    def test_build_runner_orb_composer_when_reentry_enabled(self):
        from app.backtest.runner import BacktestConfig, _build_runner
        from app.bot_config import StrategyParams
        from app.strategy.orb import ORBComposer, ORBRunner

        s = StrategyParams(engine="orb", orb_r_multiple=Decimal("2.5"),
                           orb_reentry_after_stop=True)
        cfg = BacktestConfig(instrument="MNQ", bars=iter([]),
                             timeframe="5min", strategy_params=s)
        runner = _build_runner(cfg)
        assert isinstance(runner, ORBRunner)
        assert isinstance(runner.composer, ORBComposer)
        assert runner.composer.reentry_after_stop is True
