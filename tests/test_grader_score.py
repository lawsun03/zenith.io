# tests/test_grader_score.py
from decimal import Decimal
import pytest
from app.strategy.grader import SetupGrader

@pytest.fixture
def grader():
    return SetupGrader()

# --- _score_to_grade ---

def test_score_to_grade_a(grader):
    assert grader._score_to_grade(75) == "A"
    assert grader._score_to_grade(100) == "A"

def test_score_to_grade_b(grader):
    assert grader._score_to_grade(55) == "B"
    assert grader._score_to_grade(74) == "B"

def test_score_to_grade_c(grader):
    assert grader._score_to_grade(35) == "C"
    assert grader._score_to_grade(54) == "C"

def test_score_to_grade_d(grader):
    assert grader._score_to_grade(15) == "D"
    assert grader._score_to_grade(34) == "D"

def test_score_to_grade_f(grader):
    assert grader._score_to_grade(0) == "F"
    assert grader._score_to_grade(14) == "F"

# --- _compute_score ---

def test_compute_score_all_zero(grader):
    # weak momentum, no P/D, no delivery, no BPR, no target
    score = grader._compute_score(
        momentum_quality="weak",
        pd_ok=False,
        delivery=False,
        delivery_in_pd=False,
        bpr=False,
        target_clear=False,
        fib_ext=Decimal("0.5"),
    )
    assert score == 0

def test_compute_score_decent_momentum_only(grader):
    score = grader._compute_score(
        momentum_quality="decent",
        pd_ok=False,
        delivery=False,
        delivery_in_pd=False,
        bpr=False,
        target_clear=False,
        fib_ext=Decimal("0.5"),
    )
    assert score == 7  # momentum decent only

def test_compute_score_fib_partial(grader):
    score = grader._compute_score(
        momentum_quality="decent",
        pd_ok=False,
        delivery=False,
        delivery_in_pd=False,
        bpr=False,
        target_clear=True,
        fib_ext=Decimal("1.2"),
    )
    assert score == 7 + 5 + 15  # momentum=7, target=5, fib partial=15

def test_compute_score_max(grader):
    score = grader._compute_score(
        momentum_quality="strong",
        pd_ok=True,
        delivery=True,
        delivery_in_pd=True,
        bpr=True,
        target_clear=True,
        fib_ext=Decimal("1.5"),
    )
    assert score == 100  # 30+20+20+15+10+5

def test_compute_score_delivery_without_pd(grader):
    # delivery correct side but not in P/D = 10 pts
    score = grader._compute_score(
        momentum_quality="weak",
        pd_ok=False,
        delivery=True,
        delivery_in_pd=False,
        bpr=False,
        target_clear=False,
        fib_ext=Decimal("0.5"),
    )
    assert score == 10

def test_compute_score_typical_loser(grader):
    # Today's 2026-06-08 worst setup: fib=0.81x, P/D=off, no delivery, decent, no BPR, target=yes
    score = grader._compute_score(
        momentum_quality="decent",
        pd_ok=False,
        delivery=False,
        delivery_in_pd=False,
        bpr=False,
        target_clear=True,
        fib_ext=Decimal("0.81"),
    )
    assert score == 12  # momentum=7, target=5

def test_score_to_grade_typical_loser_is_f(grader):
    assert grader._score_to_grade(12) == "F"
