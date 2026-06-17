"""Feed-dead watchdog: session gate, state machine, clock, config.

See docs/superpowers/specs/2026-06-16-feed-dead-watchdog-design.md.
Deterministic — injected `now`, no real sleeping.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.risk.flatten import bars_expected


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class TestBarsExpected:
    # 2026-01-15 is a Thursday, CST (UTC-6): 10:00 CT == 16:00 UTC (active)
    def test_active_midsession(self):
        assert bars_expected(_utc(2026, 1, 15, 16, 0))      # 10:00 CT Thu

    def test_daily_maintenance_break(self):
        # 16:30 CT == 22:30 UTC — inside 16:00-17:00 CT break
        assert not bars_expected(_utc(2026, 1, 15, 22, 30))

    def test_reopen_after_break(self):
        # 17:00 CT == 23:00 UTC — session reopens
        assert bars_expected(_utc(2026, 1, 15, 23, 0))

    def test_saturday_closed(self):
        # 2026-01-17 is Saturday
        assert not bars_expected(_utc(2026, 1, 17, 18, 0))

    def test_friday_evening_closed(self):
        # Fri 2026-01-16 16:30 CT == 22:30 UTC (after Fri 16:00 close)
        assert not bars_expected(_utc(2026, 1, 16, 22, 30))

    def test_sunday_reopen(self):
        # Sun 2026-01-18 17:30 CT == 23:30 UTC (after Sun 17:00 open)
        assert bars_expected(_utc(2026, 1, 18, 23, 30))

    def test_early_close_day_afternoon_closed(self):
        # 2026-07-03 early close (noon CT). 13:00 CT CDT == 18:00 UTC — closed
        assert not bars_expected(_utc(2026, 7, 3, 18, 0))

    def test_early_close_day_morning_open(self):
        # 2026-07-03 10:00 CT CDT == 15:00 UTC — still open before noon
        assert bars_expected(_utc(2026, 7, 3, 15, 0))


class TestWatchdogStep:
    def _engine(self, tf="5min"):
        from types import SimpleNamespace
        from app.broker.paper import PaperBroker
        from app.execution.engine import ExecutionEngine
        from app.risk.config import fifty_k_combine
        from app.risk.state import RiskState
        runner = SimpleNamespace(instrument="MNQ", timeframe=tf, signal_instrument="")
        return ExecutionEngine(
            broker=PaperBroker(), risk_state=RiskState(config=fifty_k_combine()),
            runners=[runner], replay_mode=True, feed_watchdog_enabled=True)

    def test_threshold_5min(self):
        assert self._engine("5min")._watchdog_threshold_s() == 900   # max(3*300, 600)

    def test_threshold_1min_floor(self):
        assert self._engine("1min")._watchdog_threshold_s() == 600   # max(3*60, 600)

    def test_live_to_dead_when_expected(self):
        eng = self._engine()
        eng._last_bar_at = _utc(2026, 1, 15, 16, 0)          # 10:00 CT
        out = eng._watchdog_step(_utc(2026, 1, 15, 16, 20))  # 20 min later, expected
        assert out["status"] == "dead" and out["transition"] == "dead"
        assert eng._feed_status == "DEAD"

    def test_dead_does_not_realert(self):
        eng = self._engine()
        eng._last_bar_at = _utc(2026, 1, 15, 16, 0)
        eng._watchdog_step(_utc(2026, 1, 15, 16, 20))        # -> dead
        out = eng._watchdog_step(_utc(2026, 1, 15, 16, 25))  # still dead
        assert out["transition"] is None and eng._feed_status == "DEAD"

    def test_recovery_on_bar(self):
        eng = self._engine()
        eng._last_bar_at = _utc(2026, 1, 15, 16, 0)
        eng._watchdog_step(_utc(2026, 1, 15, 16, 20))        # dead
        eng._last_bar_at = _utc(2026, 1, 15, 16, 26)         # a bar arrived
        out = eng._watchdog_step(_utc(2026, 1, 15, 16, 26))
        assert out["transition"] == "recovered" and eng._feed_status == "LIVE"

    def test_quiet_suppressed_in_break(self):
        eng = self._engine()
        eng._last_bar_at = _utc(2026, 1, 15, 22, 0)          # 16:00 CT
        out = eng._watchdog_step(_utc(2026, 1, 15, 22, 40))  # 16:40 CT, gap but break
        assert out["status"] == "quiet" and out["transition"] is None
        assert eng._feed_status == "QUIET_EXPECTED"

    def test_not_armed_never_alerts(self):
        eng = self._engine()
        eng._last_bar_at = None
        out = eng._watchdog_step(_utc(2026, 1, 15, 16, 20))
        assert out["transition"] is None and eng._feed_status == "LIVE"


def _mk_engine(*, replay, enabled, cb=None):
    from app.broker.paper import PaperBroker
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState
    broker = PaperBroker()
    eng = ExecutionEngine(broker=broker, risk_state=RiskState(config=fifty_k_combine()),
                          runners=[], replay_mode=replay, feed_watchdog_enabled=enabled,
                          on_feed_status=cb)
    return broker, eng


def test_watchdog_disabled_no_task():
    import asyncio
    broker, eng = _mk_engine(replay=False, enabled=False)
    async def go():
        await broker.connect(); await eng.start()
        assert eng._watchdog_task is None
        await eng.stop()
    asyncio.run(go())


def test_watchdog_enabled_starts_and_stops_task():
    import asyncio
    broker, eng = _mk_engine(replay=False, enabled=True)
    async def go():
        await broker.connect(); await eng.start()
        assert eng._watchdog_task is not None
        await eng.stop()
        assert eng._watchdog_task is None
    asyncio.run(go())


def test_watchdog_clock_invokes_callback():
    import asyncio
    seen = []
    async def cb(payload): seen.append(payload)
    broker, eng = _mk_engine(replay=True, enabled=True, cb=cb)
    async def go():
        await broker.connect(); await eng.start()
        await eng._watchdog_tick()      # one tick directly, no sleep
        await eng.stop()
    asyncio.run(go())
    assert seen and seen[0]["kind"] == "feed_watchdog"
