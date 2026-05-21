"""Tests for walk-forward optimizer."""
from datetime import date, timedelta
from decimal import Decimal
import pytest

from app.optimizer.walkforward import (
    CombineOutcome,
    WindowResult,
    _classify_outcome,
    _rolling_windows,
    _score_config,
)
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState


def _risk(net_pnl: Decimal, mll_breached: bool = False) -> RiskState:
    state = RiskState(config=fifty_k_combine())
    if mll_breached:
        from app.risk.state import LockoutReason
        state.locked_out = LockoutReason(code="MLL_BREACH", message="test")
    state.realized_balance = state.config.starting_balance + net_pnl
    return state


def test_classify_pass():
    """Net P&L >= $3000 with no MLL breach is PASS."""
    risk = _risk(Decimal("3100"))
    assert _classify_outcome(risk, Decimal("3100")) == CombineOutcome.PASS


def test_classify_fail():
    """MLL breach is FAIL regardless of P&L."""
    risk = _risk(Decimal("100"), mll_breached=True)
    assert _classify_outcome(risk, Decimal("100")) == CombineOutcome.FAIL


def test_classify_incomplete():
    """Profitable but below target, no breach, is INCOMPLETE."""
    risk = _risk(Decimal("1500"))
    assert _classify_outcome(risk, Decimal("1500")) == CombineOutcome.INCOMPLETE


def test_rolling_windows_count():
    """With 250 trading days, 30d train / 10d test / 5d step -> ~42 windows."""
    base = date(2025, 6, 1)
    all_dates = [base + timedelta(days=i) for i in range(365) if (base + timedelta(days=i)).weekday() < 5]
    windows = list(_rolling_windows(all_dates, train_days=30, test_days=10, step_days=5))
    assert 35 <= len(windows) <= 50


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
