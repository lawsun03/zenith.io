"""
B74: equity_export --trade-csv flag defining-behavior tests.

Tests:
1. With --trade-csv: output CSV has the required columns and correct rows,
   with engine_type="ifvg" for graded trades and "orb" for ungraded.
2. Without --trade-csv: no trade CSV file is written (existing behavior unchanged).

WHY: The per-hour PF audit (B74) depends on accurate engine_type labeling.
An iFVG trade misclassified as ORB (or vice versa) would corrupt the per-hour
PF breakdown and invalidate the GO/NO-GO decision.
"""
from __future__ import annotations

import csv
import sys
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _fake_trade(entry_ts: str, side: str, pnl: str, grade: str | None) -> dict:
    """Build a minimal trade dict as returned by BacktestResult.trades."""
    t: dict = {
        "entry_ts": entry_ts,
        "exit_ts": entry_ts,  # same ts is fine for this test
        "side": side,
        "realized_pnl": Decimal(pnl),
    }
    if grade is not None:
        t["grade"] = grade
        t["criteria"] = {}
    return t


def _fake_backtest_result(trades: list[dict]) -> MagicMock:
    result = MagicMock()
    result.trades = trades
    result.stats.equity_curve = []
    result.stats.trades = len(trades)
    result.stats.net_pnl = Decimal("0")
    result.stats.profit_factor = Decimal("1")
    return result


class TestTradeCsvOutput:
    def test_trade_csv_written_with_required_columns_and_engine_type(self, tmp_path):
        """--trade-csv writes one row per trade; engine_type=ifvg for graded,
        engine_type=orb for ungraded.

        WHY: The per-hour analysis pivots on engine_type to isolate iFVG signals
        from ORB signals. If engine_type is missing or wrong, the entire audit
        produces nonsense buckets.
        """
        trades = [
            _fake_trade("2024-03-01T10:00:00", "long", "100", "B"),   # iFVG (has grade)
            _fake_trade("2024-03-01T09:35:00", "long", "-50", None),  # ORB (no grade)
        ]
        result = _fake_backtest_result(trades)
        out_csv = tmp_path / "equity.csv"
        trade_csv = tmp_path / "trades.csv"

        with patch("scripts.equity_export.load_bot_config"), \
             patch("scripts.equity_export.strategy_for"), \
             patch("scripts.equity_export.load_bars_csv", return_value=[]), \
             patch("scripts.equity_export.asyncio.run", return_value=result):
            sys.argv = [
                "equity_export.py",
                "--bars", "dummy.csv",
                "--out", str(out_csv),
                "--trade-csv", str(trade_csv),
            ]
            import scripts.equity_export as mod
            mod.main()

        assert trade_csv.exists(), "trade CSV should be written when --trade-csv is set"
        rows = list(csv.DictReader(trade_csv.open()))
        assert len(rows) == 2
        assert {"entry_ts", "exit_ts", "side", "pnl_usd", "engine_type"}.issubset(set(rows[0].keys()))
        assert rows[0]["engine_type"] == "ifvg"
        assert rows[1]["engine_type"] == "orb"

    def test_no_trade_csv_without_flag(self, tmp_path):
        """Without --trade-csv, no trade CSV file is written.

        WHY: Adding --trade-csv must be opt-in; callers that omit it expect
        only the equity-curve output. Writing unexpected files would break
        existing pipelines that check directory contents.
        """
        result = _fake_backtest_result([])
        out_csv = tmp_path / "equity.csv"
        trade_csv = tmp_path / "trades.csv"

        with patch("scripts.equity_export.load_bot_config"), \
             patch("scripts.equity_export.strategy_for"), \
             patch("scripts.equity_export.load_bars_csv", return_value=[]), \
             patch("scripts.equity_export.asyncio.run", return_value=result):
            sys.argv = [
                "equity_export.py",
                "--bars", "dummy.csv",
                "--out", str(out_csv),
                # no --trade-csv
            ]
            import scripts.equity_export as mod
            mod.main()

        assert not trade_csv.exists(), "trade CSV must NOT be written without --trade-csv"
