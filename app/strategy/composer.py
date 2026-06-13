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

    # Price-normalized stop buffer (B6). 0 = use fixed stop_buffer.
    # >0 = buffer = stop_anchor * pct. Calibration: 3.0pts / 21000 ≈ 0.000143.
    stop_buffer_pct: Decimal = Decimal("0")

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

    # Side gate: "both" | "long" | "short". Non-matching signals suppressed
    # at emission (ablation T1 — long-only test).
    allowed_sides: str = "both"

    # Stop-width cap in ATR multiples (0 = off): swing-anchored stop too far
    # → fall back to sweep-extreme anchor; still too far → no trade.
    max_stop_atr: Decimal = Decimal("0")

    # "ifvg" | "displacement_only" | "ob_fallback" — see StrategyParams.
    # ob_fallback: iFVG signals unchanged; when displacement fires WITHOUT an
    # FVG inversion, the last opposite-direction candle (the order block)
    # supplies the entry zone instead. Strict superset of iFVG signals.
    confirmation: str = "ifvg"

    # Inversion bar quality gate (B16): the displacement bar's body must be at
    # least this fraction of the planned stop distance to fire a signal.
    # 0 = disabled (default). 0.15 = body >= 15% of stop (blocks doji inversions).
    inversion_min_body_r: Decimal = Decimal("0")


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
        # Telemetry: total sweeps armed this process (EOD summary heartbeat —
        # distinguishes "detector saw nothing" from "gates blocked everything").
        self.sweeps_armed_total: int = 0

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
        self.sweeps_armed_total += 1

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
            if self.config.confirmation == "displacement_only":
                pass  # T5 semantics: no zone needed
            elif self.config.confirmation == "ob_fallback":
                if self._ob_bar(event) is None:
                    return None  # no opposite-candle order block — no zone
            else:
                return None  # no entry zone, no trade

        if self.config.allowed_sides != "both":
            want_side = "long" if event.side == "bullish" else "short"
            if want_side != self.config.allowed_sides:
                log.info(
                    "Signal blocked: %s side disabled (allowed_sides=%s)",
                    want_side, self.config.allowed_sides,
                )
                return None

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

            # Inversion bar quality gate (B16): body must be >= min_body_r × stop_dist.
            _cfg = self.config
            if _cfg.inversion_min_body_r > 0 and event.fvg is not None:
                _fvg = event.fvg
                _sweep_ext = awaiting.sweep.sweep_extreme
                _buf = (_sweep_ext * _cfg.stop_buffer_pct
                        if _cfg.stop_buffer_pct > 0
                        else _cfg.stop_buffer)
                _stop_dist = (_fvg.high - (_sweep_ext - _buf) if event.side == "bullish"
                              else (_sweep_ext + _buf) - _fvg.low)
                if _stop_dist > 0 and event.body_size < _cfg.inversion_min_body_r * _stop_dist:
                    log.info(
                        "Signal blocked: inversion bar body %s < min_body_r %s × stop_dist %s",
                        event.body_size, _cfg.inversion_min_body_r, _stop_dist,
                    )
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

    @staticmethod
    def _ob_bar(event: DisplacementEvent) -> Bar | None:
        """Order-block candidate: the bar immediately before the displacement
        bar, valid only when its body opposes the displacement direction."""
        ob = event.prev_bar
        if ob is None:
            return None
        if event.side == "bearish" and ob.close > ob.open:
            return ob
        if event.side == "bullish" and ob.close < ob.open:
            return ob
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
        displacement_only = cfg.confirmation == "displacement_only"
        # Entry zone: the inverted FVG when present, else the order block
        # (ob_fallback mode). displacement_only takes no zone (T5 semantics).
        zone_low = zone_high = None
        zone_kind = ""
        if fvg is not None:
            zone_low, zone_high, zone_kind = fvg.low, fvg.high, "FVG"
        elif cfg.confirmation == "ob_fallback":
            ob = self._ob_bar(event)
            assert ob is not None  # guarded by caller
            zone_low, zone_high, zone_kind = ob.low, ob.high, "OB"
        assert zone_low is not None or displacement_only  # guarded by caller

        lookback = cfg.swing_stop_lookback
        if event.side == "bullish":
            side: Side = "long"
            entry = bar.close if displacement_only or zone_high is None else zone_high
            if lookback > 0 and self._bar_lows:
                swing_anchor = min(self._bar_lows)
                stop_anchor = min(swing_anchor, awaiting.sweep.sweep_extreme)
            else:
                stop_anchor = awaiting.sweep.sweep_extreme
            buf = (stop_anchor * cfg.stop_buffer_pct
                   if cfg.stop_buffer_pct > 0
                   else cfg.stop_buffer)
            stop = stop_anchor - buf
            r = entry - stop
            target = entry + r * cfg.r_multiple
        else:
            side = "short"
            entry = bar.close if displacement_only or zone_low is None else zone_low
            if lookback > 0 and self._bar_highs:
                swing_anchor = max(self._bar_highs)
                stop_anchor = max(swing_anchor, awaiting.sweep.sweep_extreme)
            else:
                stop_anchor = awaiting.sweep.sweep_extreme
            buf = (stop_anchor * cfg.stop_buffer_pct
                   if cfg.stop_buffer_pct > 0
                   else cfg.stop_buffer)
            stop = stop_anchor + buf
            r = stop - entry
            target = entry - r * cfg.r_multiple

        # Stop-width cap (ATR-relative): if the swing-anchored stop is wider
        # than max_stop_atr × ATR, fall back to the tighter structural anchor
        # (the sweep extreme); if even that exceeds the cap, no trade. Also
        # shrinks the target proportionally (target = R-multiple × stop width).
        if cfg.max_stop_atr > 0 and event.atr_at_event > 0:
            cap = cfg.max_stop_atr * event.atr_at_event
            if r > cap:
                extreme = awaiting.sweep.sweep_extreme
                fallback_buf = (extreme * cfg.stop_buffer_pct
                                if cfg.stop_buffer_pct > 0
                                else cfg.stop_buffer)
                if side == "long":
                    stop = extreme - fallback_buf
                    r = entry - stop
                    target = entry + r * cfg.r_multiple
                else:
                    stop = extreme + fallback_buf
                    r = stop - entry
                    target = entry - r * cfg.r_multiple
                if r > cap or r <= 0:
                    log.info(
                        "Signal blocked: stop width %s exceeds %s×ATR cap (%s) "
                        "even at the sweep-extreme anchor",
                        r, cfg.max_stop_atr, cap,
                    )
                    return None
                log.info(
                    "Stop cap: swing anchor too wide — fell back to sweep "
                    "extreme (r=%s, cap=%s)", r, cap,
                )

        fvg_desc = (f"{zone_kind} {zone_low}–{zone_high}" if zone_low is not None
                    else "no-FVG (displacement-only)")
        rationale = (
            f"{awaiting.killzone_name}: "
            f"{awaiting.sweep.pattern} sweep of {awaiting.sweep.side} "
            f"@ {awaiting.sweep.swept_swing.price}, "
            f"{event.side} displacement "
            f"({event.body_to_atr:.2f}× ATR), "
            f"{fvg_desc}"
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
            fvg_low=zone_low,
            fvg_high=zone_high,
            rationale=rationale,
            sweep_bar_range=awaiting.sweep.sweep_bar.high - awaiting.sweep.sweep_bar.low,
        )
