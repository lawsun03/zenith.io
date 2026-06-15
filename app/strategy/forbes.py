"""Forbes Model — ICT session-liquidity engine (engine="forbes"). Backtest-only,
default-off. Reuses DisplacementDetector/LiquidityTracker/ORBDetector/KillzoneLevelTracker
on a single 1-min feed; aggregates 1m->15m internally for swing POIs. See spec
docs/superpowers/specs/2026-06-15-forbes-model-design.md."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Optional

from app.broker.events import Bar


class _FifteenMinAggregator:
    """Buffer 1-min bars into N-min OHLC. Emits the just-closed bucket when a bar in
    the next bucket arrives (close-on-rollover). Bucketed by floor(ts.minute / N)."""

    def __init__(self, minutes: int = 15) -> None:
        self.minutes = minutes
        self._bucket_key: tuple | None = None
        self._o = self._h = self._l = self._c = None
        self._ts = None

    def _key(self, ts: datetime) -> tuple:
        return (ts.year, ts.month, ts.day, ts.hour, ts.minute // self.minutes)

    def on_bar(self, bar: Bar) -> Optional[Bar]:
        k = self._key(bar.ts)
        out = None
        if self._bucket_key is not None and k != self._bucket_key:
            out = Bar(instrument=bar.instrument, timeframe=f"{self.minutes}min",
                      ts=self._ts, open=self._o, high=self._h, low=self._l,
                      close=self._c, volume=0)
            self._o = None
        if self._o is None:
            self._o, self._h, self._l, self._c, self._ts = (
                bar.open, bar.high, bar.low, bar.close, bar.ts)
        else:
            self._h = max(self._h, bar.high)
            self._l = min(self._l, bar.low)
            self._c = bar.close
        self._bucket_key = k
        return out
