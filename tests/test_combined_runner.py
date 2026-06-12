"""CombinedRunner: both sub-runners see every bar; one signal out per bar;
iFVG primary wins same-bar collisions; engine-facing attrs delegate."""
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

from app.broker.events import Bar
from app.strategy.combined import CombinedRunner


def _bar():
    return Bar(instrument="MNQ", timeframe="5min",
               ts=datetime(2026, 3, 4, 14, 30, tzinfo=timezone.utc),
               open=Decimal("1"), high=Decimal("2"), low=Decimal("1"),
               close=Decimal("2"), volume=1)


@dataclass
class _StubRunner:
    instrument: str = "MNQ"
    timeframe: str = "5min"
    strategy_cfg: object = None
    vp: object = None
    composer: object = None
    grader: object = None
    signal_instrument: str = ""
    last_reject: object = None
    queued: list = field(default_factory=list)
    bars_seen: int = 0

    def on_bar(self, bar):
        self.bars_seen += 1
        return self.queued.pop(0) if self.queued else None


class TestCombinedRunner:
    def test_both_subrunners_see_every_bar(self):
        a, b = _StubRunner(), _StubRunner()
        c = CombinedRunner(primary=a, secondary=b)
        for _ in range(3):
            c.on_bar(_bar())
        assert a.bars_seen == 3 and b.bars_seen == 3

    def test_secondary_signal_passes_through(self):
        a, b = _StubRunner(), _StubRunner(queued=["orb-sig"])
        assert CombinedRunner(primary=a, secondary=b).on_bar(_bar()) == "orb-sig"

    def test_primary_wins_collision(self):
        a, b = _StubRunner(queued=["ifvg-sig"]), _StubRunner(queued=["orb-sig"])
        assert CombinedRunner(primary=a, secondary=b).on_bar(_bar()) == "ifvg-sig"

    def test_delegates_engine_facing_attrs(self):
        a = _StubRunner(instrument="MNQ", last_reject="rej")
        c = CombinedRunner(primary=a, secondary=_StubRunner())
        assert c.instrument == "MNQ"
        assert c.last_reject == "rej"
        assert c.vp is a.vp and c.grader is a.grader and c.composer is a.composer


class TestEngineSelection:
    def test_build_runner_combined(self):
        from app.backtest.runner import BacktestConfig, _build_runner
        from app.bot_config import StrategyParams
        from app.execution.engine import StrategyRunner
        from app.strategy.orb import ORBRunner

        s = StrategyParams(engine="combined", orb_r_multiple=Decimal("2.5"))
        cfg = BacktestConfig(instrument="MNQ", bars=iter([]),
                             timeframe="5min", strategy_params=s)
        runner = _build_runner(cfg)
        assert isinstance(runner.primary, StrategyRunner)
        assert isinstance(runner.secondary, ORBRunner)
        assert runner.secondary.detector.config.r_multiple == Decimal("2.5")
        assert runner.instrument == "MNQ"
