"""Gate 4 — walk-forward (docs/research-loop/gates.md).

Rejects when any fold's OOS return is negative, or Sharpe decay
((is - oos) / is) is worse than 60% on any fold. Folds come from the frozen
walk-forward schedule (research/data/folds.py) — this module doesn't read
that file itself; it takes each fold's already-computed fit/OOS performance,
consistent with the rest of this package (see metrics.py docstring: gates
consume pre-computed statistics, they don't run backtests).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from research.gates.types import GateResult

MAX_SHARPE_DECAY = 0.60


@dataclass(frozen=True)
class FoldResult:
    fit_sharpe: float
    oos_return: float
    oos_sharpe: float


def fold_decay(fold: FoldResult) -> float:
    if fold.fit_sharpe <= 0:
        # No in-sample edge to decay from at all — treat as total decay
        # rather than dividing by a non-positive number.
        return 1.0
    return (fold.fit_sharpe - fold.oos_sharpe) / fold.fit_sharpe


def evaluate(folds: Sequence[FoldResult]) -> GateResult:
    if not folds:
        raise ValueError("no walk-forward folds to evaluate")

    any_negative_oos = any(f.oos_return < 0 for f in folds)
    worst_decay = max(fold_decay(f) for f in folds)
    passed = (not any_negative_oos) and worst_decay <= MAX_SHARPE_DECAY

    return GateResult(gate=4, passed=passed, measured=worst_decay, threshold=MAX_SHARPE_DECAY)
