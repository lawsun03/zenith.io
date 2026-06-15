"""
sweep_bos — liquidity sweep + break of structure, NO displacement/FVG leg.

Revelio's simple chain, tested verbatim: a sweep of a swing high/low arms
the setup; a later bar CLOSING beyond the nearest opposing swing (the
break of structure confirming the reversal) fires the signal in the
reversal direction. Entry = BOS bar close, stop = past the sweep extreme
(same placement as the iFVG engine), fixed-R target.

The opposing-swing reference is snapshotted when the sweep registers
(nearest confirmed swing low below price for a high sweep; mirror for
low sweeps). Sweeps age out after bos_window_bars. Reuses the live
LiquidityTracker for swings + sweep detection; killzone-gated at sweep
registration like the composer.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional

from app.bot_config import StrategyParams
from app.broker.events import Bar
from app.strategy.composer import Signal
from app.strategy.grader import SetupGrader
from app.strategy.killzone import Killzone, default_killzones, in_killzone
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker, SweepEvent

log = logging.getLogger(__name__)


@dataclass
class SweepBOSConfig:
    instrument: str
    swing_lookback: int = 2
    min_penetration: Decimal = Decimal("0.20")
    multi_bar_window: int = 3
    stop_buffer: Decimal = Decimal("0.30")
    r_multiple: Decimal = Decimal("2.5")
    bos_window_bars: int = 10            # bars a sweep stays armed (reuses ifvg_sweep_window_bars)
    killzones: list[Killzone] | None = None


@dataclass
class _Awaiting:
    sweep: SweepEvent
    bos_level: Decimal      # the opposing swing to break (close beyond = BOS)
    killzone_name: str
    bars: int = 0


class SweepBOSDetector:
    def __init__(self, config: SweepBOSConfig) -> None:
        self.config = config
        self._zones = config.killzones or default_killzones()
        self.liq = LiquidityTracker(LiquidityConfig(
            swing_lookback=config.swing_lookback,
            min_penetration=config.min_penetration,
            multi_bar_window=config.multi_bar_window,
            max_swings=50,
        ))
        self._awaiting: list[_Awaiting] = []

    def _bos_ref(self, sweep: SweepEvent, bar: Bar) -> Decimal | None:
        """Nearest opposing confirmed swing: below price for a high sweep
        (reversal short breaks a low), above for a low sweep."""
        if sweep.side == "high":
            below = [s.price for s in self.liq.recent_low_swings if s.price < bar.close]
            return max(below) if below else None
        above = [s.price for s in self.liq.recent_high_swings if s.price > bar.close]
        return min(above) if above else None

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        sweeps = self.liq.on_bar(bar)

        # Check BOS against existing awaitings BEFORE registering this bar's
        # sweeps (a sweep can't be broken by its own bar).
        signal = None
        for a in reversed(self._awaiting):
            if a.sweep.side == "high" and bar.close < a.bos_level:
                signal = self._build(bar, a, "short")
                break
            if a.sweep.side == "low" and bar.close > a.bos_level:
                signal = self._build(bar, a, "long")
                break
        if signal is not None:
            self._awaiting = []
            return signal

        # Age out stale sweeps.
        kept = []
        for a in self._awaiting:
            a.bars += 1
            if a.bars < self.config.bos_window_bars:
                kept.append(a)
        self._awaiting = kept

        # Register new sweeps (killzone-gated, like the composer).
        for sw in sweeps:
            zone = in_killzone(bar.ts, self._zones)
            if zone is None:
                continue
            if any(a.sweep.swept_swing is sw.swept_swing for a in self._awaiting):
                continue
            ref = self._bos_ref(sw, bar)
            if ref is None:
                continue  # no opposing structure to break — no setup
            self._awaiting.append(_Awaiting(sweep=sw, bos_level=ref,
                                            killzone_name=zone.name))
        return None

    def _build(self, bar: Bar, a: _Awaiting, side: str) -> Signal:
        cfg = self.config
        entry = bar.close
        if side == "short":
            stop = a.sweep.sweep_extreme + cfg.stop_buffer
            r = stop - entry
            target = entry - r * cfg.r_multiple
        else:
            stop = a.sweep.sweep_extreme - cfg.stop_buffer
            r = entry - stop
            target = entry + r * cfg.r_multiple
        log.info("sweep_bos: %s %s entry=%s stop=%s target=%s bos_level=%s",
                 cfg.instrument, side, entry, stop, target, a.bos_level)
        return Signal(
            instrument=cfg.instrument, side=side, entry=entry,
            stop=stop, target=target, created_at=bar.ts,
            killzone=a.killzone_name,
            sweep_pattern=a.sweep.pattern,
            sweep_extreme=a.sweep.sweep_extreme,
            fvg_low=None, fvg_high=None,
            rationale=(f"sweep_bos: {a.sweep.pattern} sweep of {a.sweep.side} "
                       f"@ {a.sweep.swept_swing.price}, BOS close {entry} "
                       f"through {a.bos_level}"),
            sweep_bar_range=a.sweep.sweep_bar.high - a.sweep.sweep_bar.low,
        )


class _NoopComposer:
    def on_stop_loss(self) -> None:
        pass


@dataclass
class SweepBOSRunner:
    """Duck-type of the StrategyRunner surface ExecutionEngine touches."""

    instrument: str
    timeframe: str
    detector: SweepBOSDetector
    strategy_cfg: StrategyParams
    vp: None = None
    signal_instrument: str = ""
    last_reject: None = field(default=None, init=False)
    composer: _NoopComposer = field(default_factory=_NoopComposer)
    grader: SetupGrader = field(default_factory=SetupGrader)

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        return self.detector.on_bar(bar)
