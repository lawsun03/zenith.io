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
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import TYPE_CHECKING, Literal
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from app.strategy.grader import SetupGrade
    from app.strategy.armed_zone import ArmedZone

from app.broker.events import Bar
from app.risk.pretrade import Side

from .displacement import DisplacementDetector, DisplacementEvent, FairValueGap
from .killzone import Killzone, default_killzones, in_killzone
from .liquidity import LiquidityTracker, SweepEvent, Swing

log = logging.getLogger(__name__)
_ET = ZoneInfo("America/New_York")


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
    # B58: confluence count at signal emission time.
    # Features: +1 long side, +1 rank-1 of day, +1 Silver Bullet hour, +1 combined-engine.
    # 0 = uncounted (non-iFVG engines, old signals). Default 0 is neutral (no up/down sizing).
    confluence_count: int = 0
    # B69: timestamp of when the FVG zone was originally formed (bar that confirmed it).
    # gap_bars = (created_at - displacement_ts) / timeframe = FVG age at inversion time.
    # event.displacement_bar.ts is always 1 bar before created_at (structural), so
    # event.fvg.created_at is the meaningful reference for how stale the FVG was.
    # None for non-iFVG engines, hand-built signals, and displacement-only mode (no FVG).
    displacement_ts: "datetime | None" = None


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

    # B23: cap signals emitted per ET calendar day. 0 = disabled (unlimited).
    # >0 = suppress signals once this many have been emitted today (rank-1 has
    # PF=1.129; rank-2+ drags to 0.970 — cap=1 isolates the highest-quality signal).
    daily_signal_cap: int = 0

    # B30: day-of-week filter. Empty list = no suppression. Suppress signal emission
    # on these ET weekday names; sweep/displacement state still accumulates.
    # Names match strftime("%A"): "Monday", ..., "Sunday".
    skip_trading_days: list[str] = field(default_factory=list)

    # B35: daily directional bias gate. See StrategyParams.daily_bias_gate_enabled.
    daily_bias_gate_enabled: bool = False

    # B36: stop placement mode. "swing" = sweep extreme (default).
    # "fvg_mid" = FVG zone midpoint (tighter, target scaled by new r).
    # "fvg_mid_abs" = FVG midpoint stop, original absolute target (higher R).
    stop_mode: str = "swing"

    # B44: iFVG mid-session block by ET hour. Empty list = no blocking (default).
    # Suppress iFVG signal emission when bar.ts ET hour is in this list. Sweep state
    # accumulates regardless. Example: [11, 12, 13] blocks 11:00-14:00 ET.
    block_hours: list[int] = field(default_factory=list)

    # B48: hybrid rank-aware short filter. 0 = disabled (existing behavior).
    # 1 = allow only rank-1 short per ET calendar day; suppress rank-2+ shorts
    # (rank-2+ short PF=0.858, loss-making over 5y). Long signals unaffected.
    max_short_rank: int = 0

    # B55: ICT "Silver Bullet" hour gate. When True, iFVG signal emission is restricted
    # to 10:00-11:00 ET only. Sweep state accumulates outside the window; only emission
    # is gated. Data: 10:xx ET is the strongest NY-AM hour (PF=1.235 in research baseline).
    silver_bullet_only: bool = False


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
        # B23: daily signal cap state (reset at ET-day boundary).
        self._daily_signal_count: int = 0
        self._current_et_day: date | None = None
        # B48: daily short rank state (reset at ET-day boundary).
        self._daily_short_count: int = 0
        self._current_short_rank_day: date | None = None
        # B35: daily bias gate — running current-day OHLC and committed prior-day OHLC.
        # Tracking happens in on_bar_close; gate applied in on_displacement.
        self._bias_current_day: date | None = None
        self._bias_current_open: Decimal | None = None
        self._bias_current_high: Decimal | None = None
        self._bias_current_low: Decimal | None = None
        self._bias_last_close: Decimal | None = None
        self._bias_prior_open: Decimal | None = None
        self._bias_prior_high: Decimal | None = None
        self._bias_prior_low: Decimal | None = None
        self._bias_prior_close: Decimal | None = None
        # B47: injected by CombinedRunner when confluence_gate=True.
        self.session_ctx = None
        # B56: injected by CombinedRunner when alignment_gate=True.
        self.alignment_ctx = None
        # B58: daily signal rank — always tracked (regardless of daily_signal_cap).
        # Rank-1 = first signal emitted today; rank-2+ = subsequent signals.
        self._daily_signal_rank: int = 0
        self._daily_rank_et_day: "date | None" = None

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
        # B30: day-of-week filter — suppress signal emission only, not sweep state.
        if self.config.skip_trading_days:
            et_weekday = bar.ts.astimezone(_ET).strftime("%A")
            if et_weekday in self.config.skip_trading_days:
                return None

        # B44: mid-session hour block — suppress signal emission only, not sweep state.
        if self.config.block_hours:
            et_hour = bar.ts.astimezone(_ET).hour
            if et_hour in self.config.block_hours:
                return None

        # B55: Silver Bullet hour gate — allow emission ONLY during 10:00-11:00 ET.
        # Sweep state continues accumulating outside the window.
        if self.config.silver_bullet_only:
            if bar.ts.astimezone(_ET).hour != 10:
                return None

        # B35: daily directional bias gate — suppress signals against prior-day bias
        # and when price has already consumed the prior-day target level.
        if self.config.daily_bias_gate_enabled:
            _et_day = bar.ts.astimezone(_ET).date()
            # on_displacement is called before on_bar_close for the same bar, so on
            # the first bar of a new ET day the day transition hasn't been committed.
            # In that case, use the accumulated current-day data as the prior-day ref.
            if _et_day != self._bias_current_day and self._bias_current_day is not None:
                _p_open, _p_high, _p_low, _p_close = (
                    self._bias_current_open, self._bias_current_high,
                    self._bias_current_low, self._bias_last_close,
                )
            else:
                _p_open, _p_high, _p_low, _p_close = (
                    self._bias_prior_open, self._bias_prior_high,
                    self._bias_prior_low, self._bias_prior_close,
                )
            if _p_open is not None and _p_close is not None:
                _is_long = event.side == "bullish"
                _bias_long = _p_close > _p_open
                if _is_long != _bias_long:
                    log.info(
                        "Signal blocked: daily bias gate — %s bias, counter-bias signal suppressed",
                        "long" if _bias_long else "short",
                    )
                    return None
                if _is_long and _p_high is not None and bar.close >= _p_high:
                    log.info(
                        "Signal blocked: daily bias gate — long suppressed "
                        "(price %s >= prior high %s)",
                        bar.close, _p_high,
                    )
                    return None
                if not _is_long and _p_low is not None and bar.close <= _p_low:
                    log.info(
                        "Signal blocked: daily bias gate — short suppressed "
                        "(price %s <= prior low %s)",
                        bar.close, _p_low,
                    )
                    return None

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

        # B48: hybrid rank-aware short filter — suppress rank-2+ shorts per ET day.
        if self.config.max_short_rank > 0:
            want_side_b48 = "long" if event.side == "bullish" else "short"
            if want_side_b48 == "short":
                et_day_b48 = bar.ts.astimezone(_ET).date()
                if et_day_b48 != self._current_short_rank_day:
                    self._current_short_rank_day = et_day_b48
                    self._daily_short_count = 0
                if self._daily_short_count >= self.config.max_short_rank:
                    log.info(
                        "Signal blocked: rank-%d+ short suppressed (max_short_rank=%d, day=%s)",
                        self._daily_short_count + 1, self.config.max_short_rank, et_day_b48,
                    )
                    return None

        # B23: daily signal cap — reset counter at ET-day boundary, then gate.
        if self.config.daily_signal_cap > 0:
            et_day = bar.ts.astimezone(_ET).date()
            if et_day != self._current_et_day:
                self._current_et_day = et_day
                self._daily_signal_count = 0
            if self._daily_signal_count >= self.config.daily_signal_cap:
                log.info(
                    "Daily signal cap (%d) reached — signal suppressed (day %s)",
                    self.config.daily_signal_cap, et_day,
                )
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

            # B47: Gate 2 — suppress post-ORB iFVG that opposes ORB direction.
            sig_side = "long" if event.side == "bullish" else "short"
            if self.session_ctx is not None and self.session_ctx.gate2_ifvg_suppressed(
                bar.ts.astimezone(_ET).date(), sig_side
            ):
                log.info(
                    "Signal blocked: B47 Gate 2 — iFVG %s opposes ORB direction", sig_side
                )
                self._awaiting = []
                return None

            # B58: compute confluence count (features with validated edges).
            _et_day_rank = bar.ts.astimezone(_ET).date()
            if _et_day_rank != self._daily_rank_et_day:
                self._daily_rank_et_day = _et_day_rank
                self._daily_signal_rank = 0
            _cc = 0
            if event.side == "bullish":
                _cc += 1  # +1 long side (B15)
            if self._daily_signal_rank == 0:
                _cc += 1  # +1 rank-1 of day (B23)
            if bar.ts.astimezone(_ET).hour == 10:
                _cc += 1  # +1 Silver Bullet 10-11 ET (B55/B18)
            if self.session_ctx is not None:
                _cc += 1  # +1 combined-engine context (B40)

            signal = self._build_signal(bar, awaiting, event, confluence_count=_cc)
            self._awaiting = []
            if signal is not None:
                self._daily_signal_rank += 1
            if signal is not None and self.config.daily_signal_cap > 0:
                self._daily_signal_count += 1
            if signal is not None and self.config.max_short_rank > 0 and event.side == "bearish":
                self._daily_short_count += 1
            if signal is not None and self.session_ctx is not None:
                self.session_ctx.record_ifvg_signal(
                    bar.ts.astimezone(_ET).date(), sig_side
                )
            if signal is not None and self.alignment_ctx is not None:
                self.alignment_ctx.record_ifvg_signal(
                    bar.ts.astimezone(_ET).date(), sig_side
                )
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
        # B35: daily bias gate OHLC tracking — commit day transitions and update running data.
        if self.config.daily_bias_gate_enabled:
            _et_day = bar.ts.astimezone(_ET).date()
            if _et_day != self._bias_current_day:
                if self._bias_current_day is not None:
                    self._bias_prior_open = self._bias_current_open
                    self._bias_prior_high = self._bias_current_high
                    self._bias_prior_low = self._bias_current_low
                    self._bias_prior_close = self._bias_last_close
                self._bias_current_day = _et_day
                self._bias_current_open = bar.open
                self._bias_current_high = bar.high
                self._bias_current_low = bar.low
            else:
                if self._bias_current_high is not None:
                    self._bias_current_high = max(self._bias_current_high, bar.high)
                if self._bias_current_low is not None:
                    self._bias_current_low = min(self._bias_current_low, bar.low)
            self._bias_last_close = bar.close

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
        confluence_count: int = 0,
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

        # B36: FVG-midpoint stop override (applied after max_stop_atr check)
        if cfg.stop_mode in ("fvg_mid", "fvg_mid_abs") and zone_low is not None and zone_high is not None:
            fvg_mid = (zone_low + zone_high) / 2
            orig_target = target
            new_stop = fvg_mid
            new_r = (entry - new_stop) if side == "long" else (new_stop - entry)
            if new_r <= 0:
                log.info(
                    "fvg_mid stop invalid (r=%s <= 0); skipping (entry=%s, fvg_mid=%s)",
                    new_r, entry, fvg_mid,
                )
                return None
            stop = new_stop
            r = new_r
            if cfg.stop_mode == "fvg_mid_abs":
                target = orig_target
            elif side == "long":
                target = entry + r * cfg.r_multiple
            else:
                target = entry - r * cfg.r_multiple

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
            confluence_count=confluence_count,
            displacement_ts=event.fvg.created_at if event.fvg is not None else None,
        )
