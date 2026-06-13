"""
ORB — Opening Range Breakout detector (second signal engine).

Clock-driven: the opening range is the high/low of the first
`range_minutes` after `open_et`; a later bar CLOSING beyond an edge
fires a Signal (closed-bar confirmation only — the forming-bar lesson).
Stop = opposite range edge; target = fixed R multiple. One signal per
trading day by default. No FVGs, no sweeps, no grader.

ORBRunner duck-types the slice of StrategyRunner the engine touches, so
it plugs into ExecutionEngine/run_backtest unchanged (solo benchmark).
"""
from __future__ import annotations

import logging
import statistics
from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from app.bot_config import StrategyParams
from app.broker.events import Bar
from app.strategy.composer import Signal
from app.strategy.grader import SetupGrader

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


@dataclass
class ORBConfig:
    instrument: str
    open_et: str = "09:30"          # "HH:MM" ET; 09:30 = cash open, 08:30 = data open
    range_minutes: int = 15
    r_multiple: Decimal = Decimal("2.0")
    max_trades_per_day: int = 1
    pdr_enabled: bool = False       # prior-day-range qualifier (default-off)
    pdr_lookback: int = 60          # trading days; waits until window full
    reentry_after_stop: bool = False  # re-arm once per day after a confirmed stop


class ORBDetector:
    """Streaming: feed closed bars, get at most one Signal per day."""

    def __init__(self, config: ORBConfig) -> None:
        self.config = config
        hh, mm = config.open_et.split(":")
        self._open_t = time(int(hh), int(mm))
        self._day: date | None = None
        self._or_high: Decimal | None = None
        self._or_low: Decimal | None = None
        self._fired = 0
        self._rearm_count = 0  # max 1 re-arm per day; prevents two stops from doubling entries
        self._or_range_logged = False  # avoid re-logging established range each bar
        # Prior-day-range qualifier state
        self._pdr_ranges: deque[Decimal] = deque(maxlen=config.pdr_lookback)
        self._pdr_day_high: Decimal | None = None
        self._pdr_day_low: Decimal | None = None
        self._pdr_day_close: Decimal | None = None

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        et = bar.ts.astimezone(ET)

        if et.date() != self._day:
            # Commit the completed day's range into the PDR deque before resetting
            if (self._day is not None and self._pdr_day_high is not None
                    and self._pdr_day_close and self._pdr_day_close > 0):
                rng_pct = (self._pdr_day_high - self._pdr_day_low) / self._pdr_day_close
                self._pdr_ranges.append(rng_pct)
            self._day = et.date()
            self._or_high = self._or_low = None
            self._fired = 0
            self._rearm_count = 0
            self._or_range_logged = False
            self._pdr_day_high = None
            self._pdr_day_low = None
            self._pdr_day_close = None

        # Track running high/low/close for the current ET day (all bars)
        if self._pdr_day_high is None:
            self._pdr_day_high = bar.high
            self._pdr_day_low = bar.low
        else:
            self._pdr_day_high = max(self._pdr_day_high, bar.high)
            self._pdr_day_low = min(self._pdr_day_low, bar.low)
        self._pdr_day_close = bar.close

        start = datetime.combine(et.date(), self._open_t, tzinfo=ET)
        end = start + timedelta(minutes=self.config.range_minutes)

        if et < start:
            return None
        if et < end:  # building the opening range
            self._or_high = bar.high if self._or_high is None else max(self._or_high, bar.high)
            self._or_low = bar.low if self._or_low is None else min(self._or_low, bar.low)
            return None
        if self._or_high is None or self._or_low is None:
            return None  # no bars landed in the range window (holiday/gap)
        if not self._or_range_logged:
            log.info("ORB range established: %s OR=[%s-%s]",
                     self.config.instrument, self._or_low, self._or_high)
            self._or_range_logged = True
        if self._fired >= self.config.max_trades_per_day:
            return None

        # Prior-day-range qualifier: only engage once the lookback window is full
        if self.config.pdr_enabled and len(self._pdr_ranges) >= self.config.pdr_lookback:
            prior = self._pdr_ranges[-1]  # most recent completed day
            median = Decimal(str(statistics.median(self._pdr_ranges)))
            if prior < median:
                return None  # below-median prior-day range → skip ORB today

        if bar.close > self._or_high:
            side, stop, broken = "long", self._or_low, self._or_high
        elif bar.close < self._or_low:
            side, stop, broken = "short", self._or_high, self._or_low
        else:
            return None

        entry = bar.close
        r = abs(entry - stop)
        if r == 0:
            return None
        target = entry + r * self.config.r_multiple if side == "long" \
            else entry - r * self.config.r_multiple
        self._fired += 1
        log.info("ORB breakout: %s %s close=%s OR=[%s-%s] stop=%s target=%s pdr_enabled=%s",
                 self.config.instrument, side, entry,
                 self._or_low, self._or_high, stop, target, self.config.pdr_enabled)
        return Signal(
            instrument=self.config.instrument,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            created_at=bar.ts,
            killzone="ORB",
            sweep_pattern="ORB",
            sweep_extreme=broken,
            fvg_low=None,
            fvg_high=None,
            rationale=(f"ORB {self.config.open_et}+{self.config.range_minutes}min: "
                       f"{side} breakout close {entry} of OR "
                       f"{self._or_low}-{self._or_high}"),
            sweep_bar_range=self._or_high - self._or_low,
        )

    def _rearm(self) -> None:
        """Allow one more signal this day — called by ORBComposer on a confirmed stop."""
        if self._rearm_count == 0:
            self._fired = 0
            self._rearm_count = 1

    def state(self) -> dict:
        """Current OR range and signal count — for the live dashboard. Never mutates.

        or_established is True only after the range window has closed (first post-range
        bar processed); False while still accumulating during the window.
        """
        return {
            "or_high": str(self._or_high) if self._or_high is not None else None,
            "or_low": str(self._or_low) if self._or_low is not None else None,
            "or_established": self._or_range_logged,
            "fired": self._fired,
        }


@dataclass
class ORBComposer:
    """Stop-fill hook for ORB. Re-arms the detector once per day when enabled."""

    detector: ORBDetector
    reentry_after_stop: bool = False

    def on_stop_loss(self) -> None:
        if self.reentry_after_stop:
            self.detector._rearm()


@dataclass
class ORBRunner:
    """Duck-type of the StrategyRunner surface ExecutionEngine touches."""

    instrument: str
    timeframe: str
    detector: ORBDetector
    strategy_cfg: StrategyParams
    vp: None = None                       # engine skips VP when None
    signal_instrument: str = ""
    last_reject: None = field(default=None, init=False)
    composer: ORBComposer = field(default=None)  # set by _build_runner; default built in __post_init__
    grader: SetupGrader = field(default_factory=SetupGrader)  # empty swings → no TP1

    def __post_init__(self) -> None:
        if self.composer is None:
            self.composer = ORBComposer(detector=self.detector)

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        return self.detector.on_bar(bar)
