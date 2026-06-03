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
from app.bot_config import load_bot_config
from app.replay import load_bars_csv

_BARS = Path("bars/bars_MGC_1min_20251111_20260511.csv")


@pytest.mark.skipif(not _BARS.exists(), reason="MGC 1min bars fixture not present")
def test_faithful_backtest_produces_trades_with_htf_feed():
    cfg = load_bot_config(Path("bot_config.json"))
    assert cfg.strategy.htf_target_enabled, "fixture assumes htf_target_enabled"
    bars = list(load_bars_csv(str(_BARS), instrument="MGC", timeframe="1min"))
    bc = BacktestConfig(
        instrument="MGC", bars=iter(bars), strategy_params=cfg.strategy,
        enabled_killzones=cfg.enabled_killzones, timeframe="1min",
    )
    result = asyncio.run(run_backtest(bc))
    assert result.stats.trades > 0, (
        "faithful backtest generated 0 trades — HTF feed missing, grader "
        "target-clarity gate is starving the pipeline"
    )
