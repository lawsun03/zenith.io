"""
Defining-behavior tests for B63(a) early_win_boost rule.

Rule: if the first trade of the day won (realized_pnl > 0), scale the SECOND
trade's PnL by 1.5x. All other trades are unchanged. Reset each day.

Verifies:
1. First trade wins -> second trade PnL scaled by 1.5x.
2. First trade loses -> second trade unchanged.
3. Only one trade that day -> no boost applied.
4. First trade wins -> all trades beyond the second are unchanged.
5. Boost fires on the second trade regardless of its own outcome (win or loss).
"""
from __future__ import annotations


def _apply_boost(day_trades: list[dict], boost: float = 0.5) -> list[float]:
    """
    Given a list of dicts with 'pnl' for one trading day (sorted by entry_ts),
    return the list of PnLs with the early_win_boost applied.
    """
    from scripts.run_b63_pipeline import apply_early_win_boost_day
    return apply_early_win_boost_day(day_trades, boost=boost)


def test_first_wins_second_scaled():
    """First trade wins -> second trade PnL = original × 1.5."""
    trades = [{"pnl": 500.0}, {"pnl": -300.0}, {"pnl": 200.0}]
    result = _apply_boost(trades)
    assert result[0] == 500.0, "First trade unchanged"
    assert abs(result[1] - (-300.0 * 1.5)) < 0.01, "Second trade × 1.5"
    assert result[2] == 200.0, "Third trade unchanged"


def test_first_loses_second_unchanged():
    """First trade loses -> no boost applied to second."""
    trades = [{"pnl": -500.0}, {"pnl": 300.0}]
    result = _apply_boost(trades)
    assert result[0] == -500.0
    assert result[1] == 300.0


def test_single_trade_no_boost():
    """Only one trade -> return unchanged."""
    trades = [{"pnl": 400.0}]
    result = _apply_boost(trades)
    assert result == [400.0]


def test_second_win_also_scaled():
    """Boost applies regardless of second trade outcome (win or loss)."""
    winning_second = [{"pnl": 600.0}, {"pnl": 400.0}]
    result = _apply_boost(winning_second)
    assert abs(result[1] - 400.0 * 1.5) < 0.01

    losing_second = [{"pnl": 600.0}, {"pnl": -400.0}]
    result2 = _apply_boost(losing_second)
    assert abs(result2[1] - (-400.0 * 1.5)) < 0.01


def test_empty_trades():
    """Empty list -> empty result."""
    assert _apply_boost([]) == []
