"""Diagnostic: replay parity MGC bars and show which FVG each signal's entry came from.

Verifies the stale-FVG hypothesis for the 2026-06-10 parity bug: in
ifvg_entry_mode="close", signal entry = inverted FVG edge, and
_find_inverted_fvg can match an FVG created long before the signal bar.
"""
from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.bot_config import StrategyParams
from app.broker.events import Bar
from app.execution.engine import StrategyRunner
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.grader import SetupGrader
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
from app.strategy.killzone import killzones_from_names


def main() -> None:
    cfg_blob = json.loads(Path("backtests/parity_MGC_0610.json").read_text())["config"]
    s = StrategyParams(**cfg_blob["strategy"])
    zones = killzones_from_names(cfg_blob["enabled_killzones"])

    detector = DisplacementDetector(DisplacementConfig(
        atr_period=s.atr_period,
        body_atr_multiple=s.body_atr_multiple,
        min_body_to_range_ratio=s.min_body_to_range_ratio,
        min_absolute_body=s.min_absolute_body,
    ))

    # Record what _find_inverted_fvg returns so we can see the FVG's age.
    matched: dict[str, object] = {"fvg": None}
    orig_find = detector._find_inverted_fvg

    def traced_find(bar, side, *args, **kwargs):
        fvg = orig_find(bar, side, *args, **kwargs)
        if fvg is not None:
            matched["fvg"] = fvg
        return fvg

    detector._find_inverted_fvg = traced_find  # type: ignore[method-assign]

    runner = StrategyRunner(
        instrument="MGC",
        timeframe="1min",
        liquidity=LiquidityTracker(LiquidityConfig(
            swing_lookback=s.swing_lookback,
            min_penetration=s.min_penetration,
            multi_bar_window=s.multi_bar_window,
            max_swings=50,
            min_penetration_atr_factor=(
                s.min_penetration_atr_factor if s.min_penetration_atr_factor > 0 else None
            ),
        )),
        displacement=detector,
        composer=SweepDisplacementComposer(ComposerConfig(
            instrument="MGC",
            displacement_window_bars=s.displacement_window_bars,
            stop_buffer=s.stop_buffer,
            r_multiple=s.r_multiple,
            killzones=zones,
            trend_ema_period=s.trend_ema_period,
            cooldown_bars_after_stop=s.cooldown_bars_after_stop,
            min_atr_filter=s.min_atr_filter,
            max_atr_filter=s.max_atr_filter,
            swing_stop_lookback=s.swing_stop_lookback,
        )),
        grader=SetupGrader(target_clarity_mode=s.target_clarity_mode),
        strategy_cfg=s,
    )

    with open("bars/bars_MGC_1min_20260610.csv", newline="") as f:
        for row in csv.DictReader(f):
            bar = Bar(
                instrument="MGC",
                ts=datetime.fromisoformat(row["timestamp"]),
                open=Decimal(row["open"]),
                high=Decimal(row["high"]),
                low=Decimal(row["low"]),
                close=Decimal(row["close"]),
                volume=int(row["volume"]),
                timeframe="1min",
            )
            sig = runner.on_bar(bar)
            if sig is not None:
                fvg = matched["fvg"]
                age_min = (
                    (bar.ts - fvg.created_at).total_seconds() / 60 if fvg else "?"
                )
                dist = (
                    min(abs(sig.entry - bar.high), abs(sig.entry - bar.low))
                    if not (bar.low <= sig.entry <= bar.high) else Decimal("0")
                )
                print(
                    f"{bar.ts:%m-%d %H:%M} {sig.side:5s} entry={sig.entry} "
                    f"bar=[{bar.low}-{bar.high}] outside_by={dist} "
                    f"fvg=[{sig.fvg_low}-{sig.fvg_high}] "
                    f"fvg_created={fvg.created_at:%m-%d %H:%M} age_min={age_min:.0f}"
                    if fvg else f"{bar.ts} {sig.side} entry={sig.entry} (no fvg traced)"
                )


if __name__ == "__main__":
    main()
