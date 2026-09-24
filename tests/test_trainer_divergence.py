"""Divergence tracking tests (PHASE-PROMPTS.md Phase 6, requirement 4).

Bars are built by hand so stop/target touches are unambiguous — the point
is to check the equity-curve and fraction-taken arithmetic, not to
exercise the IR predicate engine (that's test_ir_engine.py's job).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.sim.events import Bar
from research.gates.ensemble import build_ensemble
from research.ir.engine import DecisionPoint
from research.trainer.divergence import compute_divergence
from research.trainer.sampling import Candidate
from research.trainer.scoring import Answer, GroundTruth, score_decision
from research.trainer.sessions import DecisionRecord

BASE_TS = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)
TICK = Decimal("0.25")

_IR_DOC = {
    "ir_version": "1.0", "name": "divergence fixture", "instruments": ["NQ"],
    "target": {"type": "r_multiple", "multiple": 2.0},
}


def _bar(minute: int, o, h, l, c) -> Bar:
    return Bar(instrument="NQ", timeframe="1min", ts=BASE_TS + timedelta(minutes=minute),
               open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=100)


def _ensemble():
    return build_ensemble("fam", [("hyp-1", _IR_DOC)])


def _record(minute: int, *, fired: bool, side=None, entry=None, stop=None, target=None,
            answer: Answer) -> DecisionRecord:
    point = DecisionPoint(
        ts=BASE_TS + timedelta(minutes=minute), fired=fired, near_miss=False,
        side=side, entry_price=entry, stop_price=stop, target_price=target,
    )
    candidate = Candidate(hypothesis_id="hyp-1", instrument="NQ", regime_label=None, point=point)
    truth = GroundTruth(fired=fired, side=side, stop_price=stop)
    score = score_decision(truth, answer, tick_size=TICK)
    return DecisionRecord(candidate=candidate, answer=answer, score=score)


def test_fraction_taken_counts_fired_decisions_the_trainee_also_called():
    records = [
        _record(0, fired=True, side="long", entry=Decimal("100"), stop=Decimal("99"),
                target=Decimal("102"),
                answer=Answer(is_setup=True, direction="long", stop_price=Decimal("99"))),
        _record(10, fired=True, side="long", entry=Decimal("100"), stop=Decimal("99"),
                target=Decimal("102"),
                answer=Answer(is_setup=False)),
        _record(20, fired=False, answer=Answer(is_setup=False)),
    ]
    bars_by_instrument = {"NQ": [_bar(m, "100", "101", "99.5", "100") for m in range(0, 31)]}
    result = compute_divergence(_ensemble(), records, bars_by_instrument)
    assert result.trades_available == 2
    assert result.trades_taken == 1
    assert result.fraction_taken == Decimal("1") / Decimal("2")


def test_strategy_equity_curve_reflects_stop_and_target_touches():
    # Trade 1: long from 100, stop 99, target 102 -> target touches at minute 2 (+1R... target is 2R away, so +2)
    bars = [_bar(0, "100", "100", "100", "100")]
    bars.append(_bar(1, "100", "100", "100", "100"))
    bars.append(_bar(2, "100", "102.5", "100", "102"))  # target touch
    # Trade 2 starts at minute 10: long from 100, stop 99, target 102 -> stop touches at minute 12
    bars.append(_bar(10, "100", "100", "100", "100"))
    bars.append(_bar(11, "100", "100", "100", "100"))
    bars.append(_bar(12, "100", "100", "98.5", "99"))  # stop touch
    bars_by_instrument = {"NQ": bars}

    records = [
        _record(0, fired=True, side="long", entry=Decimal("100"), stop=Decimal("99"),
                target=Decimal("102"),
                answer=Answer(is_setup=True, direction="long", stop_price=Decimal("99"))),
        _record(10, fired=True, side="long", entry=Decimal("100"), stop=Decimal("99"),
                target=Decimal("102"),
                answer=Answer(is_setup=True, direction="long", stop_price=Decimal("99"))),
    ]
    result = compute_divergence(_ensemble(), records, bars_by_instrument)
    assert result.strategy_equity_r == [Decimal("2"), Decimal("2") - Decimal("1")]


def test_trainee_equity_curve_uses_the_trainees_own_stop_not_the_irs():
    # IR never fires here (false-positive case): trainee thinks there's a
    # long setup at minute 0 (bar close 100), picks a tight stop at 99.5.
    # Price runs straight to the trainee's own 2R target (101 away *2 = 101... risk=0.5, target=101).
    bars = [
        _bar(0, "100", "100", "100", "100"),
        _bar(1, "100", "101.5", "100", "101"),  # touches trainee's target (100 + 2*0.5 = 101)
    ]
    bars_by_instrument = {"NQ": bars}
    records = [
        _record(0, fired=False,
                answer=Answer(is_setup=True, direction="long", stop_price=Decimal("99.5"))),
    ]
    result = compute_divergence(_ensemble(), records, bars_by_instrument)
    assert result.trades_available == 0  # IR never fired -> nothing "available" to take
    assert result.trainee_equity_r == [Decimal("2")]  # trainee's own hypothetical trade still tracked


def test_no_bars_after_decision_yields_zero_r_not_an_error():
    bars_by_instrument = {"NQ": [_bar(0, "100", "100", "100", "100")]}
    records = [
        _record(0, fired=True, side="long", entry=Decimal("100"), stop=Decimal("99"),
                target=Decimal("102"),
                answer=Answer(is_setup=True, direction="long", stop_price=Decimal("99"))),
    ]
    result = compute_divergence(_ensemble(), records, bars_by_instrument)
    assert result.strategy_equity_r == [Decimal("0")]


def test_trainee_who_never_takes_a_trade_has_an_empty_equity_curve():
    records = [
        _record(0, fired=True, side="long", entry=Decimal("100"), stop=Decimal("99"),
                target=Decimal("102"), answer=Answer(is_setup=False)),
    ]
    bars_by_instrument = {"NQ": [_bar(m, "100", "100", "100", "100") for m in range(5)]}
    result = compute_divergence(_ensemble(), records, bars_by_instrument)
    assert result.trainee_equity_r == []
