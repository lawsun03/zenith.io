"""
VWAP mean-reversion detector (third candidate engine).

Session-anchored VWAP with volume-weighted σ-bands. A bar CLOSING beyond
vwap ± band_sigma·σ fades back toward the mean: target = VWAP at entry,
stop = stop_sigma·σ beyond entry. Closed-bar confirmation only.

Episodic re-arm: after a side fires it stays disarmed until a later bar
closes back at/through VWAP — one fade per stretch episode, which is the
regime filter that stops the detector averaging into a trend day.

VWAPRunner duck-types the StrategyRunner surface the engine touches
(same shim shape as ORBRunner).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from app.bot_config import StrategyParams
from app.sim.events import Bar
from app.strategy.composer import Signal
from app.strategy.grader import SetupGrader

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


class SessionVWAP:
    """Anchored VWAP/σ accumulator with daily session reset at anchor (ET)."""

    def __init__(self, anchor_et: str = "09:30") -> None:
        hh, mm = anchor_et.split(":")
        self._anchor_t = time(int(hh), int(mm))
        self._session_start: datetime | None = None
        self._sum_w = Decimal("0")
        self._sum_p = Decimal("0")
        self._sum_p2 = Decimal("0")

    @property
    def vwap(self) -> Decimal | None:
        if self._sum_w == 0:
            return None
        return self._sum_p / self._sum_w

    @property
    def sigma(self) -> Decimal | None:
        v = self.vwap
        if v is None:
            return None
        var = self._sum_p2 / self._sum_w - v * v
        if var <= 0:
            return Decimal("0")
        return var.sqrt()

    def _session_start_for(self, et: datetime) -> datetime:
        start = datetime.combine(et.date(), self._anchor_t, tzinfo=ET)
        if et < start:
            start -= timedelta(days=1)
        return start

    def on_bar(self, bar: Bar) -> bool:
        """Accumulate; returns True when a new session started on this bar."""
        et = bar.ts.astimezone(ET)
        start = self._session_start_for(et)
        new_session = start != self._session_start
        if new_session:
            self._session_start = start
            self._sum_w = self._sum_p = self._sum_p2 = Decimal("0")
        vol = Decimal(bar.volume or 0)
        tp = (bar.high + bar.low + bar.close) / 3
        self._sum_w += vol
        self._sum_p += tp * vol
        self._sum_p2 += tp * tp * vol
        return new_session


@dataclass
class VWAPConfig:
    instrument: str
    anchor_et: str = "09:30"                  # "09:30" cash open | "18:00" futures day
    band_sigma: Decimal = Decimal("2.5")      # entry band: close beyond vwap ± k·σ
    stop_sigma: Decimal = Decimal("1.5")      # stop distance beyond entry, in σ
    min_bars: int = 6                         # bars after anchor before signals


class VWAPDetector:
    """Streaming: feed closed bars, get fade Signals at band stretches."""

    def __init__(self, config: VWAPConfig) -> None:
        self.config = config
        self._sv = SessionVWAP(config.anchor_et)
        self._bars = 0
        self._armed_long = True
        self._armed_short = True

    @property
    def vwap(self) -> Decimal | None:
        return self._sv.vwap

    @property
    def sigma(self) -> Decimal | None:
        return self._sv.sigma

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        if self._sv.on_bar(bar):
            self._bars = 0
            self._armed_long = self._armed_short = True
        self._bars += 1

        vwap = self.vwap
        sigma = self.sigma
        if vwap is None or sigma is None:
            return None

        # Re-arm a fired side once price has closed back at/through the mean.
        if not self._armed_long and bar.close >= vwap:
            self._armed_long = True
        if not self._armed_short and bar.close <= vwap:
            self._armed_short = True

        if self._bars < self.config.min_bars or sigma == 0:
            return None

        k = self.config.band_sigma
        lower = vwap - k * sigma
        upper = vwap + k * sigma

        if self._armed_long and bar.close < lower:
            side, entry = "long", bar.close
            target = vwap
            stop = entry - self.config.stop_sigma * sigma
            band = lower
            self._armed_long = False
        elif self._armed_short and bar.close > upper:
            side, entry = "short", bar.close
            target = vwap
            stop = entry + self.config.stop_sigma * sigma
            band = upper
            self._armed_short = False
        else:
            return None

        log.info("VWAP fade: %s %s close=%s vwap=%s sigma=%s stop=%s",
                 self.config.instrument, side, entry, vwap, sigma, stop)
        return Signal(
            instrument=self.config.instrument,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            created_at=bar.ts,
            killzone="VWAP",
            sweep_pattern="VWAP",
            sweep_extreme=band,
            fvg_low=None,
            fvg_high=None,
            rationale=(f"VWAP {self.config.anchor_et} fade: {side} close {entry} "
                       f"beyond {self.config.band_sigma}σ band {band} "
                       f"(vwap {vwap}, σ {sigma})"),
            sweep_bar_range=sigma,
        )


class _NoopComposer:
    """Stop-fill hook the engine calls on every runner; VWAP has no cooldown."""

    def on_stop_loss(self) -> None:
        pass


@dataclass
class VWAPRunner:
    """Duck-type of the StrategyRunner surface ExecutionEngine touches."""

    instrument: str
    timeframe: str
    detector: VWAPDetector
    strategy_cfg: StrategyParams
    vp: None = None                       # engine skips VP when None
    signal_instrument: str = ""
    last_reject: None = field(default=None, init=False)
    composer: _NoopComposer = field(default_factory=_NoopComposer)
    grader: SetupGrader = field(default_factory=SetupGrader)  # empty swings → no TP1

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        return self.detector.on_bar(bar)
