"""ORB detector tests — opening range, closed-bar breakout confirmation,
one-trade-per-day, daily reset, both anchors."""
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.events import Bar
from app.strategy.orb import ORBConfig, ORBDetector

ET = ZoneInfo("America/New_York")


def bar(h, m, o, hi, lo, c, day=4):
    return Bar(instrument="MNQ", timeframe="5min",
               ts=datetime(2026, 3, day, h, m, tzinfo=ET),
               open=Decimal(o), high=Decimal(hi), low=Decimal(lo),
               close=Decimal(c), volume=100)


def _cfg(**kw):
    return ORBConfig(instrument="MNQ", **kw)


def feed_range(det, day=4):
    """Three 5min bars from 9:30 ET → OR = 21000–21020."""
    assert det.on_bar(bar(9, 30, "21005", "21015", "21000", "21010", day)) is None
    assert det.on_bar(bar(9, 35, "21010", "21020", "21005", "21018", day)) is None
    assert det.on_bar(bar(9, 40, "21018", "21019", "21008", "21012", day)) is None


class TestORB:
    def test_long_breakout_on_close_beyond_range(self):
        det = ORBDetector(_cfg(range_minutes=15, r_multiple=Decimal("2.0")))
        feed_range(det)
        # wick above OR high but close inside -> no signal (close-confirmed)
        assert det.on_bar(bar(9, 45, "21012", "21025", "21010", "21015")) is None
        sig = det.on_bar(bar(9, 50, "21015", "21030", "21014", "21028"))
        assert sig is not None
        assert sig.side == "long"
        assert sig.entry == Decimal("21028")          # breakout bar close
        assert sig.stop == Decimal("21000")           # opposite OR edge
        # target = entry + 2R, R = 28
        assert sig.target == Decimal("21084")
        assert sig.killzone == "ORB"

    def test_fib_target_ext_overrides_fixed_r(self):
        # B101: target = entry ± ext × OR_width, stop (opposite edge) unchanged.
        det = ORBDetector(_cfg(range_minutes=15, r_multiple=Decimal("2.0"),
                               fib_target_ext=Decimal("1.618")))
        feed_range(det)  # OR = [21000, 21020], width 20
        sig = det.on_bar(bar(9, 50, "21015", "21030", "21014", "21028"))
        assert sig is not None
        assert sig.stop == Decimal("21000")            # unchanged
        # 21028 + 1.618 * 20 = 21060.36  (NOT entry + 2R = 21084)
        assert sig.target == Decimal("21060.36")

    def test_short_breakout(self):
        det = ORBDetector(_cfg())
        feed_range(det)
        sig = det.on_bar(bar(9, 45, "21010", "21012", "20985", "20990"))
        assert sig is not None
        assert sig.side == "short"
        assert sig.stop == Decimal("21020")

    def test_one_trade_per_day_and_daily_reset(self):
        det = ORBDetector(_cfg())
        feed_range(det, day=4)
        assert det.on_bar(bar(9, 45, "21015", "21030", "21014", "21028", 4)) is not None
        # second breakout same day suppressed
        assert det.on_bar(bar(10, 0, "21028", "21040", "21025", "21039", 4)) is None
        # next day: fresh range, fires again
        feed_range(det, day=5)
        assert det.on_bar(bar(9, 45, "21015", "21030", "21014", "21028", 5)) is not None

    def test_no_signal_inside_range_or_before_open(self):
        det = ORBDetector(_cfg())
        assert det.on_bar(bar(9, 25, "21000", "21100", "20900", "21050")) is None
        feed_range(det)
        assert det.on_bar(bar(9, 45, "21012", "21019", "21001", "21010")) is None

    def test_830_anchor(self):
        det = ORBDetector(_cfg(open_et="08:30", range_minutes=15))
        assert det.on_bar(bar(8, 30, "21005", "21015", "21000", "21010")) is None
        assert det.on_bar(bar(8, 35, "21010", "21020", "21005", "21018")) is None
        assert det.on_bar(bar(8, 40, "21018", "21019", "21008", "21012")) is None
        sig = det.on_bar(bar(8, 45, "21015", "21030", "21014", "21028"))
        assert sig is not None and sig.side == "long"


class TestEngineSelection:
    def test_build_runner_returns_orb_runner(self):
        from app.backtest.runner import BacktestConfig, _build_runner
        from app.bot_config import StrategyParams
        from app.strategy.orb import ORBRunner

        s = StrategyParams(engine="orb", orb_range_minutes=30,
                           orb_r_multiple=Decimal("2.5"), orb_open_et="08:30")
        cfg = BacktestConfig(instrument="MNQ", bars=iter([]),
                             timeframe="5min", strategy_params=s)
        runner = _build_runner(cfg)
        assert isinstance(runner, ORBRunner)
        assert runner.detector.config.range_minutes == 30
        assert runner.detector.config.open_et == "08:30"
        assert runner.detector.config.r_multiple == Decimal("2.5")
