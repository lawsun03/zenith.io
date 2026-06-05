"""
Armed zone state machine — manages pending iFVG entries.

An ArmedZone is created when an iFVG inversion is confirmed. It tracks
the entry price (mode-dependent), stop price (beyond iFVG extreme), and
CE (Consequent Encroachment = midpoint, always computed for journaling).

The zone remains active until:
  - The entry price is reached (filled)
  - A bar closes back through the iFVG far edge in the original direction (invalidated)
  - It is cancelled externally (e.g. opposing liquidity taken before entry)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.broker.events import Bar

log = logging.getLogger(__name__)

EntryMode = Literal["ifvg_edge", "retrace_ce", "close"]
ArmStatus = Literal["filled", "invalidated", "pending"]


@dataclass(frozen=True)
class ArmedZone:
    """An iFVG zone that has been armed for entry."""
    side: Literal["long", "short"]
    fvg_low: Decimal           # iFVG zone bottom
    fvg_high: Decimal          # iFVG zone top
    box_boundary: Decimal      # ifvg_edge entry: long=fvg_high, short=fvg_low
    ce: Decimal                # (fvg_low + fvg_high) / 2 — always computed
    entry_mode: EntryMode
    entry_price: Decimal       # the limit price to arm
    stop_price: Decimal        # beyond iFVG extreme + buffer
    created_at: datetime
    killzone: str
    tp1_price: Decimal | None = None  # nearest HTF swing in trade direction — premature-liquidity cancel


class ArmedZoneTracker:
    """
    Per-instrument state machine tracking one active armed zone at a time.

    Usage:
        tracker = ArmedZoneTracker()
        zone = tracker.arm(side, fvg_low, fvg_high, entry_mode,
                           stop_buffer, created_at, killzone)
        for bar in bars:
            status = tracker.on_bar(bar)
            if status == "filled":
                # execute entry at zone.entry_price
                pass
            elif status == "invalidated":
                # discard
                pass
        tracker.cancel()  # external cancel
    """

    def __init__(self) -> None:
        self._active: ArmedZone | None = None

    @property
    def active(self) -> ArmedZone | None:
        return self._active

    def arm(
        self,
        side: Literal["long", "short"],
        fvg_low: Decimal,
        fvg_high: Decimal,
        entry_mode: EntryMode,
        stop_buffer: Decimal,
        created_at: datetime,
        killzone: str,
        sweep_extreme: Decimal | None = None,
    ) -> ArmedZone:
        """
        Create an ArmedZone from an iFVG zone.

        box_boundary:
          - long: fvg_high (ceiling — price enters from above on retrace down)
          - short: fvg_low (floor — price enters from below on retrace up)

        ce = (fvg_low + fvg_high) / 2  — always computed regardless of mode

        entry_price:
          - ifvg_edge: box_boundary
          - retrace_ce: ce
          - close: box_boundary (caller treats this as immediate fill)

        stop_price (beyond the sweep wick that took liquidity, so a normal
        retest of the wick doesn't stop the trade — matches composer.py's path):
          - long:  min(sweep_extreme, fvg_low)  - stop_buffer
          - short: max(sweep_extreme, fvg_high) + stop_buffer
          - sweep_extreme omitted/None → falls back to the iFVG edge. The
            min/max guard keeps the stop from ever landing *tighter* than the
            iFVG edge on a degenerate sweep inside the FVG.
        """
        ce = (fvg_low + fvg_high) / 2

        if side == "long":
            box_boundary = fvg_high
            stop_anchor = min(sweep_extreme, fvg_low) if sweep_extreme is not None else fvg_low
            stop_price = stop_anchor - stop_buffer
        else:
            box_boundary = fvg_low
            stop_anchor = max(sweep_extreme, fvg_high) if sweep_extreme is not None else fvg_high
            stop_price = stop_anchor + stop_buffer

        if entry_mode == "retrace_ce":
            entry_price = ce
        else:
            entry_price = box_boundary

        zone = ArmedZone(
            side=side,
            fvg_low=fvg_low,
            fvg_high=fvg_high,
            box_boundary=box_boundary,
            ce=ce,
            entry_mode=entry_mode,
            entry_price=entry_price,
            stop_price=stop_price,
            created_at=created_at,
            killzone=killzone,
        )
        self._active = zone
        log.info(
            "ArmedZone armed: %s %s zone [%s-%s], entry=%s, stop=%s, mode=%s",
            killzone, side, fvg_low, fvg_high, entry_price, stop_price, entry_mode,
        )
        return zone

    def on_bar(self, bar: Bar) -> ArmStatus | None:
        """
        Process one closed bar against the active zone.

        Returns:
          "filled"      — bar traded through the entry_price
          "invalidated" — bar CLOSED back through the far edge (body-only; wick doesn't count)
          "pending"     — zone still live
          None          — no active zone

        Invalidation rule (body-close, not wick):
          long zone:  bar.close < fvg_low  (close back below bottom of bullish iFVG)
          short zone: bar.close > fvg_high (close back above top of bearish iFVG)
        """
        if self._active is None:
            return None

        zone = self._active

        # Check invalidation first (body-close, not wick).
        # If the bar closes through the far edge the zone is dead regardless of whether
        # the bar also traded through entry — a bar that blows through the entire zone
        # is an invalidation event, not a fill. Checking close before low/high prevents
        # treating a gap-through candle as a valid entry.
        if zone.side == "long" and bar.close < zone.fvg_low:
            log.info("ArmedZone invalidated: %s long — close %s < fvg_low %s",
                     zone.killzone, bar.close, zone.fvg_low)
            self._active = None
            return "invalidated"
        if zone.side == "short" and bar.close > zone.fvg_high:
            log.info("ArmedZone invalidated: %s short — close %s > fvg_high %s",
                     zone.killzone, bar.close, zone.fvg_high)
            self._active = None
            return "invalidated"

        # Check fill: did price trade through entry_price?
        if zone.side == "long" and bar.low <= zone.entry_price:
            log.info("ArmedZone filled: %s long @ %s", zone.killzone, zone.entry_price)
            self._active = None
            return "filled"
        if zone.side == "short" and bar.high >= zone.entry_price:
            log.info("ArmedZone filled: %s short @ %s", zone.killzone, zone.entry_price)
            self._active = None
            return "filled"

        return "pending"

    def cancel(self) -> None:
        """External cancel — e.g. opposing liquidity taken before entry fills."""
        if self._active is not None:
            log.info("ArmedZone cancelled: %s %s", self._active.killzone, self._active.side)
        self._active = None
