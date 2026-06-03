from decimal import Decimal
from datetime import datetime, timezone
from app.broker.events import Bar
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
