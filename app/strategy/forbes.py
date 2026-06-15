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


@dataclass
class _Level:
    price: Decimal
    kind: str              # "session_high"|"session_low"|"swing_high"|"swing_low"
    swept: bool = False


class ForbesPOIMap:
    """Liquidity map: 15m swing H/L + session H/L, each swept/unswept. POIs and targets
    are the same objects (spec). add() de-dups by (price, kind)."""

    def __init__(self) -> None:
        self._levels: list[_Level] = []

    def add(self, lvl: _Level) -> None:
        if not any(x.price == lvl.price and x.kind == lvl.kind for x in self._levels):
            self._levels.append(lvl)

    def update_swept(self, bar_high: Decimal, bar_low: Decimal) -> None:
        for lvl in self._levels:
            if lvl.swept:
                continue
            is_high = lvl.kind.endswith("high")
            if (is_high and bar_high >= lvl.price) or (not is_high and bar_low <= lvl.price):
                lvl.swept = True

    def nearest_unswept_opposing(self, side: str, price: Decimal) -> "Optional[_Level]":
        # long -> target above (unswept highs); short -> target below (unswept lows).
        if side == "long":
            cands = [l for l in self._levels if not l.swept and l.price > price]
            return min(cands, key=lambda l: l.price) if cands else None
        cands = [l for l in self._levels if not l.swept and l.price < price]
        return max(cands, key=lambda l: l.price) if cands else None

    def reset_day(self) -> None:
        self._levels = []
