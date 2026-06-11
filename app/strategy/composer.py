"""
Sweep + displacement composer — turns detector events into trade signals.

State machine per instrument:

    IDLE
      └─ on SweepEvent (in killzone) → SWEPT
                                            └─ on DisplacementEvent
                                                  (opposite direction,
                                                   within window)
                                                  → SIGNAL
                                            └─ on K bars elapsed → IDLE

The directional logic:
  - Sweep on the HIGH side took out buy-stops above old highs. The
    classic reversal direction is SHORT, so we want a BEARISH
    displacement to confirm.
  - Sweep on the LOW side took out sell-stops below old lows. We want
    a BULLISH displacement to confirm a LONG.

A signal includes:
  - side (long/short)
  - entry zone (the FVG; market or limit-on-retrace is up to the
    execution engine)
  - stop (just past the sweep extreme — the level price already proved
    it wouldn't hold)
  - target (configurable: fixed R-multiple or opposing liquidity)

What this module does NOT do:
  - Place orders. It emits Signals; the execution engine acts.
  - Apply risk gates. The pretrade module does that.
  - Track FVG fills or signal expiry post-emission. Once emitted, the
    signal is the engine's problem.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from app.strategy.grader import SetupGrade
    from app.strategy.armed_zone import ArmedZone

from app.broker.events import Bar
from app.risk.pretrade import Side

from .displacement import DisplacementDetector, DisplacementEvent, FairValueGap
from .killzone import Killzone, default_killzones, in_killzone
from .liquidity import LiquidityTracker, SweepEvent, Swing

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Signal:
    """
    A tradeable setup. The execution engine consumes these.

    `entry` is the suggested price to fill. For FVG-retrace entries
    this is the *near* edge of the FVG (top for bearish, bottom for
    bullish), but the engine may turn this into a limit order at the
    far edge to improve fill quality. That's an engine concern.
    """

    instrument: str
    side: Side
    entry: Decimal
    stop: Decimal
    target: Decimal
    created_at: datetime
    killzone: str
    sweep_pattern: str           # for journal: "A_multi_bar" or "B_one_bar"
    sweep_extreme: Decimal       # the level price tried to take
    fvg_low: Decimal | None      # for the dashboard / journal
    fvg_high: Decimal | None
    rationale: str               # human-readable, one line
    # Manipulation bar range (high - low of the bar that set the sweep
    # extreme) — the grader's fib-extension denominator. None only for
    # hand-built signals (e.g. DEBUG force-signal), which then grade fib=0.
    sweep_bar_range: Decimal | None = None
    setup_grade: "SetupGrade | None" = None
    armed_zone: "ArmedZone | None" = None
    ce: Decimal | None = None


@dataclass
class ComposerConfig:
    """Composer parameters."""

    instrument: str

    # Bars after a sweep that we still consider for displacement
    # confirmation. Past this, the sweep "ages out" and the state
    # returns to IDLE.
    displacement_window_bars: int = 5

    # Stop placement: ticks past the sweep extreme. Buffer for noise.
    stop_buffer: Decimal = Decimal("0.30")  # /MGC = 3 ticks

    # Target: fixed R-multiple. R = |entry - stop|. 2R = take 2x risk.
    # Override in the future to use opposing liquidity instead.
    r_multiple: Decimal = Decimal("2.0")

    # Killzones to honor. None = use default (London/NY AM/NY PM).
    killzones: list[Killzone] | None = None

    # Trend EMA filter. N-period EMA on bar closes; only long signals
    # emit when close > EMA, only short when close < EMA. 0 = disabled.
    # Warmup: filter is inactive until N bars have been seen.
    trend_ema_period: int = 50

    # Bars to suppress new signals after a stop fill. 0 = disabled.
    cooldown_bars_after_stop: int = 0

    # ATR volatility gates. 0 = disabled (same convention as trend_ema_period).
    # min_atr_filter: skip entries when ATR is too low (dead market, no momentum).
    # max_atr_filter: skip entries when ATR is too high (whipsaw / news spike).
    min_atr_filter: Decimal = Decimal("0")
    max_atr_filter: Decimal = Decimal("0")

    # Swing stop: look back N bars and use min-low / max-high as stop anchor
    # instead of the immediate sweep extreme. 0 = disabled.
    swing_stop_lookback: int = 0


@dataclass
class _Awaiting:
    """Internal: a sweep has fired; we're waiting for displacement."""

    sweep: SweepEvent
    bars_since_sweep: int
    killzone_name: str


class SweepDisplacementComposer:
    """
    Composes sweep + displacement events into Signals.

    Note: this is the state machine ONLY. It does not run the detectors
    itself; the strategy harness wires LiquidityTracker and
    DisplacementDetector to it. That's the standard composable design:
    each module does one thing, the harness wires them.

    Wiring:
        composer = SweepDisplacementComposer(config)
        liquidity = LiquidityTracker(...)
        displacement = DisplacementDetector(...)

        for bar in bar_stream:
            sweeps = liquidity.on_bar(bar)
            disp = displacement.on_bar(bar)
            for s in sweeps:
                composer.on_sweep(bar, s)
            if disp is not None:
                signal = composer.on_displacement(bar, disp)
                if signal:
                    yield signal
            composer.on_bar_close(bar)  # for window bookkeeping
    """

    def __init__(self, config: ComposerConfig) -> None:
        self.config = config
        self._zones = config.killzones or default_killzones()
        self._awaiting: list[_Awaiting] = []
        self._ema: Decimal | None = None
        self._ema_bars: int = 0
        self._cooldown_remaining: int = 0
        _buf = max(1, config.swing_stop_lookback) if config.swing_stop_lookback > 0 else 1
        self._bar_lows: deque[Decimal] = deque(maxlen=_buf)
        self._bar_highs: deque[Decimal] = deque(maxlen=_buf)

    # ------------------------------------------------------------------
    # Read-only — for tests and dashboards.
    # ------------------------------------------------------------------

    @property
    def awaiting(self) -> list[SweepEvent]:
        return [a.sweep for a in self._awaiting]

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def on_sweep(self, bar: Bar, sweep: SweepEvent) -> None:
        """
        Record a sweep. Only if we're in a killzone — sweeps outside
        trading hours are ignored at this layer (the detectors don't
        know or care about hours).
        """
        zone = in_killzone(bar.ts, self._zones)
        if zone is None:
            return  # outside trading windows
        # Don't track duplicate awaitings on the same swing.
        if any(a.sweep.swept_swing is sweep.swept_swing for a in self._awaiting):
            return
        self._awaiting.append(_Awaiting(
            sweep=sweep,
            bars_since_sweep=0,
            killzone_name=zone.name,
        ))

    def on_stop_loss(self) -> None:
        """Called by the engine when a stop fill is confirmed for this instrument."""
        if self.config.cooldown_bars_after_stop > 0:
            self._cooldown_remaining = self.config.cooldown_bars_after_stop

    def on_displacement(
        self,
        bar: Bar,
        event: DisplacementEvent,
    ) -> Signal | None:
        """
        If displacement direction matches a pending sweep's expected
        reversal, emit a Signal and clear the awaiting state.

        Returns at most one Signal per call. If multiple awaitings could
        match, we take the most recent — that's the freshest setup.
        """
        if event.fvg is None:
            return None  # no entry zone, no trade

        if self._cooldown_remaining > 0:
            log.info("Cooldown active (%d bars remaining) — signal suppressed", self._cooldown_remaining)
            return None

        # Volatility regime filter: skip entries outside the configured ATR range.
        if self.config.min_atr_filter > 0 and event.atr_at_event < self.config.min_atr_filter:
            log.info(
                "Signal blocked: ATR %s below min_atr_filter %s (low-vol regime)",
                event.atr_at_event, self.config.min_atr_filter,
            )
            return None
        if self.config.max_atr_filter > 0 and event.atr_at_event > self.config.max_atr_filter:
            log.info(
                "Signal blocked: ATR %s above max_atr_filter %s (high-vol regime)",
                event.atr_at_event, self.config.max_atr_filter,
            )
            return None

        # Required reversal direction for each sweep side:
        #   high sweep → bearish displacement → SHORT
        #   low sweep  → bullish displacement → LONG
        wanted: dict[str, str] = {"high": "bearish", "low": "bullish"}

        # Trend filter: block counter-trend signals once the EMA has warmed up.
        period = self.config.trend_ema_period
        trend_active = (
            period > 0
            and self._ema is not None
            and self._ema_bars >= period
        )

        for awaiting in reversed(self._awaiting):
            if wanted[awaiting.sweep.side] != event.side:
                continue

            if trend_active:
                assert self._ema is not None
                is_long = event.side == "bullish"
                if is_long and bar.close < self._ema:
                    log.info(
                        "Trend filter: long signal blocked "
                        "(close=%s < EMA%d=%s) — skipping",
                        bar.close, period,
                        self._ema.quantize(Decimal("0.01")),
                    )
                    self._awaiting = []
                    return None
                if not is_long and bar.close > self._ema:
                    log.info(
                        "Trend filter: short signal blocked "
                        "(close=%s > EMA%d=%s) — skipping",
                        bar.close, period,
                        self._ema.quantize(Decimal("0.01")),
                    )
                    self._awaiting = []
                    return None

            signal = self._build_signal(bar, awaiting, event)
            self._awaiting = []
            return signal

        return None

    def on_bar_close(self, bar: Bar) -> None:
        """
        Bookkeeping: increment bar counters, expire old sweeps, update EMA.

        Call this AFTER on_sweep/on_displacement for the bar — otherwise
        a sweep that fires on bar N would be aged by 1 immediately.
        """
        self._bar_lows.append(bar.low)
        self._bar_highs.append(bar.high)

        if self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1

        window = self.config.displacement_window_bars
        kept: list[_Awaiting] = []
        for a in self._awaiting:
            a_aged = _Awaiting(
                sweep=a.sweep,
                bars_since_sweep=a.bars_since_sweep + 1,
                killzone_name=a.killzone_name,
            )
            if a_aged.bars_since_sweep < window:
                kept.append(a_aged)
        self._awaiting = kept

        period = self.config.trend_ema_period
        if period > 0:
            self._ema_bars += 1
            if self._ema is None:
                self._ema = bar.close
            else:
                alpha = Decimal(2) / (Decimal(period) + 1)
                self._ema = alpha * bar.close + (1 - alpha) * self._ema

    # ------------------------------------------------------------------
    # Signal construction
    # ------------------------------------------------------------------

    def _build_signal(
        self,
        bar: Bar,
        awaiting: _Awaiting,
        event: DisplacementEvent,
    ) -> Signal:
        cfg = self.config
        fvg = event.fvg
        assert fvg is not None  # guarded by caller

        lookback = cfg.swing_stop_lookback
        if event.side == "bullish":
            side: Side = "long"
            entry = fvg.high
            if lookback > 0 and self._bar_lows:
                swing_anchor = min(self._bar_lows)
                stop_anchor = min(swing_anchor, awaiting.sweep.sweep_extreme)
            else:
                stop_anchor = awaiting.sweep.sweep_extreme
            stop = stop_anchor - cfg.stop_buffer
            r = entry - stop
            target = entry + r * cfg.r_multiple
        else:
            side = "short"
            entry = fvg.low
            if lookback > 0 and self._bar_highs:
                swing_anchor = max(self._bar_highs)
                stop_anchor = max(swing_anchor, awaiting.sweep.sweep_extreme)
            else:
                stop_anchor = awaiting.sweep.sweep_extreme
            stop = stop_anchor + cfg.stop_buffer
            r = stop - entry
            target = entry - r * cfg.r_multiple

        rationale = (
            f"{awaiting.killzone_name}: "
            f"{awaiting.sweep.pattern} sweep of {awaiting.sweep.side} "
            f"@ {awaiting.sweep.swept_swing.price}, "
            f"{event.side} displacement "
            f"({event.body_to_atr:.2f}× ATR), "
            f"FVG {fvg.low}–{fvg.high}"
        )

        return Signal(
            instrument=cfg.instrument,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            created_at=bar.ts,
            killzone=awaiting.killzone_name,
            sweep_pattern=awaiting.sweep.pattern,
            sweep_extreme=awaiting.sweep.sweep_extreme,
            fvg_low=fvg.low,
            fvg_high=fvg.high,
            rationale=rationale,
            sweep_bar_range=awaiting.sweep.sweep_bar.high - awaiting.sweep.sweep_bar.low,
        )
