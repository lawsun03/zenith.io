"""UI backtest parity: the UI subprocess (app/backtest.py) must delegate to the
canonical runner.run_backtest while preserving the result-JSON shape the
frontend reads. These tests lock that contract.

NOTE: BacktestConfig takes `bars` (an Iterator[Bar]), not `bars_path`. Each
helper call builds a FRESH iterator because run_backtest consumes it.
"""
from __future__ import annotations

import asyncio
from decimal import Decimal

from app.backtest.runner import BacktestConfig, run_backtest
from app.replay import load_bars_csv

FIXTURE = "tests/fixtures/bars_parity_mini.csv"


def _mini_cfg() -> BacktestConfig:
    return BacktestConfig(
        instrument="MNQ",
        bars=load_bars_csv(FIXTURE, instrument="MNQ", timeframe="1min"),
        timeframe="1min",
        enforce_risk_limits=False,
    )


def test_result_carries_fills():
    result = asyncio.run(run_backtest(_mini_cfg()))
    assert hasattr(result, "fills"), "BacktestResult must expose fills"
    assert isinstance(result.fills, list)
