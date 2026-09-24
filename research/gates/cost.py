"""Gate 2 — cost survival (docs/research-loop/gates.md, docs/research-loop/cost-model.md).

PASS if mean_edge_per_trade >= 2.0 x modelled_round_turn_cost. The 2x
headroom (not 1x) is deliberate: cost-model.md is explicit that every
slippage assumption below is optimistic.

Commission and exchange/clearing fees are NOT hardcoded here — cost-model.md
says outright to "verify against your broker's current schedule before
trusting a gate-2 result," and inventing a specific dollar figure that isn't
in that doc would be worse than requiring the caller to supply one. Only the
slippage-ticks-by-condition table, which cost-model.md DOES give numerically,
is a constant here.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Literal

from research.gates.types import GateResult

COST_HEADROOM_MIN = 2.0

Condition = Literal[
    "limit_normal", "market_normal", "stop", "first_minute_rth", "near_macro_release",
]

# Slippage ticks PER SIDE, cost-model.md "Baseline slippage" table.
SLIPPAGE_TICKS: dict[Condition, Decimal] = {
    "limit_normal": Decimal("0"),
    "market_normal": Decimal("1"),
    "stop": Decimal("1.5"),
    "first_minute_rth": Decimal("2"),
    "near_macro_release": Decimal("3"),
}

# cost-model.md "Human latency penalty": 1 additional tick per side, entry
# only, and only when entry is discretionary-triggered (not a resting order).
HUMAN_LATENCY_TICKS_PER_SIDE = Decimal("1")


def round_turn_cost(
    *,
    condition: Condition,
    tick_value: Decimal,
    commission_and_fees_per_side: Decimal,
    discretionary_entry: bool = True,
) -> Decimal:
    slip = SLIPPAGE_TICKS[condition]
    cost = commission_and_fees_per_side * 2 + (slip * 2) * tick_value
    if discretionary_entry:
        cost += HUMAN_LATENCY_TICKS_PER_SIDE * tick_value
    return cost


def cost_in_sharpe_units(
    annual_cost_currency: Decimal, account_equity: Decimal, vol_target_annual: Decimal,
) -> Decimal:
    """cost-model.md "Express cost in Sharpe units": comparable across
    instruments of different volatility, unlike a raw currency figure."""
    return annual_cost_currency / (account_equity * vol_target_annual)


def evaluate(mean_edge_per_trade: Decimal, modelled_round_turn_cost: Decimal) -> GateResult:
    if modelled_round_turn_cost <= 0:
        raise ValueError("modelled_round_turn_cost must be positive")
    ratio = float(mean_edge_per_trade / modelled_round_turn_cost)
    return GateResult(
        gate=2, passed=ratio >= COST_HEADROOM_MIN,
        measured=ratio, threshold=COST_HEADROOM_MIN,
    )
