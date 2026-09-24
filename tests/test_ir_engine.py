"""IR execution engine: entry/exit/stop/target -> trade sequence."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.sim.events import Bar
from research.ir.engine import run_backtest
from research.ir.schema import validate

BASE_TS = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)


def bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(instrument="TEST", timeframe="1min", ts=BASE_TS + timedelta(minutes=i),
               open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=100)


_IFVG_DOC = {
    "ir_version": "1.0", "name": "test iFVG fixture",
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


def _ifvg_long_fixture_bars() -> list[Bar]:
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
        (24, 99.4, 108.0, 99.3, 107.5),  # runs up through target
    ]
    return [bar(i, str(o), str(h), str(l), str(c)) for i, o, h, l, c in rows]


def test_ifvg_doc_is_schema_valid() -> None:
    assert validate(_IFVG_DOC) == []


def test_engine_produces_expected_long_trade() -> None:
    trades = run_backtest(_IFVG_DOC, "TEST", _ifvg_long_fixture_bars())
    assert len(trades) == 1
    t = trades[0]
    assert t.side == "long"
    assert t.entry_price == Decimal("98.8")
    assert t.stop_price == Decimal("95.2")   # sweep_extreme 95.5 - buffer 0.30
    assert t.target_price == Decimal("107.8")  # entry + 2.5 * (98.8 - 95.2)
    assert t.exit_reason == "target"
    assert t.exit_price == Decimal("107.8")
    assert t.pnl_points == Decimal("9.0")
    assert t.entry_ts == BASE_TS + timedelta(minutes=23)


def test_engine_exits_at_session_end_when_neither_stop_nor_target_hit() -> None:
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
        # drift sideways, well clear of stop (95.5) and target (107.05),
        # into and past the 11:00 ET session end (08:30 + 150 minutes)
        (24, 99.4, 99.6, 99.2, 99.4),
        (150, 99.4, 99.7, 99.2, 99.5),
        (151, 99.5, 99.7, 99.3, 99.5),
    ]
    bars = [bar(i, str(o), str(h), str(l), str(c)) for i, o, h, l, c in rows]
    trades = run_backtest(_IFVG_DOC, "TEST", bars)
    assert len(trades) == 1
    assert trades[0].exit_reason == "session_end" or trades[0].exit_reason == "exit_rule"


def test_no_entry_signal_no_trades() -> None:
    rows = [(i, 100.0, 100.1, 99.9, 100.0) for i in range(30)]
    bars = [bar(i, str(o), str(h), str(l), str(c)) for i, o, h, l, c in rows]
    trades = run_backtest(_IFVG_DOC, "TEST", bars)
    assert trades == []


def test_stop_buffer_widens_stop_beyond_the_anchor() -> None:
    """stop.buffer pushes the stop further from entry than the bare anchor,
    on both sides — matches app/strategy/composer.py: stop = anchor -/+ buf
    (buf widens, never tightens, the stop)."""
    # 0.1 buffer, not 0.30, so the widened target (107.3) still stays under
    # bar24's high (108.0) in this fixture and the trade actually closes.
    doc_no_buffer = {**_IFVG_DOC, "stop": {**_IFVG_DOC["stop"], "buffer": 0}}
    doc_buffer = {**_IFVG_DOC, "stop": {**_IFVG_DOC["stop"], "buffer": 0.1}}

    bars = _ifvg_long_fixture_bars()
    t_no_buffer = run_backtest(doc_no_buffer, "TEST", bars)[0]
    t_buffer = run_backtest(doc_buffer, "TEST", bars)[0]

    assert t_no_buffer.stop_price == Decimal("95.5")
    assert t_buffer.stop_price == Decimal("95.4")  # 95.5 - 0.1, further from the long entry
    assert t_buffer.stop_price < t_no_buffer.stop_price < t_buffer.entry_price


def test_open_line_10am_strategy_is_valid_and_runs() -> None:
    # No hand-coded "10 AM open line" strategy exists in this repo under
    # this name or an equivalent mechanism (the closest relative, the
    # "Silver Bullet" 10:00-11:00 ET window in app/strategy/composer.py,
    # is a time gate on the iFVG signal, not a session-open-cross
    # strategy) — so unlike test_ir_ifvg_bitidentical.py, this is a
    # schema-and-mechanics smoke test only. It makes no bit-identical
    # claim because there is nothing to diff it against.
    import json
    from pathlib import Path
    doc = json.loads(
        (Path(__file__).resolve().parents[1] / "research" / "ir" / "strategies"
         / "open_line_10am.json").read_text()
    )
    assert validate(doc) == []

    # 10:00 ET = 15:00 UTC. Build a session_open at 100.0, then cross up.
    base = datetime(2026, 1, 6, 15, 0, tzinfo=timezone.utc)

    def b(i, o, h, l, c):
        return Bar(instrument="TEST", timeframe="1min", ts=base + timedelta(minutes=i),
                   open=Decimal(str(o)), high=Decimal(str(h)), low=Decimal(str(l)),
                   close=Decimal(str(c)), volume=100)

    bars = [
        b(0, 100.0, 100.2, 99.8, 99.9),    # 10:00 ET -> session_open = 100.0 (from open); close below it
        b(1, 99.9, 101.5, 99.6, 101.2),    # crosses up through 100.0 -> long entry
        b(2, 101.2, 104.0, 101.0, 103.8),  # runs up through the target
    ]
    trades = run_backtest(doc, "TEST", bars)
    assert len(trades) == 1
    assert trades[0].side == "long"
    assert trades[0].entry_price == Decimal("101.2")  # cross_of has no FVG zone -> bar.close
    assert trades[0].stop_price == Decimal("99.6")     # anchor=entry_bar -> entry bar's low
    assert trades[0].target_price == Decimal("103.6")  # 1.5R = 101.2 + 1.5*(101.2-99.6)
    assert trades[0].exit_reason == "target"
