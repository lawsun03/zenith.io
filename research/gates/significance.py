"""Gate 6 — deflated Sharpe ratio / PBO (docs/research-loop/gates.md).

Two independent conditions the table folds into one gate:

  1. "Fails significance given trial count" — the deflated Sharpe ratio
     (research.stats.deflated_sharpe) must clear DSR_MIN_PROBABILITY.
     gates.md gives no explicit number for this; 0.95 is the standard
     confidence threshold Bailey & Lopez de Prado's own worked examples use
     for "significant," stated here as this implementation's judgment call
     (CLAUDE.md rule 1), not a value copied from the spec.
  2. "loop-level PBO > 0.5, which blocks ALL promotion" — a LOOP-level
     condition, not per-candidate: pass `loop_pbo` (the current CSCV result
     over the whole loop's variant population, research.stats.cscv) and a
     PBO above PBO_LOOP_BLOCK_THRESHOLD fails every candidate's gate 6
     regardless of its own deflated Sharpe, for as long as it holds.
"""
from __future__ import annotations

from research.gates.types import GateResult
from research.stats.deflated_sharpe import deflated_sharpe_ratio

DSR_MIN_PROBABILITY = 0.95
PBO_LOOP_BLOCK_THRESHOLD = 0.5


def loop_level_pbo_blocks_promotion(loop_pbo: float | None) -> bool:
    return loop_pbo is not None and loop_pbo > PBO_LOOP_BLOCK_THRESHOLD


def evaluate(
    observed_sr: float, n_trials: int, skew: float, kurtosis: float, n_obs: int,
    *, sr_variance_across_trials: float | None = None, loop_pbo: float | None = None,
) -> GateResult:
    """`sr_variance_across_trials` is V[{SR_n}] in the same per-period units
    as `observed_sr` — required whenever n_trials > 1 (the DSR raises
    otherwise). Like `loop_pbo` it is a property of the whole search, not
    of this candidate, so the caller computes it once per loop state:
    research.stats.deflated_sharpe.empirical_sr_variance over the recorded
    trials, or null_sr_variance(n_obs) while too few exist to measure it."""
    dsr = deflated_sharpe_ratio(
        observed_sr, n_trials, skew, kurtosis, n_obs,
        sr_variance_across_trials=sr_variance_across_trials,
    )
    passed = dsr >= DSR_MIN_PROBABILITY and not loop_level_pbo_blocks_promotion(loop_pbo)
    return GateResult(gate=6, passed=passed, measured=dsr, threshold=DSR_MIN_PROBABILITY)
