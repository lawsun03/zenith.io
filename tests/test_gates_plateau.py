"""Gate 3 — parameter plateau."""
from __future__ import annotations

import pytest

from research.gates.plateau import PLATEAU_MIN_NEIGHBOR_RATIO, evaluate


def test_flat_plateau_passes():
    axes = [[1, 2, 3]]
    surface = {(1,): 0.9, (2,): 1.0, (3,): 0.95}
    result = evaluate(surface, axes)
    assert result.passed
    assert result.threshold == PLATEAU_MIN_NEIGHBOR_RATIO


def test_isolated_spike_fails():
    axes = [[1, 2, 3]]
    surface = {(1,): 0.05, (2,): 1.0, (3,): 0.05}
    result = evaluate(surface, axes)
    assert not result.passed
    assert result.measured == pytest.approx(0.05)


def test_non_positive_peak_fails_without_dividing_by_zero():
    axes = [[1, 2]]
    surface = {(1,): -0.5, (2,): -0.1}
    result = evaluate(surface, axes)
    assert not result.passed
    assert result.measured == 0.0


def test_2d_grid_neighbors():
    axes = [[1, 2], [10, 20]]
    surface = {
        (1, 10): 0.8, (1, 20): 0.9,
        (2, 10): 1.0, (2, 20): 0.85,
    }
    # Best point (2,10)=1.0; neighbors (1,10)=0.8 and (2,20)=0.85 -> avg 0.825.
    result = evaluate(surface, axes)
    assert result.measured == pytest.approx(0.825)


def test_empty_surface_raises():
    with pytest.raises(ValueError):
        evaluate({}, [[1]])
