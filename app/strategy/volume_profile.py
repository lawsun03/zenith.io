"""
Volume profile filter and target selector.

Builds a prior-session volume profile from OHLC bars and provides two
services to the execution engine:
  1. Filter — reject signals whose FVG entry is clearly outside the prior
     session value area (plus a configurable tolerance band).
  2. Target — replace the fixed R-multiple target with the nearest VP level
     (POC, VAH/VAL, or HVN) that delivers at least the configured minimum R.

Design: pure module — bars in, profile state updated. No I/O, no broker
dependency. The engine wires this alongside the existing strategy stack.
"""

from __future__ import annotations

import dataclasses
import logging
from datetime import date
from decimal import Decimal, ROUND_HALF_UP, ROUND_FLOOR

from app.broker.events import Bar
from app.bot_config import StrategyParams
from app.strategy.composer import Signal

log = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class VolumeProfile:
    """Finalized single-session volume profile."""

    session_date: date
    poc: Decimal        # price level with highest volume
    vah: Decimal        # value area high (upper bound of value_area_pct of volume)
    val: Decimal        # value area low
    hvns: list[Decimal] # sorted high-volume nodes (volume > mean × hvn_threshold)
    total_volume: int


def _compute_profile(
    bins: dict[Decimal, int],
    session_date: date,
    value_area_pct: float,
    hvn_threshold: float,
) -> VolumeProfile | None:
    """
    Compute POC, VAH/VAL, and HVNs from accumulated bin data.

    Value area algorithm: start from POC, expand outward one bin at a
    time always taking the higher-volume neighbor (upper on ties), until
    cumulative volume >= total * value_area_pct.
    """
    if not bins:
        return None
    total = sum(bins.values())
    if total == 0:
        return None

    poc = max(bins, key=lambda p: bins[p])

    sorted_prices = sorted(bins.keys())
    poc_idx = sorted_prices.index(poc)

    included: set[Decimal] = {poc}
    cumulative = bins[poc]
    target_vol = total * value_area_pct

    lo_idx = poc_idx - 1
    hi_idx = poc_idx + 1

    while cumulative < target_vol:
        lo_vol = bins[sorted_prices[lo_idx]] if lo_idx >= 0 else -1
        hi_vol = bins[sorted_prices[hi_idx]] if hi_idx < len(sorted_prices) else -1

        if lo_vol < 0 and hi_vol < 0:
            break

        if hi_vol >= lo_vol:  # tie goes to upper
            included.add(sorted_prices[hi_idx])
            cumulative += hi_vol
            hi_idx += 1
        else:
            included.add(sorted_prices[lo_idx])
            cumulative += lo_vol
            lo_idx -= 1

    vah = max(included)
    val = min(included)

    mean_vol = total / len(bins)
    hvns = sorted(p for p, v in bins.items() if v > mean_vol * hvn_threshold)

    return VolumeProfile(
        session_date=session_date,
        poc=poc,
        vah=vah,
        val=val,
        hvns=hvns,
        total_volume=total,
    )
