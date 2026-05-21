"""Tests for walk-forward optimizer."""
from datetime import date, timedelta
from decimal import Decimal
import pytest

from app.optimizer.walkforward import (
    CombineOutcome,
    WindowResult,
    _classify_outcome_from_stats,
    _rolling_windows,
    _score_config,
)
from app.backtest.runner import BacktestStats
from datetime import datetime, timezone


def _make_stats(net_pnl: Decimal, passed_combine: bool, mll_breached: bool) -> BacktestStats:
    """Helper to build a minimal BacktestStats for classification tests."""
    return BacktestStats(
        trades=1,
        wins=1 if net_pnl > 0 else 0,
        losses=0 if net_pnl > 0 else 1,
        win_rate=100.0 if net_pnl > 0 else 0.0,
        net_pnl=net_pnl,
        gross_win=net_pnl if net_pnl > 0 else Decimal("0"),
        gross_loss=Decimal("0") if net_pnl > 0 else abs(net_pnl),
        avg_win=net_pnl if net_pnl > 0 else Decimal("0"),
        avg_loss=Decimal("0") if net_pnl > 0 else abs(net_pnl),
        profit_factor=None,
        max_drawdown=Decimal("0"),
        expectancy=net_pnl,
        is_profitable=net_pnl > 0,
        passed_combine=passed_combine,
        mll_breached=mll_breached,
        equity_curve=[],
        by_killzone={},
    )


def test_classify_pass():
    """passed_combine=True → PASS."""
    stats = _make_stats(Decimal("3100"), passed_combine=True, mll_breached=False)
    assert _classify_outcome_from_stats(stats) == CombineOutcome.PASS


def test_classify_fail():
    """mll_breached=True → FAIL regardless of P&L."""
    stats = _make_stats(Decimal("100"), passed_combine=False, mll_breached=True)
    assert _classify_outcome_from_stats(stats) == CombineOutcome.FAIL


def test_classify_incomplete():
    """Profitable but below target, no breach → INCOMPLETE."""
    stats = _make_stats(Decimal("1500"), passed_combine=False, mll_breached=False)
    assert _classify_outcome_from_stats(stats) == CombineOutcome.INCOMPLETE


def test_rolling_windows_count():
    """With 250 trading days, 30d train / 10d test / 5d step -> ~42 windows."""
    base = date(2025, 6, 1)
    all_dates = [base + timedelta(days=i) for i in range(365) if (base + timedelta(days=i)).weekday() < 5]
    windows = list(_rolling_windows(all_dates, train_days=30, test_days=10, step_days=5))
    assert 40 <= len(windows) <= 46


def test_score_config():
    """Score = pass_rate - 2 * fail_rate."""
    windows = [
        WindowResult(outcome=CombineOutcome.PASS, net_pnl=Decimal("3100"), max_drawdown=Decimal("200"), trades=10, win_rate=60.0, profit_factor=2.0),
        WindowResult(outcome=CombineOutcome.PASS, net_pnl=Decimal("3500"), max_drawdown=Decimal("150"), trades=12, win_rate=65.0, profit_factor=2.5),
        WindowResult(outcome=CombineOutcome.FAIL, net_pnl=Decimal("-500"), max_drawdown=Decimal("2100"), trades=5, win_rate=20.0, profit_factor=0.5),
        WindowResult(outcome=CombineOutcome.INCOMPLETE, net_pnl=Decimal("1200"), max_drawdown=Decimal("300"), trades=8, win_rate=50.0, profit_factor=1.5),
    ]
    score = _score_config(windows)
    # pass_rate = 2/4 = 0.5, fail_rate = 1/4 = 0.25
    # score = 0.5 - 2 * 0.25 = 0.0
    assert abs(score - 0.0) < 1e-6
