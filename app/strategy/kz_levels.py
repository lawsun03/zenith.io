"""
Killzone session H/L sweep detector + runner.

KillzoneLevelTracker: tracks the high and low of each named killzone session
(London, NY AM, etc.) while it is open, locks the range once the session
closes, then detects pattern-A and pattern-B sweeps of those levels. Feeds
SweepEvent objects to the composer alongside LiquidityTracker.

KZLevelsRunner: duck-type of StrategyRunner that uses KillzoneLevelTracker as
the sweep source instead of LiquidityTracker. The same SweepDisplacementComposer
and DisplacementDetector handle the iFVG confirmation step.

Design: pure module — bars in, SweepEvents / Signals out. No I/O, no broker.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

from dataclasses import replace as dc_replace

from app.sim.events import Bar
from app.strategy.composer import Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementDetector
from app.strategy.grader import SetupGrader
from app.strategy.killzone import Killzone, default_killzones, in_killzone
from app.strategy.liquidity import Swing, SweepEvent

if TYPE_CHECKING:
    from app.bot_config import StrategyParams

log = logging.getLogger(__name__)


class KillzoneLevelTracker:
    """Per-instrument killzone H/L sweep detector.

    Tracks session ranges, locks them when the session closes, then emits
    SweepEvent objects (same type as LiquidityTracker) when those levels are
    swept in a later session.
    """

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

    def locked_ranges(self) -> "dict[str, tuple[Decimal, Decimal]]":
        """Session ranges that have closed/locked this day. {name: (high, low)}. Pure read."""
        return dict(self._kz_ranges)

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
        # Only emit (and consume) levels when the current bar is inside a trading
        # window. Sweeps that occur in the gap between sessions (e.g., 05:00–08:30 ET
        # between London close and NY AM open) would be ignored by the composer's
        # killzone gate AND would consume the level before the session opens.
        # Keeping levels alive across the gap lets NY AM sweep London ranges, which
        # is the core mechanism this engine is designed to trade.
        min_pen = cfg.min_penetration
        window = cfg.multi_bar_window
        in_trading = in_killzone(bar.ts, zones) is not None
        events: list[SweepEvent] = []

        for name, (kz_high, kz_low) in list(self._kz_ranges.items()):
            high_key = f"{name}_high"
            low_key = f"{name}_low"

            # ----- Pattern B: one-bar sweep (high side) -----
            if bar.high >= kz_high + min_pen and bar.close < kz_high:
                if in_trading:
                    log.info("KZ sweep: %s high B_one_bar (extreme: %s -> level: %s)", name, bar.high, kz_high)
                    events.append(self._make_sweep(bar, "high", kz_high, bar.high, "B_one_bar"))
                    del self._kz_ranges[name]
                    self._pending_a.pop(high_key, None)
                    self._bars_since_tag.pop(high_key, None)
                continue  # skip low-side checks for this level on this bar

            # ----- Pattern B: one-bar sweep (low side) -----
            if bar.low <= kz_low - min_pen and bar.close > kz_low:
                if in_trading:
                    log.info("KZ sweep: %s low B_one_bar (extreme: %s -> level: %s)", name, bar.low, kz_low)
                    events.append(self._make_sweep(bar, "low", kz_low, bar.low, "B_one_bar"))
                    del self._kz_ranges[name]
                    self._pending_a.pop(low_key, None)
                    self._bars_since_tag.pop(low_key, None)
                continue

            # ----- Pattern A: multi-bar (high side) -----
            if high_key in self._pending_a:
                self._bars_since_tag[high_key] = self._bars_since_tag.get(high_key, 0) + 1
                if bar.close < kz_high:
                    if in_trading:
                        extreme = self._pending_a.pop(high_key)
                        self._bars_since_tag.pop(high_key, None)
                        log.info("KZ sweep: %s high A_multi_bar (extreme: %s -> level: %s)", name, extreme, kz_high)
                        events.append(self._make_sweep(bar, "high", kz_high, extreme, "A_multi_bar"))
                        del self._kz_ranges[name]
                    else:
                        # Close-back confirmed outside trading window: clear tag,
                        # keep level alive for the next trading session.
                        self._pending_a.pop(high_key, None)
                        self._bars_since_tag.pop(high_key, None)
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
                    if in_trading:
                        extreme = self._pending_a.pop(low_key)
                        self._bars_since_tag.pop(low_key, None)
                        log.info("KZ sweep: %s low A_multi_bar (extreme: %s -> level: %s)", name, extreme, kz_low)
                        events.append(self._make_sweep(bar, "low", kz_low, extreme, "A_multi_bar"))
                        del self._kz_ranges[name]
                    else:
                        self._pending_a.pop(low_key, None)
                        self._bars_since_tag.pop(low_key, None)
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
            sweep_bar=bar,
        )


class _NoopComposer:
    """Stop-fill hook the engine calls; KZLevels uses the real composer for this."""

    def on_stop_loss(self) -> None:
        pass


@dataclass
class KZLevelsRunner:
    """
    Duck-type of the StrategyRunner surface ExecutionEngine/run_backtest touch.

    Replaces LiquidityTracker with KillzoneLevelTracker as the sweep source;
    the rest of the iFVG chain (DisplacementDetector + SweepDisplacementComposer
    + SetupGrader) is unchanged. Skips the armed-zone path (default-off anyway).
    """

    instrument: str
    timeframe: str
    kz_tracker: KillzoneLevelTracker
    displacement: DisplacementDetector
    composer: SweepDisplacementComposer
    grader: SetupGrader
    strategy_cfg: "StrategyParams"
    zones: list[Killzone] = field(default_factory=default_killzones)
    vp: None = None                       # engine skips VP when None
    signal_instrument: str = ""
    last_reject: None = field(default=None, init=False)
    _prev_atr: Decimal | None = field(default=None, init=False, repr=False)

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """Run KZ sweep + displacement + composition. Returns at most one Signal."""
        self.last_reject = None

        # 1. KZ sweep detection (replaces LiquidityTracker)
        kz_sweeps = self.kz_tracker.on_bar(bar, self.zones, self.strategy_cfg)
        for sw in kz_sweeps:
            self.composer.on_sweep(bar, sw)

        # 2. Displacement detection + signal composition
        disp = self.displacement.on_bar(bar)
        signal: Optional[Signal] = None
        if disp is not None:
            candidate = self.composer.on_displacement(bar, disp)
            if candidate is not None:
                grade = self.grader.score(
                    candidate,
                    disp,
                    self.displacement.active_fvgs,
                    bars_since_sweep=0,
                    sweep_window_bars=self.strategy_cfg.ifvg_sweep_window_bars,
                    min_displacement_mult=self.strategy_cfg.ifvg_min_displacement_mult,
                    min_grade=self.strategy_cfg.grader_min_grade,
                    gapping_sack_enabled=self.strategy_cfg.ifvg_gapping_sack_enabled,
                )
                if grade.passes:
                    signal = dc_replace(candidate, setup_grade=grade)

        # 3. Bookkeeping
        self._prev_atr = self.displacement.atr
        kz = in_killzone(bar.ts, self.composer._zones)
        self.grader.update_session_range(bar, kz.name if kz else None)
        self.composer.on_bar_close(bar)

        return signal
