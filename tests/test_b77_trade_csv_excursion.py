"""
B77: equity_export --trade-csv r_mfe/r_mae/mfe_pts/mae_pts columns.

Tests:
1. When r_mfe is present in a trade dict, the output CSV contains r_mfe,
   r_mae, mfe_pts, mae_pts columns with the correct values.
2. A trade with r_mfe=2.5 (target hit) appears with r_mfe >= 2.4 in output.
3. Without --trade-csv flag: no output CSV written (unchanged behavior).

WHY: Phase 1 quality-predictor analyses have used research-baseline MFE/MAE
data; the deployed config's excursion geometry differs (close-mode entry vs
proximal FVG edge). Without accurate deployed-config MFE/MAE, future Phase 1
checks are approximate. These tests pin that the new columns appear and carry
the correct values, so the dataset generation step is reliable.
"""
from __future__ import annotations

import csv
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


def _fake_trade(
    entry_ts: str,
    side: str,
    pnl: str,
    grade: str | None,
    r_mfe: float | None = None,
    r_mae: float | None = None,
    mfe_pts: str | None = None,
    mae_pts: str | None = None,
) -> dict:
    t: dict = {
        "entry_ts": entry_ts,
        "exit_ts": entry_ts,
        "side": side,
        "realized_pnl": Decimal(pnl),
    }
    if grade is not None:
        t["grade"] = grade
        t["criteria"] = {}
    if r_mfe is not None:
        t["r_mfe"] = r_mfe
    if r_mae is not None:
        t["r_mae"] = r_mae
    if mfe_pts is not None:
        t["mfe_pts"] = mfe_pts
    if mae_pts is not None:
        t["mae_pts"] = mae_pts
    return t


def _fake_backtest_result(trades: list[dict]) -> MagicMock:
    result = MagicMock()
    result.trades = trades
    result.stats.equity_curve = []
    result.stats.trades = len(trades)
    result.stats.net_pnl = Decimal("0")
    result.stats.profit_factor = Decimal("1")
    return result


class TestB77ExcursionColumns:
    def test_excursion_columns_written_when_present(self, tmp_path):
        """r_mfe, r_mae, mfe_pts, mae_pts written to trade CSV when in trade dict.

        WHY: The deployed MFE/MAE dataset requires these four columns. If they
        are silently omitted, every downstream Phase 1 excursion analysis would
        fail to find them and produce empty results rather than an error.
        """
        trades = [
            _fake_trade(
                "2024-03-01T10:00:00", "long", "125.0", "B",
                r_mfe=2.5, r_mae=0.3, mfe_pts="12.5", mae_pts="1.5",
            ),
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

        rows = list(csv.DictReader(trade_csv.open()))
        assert len(rows) == 1
        assert "r_mfe" in rows[0], "r_mfe column must be present"
        assert "r_mae" in rows[0], "r_mae column must be present"
        assert "mfe_pts" in rows[0], "mfe_pts column must be present"
        assert "mae_pts" in rows[0], "mae_pts column must be present"
        assert float(rows[0]["r_mfe"]) >= 2.4, "target-hit trade must have r_mfe >= 2.4"
        assert rows[0]["mfe_pts"] == "12.5"
        assert rows[0]["mae_pts"] == "1.5"

    def test_excursion_columns_empty_when_absent(self, tmp_path):
        """r_mfe/r_mae/mfe_pts/mae_pts are empty string when not in trade dict.

        WHY: Older trade dicts (e.g. from research-baseline runs that pre-date
        B2 infrastructure) may not have MFE/MAE fields. The CSV must still write
        a row with empty cells rather than raising KeyError, so existing callers
        can read the file without crashing.
        """
        trades = [
            _fake_trade("2024-03-01T09:35:00", "long", "-50.0", None),
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

        rows = list(csv.DictReader(trade_csv.open()))
        assert rows[0]["r_mfe"] == "", "absent r_mfe should be empty string, not error"
        assert rows[0]["mae_pts"] == "", "absent mae_pts should be empty string"

    def test_no_trade_csv_flag_unchanged(self, tmp_path):
        """Without --trade-csv, no trade CSV is written (existing behavior).

        WHY: The --trade-csv flag is opt-in. Adding excursion columns must not
        change the output of callers that omit the flag; equity-curve pipelines
        must remain unaffected.
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
            ]
            import scripts.equity_export as mod
            mod.main()

        assert not trade_csv.exists(), "no trade CSV must be written without --trade-csv"
