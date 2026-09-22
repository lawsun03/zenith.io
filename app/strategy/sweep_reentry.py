"""
sweep_reentry — Long-only session-low/prior-day-low reentry micro-engine (B59).

Activates ONLY after an ORB long stop-out on the same day. Watches for a
downside sweep of the session low (since 09:30 ET) or the prior-day low by
>= sweep_depth_atr × ATR, then requires a bullish displacement + FVG inversion
(Lesson 1: the inversion IS the quality filter). Entry at inversion bar close;
stop below the swept extreme. Long-only — never emits short signals.

Design mirrors orb.py / sweep_bos.py: standalone detector + runner shim that
duck-types the StrategyRunner surface so it plugs into ExecutionEngine and the
backtest harness unchanged.

The SweepReentryRunner bundles an ORBDetector with the SweepReentryDetector so
the ORB position's stop-loss can arm the overlay. When the ORB long stops, the
SweepReentryComposer calls arm_for_reentry(); when the overlay fires, the
runner returns its signal from on_bar().
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from app.bot_config import StrategyParams
from app.sim.events import Bar
from app.strategy.composer import Signal
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.grader import SetupGrader
from app.strategy.orb import ORBComposer, ORBConfig, ORBDetector

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


@dataclass
class SweepReentryConfig:
    instrument: str
    # Sweep must break the reference level by at least this many ATR units.
    sweep_depth_atr: Decimal = Decimal("0.25")
    atr_period: int = 14
    # Displacement bar quality thresholds (reused from iFVG engine defaults).
    min_absolute_body: Decimal = Decimal("5.0")
    body_atr_multiple: Decimal = Decimal("1.0")
    min_body_to_range_ratio: Decimal = Decimal("0.6")
    # Exit geometry.
    r_multiple: Decimal = Decimal("2.0")
    stop_buffer: Decimal = Decimal("0.30")
    # Session reference: track the low since this time (ET "HH:MM").
    session_open_et: str = "09:30"
    # Bars after the sweep to wait for a qualifying displacement event.
    displacement_window_bars: int = 10


class SweepReentryDetector:
    """
    Streaming detector for the B59 long-only sweep-reentry overlay.

    State machine:
      IDLE     → waiting for arm_for_reentry() (ORB long stopped)
      ARMED    → watching for sweep of session_low or prior_day_low
      SWEPT    → sweep confirmed, watching for displacement+FVG within window
      USED     → signal fired today; ignore all subsequent calls this day
    """

    def __init__(self, config: SweepReentryConfig) -> None:
        self.config = config
        # Reuse DisplacementDetector for ATR tracking + FVG inversion quality filter.
        self._displacement = DisplacementDetector(DisplacementConfig(
            atr_period=config.atr_period,
            body_atr_multiple=config.body_atr_multiple,
            min_body_to_range_ratio=config.min_body_to_range_ratio,
            min_absolute_body=config.min_absolute_body,
        ))
        hh, mm = config.session_open_et.split(":")
        self._session_open_t: time = time(int(hh), int(mm))

        # Day-scoped state (reset at each ET day boundary)
        self._day: date | None = None
        self._armed: bool = False
        self._used: bool = False            # one overlay signal per day
        self._session_low: Decimal | None = None       # running min since session_open_et
        self._session_low_at_arm: Decimal | None = None  # snapshot when armed
        self._day_low: Decimal | None = None            # running day low (any hour)

        # Persisted across days
        self._prior_day_low: Decimal | None = None

        # Sweep state
        self._sweep_occurred: bool = False
        self._sweep_extreme: Decimal | None = None   # deepest point during the sweep
        self._bars_since_sweep: int = 0

    # ------------------------------------------------------------------
    # External API
    # ------------------------------------------------------------------

    def arm_for_reentry(self) -> None:
        """
        Called by the composer when an ORB LONG position stops out.
        Arms the detector to look for a session-low/prior-day-low sweep.
        No-op if a signal has already fired today.
        """
        if self._used:
            return
        if not self._armed:
            self._armed = True
            # Snapshot reference level at arm time so drift doesn't eat the threshold.
            self._session_low_at_arm = self._session_low
            log.info(
                "sweep_reentry: armed — %s session_low=%s prior_day_low=%s",
                self.config.instrument,
                self._session_low_at_arm,
                self._prior_day_low,
            )

    # ------------------------------------------------------------------
    # Streaming
    # ------------------------------------------------------------------

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        et = bar.ts.astimezone(ET)
        bar_date = et.date()

        # --- Day boundary ---
        if bar_date != self._day:
            if self._day is not None and self._day_low is not None:
                self._prior_day_low = self._day_low
            self._day = bar_date
            self._armed = False
            self._used = False
            self._session_low = None
            self._session_low_at_arm = None
            self._day_low = None
            self._sweep_occurred = False
            self._sweep_extreme = None
            self._bars_since_sweep = 0

        # Always feed DisplacementDetector (ATR warmup + FVG inversion tracking).
        disp_event = self._displacement.on_bar(bar)
        atr: Decimal = self._displacement.atr or Decimal("20")  # fallback for NQ warmup

        # Track running day low.
        if self._day_low is None:
            self._day_low = bar.low
        else:
            self._day_low = min(self._day_low, bar.low)

        # Track session low from session_open_et.
        if et.time() >= self._session_open_t:
            if self._session_low is None:
                self._session_low = bar.low
            else:
                self._session_low = min(self._session_low, bar.low)

        if not self._armed or self._used:
            return None

        min_depth = self.config.sweep_depth_atr * atr

        # --- Phase 1: Detect sweep ---
        if not self._sweep_occurred:
            level_swept: Decimal | None = None

            # Check session_low reference (snapshotted at arm time).
            if (self._session_low_at_arm is not None
                    and bar.low < self._session_low_at_arm - min_depth):
                level_swept = self._session_low_at_arm

            # Check prior-day low.
            if (level_swept is None
                    and self._prior_day_low is not None
                    and bar.low < self._prior_day_low - min_depth):
                level_swept = self._prior_day_low

            if level_swept is not None:
                self._sweep_occurred = True
                self._sweep_extreme = bar.low
                self._bars_since_sweep = 0
                log.info(
                    "sweep_reentry: %s swept level=%s extreme=%s depth=%.2f atr=%.2f",
                    self.config.instrument, level_swept, bar.low,
                    float(level_swept - bar.low), float(atr),
                )
            # Never fire on the same bar as the sweep (closed-bar discipline).
            return None

        # --- Phase 2: Track sweep extreme + count bars ---
        if bar.low < self._sweep_extreme:
            self._sweep_extreme = bar.low
        self._bars_since_sweep += 1

        if self._bars_since_sweep > self.config.displacement_window_bars:
            log.debug("sweep_reentry: displacement window expired, resetting sweep state")
            self._sweep_occurred = False
            self._sweep_extreme = None
            self._bars_since_sweep = 0
            return None

        # --- Phase 3: Look for bullish displacement + FVG inversion ---
        if (disp_event is not None
                and disp_event.side == "bullish"
                and disp_event.fvg is not None):
            entry = bar.close
            stop = self._sweep_extreme - self.config.stop_buffer
            r = entry - stop
            if r <= 0:
                return None
            target = entry + r * self.config.r_multiple

            self._armed = False
            self._used = True
            self._sweep_occurred = False

            log.info(
                "sweep_reentry: LONG signal entry=%s stop=%s target=%s "
                "fvg=[%s-%s] swept_extreme=%s r=%.1f",
                entry, stop, target,
                disp_event.fvg.low, disp_event.fvg.high,
                self._sweep_extreme, self.config.r_multiple,
            )
            return Signal(
                instrument=self.config.instrument,
                side="long",
                entry=entry,
                stop=stop,
                target=target,
                created_at=bar.ts,
                killzone="sweep_reentry",
                sweep_pattern="sweep_reentry",
                sweep_extreme=self._sweep_extreme,
                fvg_low=disp_event.fvg.low,
                fvg_high=disp_event.fvg.high,
                rationale=(
                    f"sweep_reentry long: extreme={self._sweep_extreme} "
                    f"displacement close={entry}"
                ),
                sweep_bar_range=Decimal("0"),
            )

        return None


@dataclass
class SweepReentryComposer:
    """
    Composite stop-loss hook.

    Tracks which signal is currently live. On stop_loss:
    - If the ORB long was the live signal → arm the sweep_reentry overlay.
    - If orb_reentry_after_stop → also re-arm the ORB detector.
    """

    orb_detector: ORBDetector
    sr_detector: SweepReentryDetector
    orb_reentry_after_stop: bool = False
    _last_signal_source: str = field(default="", init=False)  # "orb_long"|"orb_short"|"sr"

    def on_stop_loss(self) -> None:
        if self._last_signal_source == "orb_long":
            self.sr_detector.arm_for_reentry()
        if self.orb_reentry_after_stop and self._last_signal_source in (
            "orb_long", "orb_short"
        ):
            self.orb_detector._rearm()


@dataclass
class SweepReentryRunner:
    """
    Duck-type of the StrategyRunner surface ExecutionEngine touches.

    Bundles an ORBDetector (primary) with a SweepReentryDetector (overlay).
    The ORB provides its normal signals; when its LONG position stops, the
    overlay arms and watches for the session-low/prior-day-low reentry setup.
    Both detectors consume every bar; at most one signal fires per bar.
    """

    instrument: str
    timeframe: str
    orb_detector: ORBDetector
    sr_detector: SweepReentryDetector
    strategy_cfg: StrategyParams
    vp: None = None
    signal_instrument: str = ""
    last_reject: None = field(default=None, init=False)
    composer: SweepReentryComposer = field(default=None)
    grader: SetupGrader = field(default_factory=SetupGrader)

    def __post_init__(self) -> None:
        if self.composer is None:
            self.composer = SweepReentryComposer(
                orb_detector=self.orb_detector,
                sr_detector=self.sr_detector,
                orb_reentry_after_stop=self.strategy_cfg.orb_reentry_after_stop,
            )

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        # ORB takes priority; overlay fires only when ORB slot is consumed.
        orb_sig = self.orb_detector.on_bar(bar)
        if orb_sig is not None:
            self.composer._last_signal_source = f"orb_{orb_sig.side}"
            return orb_sig

        sr_sig = self.sr_detector.on_bar(bar)
        if sr_sig is not None:
            self.composer._last_signal_source = "sr"
            return sr_sig

        return None
