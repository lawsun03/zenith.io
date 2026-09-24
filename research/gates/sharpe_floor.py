"""Gate 5 — Sharpe floor, by trial count (docs/research-loop/gates.md).

Thin wrapper over research.stats.cutoff_table.sr_cutoff: rejects when the
observed OOS Sharpe is below the cutoff for the trial count in force at
test time. `n_trials` must be the ledger's variant-weighted trial count
(research.ledger.api.trial_count_at), not a row count (CLAUDE.md rule 7).
"""
from __future__ import annotations

from research.gates.types import GateResult
from research.stats.cutoff_table import sr_cutoff


def evaluate(sharpe_oos: float, n_trials: float, years: float) -> GateResult:
    cutoff = sr_cutoff(n_trials, years)
    return GateResult(gate=5, passed=sharpe_oos >= cutoff, measured=sharpe_oos, threshold=cutoff)
