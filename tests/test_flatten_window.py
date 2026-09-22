"""Flatten-rule config + window math.

Why: Topstep requires flat by 3:10 PM CT (4:10 PM ET); the bot held 66
positions through that window in the 2025-26 test data. Times are config,
not code (Topstep changed rules 8x in 6 months), and DST-correct via
America/Chicago — never fixed UTC offsets.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.bot_config import BotConfig


def test_flatten_config_defaults():
    cfg = BotConfig()
    assert cfg.flatten_enabled is True
    assert cfg.flatten_time_ct == "15:05"      # 4:05 PM ET, 5 min buffer
    assert cfg.entry_cutoff_time_ct == "14:30" # 3:30 PM ET


from app.risk.flatten import in_flatten_window, past_entry_cutoff


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class TestFlattenWindow:
    # 2026-01-15 is CST (UTC-6): 15:05 CT == 21:05 UTC
    def test_before_flatten_time_not_in_window(self):
        assert not in_flatten_window(_utc(2026, 1, 15, 21, 4), "15:05")

    def test_at_flatten_time_in_window(self):
        assert in_flatten_window(_utc(2026, 1, 15, 21, 5), "15:05")

    def test_after_session_close_not_in_window(self):
        # 17:00 CT = next trading day; flatten window ended
        assert not in_flatten_window(_utc(2026, 1, 15, 23, 0), "15:05")

    def test_dst_boundary_uses_cdt(self):
        # 2026-07-15 is CDT (UTC-5): 15:05 CT == 20:05 UTC
        assert in_flatten_window(_utc(2026, 7, 15, 20, 5), "15:05")
        assert not in_flatten_window(_utc(2026, 7, 15, 20, 4), "15:05")


class TestEntryCutoff:
    def test_before_cutoff_allowed(self):
        assert not past_entry_cutoff(_utc(2026, 1, 15, 20, 29), "14:30")

    def test_after_cutoff_blocked(self):
        assert past_entry_cutoff(_utc(2026, 1, 15, 20, 30), "14:30")

    def test_evening_session_after_5pm_ct_allowed(self):
        # 18:00 CT = new trading day, overnight trading is allowed
        assert not past_entry_cutoff(_utc(2026, 1, 16, 0, 0), "14:30")


class TestEarlyCloseAutoFlatten:
    """CME early-close days (12:00 PM CT close). The bot must auto-shift the
    flatten window AND entry cutoff earlier without manual config, because the
    configured 15:05 flatten fires *after* CME has already auto-closed at noon
    — which on a funded account looks like an uncontrolled exit through the
    close. Behavior: on a known early-close trading day, flatten from 11:50 CT
    and block entries from 11:30 CT, regardless of the (later) configured times.
    """

    # --- flatten window auto-shifts earlier on early-close days ---
    def test_early_close_in_window_after_1150_cdt(self):
        # 2026-07-03 (day before July 4) is CDT (UTC-5): 11:55 CT == 16:55 UTC
        assert in_flatten_window(_utc(2026, 7, 3, 16, 55), "15:05")

    def test_early_close_not_in_window_before_1150_cdt(self):
        # 11:45 CT == 16:45 UTC, before the 11:50 early flatten
        assert not in_flatten_window(_utc(2026, 7, 3, 16, 45), "15:05")

    def test_early_close_in_window_cst(self):
        # 2026-12-24 (Christmas Eve) is CST (UTC-6): 11:55 CT == 17:55 UTC
        assert in_flatten_window(_utc(2026, 12, 24, 17, 55), "15:05")

    def test_normal_day_window_unaffected_at_same_clock_time(self):
        # Regression guard: 2026-07-15 is NOT early-close. 11:55 CT == 16:55 UTC
        # must remain OUTSIDE the window — configured 15:05 still governs.
        assert not in_flatten_window(_utc(2026, 7, 15, 16, 55), "15:05")

    # --- entry cutoff auto-shifts earlier on early-close days ---
    def test_early_close_entry_blocked_after_1130_cdt(self):
        # 11:35 CT == 16:35 UTC, past the 11:30 early cutoff
        assert past_entry_cutoff(_utc(2026, 7, 3, 16, 35), "14:30")

    def test_early_close_entry_allowed_before_1130_cdt(self):
        # 11:25 CT == 16:25 UTC, before the early cutoff
        assert not past_entry_cutoff(_utc(2026, 7, 3, 16, 25), "14:30")

    def test_normal_day_entry_cutoff_unaffected(self):
        # Regression guard: 2026-07-15 11:35 CT == 16:35 UTC must stay allowed.
        assert not past_entry_cutoff(_utc(2026, 7, 15, 16, 35), "14:30")

    def test_is_early_close_day_detects_known_date(self):
        from app.risk.flatten import is_early_close_day
        assert is_early_close_day(_utc(2026, 7, 3, 16, 0))      # 11:00 CT
        assert not is_early_close_day(_utc(2026, 7, 15, 16, 0))


def test_engine_flattens_early_close_at_noon():
    """On an early-close day the engine must flatten at ~noon CT, not 15:05.

    Why: the configured 15:05 flatten is after the 12:00 CT CME close, so on
    early-close days a position would ride through the close uncontrolled. The
    auto-shifted window must catch it at 11:50 CT.
    """
    import asyncio
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState

    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    engine = ExecutionEngine(
        broker=broker, risk_state=RiskState(config=fifty_k_combine()),
        runners=[], replay_mode=True,
        flatten_enabled=True, flatten_time_ct="15:05", entry_cutoff_time_ct="14:30",
    )

    async def go():
        await broker.connect()
        await engine.start()
        # Open at 11:00 CT on 2026-07-03 (CDT): 16:00 UTC
        await broker.inject_bar(_bar(_utc(2026, 7, 3, 16, 0)))
        await broker.place_bracket("MGC", "long", 1,
                                   Decimal("100"), Decimal("95"), Decimal("110"))
        assert len(broker.open_brackets()) == 1
        # Bar at 11:55 CT (16:55 UTC) is inside the auto-shifted window
        await broker.inject_bar(_bar(_utc(2026, 7, 3, 16, 55)))
        return broker.open_brackets()

    assert asyncio.run(go()) == [], "engine must flatten on early-close day at noon"


# ---------------------------------------------------------------------------
# Engine integration tests
# ---------------------------------------------------------------------------
import asyncio
from decimal import Decimal

from app.sim.paper import PaperBroker
from app.sim.events import Bar


def _bar(ts, price=100.0):
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts,
        open=Decimal(str(price)), high=Decimal(str(price + 1)),
        low=Decimal(str(price - 1)), close=Decimal(str(price)), volume=10,
    )


def test_engine_flattens_open_position_in_window():
    """A position open at 15:05 CT must be flattened by the next bar.

    Why: 66 trades in the 2025-26 data were held through the 4:10 PM ET
    close — a Topstep rule violation that fails real accounts.
    """
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState

    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    engine = ExecutionEngine(
        broker=broker, risk_state=RiskState(config=fifty_k_combine()),
        runners=[], replay_mode=True,
        flatten_enabled=True, flatten_time_ct="15:05", entry_cutoff_time_ct="14:30",
    )

    async def go():
        await broker.connect()
        await engine.start()
        # Open a position at 20:00 UTC (14:00 CT, before cutoff)
        await broker.inject_bar(_bar(_utc(2026, 1, 15, 20, 0)))
        await broker.place_bracket("MGC", "long", 1,
                                   Decimal("100"), Decimal("95"), Decimal("110"))
        assert len(broker.open_brackets()) == 1
        # Bar lands inside the flatten window: 21:06 UTC == 15:06 CT
        await broker.inject_bar(_bar(_utc(2026, 1, 15, 21, 6)))
        return broker.open_brackets()

    remaining = asyncio.run(go())
    assert remaining == [], "engine must flatten open positions in the window"


def test_failed_flatten_retries_next_tick():
    """A rejected close must NOT mark the day flattened — the 30s wall-clock
    backup retries until the position is actually gone. Fail-safe, not
    fail-dangerous: holding through 3:10 PM CT fails the account."""
    import asyncio
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState

    class StubBroker(PaperBroker):
        def __init__(self):
            super().__init__(slippage_ticks_market=0, commission_per_side=Decimal("0"))
            self.fail_flatten = True

        async def flatten(self, instrument):
            if self.fail_flatten:
                return False
            return await super().flatten(instrument)

    broker = StubBroker()
    engine = ExecutionEngine(
        broker=broker, risk_state=RiskState(config=fifty_k_combine()),
        runners=[], replay_mode=True,
        flatten_enabled=True, flatten_time_ct="15:05", entry_cutoff_time_ct="14:30",
    )

    async def go():
        await broker.connect()
        await engine.start()
        await broker.inject_bar(_bar(_utc(2026, 1, 15, 20, 0)))
        await broker.place_bracket("MGC", "long", 1,
                                   Decimal("100"), Decimal("95"), Decimal("110"))
        # First window tick: flatten rejected -> day NOT marked done
        await engine._enforce_flatten(_utc(2026, 1, 15, 21, 6))
        assert engine._flattened_today is None
        assert len(broker.open_brackets()) == 1
        # Broker recovers; next tick must retry and succeed
        broker.fail_flatten = False
        await engine._enforce_flatten(_utc(2026, 1, 15, 21, 7))
        assert broker.open_brackets() == []
        assert engine._flattened_today is not None

    asyncio.run(go())


def test_clock_driven_flatten_no_bar():
    """Wall-clock fires in flatten window even when no bar arrives.

    Why: on CME early-close days (~5/yr) bars stop arriving at noon but the
    configured flatten_time_ct is 15:05. Bar-driven _enforce_flatten never
    fires. The wall-clock backup calls _enforce_flatten(datetime.now()) every
    30s and must close any open position at 15:05 CT.
    """
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState

    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    engine = ExecutionEngine(
        broker=broker, risk_state=RiskState(config=fifty_k_combine()),
        runners=[], replay_mode=True,
        flatten_enabled=True, flatten_time_ct="15:05",
    )

    async def go():
        await broker.connect()
        await engine.start()
        # Open position at 14:00 CT — no more bars arrive after this
        await broker.inject_bar(_bar(_utc(2026, 1, 15, 20, 0)))
        await broker.place_bracket("MGC", "long", 1,
                                   Decimal("100"), Decimal("95"), Decimal("110"))
        assert len(broker.open_brackets()) == 1
        # Wall-clock fires at 15:10 CT (21:10 UTC); no bar injected in the window
        await engine._enforce_flatten(_utc(2026, 1, 15, 21, 10))
        assert broker.open_brackets() == []

    asyncio.run(go())


def test_flatten_wallclock_disabled_no_task():
    """flatten_wallclock_enabled=False: background clock task not started.

    Why: the flag lets the operator disable the wall-clock backup (e.g. in
    paper-replay setups where wall-clock timestamps interfere with bar replay).
    When the flag is off the asyncio task is never created.
    """
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState

    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    engine = ExecutionEngine(
        broker=broker, risk_state=RiskState(config=fifty_k_combine()),
        runners=[], replay_mode=False,
        flatten_wallclock_enabled=False,
    )

    async def go():
        await broker.connect()
        await engine.start()
        assert engine._flatten_task is None, "clock task must not be started when flag is off"
        await engine.stop()

    asyncio.run(go())
