"""Trading-day rollover in the backtest replay loop.

Why: Topstep's DLL is a per-day rule — hitting it ends the day, not the
Combine. Before the rollover wiring, the first DLL lockout in a replay
silently halted every remaining day (risk-limit artifacts in the 06-10
parity doc), making month-scale Combine simulation impossible.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.backtest.runner import _trading_day_ct
from app.risk.config import fifty_k_combine
from app.risk.state import LockoutReason, RiskState


class TestTradingDayCT:
    def test_before_5pm_ct_is_same_day(self):
        # 2026-01-15 16:59 CST = 22:59 UTC
        ts = datetime(2026, 1, 15, 22, 59, tzinfo=timezone.utc)
        assert _trading_day_ct(ts).isoformat() == "2026-01-15"

    def test_at_5pm_ct_rolls_to_next_day(self):
        # 2026-01-15 17:00 CST = 23:00 UTC
        ts = datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc)
        assert _trading_day_ct(ts).isoformat() == "2026-01-16"

    def test_dst_boundary_uses_cdt(self):
        # 2026-07-15 17:00 CDT = 22:00 UTC (UTC-5 in summer)
        ts = datetime(2026, 7, 15, 22, 0, tzinfo=timezone.utc)
        assert _trading_day_ct(ts).isoformat() == "2026-07-16"


def test_dll_lockout_clears_on_roll_but_mll_does_not():
    """A DLL lockout must survive only until the 5pm CT roll; an MLL breach
    is a Combine failure and must persist."""
    rs = RiskState(config=fifty_k_combine())
    rs.locked_out = LockoutReason(code="DLL_HIT", message="test")
    rs.daily_pnl = Decimal("-1000")
    rs.roll_trading_day(datetime(2026, 1, 15, 23, 0, tzinfo=timezone.utc))
    assert rs.locked_out is None
    assert rs.daily_pnl == Decimal("0")

    rs.locked_out = LockoutReason(code="MLL_BREACH", message="test")
    rs.roll_trading_day(datetime(2026, 1, 16, 23, 0, tzinfo=timezone.utc))
    assert rs.locked_out is not None
