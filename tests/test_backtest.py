"""Backtest stats-math + reporting tests (current run_backtest API).

The pre-refactor API (app.backtest.pairing.Trade/TradePairer,
app.backtest.stats.compute_stats) was removed when the backtest became a
package; stats are now computed by runner._compute_stats over fill-dicts, and
end-to-end trade generation is covered by tests/test_backtest_htf.py. Backtest
output drives real-money decisions, so the stats math is worth guarding.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.backtest.report import format_summary
from app.backtest.runner import BacktestStats, _compute_stats
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState


def _fill(is_entry: bool, pnl: str = "0", minute: int = 0, kz: str = "NY AM") -> dict:
    return {
        "ts": datetime(2026, 5, 11, 14, minute, tzinfo=timezone.utc).isoformat(),
        "instrument": "MGC", "side": "long", "fill_price": "2400", "size": 1,
        "is_entry": is_entry, "realized_pnl_delta": pnl, "killzone": kz,
        "order_id": f"o{minute}",
    }


def _stats(fills: list[dict]) -> BacktestStats:
    return _compute_stats(fills, RiskState(config=fifty_k_combine()), Decimal("50000"))


def test_empty_fills_safe_zeros():
    s = _stats([])
    assert s.trades == 0
    assert s.net_pnl == Decimal("0")
    assert s.is_profitable is False


def test_mixed_win_loss_math():
    """3 winners (+40) and 2 losers (-20): net +80, profitable, 3W/2L."""
    fills, m = [], 0
    for pnl in ("40", "40", "40", "-20", "-20"):
        fills.append(_fill(True, "0", m)); m += 1     # entry (zero delta)
        fills.append(_fill(False, pnl, m)); m += 1     # exit (realized P&L)
    s = _stats(fills)
    assert s.trades == 5
    assert s.wins == 3
    assert s.losses == 2
    assert s.net_pnl == Decimal("80")
    assert s.is_profitable is True


def test_format_summary_runs_on_current_stats():
    s = _stats([_fill(True, "0", 0), _fill(False, "100", 1)])
    out = format_summary(s)
    assert isinstance(out, str) and out.strip()
