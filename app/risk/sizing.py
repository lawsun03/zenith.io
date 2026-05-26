"""
Risk-based position sizing — pure, deterministic.

size = floor( (equity × risk_pct%) ÷ (stop_distance × point_value) )
clamped to [1, max_size]. Floor-to-1 means a stop so wide that one contract
already exceeds the budget still takes one contract (per design decision).

Pure function: no I/O, no state. Price/risk arithmetic is deterministic Python
(never delegated to a model) — see CLAUDE.md Rule 5.
"""
from __future__ import annotations

from decimal import Decimal


def risk_based_size(
    equity: Decimal,
    risk_pct: Decimal,        # percent units, e.g. Decimal("0.25") == 0.25%
    stop_distance: Decimal,   # price points between entry and stop, > 0
    point_value: Decimal,     # dollars per 1-point move per contract, > 0
    max_size: int,            # account max_contracts (hard cap)
) -> int:
    """Contracts to trade so dollar risk ≈ equity × risk_pct%, capped at max_size."""
    if stop_distance <= 0 or point_value <= 0:
        raise ValueError("stop_distance and point_value must be positive")
    budget = equity * (risk_pct / Decimal("100"))
    risk_per_contract = stop_distance * point_value
    raw = int(budget // risk_per_contract)   # Decimal floor division, then int
    return max(1, min(raw, max_size))
