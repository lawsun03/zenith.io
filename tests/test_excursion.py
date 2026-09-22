from decimal import Decimal
from datetime import datetime, timezone
from app.sim.events import Bar
from app.execution.excursion import ExcursionTracker, ExcursionWindow


def _bar(o, h, l, c, ts=None):
    return Bar(instrument="MGC", timeframe="1min",
               ts=ts or datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc),
               open=Decimal(o), high=Decimal(h), low=Decimal(l),
               close=Decimal(c), volume=Decimal("1"))


def test_long_excursion_mfe_mae_and_target():
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    # LONG ref=100, target=104, window=2 bars
    t.open(key="K1", kind="trade", side="long", ref=Decimal("100"),
           target=Decimal("104"), window_bars=2)
    t.on_bar(_bar("100", "103", "99", "102"))   # high 103 (+3), low 99 (-1)
    assert not emitted                          # window not done yet
    t.on_bar(_bar("102", "104.5", "101", "104")) # high 104.5 (+4.5), reaches target
    assert len(emitted) == 1
    w = emitted[0]
    assert w.mfe == Decimal("4.5")   # max(103,104.5) - 100
    assert w.mae == Decimal("1")     # 100 - min(99,101)
    assert w.reached_target is True  # high 104.5 >= 104


def test_short_excursion_mfe_mae():
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="K2", kind="rejection", side="short", ref=Decimal("100"),
           target=Decimal("96"), window_bars=1)
    t.on_bar(_bar("100", "101", "97", "98"))  # favorable: 100-97=3, adverse: 101-100=1
    assert len(emitted) == 1
    w = emitted[0]
    assert w.mfe == Decimal("3")             # ref - min_low
    assert w.mae == Decimal("1")             # max_high - ref
    assert w.reached_target is False         # low 97 > target 96


def test_multiple_windows_independent():
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="A", kind="trade", side="long", ref=Decimal("100"), target=None, window_bars=1)
    t.open(key="B", kind="trade", side="short", ref=Decimal("200"), target=None, window_bars=2)
    t.on_bar(_bar("100", "101", "99", "100"))
    assert [w.key for w in emitted] == ["A"]   # only A completes
    t.on_bar(_bar("200", "201", "198", "199"))
    assert [w.key for w in emitted] == ["A", "B"]


# ── stop-aware temporal classification (stop_hit + outcome) ──────────────

def test_stopped_then_target_long_is_flagged():
    """Stop hit first, target reached later → the too-tight-stop case."""
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="ST", kind="trade", side="long", ref=Decimal("100"),
           stop=Decimal("98"), target=Decimal("104"), window_bars=2)
    t.on_bar(_bar("100", "101", "97", "100"))   # low 97 <= stop 98  → stop hit bar1
    t.on_bar(_bar("100", "105", "100", "104"))  # high 105 >= tgt 104 → target hit bar2
    w = emitted[0]
    assert w.stop_hit is True
    assert w.reached_target is True
    assert w.outcome == "stopped_then_target"


def test_win_when_target_before_stop():
    """Target first, stop touched only afterward → a win (you'd have exited)."""
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="W", kind="trade", side="long", ref=Decimal("100"),
           stop=Decimal("98"), target=Decimal("104"), window_bars=2)
    t.on_bar(_bar("100", "104", "99", "103"))   # target hit bar1, no stop
    t.on_bar(_bar("103", "104", "97", "98"))    # stop hit bar2 (after target)
    w = emitted[0]
    assert w.outcome == "win"
    assert w.stop_hit is True            # stop WAS touched, just later


def test_loss_when_stop_and_target_never():
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="L", kind="trade", side="long", ref=Decimal("100"),
           stop=Decimal("98"), target=Decimal("110"), window_bars=2)
    t.on_bar(_bar("100", "101", "97", "99"))    # stop hit bar1
    t.on_bar(_bar("99", "102", "99", "101"))    # target 110 never reached
    w = emitted[0]
    assert w.outcome == "loss"
    assert w.reached_target is False


def test_no_resolution_when_neither_hit():
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="N", kind="trade", side="long", ref=Decimal("100"),
           stop=Decimal("90"), target=Decimal("110"), window_bars=1)
    t.on_bar(_bar("100", "101", "99", "100"))
    assert emitted[0].outcome == "no_resolution"


def test_within_bar_tie_is_pessimistic_stopped_then_target():
    """One bar spans both stop and target → can't know order; assume stop first."""
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="TIE", kind="trade", side="long", ref=Decimal("100"),
           stop=Decimal("98"), target=Decimal("104"), window_bars=1)
    t.on_bar(_bar("100", "105", "97", "101"))   # high 105 >= tgt AND low 97 <= stop
    assert emitted[0].outcome == "stopped_then_target"


def test_short_stop_hit_detection():
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="S", kind="trade", side="short", ref=Decimal("100"),
           stop=Decimal("102"), target=Decimal("96"), window_bars=2)
    t.on_bar(_bar("100", "103", "100", "102"))  # high 103 >= stop 102 → stop hit bar1
    t.on_bar(_bar("102", "102", "95", "96"))    # low 95 <= tgt 96 → target hit bar2
    w = emitted[0]
    assert w.stop_hit is True
    assert w.outcome == "stopped_then_target"


def test_stop_none_means_no_stop_hit():
    emitted = []
    t = ExcursionTracker(emit=emitted.append)
    t.open(key="NS", kind="rejection", side="long", ref=Decimal("100"),
           stop=None, target=Decimal("104"), window_bars=1)
    t.on_bar(_bar("100", "105", "97", "104"))   # target hit; stop is None
    w = emitted[0]
    assert w.stop_hit is False
    assert w.outcome == "win"
