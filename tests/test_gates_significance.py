"""Gate 6 — deflated Sharpe / PBO."""
from __future__ import annotations

from research.gates.significance import (
    DSR_MIN_PROBABILITY, PBO_LOOP_BLOCK_THRESHOLD, evaluate, loop_level_pbo_blocks_promotion,
)
import pytest

from research.stats.deflated_sharpe import deflated_sharpe_ratio, null_sr_variance

# V[{SR_n}] under the zero-skill null for a 2000-trade sample.
V_NULL_2000 = null_sr_variance(2000)


def test_high_confidence_passes():
    # Large n_obs, tiny n_trials, healthy skew/kurtosis -> DSR close to 1.
    dsr = deflated_sharpe_ratio(0.15, 2, 0.0, 3.0, 2000, sr_variance_across_trials=V_NULL_2000)
    assert dsr >= DSR_MIN_PROBABILITY
    result = evaluate(0.15, 2, 0.0, 3.0, 2000, sr_variance_across_trials=V_NULL_2000)
    assert result.passed
    assert result.measured == dsr
    assert result.threshold == DSR_MIN_PROBABILITY


def test_thin_evidence_fails():
    result = evaluate(0.05, 50, 0.0, 3.0, 30, sr_variance_across_trials=null_sr_variance(30))
    assert not result.passed


def test_loop_level_pbo_blocks_an_otherwise_passing_candidate():
    good = evaluate(0.15, 2, 0.0, 3.0, 2000, sr_variance_across_trials=V_NULL_2000, loop_pbo=None)
    blocked = evaluate(0.15, 2, 0.0, 3.0, 2000, sr_variance_across_trials=V_NULL_2000, loop_pbo=0.6)
    assert good.passed
    assert not blocked.passed
    assert loop_level_pbo_blocks_promotion(0.6)
    assert not loop_level_pbo_blocks_promotion(0.5)  # boundary: > 0.5, not >=
    assert loop_level_pbo_blocks_promotion(PBO_LOOP_BLOCK_THRESHOLD + 0.001)


def test_gate_refuses_to_run_without_the_cross_trial_variance():
    """With more than one trial, gate 6 cannot be evaluated without
    V[{SR_n}]. It must raise rather than quietly fall back to the SR
    sampling variance, which overstates significance (0.98 vs the paper's
    0.90 on its worked example). A gate that cannot be evaluated is a
    failure, not a pass (CLAUDE.md rule 12)."""
    with pytest.raises(ValueError):
        evaluate(0.15, 2, 0.0, 3.0, 2000)
