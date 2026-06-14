"""
State-dependent risk scaling policies for combine/funded simulation.

Each function returns a multiplier (relative to base_risk_pct) that scales
each day's P&L in the simulator. Only the PATH distribution changes —
per-trade expectancy and PF are invariant by construction.

Two declared policies (B50):
- combine_ramp: ramp up early, protect gains, survive near MLL
- funded_survival: full size when clear of MLL, reduced when near MLL
"""
from __future__ import annotations

from decimal import Decimal


_COMBINE_STARTING = Decimal("50000")


def combine_ramp_multiplier(
    balance: Decimal,
    mll: Decimal | None,
    base_risk_pct: Decimal,
    *,
    ramp_risk_pct: Decimal = Decimal("1.5"),
    protect_risk_pct: Decimal = Decimal("0.75"),
    survival_risk_pct: Decimal = Decimal("0.5"),
    gain_threshold: Decimal = Decimal("1500"),
    mll_cushion: Decimal = Decimal("750"),
) -> Decimal:
    """
    combine_ramp policy: build-then-protect.

    Phases (survival check takes priority):
    1. Survival: balance within mll_cushion of MLL floor → risk survival_risk_pct
    2. Protect: cumulative gain >= gain_threshold → risk protect_risk_pct
    3. Ramp: gain < gain_threshold → risk ramp_risk_pct

    Returns a multiplier = selected_risk / base_risk_pct.
    """
    if mll is not None and (balance - mll) <= mll_cushion:
        return survival_risk_pct / base_risk_pct
    gain = balance - _COMBINE_STARTING
    if gain >= gain_threshold:
        return protect_risk_pct / base_risk_pct
    return ramp_risk_pct / base_risk_pct


def funded_survival_multiplier(
    balance: Decimal,
    mll: Decimal | None,
    base_risk_pct: Decimal,
    *,
    normal_risk_pct: Decimal = Decimal("0.75"),
    survival_risk_pct: Decimal = Decimal("0.4"),
    mll_cushion: Decimal = Decimal("750"),
) -> Decimal:
    """
    funded_survival policy: full size when clear, reduced when near MLL.

    Returns a multiplier = selected_risk / base_risk_pct.
    """
    if mll is not None and (balance - mll) <= mll_cushion:
        return survival_risk_pct / base_risk_pct
    return normal_risk_pct / base_risk_pct
