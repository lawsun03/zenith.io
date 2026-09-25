"""sma_7 / sma_21 / vwap levels and level-vs-level cross_of.

Added so strategies aren't confined to ICT constructs (sweep/displacement/
FVG): indicator-style hypotheses become expressible with the existing
cross_of / close_beyond / retrace_to ops. Expected values are computed by
hand, not by re-running the implementation (CLAUDE.md rule 9 spirit).
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timedelta, timezone
from decimal import Decimal

from app.sim.events import Bar
from research.ir.engine import IRBacktestEngine
from research.ir.predicates import CrossOfLeaf, EvalCtx, LevelTracker
from research.ir.schema import validate

# 08:30 ET on a Tuesday in January (EST = UTC-5)
T0 = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)


def _bar(i, o, h, l, c, v=100, t0=T0):
    return Bar(instrument="NQ", timeframe="1min", ts=t0 + timedelta(minutes=i),
               open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(l)),
               close=Decimal(str(c)), volume=v)


def _close_bar(i, c, v=100):
    return _bar(i, c, c + 0.5, c - 0.5, c, v)


def test_sma_values_match_hand_computation():
    lt = LevelTracker(dtime(8, 30))
    seen = []
    for i in range(1, 23):
        lt.on_bar(_close_bar(i - 1, i))
        seen.append((lt.sma_7, lt.sma_21))

    assert seen[5][0] is None                      # only 6 closes so far
    assert seen[6][0] == Decimal(4)                # mean(1..7)
    assert seen[7][0] == Decimal(5)                # mean(2..8)
    assert seen[19][1] is None                     # only 20 closes
    assert seen[20][1] == Decimal(11)              # mean(1..21)
    assert seen[21][1] == Decimal(12)              # mean(2..22)


def test_vwap_is_session_anchored_and_volume_weighted():
    lt = LevelTracker(dtime(8, 30))
    lt.on_bar(_bar(-1, 100, 200, 100, 150, v=999))   # 08:29 ET: before session start, excluded
    assert lt.vwap is None
    lt.on_bar(_bar(0, 9, 10, 8, 9, v=100))           # typical price 9
    assert lt.vwap == Decimal(9)
    lt.on_bar(_bar(1, 11, 12, 10, 11, v=300))        # typical price 11
    assert lt.vwap == Decimal("10.5")                # (9*100 + 11*300) / 400

    lt.on_bar(_bar(0, 50, 52, 48, 50, v=10, t0=T0 + timedelta(days=1)))
    assert lt.vwap == Decimal(50)                    # reset on the next ET day


def test_zero_volume_bars_do_not_divide_by_zero_or_move_vwap():
    lt = LevelTracker(dtime(8, 30))
    lt.on_bar(_bar(0, 9, 10, 8, 9, v=0))
    assert lt.vwap is None
    lt.on_bar(_bar(1, 9, 10, 8, 9, v=50))
    assert lt.vwap == Decimal(9)


def test_level_vs_level_cross_fires_exactly_on_the_crossover_bar():
    closes = [100 - i for i in range(30)] + [70 + 4 * i for i in range(30)]
    bars = [_close_bar(i, c) for i, c in enumerate(closes)]

    def sma(n, upto):
        return None if upto + 1 < n else sum(closes[upto + 1 - n: upto + 1]) / n

    expected = []
    prev = None
    for i in range(len(closes)):
        a, b = sma(7, i), sma(21, i)
        if a is None or b is None:
            prev = None
            continue
        d = a - b
        if prev is not None and prev <= 0 < d:
            expected.append(i)
        prev = d
    assert len(expected) == 1  # sanity: the fixture has exactly one bullish cross

    lt = LevelTracker(dtime(8, 30))
    ctx = EvalCtx(levels=lt)
    leaf = CrossOfLeaf(level="sma_7", direction="up", level_b="sma_21")
    fired = []
    for i, bar in enumerate(bars):
        lt.on_bar(bar)
        if leaf.on_bar(bar, ctx):
            fired.append(i)
    assert fired == expected


def test_levels_never_depend_on_future_bars():
    base = [_close_bar(i, 100 + (i % 5)) for i in range(40)]
    a, b = LevelTracker(dtime(8, 30)), LevelTracker(dtime(8, 30))
    k = 25
    future_a = [_close_bar(i, 500) for i in range(k + 1, 40)]
    future_b = [_close_bar(i, 1) for i in range(k + 1, 40)]
    for bar in base[: k + 1]:
        a.on_bar(bar)
        b.on_bar(bar)
    assert (a.sma_7, a.sma_21, a.vwap) == (b.sma_7, b.sma_21, b.vwap)
    for bar in future_a:
        a.on_bar(bar)
    for bar in future_b:
        b.on_bar(bar)
    assert (a.sma_7, a.sma_21) != (b.sma_7, b.sma_21)  # the futures do differ


SMA_CROSS_IR = {
    "ir_version": "1.0", "name": "sma-7-21-cross",
    "instruments": ["NQ", "ES", "GC"],
    "session": {"start": "08:30", "end": "16:00", "tz": "America/New_York"},
    "entry": {"op": "cross_of", "level": "sma_7", "level_b": "sma_21",
              "direction": "up", "recognizable": True},
    "exit": {"op": "session_end", "recognizable": False},
    "stop": {"type": "atr", "multiple": 2.0, "lookback": 14},
    "target": {"type": "r_multiple", "multiple": 2.0},
    "sizing": {"family": "micro", "vol_target_annual": 0.15, "max_contracts": 10},
}

VWAP_IR = {
    **SMA_CROSS_IR, "name": "vwap-reclaim",
    "entry": {"op": "and", "operands": [
        {"op": "cross_of", "level": "vwap", "direction": "up", "recognizable": True},
        {"op": "close_beyond", "level": "sma_21", "direction": "up", "recognizable": True},
    ]},
}


def test_schema_accepts_indicator_strategies():
    assert validate(SMA_CROSS_IR) == []
    assert validate(VWAP_IR) == []
    assert validate({**SMA_CROSS_IR, "entry": {**SMA_CROSS_IR["entry"], "level": "sma_9"}})


def test_sma_cross_strategy_backtests_end_to_end_with_no_ict_predicate():
    closes = [100 - 0.2 * i for i in range(40)] + [92 + 0.6 * i for i in range(60)]
    bars = [_bar(i, c, c + 0.4, c - 0.4, c) for i, c in enumerate(closes)]

    trades = IRBacktestEngine(SMA_CROSS_IR, "NQ").run(bars)

    assert len(trades) >= 1
    first = trades[0]
    assert first.side == "long"
    assert first.entry_ts > T0 + timedelta(minutes=40)  # after the trend turned, not before
