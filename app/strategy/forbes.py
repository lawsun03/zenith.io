"""Forbes Model — ICT session-liquidity engine (engine="forbes"). Backtest-only,
default-off. Reuses DisplacementDetector/LiquidityTracker/ORBDetector/KillzoneLevelTracker
on a single 1-min feed; aggregates 1m->15m internally for swing POIs. See spec
docs/superpowers/specs/2026-06-15-forbes-model-design.md."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from datetime import time as _time
from decimal import Decimal
from typing import Optional

from app.broker.events import Bar
from app.strategy.killzone import ET
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
from app.strategy.kz_levels import KillzoneLevelTracker


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


def _parse_window(s: str) -> "tuple[_time, _time]":
    a, b = s.split("-")
    h1, m1 = (int(x) for x in a.split(":"))
    h2, m2 = (int(x) for x in b.split(":"))
    return _time(h1, m1), _time(h2, m2)


@dataclass
class ForbesConfig:
    instrument: str
    kz_start: _time
    kz_end: _time
    or_open: _time
    or_minutes: int
    or_min_fvgs: int
    target_mode: str
    min_rr: Decimal
    stop_mode: str
    max_trades_per_day: int
    swing_tf_min: int

    @classmethod
    def from_params(cls, instrument: str, s) -> "ForbesConfig":
        ks, ke = _parse_window(s.forbes_killzone_et)
        oh, om = (int(x) for x in s.forbes_or_open_et.split(":"))
        return cls(instrument=instrument, kz_start=ks, kz_end=ke,
                   or_open=_time(oh, om), or_minutes=s.forbes_or_minutes,
                   or_min_fvgs=s.forbes_or_min_fvgs, target_mode=s.forbes_target_mode,
                   min_rr=s.forbes_min_rr, stop_mode=s.forbes_stop_mode,
                   max_trades_per_day=s.forbes_max_trades_per_day,
                   swing_tf_min=s.forbes_poi_swing_tf_min)


class ForbesDetector:
    def __init__(self, config: ForbesConfig) -> None:
        self.config = config
        self.displacement = DisplacementDetector(DisplacementConfig())
        self.liquidity = LiquidityTracker(LiquidityConfig())
        self.kz_levels = KillzoneLevelTracker()
        self.agg = _FifteenMinAggregator(minutes=config.swing_tf_min)
        self.poi = ForbesPOIMap()
        self._day = None
        self._or_locked = False
        self._or_fvg_count = 0
        self._trades_today = 0

    def _et(self, ts):
        return ts.astimezone(ET)

    def in_killzone(self, ts) -> bool:
        t = self._et(ts).time()
        return self.config.kz_start <= t < self.config.kz_end

    def day_eligible(self) -> bool:
        return self._or_locked and self._or_fvg_count >= self.config.or_min_fvgs
