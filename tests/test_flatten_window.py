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


# ---------------------------------------------------------------------------
# Engine integration tests
# ---------------------------------------------------------------------------
import asyncio
from decimal import Decimal

from app.broker.paper import PaperBroker
from app.broker.events import Bar


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
