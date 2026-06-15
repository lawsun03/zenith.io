"""Excursion tracker — records MFE/MAE over N bars after a trade or rejection.

Pure + stateful: no I/O. open() registers a window; on_bar() advances every
window and calls emit(window) when its bar countdown completes. Used for
observability only — never affects orders.

Beyond MFE/MAE, each window tracks WHEN the stop and target levels were first
touched (bar index), so a completed window can be classified: a win (target
first), a clean loss (stop, no target), or — the actionable one —
"stopped_then_target" (stop hit first, target reached only afterward: the
stop was too tight). Within a single bar we can't know stop-vs-target order
from OHLC, so we assume the stop hit first (pessimistic)."""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Callable, Literal

from app.broker.events import Bar


@dataclass
class ExcursionWindow:
    key: str
    kind: Literal["trade", "rejection"]
    side: str                       # "long" | "short"
    ref: Decimal                    # (would-be) entry price
    target: Decimal | None
    bars_left: int
    max_high: Decimal               # running max of bar.high while open
    min_low: Decimal                # running min of bar.low while open
    reached_target: bool = False
    stop: Decimal | None = None     # (would-be) stop price
    stop_hit_bar: int | None = None    # 1-based bar index stop first touched
    target_hit_bar: int | None = None  # 1-based bar index target first touched
    bars_elapsed: int = 0
    instrument: str = ""            # root symbol, e.g. "MNQ"; "" = legacy (no filter)

    @property
    def mfe(self) -> Decimal:
        """Max favorable excursion in points (positive = in your favor)."""
        return (self.max_high - self.ref) if self.side == "long" else (self.ref - self.min_low)

    @property
    def mae(self) -> Decimal:
        """Max adverse excursion in points (positive = against you)."""
        return (self.ref - self.min_low) if self.side == "long" else (self.max_high - self.ref)

    @property
    def stop_hit(self) -> bool:
        """Whether price ever touched the stop level within the window."""
        return self.stop_hit_bar is not None

    @property
    def outcome(self) -> str:
        """Classify the resolved window by stop-vs-target order.

        win                 — target reached before the stop (you'd have exited green)
        loss                — stop hit, target never reached
        stopped_then_target — stop hit first, target reached later (stop too tight)
        no_resolution       — neither level reached within the window
        """
        s, t = self.stop_hit_bar, self.target_hit_bar
        if s is None and t is None:
            return "no_resolution"
        if t is not None and (s is None or t < s):
            return "win"
        if t is not None:               # stop hit first (s <= t), target only later
            return "stopped_then_target"
        return "loss"                   # stop hit, target never


class ExcursionTracker:
    def __init__(self, emit: Callable[[ExcursionWindow], None]) -> None:
        self._emit = emit
        self._windows: list[ExcursionWindow] = []

    def open(self, *, key: str, kind: str, side: str, ref: Decimal,
             target: Decimal | None, window_bars: int,
             stop: Decimal | None = None, instrument: str = "") -> None:
        if window_bars <= 0:
            return
        self._windows.append(ExcursionWindow(
            key=key, kind=kind, side=side, ref=ref, target=target,
            bars_left=window_bars, max_high=ref, min_low=ref, stop=stop,
            instrument=instrument,
        ))

    def on_bar(self, bar: Bar) -> None:
        """Advance every open window with this bar; emit + drop completed ones."""
        bar_root = bar.instrument.split(".")[-2] if "." in bar.instrument else bar.instrument
        still_open: list[ExcursionWindow] = []
        for w in self._windows:
            if w.instrument and bar_root != w.instrument:
                still_open.append(w)
                continue
            w.bars_elapsed += 1
            if bar.high > w.max_high:
                w.max_high = bar.high
            if bar.low < w.min_low:
                w.min_low = bar.low
            if w.target is not None and not w.reached_target:
                if (w.side == "long" and bar.high >= w.target) or \
                   (w.side == "short" and bar.low <= w.target):
                    w.reached_target = True
                    w.target_hit_bar = w.bars_elapsed
            if w.stop is not None and w.stop_hit_bar is None:
                if (w.side == "long" and bar.low <= w.stop) or \
                   (w.side == "short" and bar.high >= w.stop):
                    w.stop_hit_bar = w.bars_elapsed
            w.bars_left -= 1
            if w.bars_left <= 0:
                try:
                    self._emit(w)
                except Exception:
                    pass  # observability must never break the bar handler
            else:
                still_open.append(w)
        self._windows = still_open
