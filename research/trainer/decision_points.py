"""Turns real bars into a DecisionPoint stream for one IR document.

All of the actual entry/side/stop/near-miss logic lives in
research/ir/engine.py's on_decision_point hook — this module is plumbing
only: convert a research/data/loader.py bar frame into Bar objects, run
one IRBacktestEngine per instrument with the hook wired up, and collect
what it emits. Trainer ground truth can never drift from real backtest
ground truth this way, because it IS the real backtest engine.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Iterable

import polars as pl

from app.sim.events import Bar
from research.ir.engine import DecisionPoint, IRBacktestEngine


def bars_from_frame(df: pl.DataFrame, instrument: str, timeframe: str = "1min") -> list[Bar]:
    """Convert a load_bars() frame (Float64 OHLC) into Bar objects (Decimal
    OHLC). Decimal(str(x)), never Decimal(x) — the latter would leak a
    float's binary repr (e.g. Decimal(103.1) != Decimal('103.1'))."""
    return [
        Bar(
            instrument=instrument, timeframe=timeframe, ts=row["ts"],
            open=Decimal(str(row["open"])), high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])), close=Decimal(str(row["close"])),
            volume=int(row["volume"]),
        )
        for row in df.iter_rows(named=True)
    ]


def extract_decision_points(ir_doc: dict, instrument: str, bars: Iterable[Bar]) -> list[DecisionPoint]:
    """Every DecisionPoint this IR would face over `bars`, in order."""
    points: list[DecisionPoint] = []
    IRBacktestEngine(ir_doc, instrument, on_decision_point=points.append).run(bars)
    return points
