"""Predicate evaluator tests.

CLAUDE.md rule 9: "Predicates are pure functions of a bar window... assert
in tests that no predicate reads a bar at an index beyond the current
one." The property test below is that assertion: feeding the same prefix
of bars must produce the same sequence of results regardless of what
bars, if any, are fed afterward — i.e. nothing a predicate emits at bar i
can depend on bar i+1, i+2, ....
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from decimal import Decimal

from hypothesis import given, settings
from hypothesis import strategies as st

from app.sim.events import Bar
from research.ir.predicates import (
    DisplacementLeaf, EvalCtx, FvgLeaf, LevelTracker, SweepOfLeaf,
    compile_predicate,
)

BASE_TS = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)


def bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(instrument="TEST", timeframe="1min", ts=BASE_TS + timedelta(minutes=i),
               open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=100)


def _ctx() -> EvalCtx:
    return EvalCtx(levels=LevelTracker(session_start=time(8, 30)))


# ----------------------------------------------------------------------
# No-lookahead property test
# ----------------------------------------------------------------------

_bar_strategy = st.tuples(
    st.decimals(min_value="90", max_value="110", places=2),
    st.decimals(min_value="0", max_value="5", places=2),   # range above open
    st.decimals(min_value="0", max_value="5", places=2),   # range below open
).map(lambda t: (t[0], t[0] + t[1], t[0] - t[2]))


@given(closes=st.lists(_bar_strategy, min_size=5, max_size=40))
@settings(max_examples=60)
def test_sweep_of_no_lookahead(closes: list[tuple[Decimal, Decimal, Decimal]]) -> None:
    bars = [bar(i, str(o), str(h), str(l), str(o)) for i, (o, h, l) in enumerate(closes)]
    _assert_prefix_invariant(
        lambda: SweepOfLeaf("swing_low", n=2, min_atr=Decimal("0.1"), within_bars=5, atr_period=3),
        bars,
    )


@given(closes=st.lists(_bar_strategy, min_size=5, max_size=40))
@settings(max_examples=60)
def test_displacement_no_lookahead(closes: list[tuple[Decimal, Decimal, Decimal]]) -> None:
    bars = [bar(i, str(o), str(h), str(l), str(o)) for i, (o, h, l) in enumerate(closes)]
    _assert_prefix_invariant(
        lambda: DisplacementLeaf("either", min_atr=Decimal("0.5"), atr_period=3,
                                  min_absolute_body=Decimal("0.1")),
        bars,
    )


@given(closes=st.lists(_bar_strategy, min_size=5, max_size=40))
@settings(max_examples=60)
def test_fvg_no_lookahead(closes: list[tuple[Decimal, Decimal, Decimal]]) -> None:
    bars = [bar(i, str(o), str(h), str(l), str(o)) for i, (o, h, l) in enumerate(closes)]
    _assert_prefix_invariant(lambda: FvgLeaf("either"), bars)


def _assert_prefix_invariant(make_leaf, bars: list[Bar]) -> None:
    """Running the leaf over bars[:k] must give the same fired sequence as
    running it over the full list and looking only at the first k results
    — i.e. results up to bar k-1 never change no matter what comes after."""
    ctx = _ctx()
    full_leaf = make_leaf()
    full_results = [full_leaf.on_bar(b, ctx) for b in bars]

    for k in range(1, len(bars) + 1):
        prefix_leaf = make_leaf()
        prefix_ctx = _ctx()
        prefix_results = [prefix_leaf.on_bar(b, prefix_ctx) for b in bars[:k]]
        assert prefix_results == full_results[:k], (
            f"result at prefix length {k} changed when future bars were added — "
            "predicate read ahead of the current bar"
        )


# ----------------------------------------------------------------------
# Exact-semantics unit tests, reusing the iFVG fixture (also used by
# test_ir_ifvg_bitidentical.py against the real hand-coded chain)
# ----------------------------------------------------------------------

def _ifvg_fixture_bars() -> list[Bar]:
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
        (22, 97.3, 99.5, 97.2, 99.3),   # displacement bar (confirmed one bar later)
        (23, 99.3, 99.6, 99.1, 99.4),   # confirms idx22
    ]
    return [bar(i, str(o), str(h), str(l), str(c)) for i, o, h, l, c in rows]


# The hand-coded strategy's actual fixed min_penetration default
# (app/bot_config.py StrategyParams.min_penetration) — see
# test_ir_ifvg_bitidentical.py for the full bit-identical comparison.
_MIN_PENETRATION = Decimal("0.20")


def test_sweep_of_fires_on_pattern_b_one_bar_sweep() -> None:
    leaf = SweepOfLeaf("swing_low", n=2, min_offset=_MIN_PENETRATION, within_bars=5)
    ctx = _ctx()
    fired_at = None
    for i, b in enumerate(_ifvg_fixture_bars()):
        if leaf.on_bar(b, ctx):
            fired_at = i
    assert fired_at == 21
    assert leaf.last_extreme == Decimal("95.5")


def test_displacement_and_fvg_confirm_one_bar_after_the_impulsive_bar() -> None:
    """The displacement/inversion event references bar2 of a trailing 3-bar
    window but only fires once bar3 has closed — a structural one-bar
    confirmation lag, not a discretionary filter (you cannot trust an
    unclosed bar's high/low/close)."""
    disp = DisplacementLeaf("up", min_atr=Decimal("1.0"))
    fvg = FvgLeaf("up")
    ctx = _ctx()
    disp_fired_at = fvg_fired_at = None
    for i, b in enumerate(_ifvg_fixture_bars()):
        if disp.on_bar(b, ctx):
            disp_fired_at = i
        if fvg.on_bar(b, ctx):
            fvg_fired_at = i
    assert disp_fired_at == 23
    assert fvg_fired_at == 23
    assert fvg.last_zone == (Decimal("97.8"), Decimal("98.8"))


def test_and_combinator_reproduces_the_arm_and_wait_state_machine() -> None:
    """sweep_of arms at bar 21 (persistence window 5); displacement+fvg
    both fire at bar 23 -> AND fires at 23, not before and not after."""
    tree = compile_predicate({
        "op": "and",
        "operands": [
            {"op": "sweep_of", "level": "swing_low", "n": 2,
             "min_offset": str(_MIN_PENETRATION), "within_bars": 5, "recognizable": True},
            {"op": "displacement", "direction": "up", "min_atr": 1.0, "recognizable": True},
            {"op": "fvg", "direction": "up", "recognizable": True},
        ],
    })
    ctx = _ctx()
    fired_bars = [i for i, b in enumerate(_ifvg_fixture_bars()) if tree.on_bar(b, ctx)]
    assert fired_bars == [23]


def test_sweep_expires_after_within_bars_window() -> None:
    """If displacement+fvg never confirm within the persistence window, the
    AND never fires (sweep_of's arming decays)."""
    tree = compile_predicate({
        "op": "and",
        "operands": [
            {"op": "sweep_of", "level": "swing_low", "n": 2,
             "min_offset": str(_MIN_PENETRATION), "within_bars": 1, "recognizable": True},
            {"op": "displacement", "direction": "up", "min_atr": 1.0, "recognizable": True},
            {"op": "fvg", "direction": "up", "recognizable": True},
        ],
    })
    ctx = _ctx()
    fired_bars = [i for i, b in enumerate(_ifvg_fixture_bars()) if tree.on_bar(b, ctx)]
    assert fired_bars == []


def test_sweep_of_requires_exactly_one_of_min_atr_or_min_offset() -> None:
    import pytest

    with pytest.raises(ValueError, match="exactly one"):
        SweepOfLeaf("swing_low")  # neither given
    with pytest.raises(ValueError, match="exactly one"):
        SweepOfLeaf("swing_low", min_atr=Decimal("0.1"), min_offset=Decimal("0.2"))  # both given


def test_cross_of_session_open() -> None:
    from research.ir.predicates import CrossOfLeaf

    ctx = _ctx()
    leaf = CrossOfLeaf(level="session_open", direction="up")
    bars = [
        bar(0, "100", "100.2", "99.8", "100.0"),   # 08:30 ET -> session_open = 100.0
        bar(1, "99.9", "100.1", "99.5", "99.7"),   # closes below open
        bar(2, "99.7", "101.5", "99.6", "101.2"),  # crosses up through 100.0
    ]
    fired = []
    for i, b in enumerate(bars):
        ctx.levels.on_bar(b)
        fired.append(leaf.on_bar(b, ctx))
    assert fired == [False, False, True]
