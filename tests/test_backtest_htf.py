"""Regression guard for the 0-trades bug (2026-06-03).

The faithful backtest must replicate the live HTF refresh — aggregate the
replay stream into 30min/4h bars and feed the grader's HTF swings — or the
grader's target-clarity gate ("no structural target found") rejects every
candidate and run_backtest yields 0 trades. See memory
project-backtest-no-htf-feed.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from app.backtest.runner import BacktestConfig, run_backtest
from app.bot_config import StrategyParams
from app.replay import load_bars_csv

_BARS = Path("bars/bars_MGC_1min_20251111_20260511.csv")


@pytest.mark.skipif(not _BARS.exists(), reason="MGC 1min bars fixture not present")
def test_faithful_backtest_produces_trades_with_htf_feed():
    # Self-contained params: the regression requires the HTF target gate ON
    # (the bug starved the grader when run_backtest fed it no HTF data).
    # Reading the live bot_config here made the test depend on deployment
    # state — the deployed MNQ config has the gate off.
    strategy = StrategyParams(
        htf_target_enabled=True,
        target_clarity_mode="reject",
    )
    bars = list(load_bars_csv(str(_BARS), instrument="MGC", timeframe="1min"))
    bc = BacktestConfig(
        instrument="MGC", bars=iter(bars), strategy_params=strategy,
        enabled_killzones=["london", "ny_am", "ny_pm"], timeframe="1min",
    )
    result = asyncio.run(run_backtest(bc))
    assert result.stats.trades > 0, (
        "faithful backtest generated 0 trades — HTF feed missing, grader "
        "target-clarity gate is starving the pipeline"
    )
