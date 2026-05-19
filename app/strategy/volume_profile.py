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


class VolumeProfileTracker:
    """
    Per-instrument session tracker. Feed every bar via on_bar(); call
    apply() on any signal before the pretrade gate.

    Session boundary is detected by UTC date change in bar timestamps.
    On first day (no prior session yet), apply() is a no-op.
    """

    def __init__(self) -> None:
        self._bins: dict[Decimal, int] = {}
        self._session_date: date | None = None
        self._prior: VolumeProfile | None = None

    # ------------------------------------------------------------------
    # Bar ingestion
    # ------------------------------------------------------------------

    def on_bar(self, bar: Bar, cfg: StrategyParams) -> None:
        """Feed a closed bar. Detects session boundaries and accumulates volume."""
        bar_date = bar.ts.date()

        if self._session_date is None:
            self._session_date = bar_date
        elif bar_date != self._session_date:
            # Session ended — finalize prior profile.
            self._prior = _compute_profile(
                self._bins,
                self._session_date,
                cfg.vp_value_area_pct,
                cfg.vp_hvn_threshold,
            )
            if self._prior:
                log.info(
                    "VP: prior session %s — POC=%.2f VAH=%.2f VAL=%.2f HVNs=%d",
                    self._session_date,
                    float(self._prior.poc),
                    float(self._prior.vah),
                    float(self._prior.val),
                    len(self._prior.hvns),
                )
            self._bins = {}
            self._session_date = bar_date

        self._accumulate(bar, cfg.vp_tick_size)

    def _accumulate(self, bar: Bar, tick_size: Decimal) -> None:
        """Distribute bar volume uniformly across the high-low price range."""
        if bar.volume == 0:
            return

        # Quantize low and high to nearest tick boundary.
        low_bin = (bar.low / tick_size).to_integral_value(rounding=ROUND_FLOOR) * tick_size
        high_bin = (bar.high / tick_size).to_integral_value(rounding=ROUND_HALF_UP) * tick_size

        bins_in_range: list[Decimal] = []
        p = low_bin
        while p <= high_bin:
            bins_in_range.append(p)
            p += tick_size

        if not bins_in_range:
            return

        vol_per_bin = max(1, bar.volume // len(bins_in_range))
        for p in bins_in_range:
            self._bins[p] = self._bins.get(p, 0) + vol_per_bin

    # ------------------------------------------------------------------
    # Read-only
    # ------------------------------------------------------------------

    def has_prior_profile(self) -> bool:
        return self._prior is not None
