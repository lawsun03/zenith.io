"""RegimeSwitchRunner: daily range-regime gate routes signal emission to ORB
(small-range days) or iFVG (large-range days). Both sub-runners see every bar;
exit requests pass through regardless of the active side."""
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.broker.events import Bar
from app.strategy.regime_switch import RegimeSwitchRunner

ET = ZoneInfo("America/New_York")


def bar(day, h, m, lo, hi, c=None):
    lo, hi = Decimal(lo), Decimal(hi)
    c = Decimal(c) if c else (lo + hi) / 2
    return Bar(instrument="MNQ", timeframe="5min",
               ts=datetime(2026, 1, 1, h, m, tzinfo=ET) + timedelta(days=day),
               open=c, high=hi, low=lo, close=c, volume=100)


@dataclass
class _Stub:
    instrument: str = "MNQ"
    timeframe: str = "5min"
    strategy_cfg: object = None
    vp: object = None
    composer: object = None
    grader: object = None
    signal_instrument: str = ""
    last_reject: object = None
    exit_request: object = None
    queued: list = field(default_factory=list)
    bars_seen: int = 0

    def on_bar(self, b):
        self.bars_seen += 1
        return self.queued.pop(0) if self.queued else None


def _warm(rs, n_days=12, width_pat=None):
    """Feed n_days of two bars/day; width_pat(day) -> (lo, hi) strings."""
    for d in range(n_days):
        lo, hi = width_pat(d)
        rs.on_bar(bar(d, 10, 0, lo, hi))
        rs.on_bar(bar(d, 14, 0, lo, hi))
    return rs


class TestRegimeSwitch:
    def test_small_range_day_routes_to_orb(self):
        orb, ifvg = _Stub(), _Stub()
        rs = RegimeSwitchRunner(quiet=orb, active=ifvg)
        # alternate wide (100pt) and narrow (10pt) days to build history
        _warm(rs, 12, lambda d: ("21000", "21100") if d % 2 else ("21000", "21010"))
        # day 12 follows a NARROW day (day 11 is odd -> wide)... day 11: d%2=1 wide.
        # Feed one more narrow day so the PREVIOUS day is narrow:
        rs.on_bar(bar(12, 10, 0, "21000", "21010"))
        rs.on_bar(bar(12, 14, 0, "21000", "21010"))
        # day 13: previous day (12) was narrow -> quiet regime -> ORB active
        orb.queued = ["orb-sig"]
        ifvg.queued = ["ifvg-sig"]
        sig = rs.on_bar(bar(13, 10, 0, "21000", "21050"))
        assert sig == "orb-sig"

    def test_large_range_day_routes_to_ifvg(self):
        orb, ifvg = _Stub(), _Stub()
        rs = RegimeSwitchRunner(quiet=orb, active=ifvg)
        _warm(rs, 12, lambda d: ("21000", "21100") if d % 2 else ("21000", "21010"))
        # feed one more WIDE day so the previous day is wide
        rs.on_bar(bar(12, 10, 0, "21000", "21100"))
        rs.on_bar(bar(12, 14, 0, "21000", "21100"))
        orb.queued = ["orb-sig"]
        ifvg.queued = ["ifvg-sig"]
        sig = rs.on_bar(bar(13, 10, 0, "21000", "21050"))
        assert sig == "ifvg-sig"

    def test_both_runners_see_every_bar(self):
        orb, ifvg = _Stub(), _Stub()
        rs = RegimeSwitchRunner(quiet=orb, active=ifvg)
        for i in range(5):
            rs.on_bar(bar(0, 10, i, "21000", "21010"))
        assert orb.bars_seen == 5 and ifvg.bars_seen == 5

    def test_warmup_defaults_to_active_engine(self):
        orb, ifvg = _Stub(), _Stub()
        rs = RegimeSwitchRunner(quiet=orb, active=ifvg)
        orb.queued = ["orb-sig"]
        ifvg.queued = ["ifvg-sig"]
        # first day ever: no history -> active (iFVG) side
        assert rs.on_bar(bar(0, 10, 0, "21000", "21010")) == "ifvg-sig"

    def test_exit_request_passes_through_from_either_side(self):
        orb, ifvg = _Stub(), _Stub()
        rs = RegimeSwitchRunner(quiet=orb, active=ifvg)
        orb.exit_request = "failed_breakout"
        rs.on_bar(bar(0, 10, 0, "21000", "21010"))
        assert rs.exit_request == "failed_breakout"
        assert orb.exit_request is None


class TestEngineSelection:
    def test_build_runner_returns_regime_switch(self):
        from app.backtest.runner import BacktestConfig, _build_runner
        from app.bot_config import StrategyParams
        from app.execution.engine import StrategyRunner
        from app.strategy.orb import ORBRunner

        s = StrategyParams(engine="regime_switch", orb_r_multiple=Decimal("2.5"))
        cfg = BacktestConfig(instrument="MNQ", bars=iter([]),
                             timeframe="5min", strategy_params=s)
        runner = _build_runner(cfg)
        assert isinstance(runner.quiet, ORBRunner)
        assert isinstance(runner.active, StrategyRunner)
        assert runner.quiet.detector.config.r_multiple == Decimal("2.5")
        assert runner.instrument == "MNQ"
