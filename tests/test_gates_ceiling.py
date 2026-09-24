"""Gate 7 — Sharpe ceiling: a hold, not a "the strategy is bad" rejection,
but still short-circuits the pipeline (see research/gates/ceiling.py)."""
from __future__ import annotations

from research.gates.ceiling import SHARPE_CEILING, evaluate


def test_below_ceiling_passes():
    result = evaluate(1.2)
    assert result.passed
    assert result.threshold == SHARPE_CEILING


def test_at_ceiling_passes():
    assert evaluate(SHARPE_CEILING).passed


def test_above_ceiling_fails():
    result = evaluate(2.0)
    assert not result.passed
    assert result.measured == 2.0
