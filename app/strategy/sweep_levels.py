"""PO3 Feature A: no-lookahead session-anchored reference levels + sweep tagging.

Pure module. Maintains daily/weekly open and prior-Asian session H/L from the
streaming bars (each level exposed only once it is known, never future data),
and tags a sweep extreme against them within a tick tolerance. Measurement only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from decimal import Decimal
from zoneinfo import ZoneInfo

from app.sim.events import Bar

ET = ZoneInfo("America/New_York")
_RTH_OPEN = time(9, 30)
_ASIA_START = time(19, 0)
_ASIA_END = time(22, 0)   # exclusive — matches killzone.asia()


@dataclass
class SweepLevelTracker:
    daily_open: Decimal | None = None
    weekly_open: Decimal | None = None
    asian_high: Decimal | None = None
    asian_low: Decimal | None = None
    _day: date | None = field(default=None)
    _week: tuple[int, int] | None = field(default=None)
    _asia_accum: "tuple[Decimal, Decimal] | None" = field(default=None)
    _asia_accum_day: date | None = field(default=None)

    def on_bar(self, bar: Bar) -> None:
        et = bar.ts.astimezone(ET)
        d = et.date()
        if d != self._day:
            self._day = d
            self.daily_open = None
        if self.daily_open is None and et.time() >= _RTH_OPEN:
            self.daily_open = bar.open
        wk = (et.isocalendar().year, et.isocalendar().week)
        if wk != self._week:
            self._week = wk
            self.weekly_open = bar.open
        if _ASIA_START <= et.time() < _ASIA_END:
            if self._asia_accum is None or self._asia_accum_day != d:
                self._asia_accum = (bar.high, bar.low)
                self._asia_accum_day = d
            else:
                self._asia_accum = (max(self._asia_accum[0], bar.high),
                                    min(self._asia_accum[1], bar.low))
        elif self._asia_accum is not None and et.time() >= _ASIA_END:
            self.asian_high, self.asian_low = self._asia_accum
            self._asia_accum = None

    def levels(self) -> "dict[str, Decimal | None]":
        return {
            "daily_open": self.daily_open,
            "weekly_open": self.weekly_open,
            "asian_high": self.asian_high,
            "asian_low": self.asian_low,
        }

    def tag(self, sweep_extreme: Decimal, tick: Decimal, tolerance_ticks: int) -> list[str]:
        """Which known levels the sweep extreme is within tolerance of.
        Returns sorted matched names, or ['swing_only'] if none. Pure/deterministic."""
        tol = tick * Decimal(tolerance_ticks)
        matched = sorted(
            name for name, price in self.levels().items()
            if price is not None and abs(sweep_extreme - price) <= tol
        )
        return matched or ["swing_only"]
