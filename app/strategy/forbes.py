"""Forbes Model — ICT session-liquidity engine (engine="forbes"). Backtest-only,
default-off. Reuses DisplacementDetector/LiquidityTracker/ORBDetector/KillzoneLevelTracker
on a single 1-min feed; aggregates 1m->15m internally for swing POIs. See spec
docs/superpowers/specs/2026-06-15-forbes-model-design.md."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from datetime import time as _time
from decimal import Decimal
from typing import Optional

log = logging.getLogger(__name__)

from app.broker.events import Bar
from app.bot_config import StrategyParams
from app.strategy.composer import Signal
from app.strategy.killzone import ET, Killzone, asia, london_open, ny_am, ny_pm
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
from app.strategy.kz_levels import KillzoneLevelTracker
from app.strategy.grader import SetupGrader


def _zones_for_forbes() -> "list[Killzone]":
    """Session windows whose H/L ranges become liquidity POIs. Asia/London/NY-AM/NY-PM
    so a later (killzone) session can sweep an earlier session's level."""
    return [asia(), london_open(), ny_am(), ny_pm()]


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
        # KillzoneLevelTracker.on_bar needs a StrategyParams for min_penetration /
        # multi_bar_window; the Forbes engine reuses the defaults (no separate tuning).
        self._kz_params = StrategyParams()
        # OR high/low accumulated during the [or_open, or_open+or_minutes) ET window.
        self._or_high: Decimal | None = None
        self._or_low: Decimal | None = None

    def _et(self, ts):
        return ts.astimezone(ET)

    def in_killzone(self, ts) -> bool:
        t = self._et(ts).time()
        return self.config.kz_start <= t < self.config.kz_end

    def day_eligible(self) -> bool:
        return self._or_locked and self._or_fvg_count >= self.config.or_min_fvgs

    # ------------------------------------------------------------------
    # Trade-count gate + signal construction
    # ------------------------------------------------------------------

    def _can_trade(self) -> bool:
        return self._trades_today < self.config.max_trades_per_day

    def _build_signal(self, *, side, entry, stop, target, bar, pattern, sweep_level):
        stop_dist = abs(entry - stop)
        if stop_dist == 0:
            return None
        # Reject a stop on the wrong side of entry (can occur in beyond_or mode when the
        # entry level sits inside the OR). A non-protective stop must never become an order.
        if (side == "long" and stop >= entry) or (side == "short" and stop <= entry):
            return None
        rr = abs(target - entry) / stop_dist
        if rr < self.config.min_rr:
            return None
        return Signal(
            instrument=self.config.instrument, side=side, entry=entry, stop=stop,
            target=target, created_at=bar.ts, killzone="Forbes",
            sweep_pattern=pattern, sweep_extreme=sweep_level,
            fvg_low=None, fvg_high=None,
            rationale=f"Forbes {pattern}: {side} -> liquidity {target} (RR {rr:.2f})",
        )

    def _select_target(self, side, entry):
        if self.config.target_mode == "liquidity":
            lvl = self.poi.nearest_unswept_opposing(side, entry)
            return lvl.price if lvl is not None else None
        # or_top / midway_poi not implemented -> skip. Warn ONCE per run (not per bar).
        if not getattr(self, "_warned_target_mode", False):
            log.warning("forbes: target_mode=%s not implemented -> setups skipped (warned once)",
                        self.config.target_mode)
            self._warned_target_mode = True
        return None

    # ------------------------------------------------------------------
    # Opening-range tracking (mirrors ORBDetector's ET-window timing)
    # ------------------------------------------------------------------

    def _maybe_lock_or(self, bar: Bar) -> None:
        et = self._et(bar.ts)
        start = datetime.combine(et.date(), self.config.or_open, tzinfo=ET)
        end = start + timedelta(minutes=self.config.or_minutes)
        if et < start:
            return
        if et < end:  # accumulate the opening range
            self._or_high = bar.high if self._or_high is None else max(self._or_high, bar.high)
            self._or_low = bar.low if self._or_low is None else min(self._or_low, bar.low)
            return
        if not self._or_locked:
            # Lock the range: FVGs formed during/around the OR are the day-eligibility gate.
            self._or_locked = True
            self._or_fvg_count = len(self.displacement.active_fvgs)
            log.info(
                "Forbes OR locked: %s OR=[%s-%s] fvgs=%d",
                self.config.instrument, self._or_low, self._or_high, self._or_fvg_count,
            )

    # ------------------------------------------------------------------
    # Entry-trigger search (iFVG inversion > sweep+displacement > breakout+retest)
    # ------------------------------------------------------------------

    def _stop_for(self, side, sweep_level) -> Decimal:
        """beyond_wick = just past the swept wick; beyond_or = past the opposite OR boundary."""
        buf = self._kz_params.min_penetration
        if self.config.stop_mode == "beyond_or" and self._or_high is not None and self._or_low is not None:
            return (self._or_low - buf) if side == "long" else (self._or_high + buf)
        return (sweep_level - buf) if side == "long" else (sweep_level + buf)

    def _search_triggers(self, bar: Bar, sweeps) -> "Optional[Signal]":
        # Priority 1: iFVG inversion. peek_displacement() returns (side, b1, b2) only when
        # the latest bar both displaced AND closed through a prior active FVG (the inversion);
        # we reuse that math directly rather than re-deriving FVG/inversion geometry.
        peek = self.displacement.peek_displacement()
        if peek is not None:
            disp_side, _b1, b2 = peek
            side = "long" if disp_side == "bullish" else "short"
            # Only trade an inversion that follows a swept opposing POI (a taken level on the
            # entry side): long after a swept low, short after a swept high.
            swept = [l for l in self.poi._levels if l.swept and (
                (side == "long" and l.kind.endswith("low")) or
                (side == "short" and l.kind.endswith("high")))]
            if swept:
                sweep_level = (min(swept, key=lambda l: l.price).price if side == "long"
                               else max(swept, key=lambda l: l.price).price)
                entry = b2.close
                stop = self._stop_for(side, sweep_level)
                target = self._select_target(side, entry)
                if target is not None:
                    sig = self._build_signal(side=side, entry=entry, stop=stop,
                                             target=target, bar=bar, pattern="ifvg",
                                             sweep_level=sweep_level)
                    if sig is not None:
                        return sig

        # Priority 2: sweep + displacement. A session level was swept this bar and a
        # displacement prints back in (peek confirms direction). Enter at the swept level.
        for sw in sweeps:
            # high sweep -> short reversal; low sweep -> long reversal.
            side = "short" if sw.side == "high" else "long"
            if peek is None:
                continue
            disp_side = peek[0]
            if (side == "long") != (disp_side == "bullish"):
                continue
            entry = sw.swept_swing.price
            stop = self._stop_for(side, sw.sweep_extreme)
            target = self._select_target(side, entry)
            if target is None:
                continue
            sig = self._build_signal(side=side, entry=entry, stop=stop, target=target,
                                     bar=bar, pattern="sweep_disp",
                                     sweep_level=sw.sweep_extreme)
            if sig is not None:
                return sig

        # Priority 3: breakout + retest of an OR boundary (best-effort). The stop anchors to
        # the RECLAIMED boundary (beyond_wick), not the opposite one: a long reclaims or_high
        # so its stop sits just below or_high; a short reclaims or_low so its stop sits above it.
        if self._or_high is not None and self._or_low is not None:
            if bar.low <= self._or_high <= bar.close and bar.open > self._or_high:
                side, sweep_level = "long", self._or_high
            elif bar.high >= self._or_low >= bar.close and bar.open < self._or_low:
                side, sweep_level = "short", self._or_low
            else:
                return None
            entry = bar.close
            buf = self._kz_params.min_penetration
            stop = (sweep_level - buf) if side == "long" else (sweep_level + buf)
            target = self._select_target(side, entry)
            if target is not None:
                return self._build_signal(side=side, entry=entry, stop=stop, target=target,
                                          bar=bar, pattern="breakout_retest",
                                          sweep_level=sweep_level)
        return None

    def on_bar(self, bar):
        et_date = self._et(bar.ts).date()
        if et_date != self._day:
            self._day = et_date
            self._or_locked = False
            self._or_fvg_count = 0
            self._trades_today = 0
            self._or_high = None
            self._or_low = None
            self.poi.reset_day()
        atr = self.displacement.atr
        self.displacement.on_bar(bar)
        sweeps = self.liquidity.on_bar(bar, atr)
        self.kz_levels.on_bar(bar, _zones_for_forbes(), self._kz_params)
        for name, (hi, lo) in self.kz_levels.locked_ranges().items():
            self.poi.add(_Level(hi, "session_high"))
            self.poi.add(_Level(lo, "session_low"))
        m15 = self.agg.on_bar(bar)
        if m15 is not None:
            for sw in self.liquidity.recent_high_swings:
                self.poi.add(_Level(sw.price, "swing_high"))
            for sw in self.liquidity.recent_low_swings:
                self.poi.add(_Level(sw.price, "swing_low"))
        self.poi.update_swept(bar.high, bar.low)
        self._maybe_lock_or(bar)
        if not (self.in_killzone(bar.ts) and self.day_eligible() and self._can_trade()):
            return None
        sig = self._search_triggers(bar, sweeps)
        if sig is not None:
            self._trades_today += 1
        return sig


@dataclass
class _ForbesComposer:
    """No-op stop-fill hook. Forbes has no intraday re-arm; the engine still calls
    composer.on_stop_loss() on every stop fill, so we must expose a no-op."""

    def on_stop_loss(self) -> None:
        return None


@dataclass
class ForbesRunner:
    """Duck-type of the StrategyRunner surface ExecutionEngine touches — mirrors
    ORBRunner so engine="forbes" plugs into ExecutionEngine/run_backtest unchanged."""

    instrument: str
    timeframe: str
    detector: "ForbesDetector"
    strategy_cfg: StrategyParams
    vp: None = None                       # engine skips VP when None
    signal_instrument: str = ""
    last_reject: None = field(default=None, init=False)
    composer: _ForbesComposer = field(default_factory=_ForbesComposer)
    grader: SetupGrader = field(default_factory=SetupGrader)  # empty swings → no TP1

    @property
    def displacement(self) -> DisplacementDetector:
        # Forming-bar poll path reads runner.displacement.peek_displacement().
        return self.detector.displacement

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        return self.detector.on_bar(bar)
