"""Regression guard: liquidity sweep detection must be deterministic.

The tracker used to mark used swings by id(swing); Python reuses object ids
after GC, so a fresh swing could collide with a dead one's id → false-positive
"already used" → non-deterministic sweeps → a non-deterministic backtest
(found 2026-06-03 while debugging 0-trades). gc.collect() below provokes the
id reuse so this fails pre-fix.
"""
from __future__ import annotations

import gc
from pathlib import Path

import pytest

from app.bot_config import load_bot_config
from app.replay import load_bars_csv
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker

_BARS = Path("bars/bars_MGC_1min_20251111_20260511.csv")


@pytest.mark.skipif(not _BARS.exists(), reason="MGC 1min bars fixture not present")
def test_liquidity_sweeps_are_deterministic():
    s = load_bot_config(Path("bot_config.json")).strategy
    bars = list(load_bars_csv(str(_BARS), instrument="MGC", timeframe="1min"))[:8000]

    def run():
        lt = LiquidityTracker(LiquidityConfig(
            swing_lookback=s.swing_lookback, min_penetration=s.min_penetration,
            multi_bar_window=s.multi_bar_window, max_swings=50,
        ))
        out = []
        for i, b in enumerate(bars):
            for sw in lt.on_bar(b):
                out.append((b.ts.isoformat(), sw.side, str(sw.sweep_extreme)))
            if i % 500 == 0:
                gc.collect()  # provoke id() reuse
        return out

    assert run() == run()
