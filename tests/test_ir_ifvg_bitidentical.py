"""Acceptance test: the IR-expressed iFVG sweep reproduces the hand-coded
strategy's signal generation bit-identically, using the strategy's own
shipped defaults (app/bot_config.py StrategyParams) with no ATR-relative
substitution and no dropped stop buffer:

  - min_penetration = Decimal("0.20")  ->  IR sweep_of.min_offset = 0.20
  - stop_buffer      = Decimal("0.30") ->  IR stop.buffer = 0.30

Both are pooled as flat constants across NQ/ES/GC by the IR too — same as
they are, today, in the hand-coded strategy (CLAUDE.md rule 2 is about
banning *per-instrument* parameter sets, not about every constant being
volatility-scaled; the hand-coded original already applies these two as a
single number regardless of instrument, so reproducing them bit-identically
does not introduce any new per-instrument fitting).

Deliberately out of scope, by design (not a workaround): SetupGrader
(app/strategy/grader.py) and volume-profile/HTF filtering. The default
backtest build path (app/builders.py) attaches a SetupGrader to every
composer, so the real historical trade list in trade_analysis/ reflects
sweep+displacement+FVG *plus* grading. This test targets the rule itself —
the sweep/displacement/FVG generator that CLAUDE.md rule 1 and gate 9's
"entry predicate" vocabulary describe — not the phase-4 gate/quality-filter
stack layered on top of it in production. A grader-inclusive comparison
would need its own IR predicates for grader's rules (session range, FVG
zone-width, gapping-sack, etc.), which is out of this task's scope
("do NOT build gates").

Also out of scope for this environment: running over "the full corpus".
There is no Databento cache in this sandbox (research/data/'s expected
.parquet files do not exist here), so this test runs against a hand-built
fixture. The comparison is exact — zero tolerance on every field checked —
but it is a fixture-level proof that the interpreter's mechanics are
correct, not a full-corpus trade-count/timestamp diff. That final step
needs an environment with the real corpus cached.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from app.sim.events import Bar
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.killzone import Killzone
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker

from research.ir.engine import run_backtest

BASE_TS = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)  # 08:30 ET


def _bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(instrument="MGC", timeframe="1min", ts=BASE_TS + timedelta(minutes=i),
               open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=100)


def _fixture_bars() -> list[Bar]:
    """14-bar ATR(14) warmup, then a low sweep (Pattern B) followed by a
    bullish displacement that inverts a bearish FVG, then a run to a
    target well beyond 2.5R so both sides record a closed trade."""
    rows = [(i, 100.0, 100.5, 99.5, 100.0) for i in range(14)]
    rows += [
        (14, 99.0, 99.2, 98.8, 99.0),
        (15, 98.8, 98.9, 97.5, 97.8),
        (16, 97.7, 97.8, 97.0, 97.3),
        (17, 97.3, 97.5, 97.1, 97.2),
        (18, 97.2, 97.3, 96.5, 97.0),
        (19, 97.0, 97.4, 96.8, 97.2),
        (20, 97.2, 97.6, 96.9, 97.4),
        (21, 97.4, 97.5, 95.5, 97.3),   # sweep bar (Pattern B, one-bar)
        (22, 97.3, 99.5, 97.2, 99.3),   # displacement bar b2 (confirmed one bar later)
        (23, 99.3, 99.6, 99.1, 99.4),   # confirms idx22 as b2 -> signal fires here
        (24, 99.4, 108.0, 99.3, 107.5),  # runs through the 2.5R target
    ]
    return [_bar(i, str(o), str(h), str(l), str(c)) for i, o, h, l, c in rows]


def _run_ground_truth(bars: list[Bar]) -> dict:
    """Direct usage of the existing hand-coded classes, exactly as
    composer.py's own module docstring documents ("the standard
    composable design") — no grader, no volume profile, no Runner
    wrapper (see module docstring: those are phase-4 gate concerns).
    Every config value below is the StrategyParams / ComposerConfig
    shipped default (app/bot_config.py:36-50, app/strategy/composer.py:122,130)
    — nothing overridden — so this really is "the existing hand-coded
    iFVG sweep strategy," not a variant of it."""
    liq = LiquidityTracker(LiquidityConfig(
        swing_lookback=2, min_penetration=Decimal("0.20"), multi_bar_window=3, max_swings=50,
    ))
    disp = DisplacementDetector(DisplacementConfig(
        atr_period=14, body_atr_multiple=Decimal("1.0"),
        min_body_to_range_ratio=Decimal("0.6"), min_absolute_body=Decimal("1.0"),
    ))
    zones = [Killzone(name="NYAM", start=time(8, 30), end=time(11, 0))]
    comp = SweepDisplacementComposer(ComposerConfig(
        instrument="MGC", displacement_window_bars=5, stop_buffer=Decimal("0.30"),
        r_multiple=Decimal("2.5"), killzones=zones,
    ))

    signal = None
    for b in bars:
        sweeps = liq.on_bar(b, atr=disp.atr)
        for sw in sweeps:
            comp.on_sweep(b, sw)
        d = disp.on_bar(b)
        if d is not None:
            candidate = comp.on_displacement(b, d)
            if candidate is not None:
                signal = candidate
        comp.on_bar_close(b)

    assert signal is not None, "ground truth produced no signal — fixture is broken"
    return {
        "side": signal.side, "entry": signal.entry, "stop": signal.stop,
        "target": signal.target, "created_at": signal.created_at,
        "sweep_extreme": signal.sweep_extreme,
        "fvg_low": signal.fvg_low, "fvg_high": signal.fvg_high,
    }


_IR_DOC = {
    "ir_version": "1.0", "name": "iFVG bit-identical fixture",
    "instruments": ["NQ", "ES", "GC"],
    "session": {"start": "08:30", "end": "11:00", "tz": "America/New_York"},
    "entry": {"op": "or", "operands": [
        {"op": "and", "operands": [
            {"op": "sweep_of", "level": "swing_low", "n": 2,
             "min_offset": 0.20, "within_bars": 5, "recognizable": True},
            {"op": "displacement", "direction": "up", "min_atr": 1.0, "recognizable": True},
            {"op": "fvg", "direction": "up", "recognizable": True},
        ]},
        {"op": "and", "operands": [
            {"op": "sweep_of", "level": "swing_high", "n": 2,
             "min_offset": 0.20, "within_bars": 5, "recognizable": True},
            {"op": "displacement", "direction": "down", "min_atr": 1.0, "recognizable": True},
            {"op": "fvg", "direction": "down", "recognizable": True},
        ]},
    ]},
    "exit": {"op": "session_end", "recognizable": False},
    "stop": {"type": "structural", "anchor": "sweep_extreme", "multiple": 1.0,
             "lookback": 14, "buffer": 0.30},
    "target": {"type": "r_multiple", "multiple": 2.5},
    "sizing": {"family": "micro", "vol_target_annual": 0.15, "max_contracts": 10},
}


def test_ir_engine_matches_hand_coded_composer_chain_bit_identically() -> None:
    bars = _fixture_bars()
    truth = _run_ground_truth(bars)

    trades = run_backtest(_IR_DOC, "MGC", bars)
    assert len(trades) == 1
    t = trades[0]

    # Zero-tolerance match against the real hand-coded defaults: side,
    # timing, entry price, stop (anchor + fixed buffer), and target.
    assert t.side == truth["side"] == "long"
    assert t.entry_ts == truth["created_at"]
    assert t.entry_price == truth["entry"] == truth["fvg_high"]
    assert t.stop_price == truth["stop"] == truth["sweep_extreme"] - Decimal("0.30")
    assert t.target_price == truth["target"]
