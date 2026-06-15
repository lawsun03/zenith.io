"""
B79: equity_export --trade-csv displacement_ts column.

Defining-behavior tests:
1. --trade-csv output includes displacement_ts column; non-null for iFVG trades.
2. ORB trade has empty displacement_ts.

WHY: B79 freshness analysis computes gap_bars = (entry_ts - displacement_ts) / 5min
to bucket iFVG trades as fresh/mid/stale. If displacement_ts is missing from the CSV
layer the GO/NO-GO check cannot be run at all; if it is misrouted (ORB getting an iFVG
timestamp) the gap distribution is corrupted. These tests pin both cases.
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
    displacement_ts: str | None = None,
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
    if displacement_ts is not None:
        t["displacement_ts"] = displacement_ts
    return t


def _fake_result(trades: list[dict]) -> MagicMock:
    r = MagicMock()
    r.trades = trades
    r.stats.equity_curve = []
    r.stats.trades = len(trades)
    r.stats.net_pnl = Decimal("0")
    r.stats.profit_factor = Decimal("1")
    return r


class TestB79DisplacementTs:
    def test_displacement_ts_present_for_ifvg_trade(self, tmp_path):
        """iFVG trade with displacement_ts in trade dict → column in CSV, non-empty.

        WHY: gap_bars = (entry_ts - displacement_ts) / 5min drives the fresh/stale
        bucket assignment. If the column is missing from the CSV, all downstream
        freshness analyses silently produce NaN gaps and the GO/NO-GO criterion
        cannot be evaluated.
        """
        ts = "2024-03-01T10:00:00"
        disp_ts = "2024-03-01T09:50:00"
        trades = [_fake_trade(ts, "long", "125.0", "B", displacement_ts=disp_ts)]
        result = _fake_result(trades)
        out_csv = tmp_path / "eq.csv"
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
        assert "displacement_ts" in rows[0], "displacement_ts column must be present"
        assert rows[0]["displacement_ts"] == disp_ts, \
            "displacement_ts value must match the trade dict"

    def test_displacement_ts_empty_for_orb_trade(self, tmp_path):
        """ORB trade (no displacement_ts in dict) → displacement_ts column is empty.

        WHY: ORB trades have no FVG creation timestamp; the bucket analysis filters
        on engine_type='ifvg' before computing gaps. But the CSV must still include
        the column with an empty cell so DictReader can parse rows uniformly without
        raising KeyError or producing misaligned columns.
        """
        ts = "2024-03-01T09:35:00"
        trades = [_fake_trade(ts, "long", "-50.0", None)]  # ORB: no grade, no displacement_ts
        result = _fake_result(trades)
        out_csv = tmp_path / "eq.csv"
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
        assert "displacement_ts" in rows[0], "displacement_ts column must be present even for ORB"
        assert rows[0]["displacement_ts"] == "", \
            "ORB trade must have empty displacement_ts, not missing or erroring"
