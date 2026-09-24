"""Gate 5 — Sharpe floor, by trial count."""
from __future__ import annotations

from research.gates.sharpe_floor import evaluate
from research.stats.cutoff_table import sr_cutoff


def test_above_cutoff_passes():
    cutoff = sr_cutoff(10, 10)  # table: 0.8
    result = evaluate(cutoff + 0.1, 10, 10)
    assert result.passed
    assert result.threshold == cutoff


def test_below_cutoff_fails():
    cutoff = sr_cutoff(10, 10)
    result = evaluate(cutoff - 0.1, 10, 10)
    assert not result.passed


def test_exact_cutoff_passes():
    cutoff = sr_cutoff(50, 10)
    result = evaluate(cutoff, 50, 10)
    assert result.passed
