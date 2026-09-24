"""Gate 4 — walk-forward."""
from __future__ import annotations

import pytest

from research.gates.walk_forward import FoldResult, MAX_SHARPE_DECAY, evaluate, fold_decay


def test_healthy_folds_pass():
    folds = [
        FoldResult(fit_sharpe=1.0, oos_return=0.05, oos_sharpe=0.9),
        FoldResult(fit_sharpe=0.8, oos_return=0.02, oos_sharpe=0.7),
    ]
    result = evaluate(folds)
    assert result.passed
    assert result.measured == pytest.approx(0.125)  # worst: (0.8-0.7)/0.8


def test_any_negative_oos_return_fails():
    folds = [
        FoldResult(fit_sharpe=1.0, oos_return=0.05, oos_sharpe=0.9),
        FoldResult(fit_sharpe=1.0, oos_return=-0.01, oos_sharpe=0.9),
    ]
    assert not evaluate(folds).passed


def test_decay_worse_than_60_percent_fails():
    folds = [FoldResult(fit_sharpe=1.0, oos_return=0.01, oos_sharpe=0.3)]
    result = evaluate(folds)
    assert not result.passed
    assert result.measured == pytest.approx(0.7)
    assert result.threshold == MAX_SHARPE_DECAY


def test_non_positive_fit_sharpe_is_total_decay():
    assert fold_decay(FoldResult(fit_sharpe=0.0, oos_return=0.0, oos_sharpe=0.5)) == 1.0


def test_empty_folds_raises():
    with pytest.raises(ValueError):
        evaluate([])
