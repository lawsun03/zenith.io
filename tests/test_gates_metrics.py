"""Tests for research/gates/metrics.py — the trade-sequence statistics
shared by several gates. Hand-verified numbers throughout (CLAUDE.md rule 9:
tests must encode WHY, and these are small enough to check by hand)."""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from research.gates.metrics import (
    frequency_stats, mean_edge_dollars_per_trade, r_multiples,
    sharpe_from_trades, skew_and_kurtosis,
)
from research.ir.engine import Trade

BASE_TS = datetime(2024, 1, 2, 13, 30, tzinfo=timezone.utc)  # a Tuesday


def _trade(days_offset: float, pnl_points: str, risk_points: str = "10",
           side: str = "long", commission: str = "0") -> Trade:
    ts = BASE_TS + timedelta(days=days_offset)
    entry = Decimal("100")
    risk = Decimal(risk_points)
    stop = entry - risk if side == "long" else entry + risk
    return Trade(
        instrument="NQ", side=side, entry_ts=ts, entry_price=entry, stop_price=stop,
        target_price=entry + 2 * risk, exit_ts=ts + timedelta(hours=1),
        exit_price=entry + Decimal(pnl_points), exit_reason="target",
        pnl_points=Decimal(pnl_points), commission_points_equivalent=Decimal(commission),
    )


def test_r_multiples_divides_pnl_by_risk_distance():
    t = _trade(0, "5", risk_points="10")
    assert r_multiples([t]) == [0.5]


def test_r_multiples_rejects_zero_risk():
    t = _trade(0, "5", risk_points="10")
    # Force zero risk by making stop == entry.
    zero_risk = Trade(**{**t.__dict__, "stop_price": t.entry_price})
    with pytest.raises(ValueError, match="non-positive risk"):
        r_multiples([zero_risk])


def test_sharpe_from_trades_hand_computed():
    # Two-point symmetric distribution: R = +0.46 or -0.46 (mean 0, but we
    # want a non-zero mean) -> use +1 (n=1) and -0.5 (n=1): mean=0.25,
    # variance=((1-0.25)**2+(-0.5-0.25)**2)/(2-1) = (0.5625+0.5625)/1=1.125,
    # std=sqrt(1.125)=1.0607, sharpe=0.25/1.0607=0.23570.
    trades = [_trade(0, "10", risk_points="10"), _trade(2, "-5", risk_points="10")]
    sr = sharpe_from_trades(trades, trades_per_year=252, annualize=False)
    assert sr == pytest.approx(0.235702, abs=1e-5)


def test_sharpe_from_trades_annualizes_by_sqrt_frequency():
    trades = [_trade(0, "10", risk_points="10"), _trade(2, "-5", risk_points="10")]
    per_trade = sharpe_from_trades(trades, trades_per_year=100, annualize=False)
    annualized = sharpe_from_trades(trades, trades_per_year=100, annualize=True)
    assert annualized == pytest.approx(per_trade * math.sqrt(100))


def test_sharpe_from_trades_needs_at_least_two_trades():
    with pytest.raises(ValueError, match="at least 2 trades"):
        sharpe_from_trades([_trade(0, "1")], trades_per_year=252)


def test_sharpe_from_trades_rejects_zero_variance():
    trades = [_trade(0, "5", risk_points="10"), _trade(1, "5", risk_points="10")]
    with pytest.raises(ValueError, match="zero variance"):
        sharpe_from_trades(trades, trades_per_year=252)


def test_skew_and_kurtosis_symmetric_two_point_distribution():
    # +0.5R and -0.5R at 50/50: symmetric about 0 -> skew 0. Non-excess
    # kurtosis of a two-point equal-probability distribution is exactly 1
    # (m4/m2**2 = d**4 / d**4 = 1 for any deviation d).
    trades = [_trade(0, "5", risk_points="10"), _trade(1, "-5", risk_points="10"),
              _trade(2, "5", risk_points="10"), _trade(3, "-5", risk_points="10")]
    skew, kurtosis = skew_and_kurtosis(trades)
    assert skew == pytest.approx(0.0, abs=1e-9)
    assert kurtosis == pytest.approx(1.0, abs=1e-9)


def test_mean_edge_dollars_per_trade():
    trades = [_trade(0, "5"), _trade(1, "-1")]
    edge = mean_edge_dollars_per_trade(trades, point_value=Decimal("20"))
    assert edge == Decimal("40")  # mean(5,-1)=2 points * $20/point


def test_frequency_stats_counts_zero_trade_weeks():
    # One trade a week for 2 weeks, then a 3rd week with none, inside a
    # 3-week sample: weeks_meeting_floor should be 0 (no week ever hits 3).
    trades = [_trade(0, "1"), _trade(7, "1")]
    stats = frequency_stats(trades, date(2024, 1, 1), date(2024, 1, 21))
    assert stats.trades_total == 2
    assert stats.weeks_meeting_floor == 0.0
    assert sum(stats.weekly_histogram.values()) == 2


def test_frequency_stats_meets_floor_every_week():
    trades = []
    for week in range(4):
        for day in (0, 2, 4):
            trades.append(_trade(week * 7 + day, "1"))
    stats = frequency_stats(trades, date(2024, 1, 1), date(2024, 1, 28))
    assert stats.weeks_meeting_floor == 1.0
    assert stats.max_gap_days <= 3
