"""
chop_breakout — compression → iFVG continuation breakout (engine candidate).

IDLE → CHOP when the regime gate holds min_chop_bars consecutive bars;
CHOP → signal when a displacement bar closes OUTSIDE [chop_low, chop_high]
and inverts an FVG at/inside the breached boundary (CONTINUATION — trade
in the displacement direction). Targets = nearest prior 15m swing beyond
entry with a 1.5R floor (no room = no setup). Post-entry hard exits via
the engine's strategy-exit channel: failed-breakout (close back inside
within K bars), VWAP invalidation, optional SMA21 trail.
Deterministic; fixed defaults; no sweeps by design.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Optional
from zoneinfo import ZoneInfo

from app.bot_config import StrategyParams
from app.broker.events import Bar
from app.strategy.composer import Signal
from app.strategy.displacement import (DisplacementConfig, DisplacementDetector,
                                       DisplacementEvent)
from app.strategy.grader import SetupGrader
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
from app.strategy.vwap import SessionVWAP

log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")


@dataclass
class ChopBreakoutConfig:
    instrument: str
    regime_metric: str = "compression"        # "compression" | "vwap_cross"
    compression_lookback: int = 20
    compression_percentile: int = 30
    history_window: int = 100
    min_chop_bars: int = 12
    vwap_cross_min: int = 6
    entry_mode: str = "close"                 # v1: "close" only
    target_floor_r: Decimal = Decimal("1.5")
    failed_breakout_bars: int = 6
    vwap_invalidation: bool = True
    sma21_trail: bool = False
    # displacement thresholds, copied from the live StrategyParams at build
    atr_period: int = 14
    body_atr_multiple: Decimal = Decimal("1.0")
    min_body_to_range_ratio: Decimal = Decimal("0.6")
    min_absolute_body: Decimal = Decimal("1.0")
    swing_lookback: int = 2


class ChopBreakoutDetector:
    def __init__(self, config: ChopBreakoutConfig) -> None:
        if config.entry_mode != "close":
            raise ValueError("chop_breakout v1 supports entry_mode='close' only")
        self.config = config
        self.disp = DisplacementDetector(DisplacementConfig(
            atr_period=config.atr_period,
            body_atr_multiple=config.body_atr_multiple,
            min_body_to_range_ratio=config.min_body_to_range_ratio,
            min_absolute_body=config.min_absolute_body,
        ))
        self._liq15 = LiquidityTracker(LiquidityConfig(
            swing_lookback=config.swing_lookback, max_swings=50))
        self._vwap = SessionVWAP("18:00")
        # regime gate state
        self._hl: deque[tuple[Decimal, Decimal]] = deque(maxlen=config.compression_lookback)
        self._range_hist: deque[Decimal] = deque(maxlen=config.history_window)
        self._cross_signs: deque[int] = deque(maxlen=config.compression_lookback)
        self._streak = 0
        self.state = "idle"
        self.chop_high: Decimal | None = None
        self.chop_low: Decimal | None = None
        self._recent: deque[tuple[Decimal, Decimal]] = deque(maxlen=config.min_chop_bars)
        # (was_chop, chop_high, chop_low) as of the START of the PREVIOUS
        # bar's processing. The displacement event for bar b2 only arrives
        # when b3 closes, and b2 itself both widens the bounds AND usually
        # breaks the compression gate (a big bar blows up the rolling range).
        # Breakouts must therefore be judged against the chop state/bounds as
        # of b2's open — this snapshot — or real breakouts are unsatisfiable
        # (bounds absorb the close) or discarded (state already idle).
        self._prev_bar_start: tuple[bool, Decimal | None, Decimal | None] | None = None
        # 15m aggregation for swing targets
        self._bucket: list[Bar] = []
        self._bucket_floor = None
        # post-entry management
        self._trade: dict | None = None
        self._sma_closes: deque[Decimal] = deque(maxlen=21)
        self._day: date | None = None
        self.exit_request: str | None = None

    # ---------------- regime gate ----------------

    def _compressed(self, bar: Bar) -> bool:
        self._hl.append((bar.high, bar.low))
        if self.config.regime_metric == "vwap_cross":
            v = self._vwap.vwap
            if v is None:
                return False
            sign = 1 if bar.close >= v else -1
            self._cross_signs.append(sign)
            if len(self._cross_signs) < (self._cross_signs.maxlen or 0):
                return False
            signs = list(self._cross_signs)
            crosses = sum(1 for a, b in zip(signs, signs[1:]) if a != b)
            return crosses >= self.config.vwap_cross_min
        # default: compression percentile
        if len(self._hl) < self.config.compression_lookback:
            return False
        rng = max(h for h, _ in self._hl) - min(l for _, l in self._hl)
        threshold = None
        if len(self._range_hist) == self.config.history_window:
            s = sorted(self._range_hist)
            threshold = s[int(len(s) * self.config.compression_percentile / 100)]
        self._range_hist.append(rng)
        if threshold is None:
            return False
        return rng < threshold

    def _roll_day(self, bar: Bar) -> None:
        d = bar.ts.astimezone(ET).date()
        if d != self._day:
            self._day = d
            self.state = "idle"
            self._streak = 0
            self.chop_high = self.chop_low = None
            self._trade = None
            self._prev_bar_start = None

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._roll_day(bar)
        # Snapshot the chop state/bounds as of THIS bar's open, before any
        # update — next bar's displacement event (for this bar) is judged
        # against it. See _prev_bar_start comment in __init__.
        start_snapshot = (self.state == "chop", self.chop_high, self.chop_low)
        self._vwap.on_bar(bar)
        self._sma_closes.append(bar.close)
        self._feed_15m(bar)
        event = self.disp.on_bar(bar)

        self._manage_trade(bar)

        was_chop, ch, cl = self._prev_bar_start or (False, None, None)
        self._prev_bar_start = start_snapshot

        signal = None
        if was_chop and event is not None and ch is not None and cl is not None:
            signal = self.on_displacement(bar, event, ch, cl)

        compressed = self._compressed(bar)
        self._recent.append((bar.high, bar.low))
        if signal is not None:
            self.state = "idle"
            self._streak = 0
            self.chop_high = self.chop_low = None
            return signal
        if compressed:
            self._streak += 1
        else:
            self._streak = 0
            if self.state == "chop":
                self.state = "idle"
                self.chop_high = self.chop_low = None
        if self.state == "idle" and self._streak >= self.config.min_chop_bars:
            self.state = "chop"
            self.chop_high = max(h for h, _ in self._recent)
            self.chop_low = min(l for _, l in self._recent)
        elif self.state == "chop":
            assert self.chop_high is not None and self.chop_low is not None
            self.chop_high = max(self.chop_high, bar.high)
            self.chop_low = min(self.chop_low, bar.low)
        return None

    # ---------------- 15m swings ----------------

    def _feed_15m(self, bar: Bar) -> None:
        floor_min = bar.ts.minute - bar.ts.minute % 15
        floor = bar.ts.replace(minute=floor_min, second=0, microsecond=0)
        if self._bucket_floor is None:
            self._bucket_floor = floor
        if floor != self._bucket_floor and self._bucket:
            b15 = Bar(
                instrument=bar.instrument, timeframe="15min",
                ts=self._bucket_floor,
                open=self._bucket[0].open,
                high=max(b.high for b in self._bucket),
                low=min(b.low for b in self._bucket),
                close=self._bucket[-1].close,
                volume=sum(b.volume for b in self._bucket),
            )
            self._liq15.on_bar(b15)
            self._bucket = []
            self._bucket_floor = floor
        self._bucket.append(bar)

    def _nearest_target(self, side: str, entry: Decimal) -> Decimal | None:
        if side == "long":
            above = [s.price for s in self._liq15.recent_high_swings if s.price > entry]
            return min(above) if above else None
        below = [s.price for s in self._liq15.recent_low_swings if s.price < entry]
        return max(below) if below else None

    # ---------------- trigger ----------------

    def on_displacement(self, bar: Bar, event: DisplacementEvent,
                        ch: Decimal, cl: Decimal) -> Optional[Signal]:
        """Evaluate a breakout against the PRE-breakout range (ch, cl) — the
        chop bounds as of the displacement bar's open."""
        if event.fvg is None:
            return None  # breakout without iFVG inversion is not a setup
        d = event.displacement_bar
        mid = (ch + cl) / 2
        fvg = event.fvg

        if event.side == "bullish":
            if d.close <= ch:
                return None  # didn't close outside the range
            if fvg.low > ch:
                return None  # FVG not at/inside the breached boundary
            side, entry, broken = "long", bar.close, ch
            stop = max(fvg.low, mid)        # whichever is TIGHTER
            if stop >= entry:
                return None
            r = entry - stop
        else:
            if d.close >= cl:
                return None
            if fvg.high < cl:
                return None
            side, entry, broken = "short", bar.close, cl
            stop = min(fvg.high, mid)
            if stop <= entry:
                return None
            r = stop - entry

        target = self._nearest_target(side, entry)
        if target is None or abs(target - entry) < self.config.target_floor_r * r:
            log.info("chop_breakout floor rule: no 15m swing >= %sR away — no trade",
                     self.config.target_floor_r)
            return None

        self._trade = {
            "side": side, "entry": entry, "r": r, "bars": 0, "trail_armed": False,
            "chop_high": ch, "chop_low": cl,
        }
        log.info("chop_breakout: %s %s entry=%s stop=%s target=%s chop=[%s-%s]",
                 self.config.instrument, side, entry, stop, target, cl, ch)
        return Signal(
            instrument=self.config.instrument, side=side, entry=entry,
            stop=stop, target=target, created_at=bar.ts,
            killzone="CHOP", sweep_pattern="CHOP_BREAKOUT",
            sweep_extreme=broken,
            fvg_low=fvg.low, fvg_high=fvg.high,
            rationale=(f"chop_breakout: {side} displacement close {d.close} outside "
                       f"chop [{cl}-{ch}], iFVG "
                       f"{fvg.low}-{fvg.high} at boundary, target 15m swing {target}"),
            sweep_bar_range=d.high - d.low,
        )

    # ---------------- post-entry hard exits ----------------

    def _manage_trade(self, bar: Bar) -> None:
        t = self._trade
        if t is None:
            return
        t["bars"] += 1
        close = bar.close

        # 1. Failed-breakout rule (mandatory, K bars, no exceptions)
        if (t["bars"] <= self.config.failed_breakout_bars
                and t["chop_low"] <= close <= t["chop_high"]):
            self.exit_request = "failed_breakout"
            self._trade = None
            return

        # 2. VWAP invalidation — a close on the wrong side of session VWAP
        if self.config.vwap_invalidation:
            v = self._vwap.vwap
            if v is not None:
                wrong = close < v if t["side"] == "long" else close > v
                if wrong:
                    self.exit_request = "vwap_invalidation"
                    self._trade = None
                    return

        # 3. Optional SMA21 trail (default OFF): arms at +1.5R, exits on a
        #    close beyond the 21SMA.
        if self.config.sma21_trail and len(self._sma_closes) == 21:
            fav = close - t["entry"] if t["side"] == "long" else t["entry"] - close
            if fav >= Decimal("1.5") * t["r"]:
                t["trail_armed"] = True
            if t["trail_armed"]:
                sma = sum(self._sma_closes) / len(self._sma_closes)
                crossed = close < sma if t["side"] == "long" else close > sma
                if crossed:
                    self.exit_request = "sma21_trail"
                    self._trade = None


class _NoopComposer:
    """Stop-fill hook the engine calls on every runner; no cooldown here."""

    def on_stop_loss(self) -> None:
        pass


@dataclass
class ChopBreakoutRunner:
    """Duck-type of the StrategyRunner surface ExecutionEngine touches."""

    instrument: str
    timeframe: str
    detector: ChopBreakoutDetector
    strategy_cfg: StrategyParams
    vp: None = None                       # engine skips VP when None
    signal_instrument: str = ""
    last_reject: None = field(default=None, init=False)
    exit_request: str | None = field(default=None, init=False)
    composer: _NoopComposer = field(default_factory=_NoopComposer)
    grader: SetupGrader = field(default_factory=SetupGrader)  # empty swings → no TP1

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        sig = self.detector.on_bar(bar)
        if self.detector.exit_request is not None:
            self.exit_request = self.detector.exit_request
            self.detector.exit_request = None
        return sig
