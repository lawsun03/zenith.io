"""Fidelity scoring tests.

The literal acceptance test from docs/research-loop/PHASE-PROMPTS.md
Phase 6: "the market outcome is never a term in fidelity_score — assert
in a test" and "a user decision matching the IR on a losing trade scores
as correct."
"""
from __future__ import annotations

import ast
from decimal import Decimal
from pathlib import Path

from research.trainer.scoring import (
    Answer, GroundTruth, Score, fidelity_score, score_decision, tally_session,
)

TICK = Decimal("0.25")


def test_scoring_module_never_references_pnl_or_outcome() -> None:
    """Checked at the identifier level (names, attributes, function/arg
    names) — not by grepping raw text, since this module's own docstring
    necessarily *talks about* outcome/pnl in prose while correctly never
    using either as a term the score is computed from."""
    src = Path("research/trainer/scoring.py").read_text()
    tree = ast.parse(src)
    banned = ("pnl", "outcome")
    for node in ast.walk(tree):
        name = None
        if isinstance(node, ast.Name):
            name = node.id
        elif isinstance(node, ast.Attribute):
            name = node.attr
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            name = node.name
        elif isinstance(node, ast.arg):
            name = node.arg
        if name is None:
            continue
        lowered = name.lower()
        assert not any(b in lowered for b in banned), (
            f"scoring.py identifier {name!r} references market outcome — "
            "fidelity_score must be computable from IR agreement alone"
        )


def test_losing_trade_that_matches_the_ir_scores_fully_correct() -> None:
    """The whole design point: a trade that follows the IR and loses is
    CORRECT. Score depends only on truth vs. answer agreement — there is
    no pnl anywhere in this test's inputs at all, because there is none in
    score_decision's signature."""
    truth = GroundTruth(fired=True, side="long", stop_price=Decimal("100.00"))
    answer = Answer(is_setup=True, direction="long", stop_price=Decimal("100.00"))
    score = score_decision(truth, answer, tick_size=TICK)
    assert score == Score(correct_setup=True, correct_direction=True, correct_stop=True)
    assert fidelity_score([score]) == Decimal("1")


def test_winning_trade_that_contradicts_the_ir_scores_incorrect() -> None:
    """The mirror: ignoring the IR on a hunch that happens to win is WRONG.
    (This test doesn't even model a market outcome — score_decision has no
    input for one — it just shows disagreement scores as incorrect
    regardless of anything that "would have happened".)"""
    truth = GroundTruth(fired=False)
    answer = Answer(is_setup=True, direction="long", stop_price=Decimal("100.00"))
    score = score_decision(truth, answer, tick_size=TICK)
    assert score.correct_setup is False


def test_direction_and_stop_not_applicable_when_ir_declines_but_user_took_it() -> None:
    """A false positive: the IR says no setup, the user says yes. There is
    no ground-truth direction/stop to grade against a trade the IR never
    considered — direction/stop must be None (not applicable), not scored
    False by default (that would silently double-count one error as three)."""
    truth = GroundTruth(fired=False)
    answer = Answer(is_setup=True, direction="short", stop_price=Decimal("101"))
    score = score_decision(truth, answer, tick_size=TICK)
    assert score.correct_direction is None
    assert score.correct_stop is None


def test_direction_and_stop_not_applicable_when_setup_missed() -> None:
    """The IR fires, the user says no setup — a miss. Nothing to grade for
    direction/stop since the user never answered them."""
    truth = GroundTruth(fired=True, side="long", stop_price=Decimal("100"))
    answer = Answer(is_setup=False)
    score = score_decision(truth, answer, tick_size=TICK)
    assert score.correct_setup is False
    assert score.correct_direction is None
    assert score.correct_stop is None


def test_stop_within_tolerance_scores_correct() -> None:
    truth = GroundTruth(fired=True, side="long", stop_price=Decimal("100.00"))
    answer = Answer(is_setup=True, direction="long", stop_price=Decimal("100.20"))
    score = score_decision(truth, answer, tick_size=TICK)
    assert score.correct_stop is True  # 0.20 off, within the 0.25 tick tolerance


def test_stop_outside_tolerance_scores_incorrect() -> None:
    truth = GroundTruth(fired=True, side="long", stop_price=Decimal("100.00"))
    answer = Answer(is_setup=True, direction="long", stop_price=Decimal("101.00"))
    score = score_decision(truth, answer, tick_size=TICK)
    assert score.correct_stop is False


def test_fidelity_score_weights_setup_direction_stop_as_separate_sub_questions() -> None:
    correct = Score(correct_setup=True, correct_direction=True, correct_stop=True)
    setup_only_wrong_rest_na = Score(correct_setup=False, correct_direction=None, correct_stop=None)
    # 1 true positive (3/3 correct) + 1 miss (0/1 applicable) = 3 correct of 4 total
    assert fidelity_score([correct, setup_only_wrong_rest_na]) == Decimal("3") / Decimal("4")


def test_tally_session_maps_onto_drill_sessions_columns() -> None:
    decisions = [
        (GroundTruth(fired=True, side="long", stop_price=Decimal("100")),
         Answer(is_setup=True, direction="long", stop_price=Decimal("100")),
         Score(correct_setup=True, correct_direction=True, correct_stop=True)),
        (GroundTruth(fired=True, side="short", stop_price=Decimal("50")),
         Answer(is_setup=False),
         Score(correct_setup=False, correct_direction=None, correct_stop=None)),
        (GroundTruth(fired=False),
         Answer(is_setup=True, direction="long", stop_price=Decimal("10")),
         Score(correct_setup=False, correct_direction=None, correct_stop=None)),
    ]
    tally = tally_session(decisions)
    assert tally.n_decisions == 3
    assert tally.setups_correctly_taken == 1
    assert tally.setups_missed == 1
    assert tally.false_positives == 1
    assert tally.direction_errors == 0
    assert tally.stop_placement_errors == 0
