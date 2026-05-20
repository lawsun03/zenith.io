import pytest
from app.analytics.tools import (
    get_backtest_runs,
    get_current_config,
    get_killzone_breakdown,
    get_log_summary,
    get_performance_summary,
    get_recent_trades,
)


def test_performance_summary_structure():
    result = get_performance_summary()
    if "error" in result:
        return  # no trades on disk yet — acceptable
    for key in ("total_trades", "win_rate", "net_pnl", "profit_factor", "expectancy"):
        assert key in result
    assert 0.0 <= result["win_rate"] <= 1.0


def test_killzone_breakdown_structure():
    result = get_killzone_breakdown()
    assert isinstance(result, dict)
    for kz, data in result.items():
        assert isinstance(kz, str)
        for key in ("trades", "win_rate", "net_pnl"):
            assert key in data


def test_recent_trades_limit():
    trades = get_recent_trades(limit=5)
    assert len(trades) <= 5
    for t in trades:
        assert t["type"] == "EXIT"


def test_recent_trades_descending_order():
    trades = get_recent_trades(limit=20)
    timestamps = [t["ts"] for t in trades]
    assert timestamps == sorted(timestamps, reverse=True)


def test_backtest_runs_sorted_by_profit_factor():
    runs = get_backtest_runs(sort_by="profit_factor", limit=5)
    pfs = [r["profit_factor"] for r in runs if r.get("profit_factor") is not None]
    assert pfs == sorted(pfs, reverse=True)


def test_backtest_runs_sorted_by_net_pnl():
    runs = get_backtest_runs(sort_by="net_pnl", limit=5)
    vals = [r["net_pnl"] for r in runs]
    assert vals == sorted(vals, reverse=True)


def test_current_config_returns_dict():
    assert isinstance(get_current_config(), dict)


def test_log_summary_structure():
    result = get_log_summary(days=2)
    for key in ("total_log_lines", "vp_rejections", "signal_denials", "errors", "warnings"):
        assert key in result
    assert isinstance(result["vp_examples"], list)
