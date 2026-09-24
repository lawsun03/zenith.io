"""DecisionPoint extraction tests.

CLAUDE.md rule 9: predicates are pure functions of a bar window, no
lookahead. The new on_decision_point hook (research/ir/engine.py) computes
side/entry/stop/near-miss information that didn't exist before this
change, so it gets its own no-lookahead property test rather than relying
solely on tests/test_ir_predicates.py's coverage of the underlying nodes:
a bug in the hook's own wiring (e.g. peeking at self._position state set
by a later bar) would not be caught there.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.sim.events import Bar
from research.ir.engine import DecisionPoint, IRBacktestEngine

BASE_TS = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)  # 08:30 ET

_DOC = {
    "ir_version": "1.0", "name": "trainer fixture",
    "instruments": ["NQ", "ES", "GC"],
    "session": {"start": "08:30", "end": "11:00", "tz": "America/New_York"},
    "entry": {"op": "or", "operands": [
        {"op": "and", "operands": [
            {"op": "sweep_of", "level": "swing_low", "n": 2,
             "min_offset": 0.20, "within_bars": 5, "recognizable": True},
            {"op": "displacement", "direction": "up", "min_atr": 1.0, "recognizable": True},
        ]},
        {"op": "and", "operands": [
            {"op": "sweep_of", "level": "swing_high", "n": 2,
             "min_offset": 0.20, "within_bars": 5, "recognizable": True},
            {"op": "displacement", "direction": "down", "min_atr": 1.0, "recognizable": True},
        ]},
    ]},
    "exit": {"op": "session_end", "recognizable": False},
    "stop": {"type": "structural", "anchor": "sweep_extreme", "multiple": 1.0,
             "lookback": 14, "buffer": 0.30},
    "target": {"type": "r_multiple", "multiple": 2.5},
    "sizing": {"family": "micro", "vol_target_annual": 0.15, "max_contracts": 10},
}


def _bar(i: int, o: Decimal, h: Decimal, l: Decimal, c: Decimal) -> Bar:
    return Bar(instrument="TEST", timeframe="1min", ts=BASE_TS + timedelta(minutes=i),
               open=o, high=h, low=l, close=c, volume=100)


_bar_strategy = st.tuples(
    st.decimals(min_value="90", max_value="110", places=2),
    st.decimals(min_value="0", max_value="5", places=2),
    st.decimals(min_value="0", max_value="5", places=2),
).map(lambda t: (t[0], t[0] + t[1], t[0] - t[2]))


def _run(bars: list[Bar]) -> list[DecisionPoint]:
    points: list[DecisionPoint] = []
    IRBacktestEngine(_DOC, "TEST", on_decision_point=points.append).run(bars)
    return points


@given(closes=st.lists(_bar_strategy, min_size=5, max_size=40))
@settings(max_examples=60)
def test_decision_points_no_lookahead(closes: list[tuple[Decimal, Decimal, Decimal]]) -> None:
    bars = [_bar(i, o, h, l, o) for i, (o, h, l) in enumerate(closes)]

    full = _run(bars)
    for k in range(1, len(bars) + 1):
        prefix_points = _run(bars[:k])
        full_prefix = [p for p in full if p.ts <= bars[k - 1].ts]
        # Every point emitted within the first k bars when running the
        # full sequence must also appear, unchanged, when only the first
        # k bars are fed in — nothing about it can depend on bar k, k+1, ...
        assert [p.ts for p in full_prefix] == [p.ts for p in prefix_points][:len(full_prefix)]
        for a, b in zip(full_prefix, prefix_points):
            assert a == b


_IFVG_DOC = {
    **_DOC,
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
}


def test_fired_decision_point_matches_the_trade_the_engine_actually_opens() -> None:
    """A DecisionPoint with fired=True must carry exactly the side/stop the
    engine goes on to open a position at — this is the guarantee the whole
    trainer depends on (research/trainer/decision_points.py reuses this
    hook precisely so grading can never drift from real backtest truth).
    Fixture is tests/test_ir_engine.py's known-good iFVG long setup."""
    rows = [(i, 100.0, 100.5, 99.5, 100.0) for i in range(14)]
    rows += [
        (14, 99.0, 99.2, 98.8, 99.0),
        (15, 98.8, 98.9, 97.5, 97.8),
        (16, 97.7, 97.8, 97.0, 97.3),
        (17, 97.3, 97.5, 97.1, 97.2),
        (18, 97.2, 97.3, 96.5, 97.0),
        (19, 97.0, 97.4, 96.8, 97.2),
        (20, 97.2, 97.6, 96.9, 97.4),
        (21, 97.4, 97.5, 95.5, 97.3),
        (22, 97.3, 99.5, 97.2, 99.3),
        (23, 99.3, 99.6, 99.1, 99.4),
        (24, 99.4, 108.0, 99.3, 107.5),
    ]
    bars = [Bar(instrument="TEST", timeframe="1min", ts=BASE_TS + timedelta(minutes=i),
                open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(l)),
                close=Decimal(str(c)), volume=100)
            for i, o, h, l, c in rows]

    points: list[DecisionPoint] = []
    trades = IRBacktestEngine(_IFVG_DOC, "TEST", on_decision_point=points.append).run(bars)

    assert len(trades) == 1
    fired = [p for p in points if p.fired]
    assert len(fired) == 1
    assert fired[0].ts == trades[0].entry_ts
    assert fired[0].side == trades[0].side
    assert fired[0].stop_price == trades[0].stop_price
    assert not any(p.near_miss for p in points if p.fired)


def test_no_decision_points_outside_session_hours() -> None:
    """The engine only watches for entries in-session — the hook must not
    manufacture decision points a real trader (or the real backtest)
    would never face."""
    off_session_ts = BASE_TS.replace(hour=2, minute=0)  # well before 08:30 ET
    bars = [Bar(instrument="TEST", timeframe="1min", ts=off_session_ts + timedelta(minutes=i),
                open=Decimal("100"), high=Decimal("101"), low=Decimal("99"),
                close=Decimal("100"), volume=100)
            for i in range(10)]
    assert _run(bars) == []
