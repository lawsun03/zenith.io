"""Combinatorially Symmetric Cross-Validation (Bailey, Borwein, Lopez de
Prado & Zhu, 2015), producing a Probability of Backtest Overfitting (PBO).

Gate 6 (docs/research-loop/gates.md) blocks ALL promotion loop-wide when
PBO > 0.5 — this is the calculation behind that number.

Method: split the T-period performance matrix into S contiguous, equal-length
blocks. For every way of choosing S/2 blocks as a training set (the
complementary S/2 blocks form the test set — this enumerates all
C(S, S/2) combinatorial splits, hence "combinatorially symmetric"):

  1. Pick the strategy variant with the best in-sample performance.
  2. Find that same variant's rank among all variants' out-of-sample
     performance.
  3. Convert the relative rank to a logit. A well-generalizing variant has a
     logit centered above zero (it's still good out-of-sample); an
     overfit one is centered at/below zero (in-sample-best is unremarkable,
     or bad, out-of-sample).

PBO is the fraction of splits where that logit is <= 0.
"""
from __future__ import annotations

import itertools
import math
from typing import Callable

import numpy as np

Metric = Callable[[np.ndarray], float]


def sharpe_metric(returns: np.ndarray) -> float:
    """Per-period Sharpe ratio (mean / sample std). Not annualized — CSCV
    only needs a consistent ranking metric, not a comparable-to-gate-5 scale.

    A flat (zero-variance) block is defined to have Sharpe 0.0 rather than
    raising or producing NaN/inf, since "no variation" is a valid, rankable
    outcome (worse than any positive-mean block, tied with any other flat
    block) — this is a deliberate simplification, not a silently-defaulted
    gate.
    """
    std = returns.std(ddof=1)
    if std == 0:
        return 0.0
    return float(returns.mean() / std)


def probability_of_backtest_overfitting(
    returns: np.ndarray,
    n_splits: int = 16,
    metric: Metric = sharpe_metric,
) -> float:
    """Compute PBO over a (T, N) matrix of aligned per-period returns.

    Args:
        returns: shape (T, N) — T time periods (rows) x N strategy variants
            (columns) being compared against each other, same periods for
            every column.
        n_splits: S, the number of contiguous blocks to split the T periods
            into. Must be even (so each combinatorial split has an equal
            train/test size) and must evenly divide T (CSCV requires
            equal-length blocks, not an approximately-equal split that would
            bias which periods land in which block).
        metric: performance metric applied to each block x variant slice.
            Defaults to per-period Sharpe ratio.

    Returns:
        PBO in [0, 1]: the fraction of the C(S, S/2) combinatorial splits in
        which the in-sample-best variant performed at or below the OOS
        median.

    Raises:
        ValueError: on a shape/parameter combination CSCV cannot run on
            (fewer than 2 variants, odd or non-divisor n_splits).
    """
    returns = np.asarray(returns, dtype=float)
    if returns.ndim != 2:
        raise ValueError(f"returns must be 2D (T, N), got shape {returns.shape}")
    n_periods, n_variants = returns.shape
    if n_variants < 2:
        raise ValueError("need at least 2 strategy variants to rank against each other")
    if n_splits < 2 or n_splits % 2 != 0:
        raise ValueError(f"n_splits must be a positive even integer, got {n_splits}")
    if n_periods % n_splits != 0:
        raise ValueError(
            f"{n_periods} periods not evenly divisible by n_splits={n_splits}; "
            "CSCV requires equal-length blocks"
        )

    blocks = np.array_split(returns, n_splits, axis=0)
    half = n_splits // 2

    logits: list[float] = []
    for train_idx in itertools.combinations(range(n_splits), half):
        test_idx = tuple(i for i in range(n_splits) if i not in train_idx)
        train = np.concatenate([blocks[i] for i in train_idx], axis=0)
        test = np.concatenate([blocks[i] for i in test_idx], axis=0)

        is_perf = np.array([metric(train[:, j]) for j in range(n_variants)])
        oos_perf = np.array([metric(test[:, j]) for j in range(n_variants)])

        # Ties broken by first occurrence (np.argmax) — deterministic, not
        # random, so a given input always reproduces the same PBO.
        n_star = int(np.argmax(is_perf))

        # Ordinal rank of n_star's OOS performance among all N variants,
        # 1..N. Ties count generously (<=), matching the paper's treatment.
        rank = int((oos_perf <= oos_perf[n_star]).sum())
        omega = rank / (n_variants + 1)
        logits.append(math.log(omega / (1.0 - omega)))

    n_at_or_below_median = sum(1 for lam in logits if lam <= 0)
    return n_at_or_below_median / len(logits)
