"""
Killzone session H/L sweep detector.

Tracks the high and low of each named killzone session (London, NY AM,
etc.) while it is open, locks the range once the session closes, then
detects pattern-A and pattern-B sweeps of those levels. Feeds
SweepEvent objects to the composer alongside LiquidityTracker.

Design: pure module — bars in, SweepEvents out. No I/O, no broker.

Key differences from LiquidityTracker:
  - Levels come from session ranges, not point swings.
  - Each level fires at most once per session (consumed on first sweep).
  - No lookback lag — levels are confirmed as soon as the session closes.
  - Synthetic Swing: bar_ts == confirmed_ts (no lookback delay).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from app.broker.events import Bar
from app.strategy.killzone import Killzone, in_killzone
from app.strategy.liquidity import Swing, SweepEvent

if TYPE_CHECKING:
    from app.bot_config import StrategyParams

log = logging.getLogger(__name__)


class KillzoneLevelTracker:
    """Per-instrument killzone H/L sweep detector."""

    def __init__(self) -> None:
        # Finalized (high, low) per killzone name for today.
        self._kz_ranges: dict[str, tuple[Decimal, Decimal]] = {}
        # H/L accumulating for currently-open killzones.
        self._active_accum: dict[str, tuple[Decimal, Decimal]] = {}
        # UTC date of last bar; triggers full reset on change.
        self._session_date: date | None = None
        # Pattern A state: level_key -> extreme price that tagged it.
        self._pending_a: dict[str, Decimal] = {}
        # Bars elapsed since tag (for window timeout).
        self._bars_since_tag: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def on_bar(
        self,
        bar: Bar,
        zones: list[Killzone],
        cfg: "StrategyParams",
    ) -> list[SweepEvent]:
        """Process a closed bar. Returns any new sweep events."""

        # 1. Daily reset on UTC date change.
        bar_date = bar.ts.astimezone(timezone.utc).date()
        if bar_date != self._session_date:
            self._kz_ranges = {}
            self._active_accum = {}
            self._pending_a = {}
            self._bars_since_tag = {}
            self._session_date = bar_date

        # 2. Accumulate H/L for active zones.
        for zone in zones:
            active = in_killzone(bar.ts, [zone]) is not None
            if active:
                prev = self._active_accum.get(zone.name)
                if prev is None:
                    self._active_accum[zone.name] = (bar.high, bar.low)
                else:
                    self._active_accum[zone.name] = (
                        max(prev[0], bar.high),
                        min(prev[1], bar.low),
                    )

        # 3. Finalize closed zones that haven't been finalized yet.
        for zone in zones:
            if (
                zone.name in self._active_accum
                and zone.name not in self._kz_ranges
                and in_killzone(bar.ts, [zone]) is None
            ):
                high, low = self._active_accum.pop(zone.name)
                self._kz_ranges[zone.name] = (high, low)
                log.debug(
                    "KZ level locked: %s high=%s low=%s",
                    zone.name, high, low,
                )

        # 4. Sweep detection.
        min_pen = cfg.min_penetration
        window = cfg.multi_bar_window
        events: list[SweepEvent] = []

        for name, (kz_high, kz_low) in list(self._kz_ranges.items()):
            high_key = f"{name}_high"
            low_key = f"{name}_low"

            # ----- Pattern B: one-bar sweep (high side) -----
            if bar.high >= kz_high + min_pen and bar.close < kz_high:
                log.info("KZ sweep: %s high B_one_bar (extreme: %s → level: %s)", name, bar.high, kz_high)
                events.append(self._make_sweep(bar, "high", kz_high, bar.high, "B_one_bar"))
                del self._kz_ranges[name]
                self._pending_a.pop(high_key, None)
                self._bars_since_tag.pop(high_key, None)
                continue  # level consumed; skip low check for this name

            # ----- Pattern B: one-bar sweep (low side) -----
            if bar.low <= kz_low - min_pen and bar.close > kz_low:
                log.info("KZ sweep: %s low B_one_bar (extreme: %s → level: %s)", name, bar.low, kz_low)
                events.append(self._make_sweep(bar, "low", kz_low, bar.low, "B_one_bar"))
                del self._kz_ranges[name]
                self._pending_a.pop(low_key, None)
                self._bars_since_tag.pop(low_key, None)
                continue

            # ----- Pattern A: multi-bar (high side) -----
            if high_key in self._pending_a:
                self._bars_since_tag[high_key] = self._bars_since_tag.get(high_key, 0) + 1
                if bar.close < kz_high:
                    extreme = self._pending_a.pop(high_key)
                    self._bars_since_tag.pop(high_key, None)
                    log.info("KZ sweep: %s high A_multi_bar (extreme: %s → level: %s)", name, extreme, kz_high)
                    events.append(self._make_sweep(bar, "high", kz_high, extreme, "A_multi_bar"))
                    del self._kz_ranges[name]
                    continue
                elif self._bars_since_tag.get(high_key, 0) >= window:
                    self._pending_a.pop(high_key, None)
                    self._bars_since_tag.pop(high_key, None)
            elif bar.high >= kz_high + min_pen and bar.close >= kz_high:
                # Tag without close-back — start pattern A tracking.
                self._pending_a[high_key] = bar.high
                self._bars_since_tag[high_key] = 0
                log.info("KZ pattern A tag: %s high @ %s (bar extreme: %s)", name, kz_high, bar.high)

            # ----- Pattern A: multi-bar (low side) -----
            if low_key in self._pending_a:
                self._bars_since_tag[low_key] = self._bars_since_tag.get(low_key, 0) + 1
                if bar.close > kz_low:
                    extreme = self._pending_a.pop(low_key)
                    self._bars_since_tag.pop(low_key, None)
                    log.info("KZ sweep: %s low A_multi_bar (extreme: %s → level: %s)", name, extreme, kz_low)
                    events.append(self._make_sweep(bar, "low", kz_low, extreme, "A_multi_bar"))
                    del self._kz_ranges[name]
                    continue
                elif self._bars_since_tag.get(low_key, 0) >= window:
                    self._pending_a.pop(low_key, None)
                    self._bars_since_tag.pop(low_key, None)
            elif bar.low <= kz_low - min_pen and bar.close <= kz_low:
                self._pending_a[low_key] = bar.low
                self._bars_since_tag[low_key] = 0
                log.info("KZ pattern A tag: %s low @ %s (bar extreme: %s)", name, kz_low, bar.low)

        return events

    @staticmethod
    def _make_sweep(
        bar: Bar,
        side: str,
        level: Decimal,
        extreme: Decimal,
        pattern: str,
    ) -> SweepEvent:
        synthetic_swing = Swing(
            kind=side,          # type: ignore[arg-type]
            price=level,
            bar_ts=bar.ts,
            confirmed_ts=bar.ts,
        )
        return SweepEvent(
            side=side,          # type: ignore[arg-type]
            swept_swing=synthetic_swing,
            pattern=pattern,    # type: ignore[arg-type]
            sweep_extreme=extreme,
            completed_at=bar.ts,
        )
