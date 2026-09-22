# chop_breakout Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the compression→iFVG-continuation breakout engine per `CHOP_BREAKOUT_SPEC.md` (Downloads) and evaluate it solo + complementarity vs iFVG, fixed defaults, NO sweeps.

**Architecture:** `app/strategy/chop_breakout.py`: `ChopBreakoutDetector` (compression/vwap-cross regime gate → CHOP state with widening bounds → continuation signal when a displacement bar closes outside the range AND inverts an FVG at the breached boundary; 15m-swing targets with a 1.5R floor; post-entry failed-breakout / VWAP-invalidation / optional SMA21-trail exit rules) + `ChopBreakoutRunner` (the established shim + an `exit_request` attribute). One small generic engine change: a strategy-initiated exit channel (`runner.exit_request` → cancel_all+flatten, only when a position is open). Reuses `DisplacementDetector`/`_find_inverted_fvg` unchanged, `LiquidityTracker` (15m-aggregated) for swings, and a `SessionVWAP` accumulator extracted from `vwap.py`.

**Tech Stack:** Python 3 / pytest / existing stack.

---

## Spec deviations (Rule 7 — decided, surfaced)

1. **Flat `cb_*` fields on StrategyParams, not a nested `"chop_breakout"` JSON block** — CLAUDE.md Rule 2 (flat BotConfig) + the harness `--set k=v` mechanism. `"enabled"` is subsumed by `engine="chop_breakout"`.
2. **`cb_entry_mode` v1 supports `"close"` only** (the spec default; evaluation uses it). Armed-zone retrace reuse is deferred until/unless the engine survives evaluation — `ValueError` on any other value (fail loud, Rule 12).
3. **Session VWAP anchors at 18:00 ET** (CME session) — the bot trades all killzones, so a 09:30 anchor would leave overnight chop with a stale prior-day VWAP. Spec says "session VWAP" without an anchor.
4. **Post-entry exit rules are armed from signal emission, not fill confirmation** (runners have no broker/fill access). The engine only acts on an exit request when `open_contracts != 0`, so a request with no position is a no-op. Rules disarm after firing once or at the session roll.
5. **"No entries whose target is unreachable before 3:30 PM ET"** is satisfied by the existing engine entry cutoff (14:30 CT = 15:30 ET); reachability itself is not deterministically computable (Rule 5).
6. **Breakout evaluation precedes bound-widening** each bar — otherwise the breakout bar's own high/low would widen the range and make `close > chop_high` unsatisfiable.

## Fixed defaults (spec) → `StrategyParams`

```python
    # chop_breakout engine (all cb_*; spec: fixed defaults, NO sweeps)
    cb_regime_metric: str = "compression"      # "compression" | "vwap_cross"
    cb_compression_lookback: int = 20
    cb_compression_percentile: int = 30
    cb_history_window: int = 100
    cb_min_chop_bars: int = 12
    cb_vwap_cross_min: int = 6
    cb_entry_mode: str = "close"               # v1: "close" only (fail loud otherwise)
    cb_target_floor_r: Decimal = Decimal("1.5")
    cb_failed_breakout_bars: int = 6
    cb_vwap_invalidation: bool = True
    cb_sma21_trail: bool = False
```

### Task 1: Extract `SessionVWAP` from vwap.py (shared by both engines)

**Files:** Modify `app/strategy/vwap.py`; Test `tests/test_vwap.py` (existing suite guards the refactor)

- [ ] Add to `app/strategy/vwap.py`:

```python
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
```

- [ ] Refactor `VWAPDetector` to compose it: replace `_session_start/_sum_*` state and the inline accumulation/reset with `self._sv = SessionVWAP(config.anchor_et)`; `vwap`/`sigma` properties delegate; `on_bar` starts with `if self._sv.on_bar(bar): self._bars = 0; self._armed_long = self._armed_short = True` then `self._bars += 1`.
- [ ] `pytest tests/test_vwap.py -q` → all 8 still PASS (the suite is the refactor guard). Commit `refactor: extract SessionVWAP accumulator`.

### Task 2: Gate — compression regime → CHOP (TDD)

**Files:** Create `app/strategy/chop_breakout.py`, `tests/test_chop_breakout.py`; Modify `app/bot_config.py` (cb_* block above + `engine` docstring gains `"chop_breakout"`)

- [ ] Failing tests (`tests/test_chop_breakout.py`):

```python
"""chop_breakout: spec-mandated tests, one per defining behavior."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.sim.events import Bar
from app.strategy.chop_breakout import ChopBreakoutConfig, ChopBreakoutDetector
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.strategy.liquidity import Swing

T0 = datetime(2026, 3, 4, 14, 0, tzinfo=timezone.utc)


def bar(i, lo, hi, c=None, o=None, vol=100):
    lo, hi = Decimal(lo), Decimal(hi)
    c = Decimal(c) if c else (lo + hi) / 2
    o = Decimal(o) if o else c
    return Bar(instrument="MNQ", timeframe="5min", ts=T0 + timedelta(minutes=5 * i),
               open=o, high=hi, low=lo, close=c, volume=vol)


def _cfg(**kw):
    return ChopBreakoutConfig(instrument="MNQ", **kw)


def feed_history(det, n=130, i0=0):
    """Wide oscillating bars: 20-bar range ≈ 60 → the percentile history."""
    for i in range(n):
        base = 21000 + (30 if i % 2 else -30)
        det.on_bar(bar(i0 + i, str(base - 5), str(base + 5)))
    return i0 + n


def tight(i, width=4):
    return bar(i, str(21000 - width // 2), str(21000 + width // 2))


class TestGate:
    def test_chop_at_exactly_min_chop_bars(self):
        det = ChopBreakoutDetector(_cfg())
        i = feed_history(det)
        seen = []
        for k in range(40):
            det.on_bar(tight(i + k))
            seen.append((det._streak, det.state))
        for streak, state in seen:
            if streak <= 11:
                assert state == "idle"
        assert ("chop" in [s for _, s in seen])
        first_chop = next(s for s in seen if s[1] == "chop")
        assert first_chop[0] == 12  # CHOP first appears exactly at streak 12

    def test_boundaries_widen_never_shrink(self):
        det = ChopBreakoutDetector(_cfg())
        i = feed_history(det)
        k = 0
        while det.state != "chop":
            det.on_bar(tight(i + k)); k += 1
        hi0, lo0 = det.chop_high, det.chop_low
        det.on_bar(bar(i + k, str(lo0), str(hi0 + 2)))      # higher high, compressed
        assert det.chop_high == hi0 + 2 and det.chop_low == lo0
        det.on_bar(bar(i + k + 1, str(lo0 + 1), str(hi0)))  # inside bar
        assert det.chop_high == hi0 + 2 and det.chop_low == lo0  # never shrink
```

- [ ] Implement config + gate skeleton in `app/strategy/chop_breakout.py`:

```python
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
from app.sim.events import Bar
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
            if len(self._cross_signs) < self._cross_signs.maxlen:
                return False
            crosses = sum(1 for a, b in zip(list(self._cross_signs),
                                            list(self._cross_signs)[1:]) if a != b)
            return crosses >= self.config.vwap_cross_min
        # default: compression percentile
        if len(self._hl) < self.config.compression_lookback:
            return False
        rng = max(h for h, _ in self._hl) - min(l for _, l in self._hl)
        hist_full = len(self._range_hist) == self.config.history_window
        threshold = None
        if hist_full:
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

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        self._roll_day(bar)
        self._vwap.on_bar(bar)
        self._sma_closes.append(bar.close)
        self._feed_15m(bar)
        event = self.disp.on_bar(bar)

        self._manage_trade(bar)

        # Breakout check BEFORE bound-widening: the breakout bar's own
        # high/low must not absorb its close back inside the range.
        signal = None
        if self.state == "chop" and event is not None:
            signal = self.on_displacement(bar, event)

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
            self.chop_high = max(self.chop_high, bar.high)
            self.chop_low = min(self.chop_low, bar.low)
        return None
```

(`on_displacement`, `_feed_15m`, `_manage_trade`, `_nearest_target` arrive in Tasks 3–4; stub them as `def on_displacement(self, bar, event): return None`, `def _feed_15m(self, bar): pass`, `def _manage_trade(self, bar): pass` so Task 2's tests run.)

- [ ] `pytest tests/test_chop_breakout.py -q` → gate tests PASS. Commit.

### Task 3: Trigger + stop/target + floor rule (TDD)

**Files:** `app/strategy/chop_breakout.py`, `tests/test_chop_breakout.py`

- [ ] Failing tests:

```python
def _in_chop(det, hi="21010", lo="20990"):
    det.state = "chop"
    det.chop_high, det.chop_low = Decimal(hi), Decimal(lo)
    return det


def _disp_event(i, side, close, fvg):
    d = bar(i, str(Decimal(close) - 2), str(Decimal(close) + 2), close)
    return d, DisplacementEvent(side=side, displacement_bar=d,
                                body_size=Decimal("20"), atr_at_event=Decimal("5"),
                                body_to_atr=Decimal("4"), fvg=fvg)


def _swing(kind, price, i=0):
    ts = T0 + timedelta(minutes=5 * i)
    return Swing(kind=kind, price=Decimal(price), bar_ts=ts, confirmed_ts=ts)


class TestTrigger:
    def test_breakout_without_inversion_no_signal(self):
        det = _in_chop(ChopBreakoutDetector(_cfg()))
        b, ev = _disp_event(200, "bullish", "21030", fvg=None)
        assert det.on_displacement(b, ev) is None

    def test_breakout_with_inversion_at_boundary_signals_continuation(self):
        det = _in_chop(ChopBreakoutDetector(_cfg()))
        det._liq15._swings.append(_swing("high", "21120"))
        fvg = FairValueGap(side="bearish", low=Decimal("21002"),
                           high=Decimal("21008"), created_at=T0)
        b, ev = _disp_event(200, "bullish", "21030", fvg=fvg)
        sig = det.on_displacement(b, ev)
        assert sig is not None
        assert sig.side == "long"                      # continuation, not reversal
        assert sig.entry == b.close
        # stop = tighter of fvg far side (21002) vs chop mid (21000) -> 21002
        assert sig.stop == Decimal("21002")
        assert sig.target == Decimal("21120")          # nearest 15m swing high

    def test_fvg_outside_boundary_no_signal(self):
        det = _in_chop(ChopBreakoutDetector(_cfg()))
        det._liq15._swings.append(_swing("high", "21120"))
        fvg = FairValueGap(side="bearish", low=Decimal("21015"),
                           high=Decimal("21020"), created_at=T0)  # above chop_high
        b, ev = _disp_event(200, "bullish", "21030", fvg=fvg)
        assert det.on_displacement(b, ev) is None


class TestFloorRule:
    def _setup(self, swing_price):
        det = _in_chop(ChopBreakoutDetector(_cfg()))
        det._liq15._swings.append(_swing("high", swing_price))
        fvg = FairValueGap(side="bearish", low=Decimal("21002"),
                           high=Decimal("21008"), created_at=T0)
        return det, *_disp_event(200, "bullish", "21030", fvg=fvg)

    def test_swing_below_floor_no_trade(self):
        # R = 28 (21030-21002); floor 1.5R = 42 -> swing must be >= 21072
        det, b, ev = self._setup("21069")   # 1.39R
        assert det.on_displacement(b, ev) is None

    def test_swing_above_floor_trades(self):
        det, b, ev = self._setup("21075")   # 1.61R
        assert det.on_displacement(b, ev) is not None
```

- [ ] Implement in `ChopBreakoutDetector`:

```python
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

    def on_displacement(self, bar: Bar, event: DisplacementEvent) -> Optional[Signal]:
        if event.fvg is None:
            return None  # breakout without iFVG inversion is not a setup
        assert self.chop_high is not None and self.chop_low is not None
        d = event.displacement_bar
        mid = (self.chop_high + self.chop_low) / 2
        fvg = event.fvg

        if event.side == "bullish":
            if d.close <= self.chop_high:
                return None  # didn't close outside the range
            if fvg.low > self.chop_high:
                return None  # FVG not at/inside the breached boundary
            side, entry, broken = "long", bar.close, self.chop_high
            stop = max(fvg.low, mid)        # whichever is TIGHTER
            if stop >= entry:
                return None
            r = entry - stop
        else:
            if d.close >= self.chop_low:
                return None
            if fvg.high < self.chop_low:
                return None
            side, entry, broken = "short", bar.close, self.chop_low
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
            "chop_high": self.chop_high, "chop_low": self.chop_low,
        }
        log.info("chop_breakout: %s %s entry=%s stop=%s target=%s chop=[%s-%s]",
                 self.config.instrument, side, entry, stop, target,
                 self.chop_low, self.chop_high)
        return Signal(
            instrument=self.config.instrument, side=side, entry=entry,
            stop=stop, target=target, created_at=bar.ts,
            killzone="CHOP", sweep_pattern="CHOP_BREAKOUT",
            sweep_extreme=broken,
            fvg_low=fvg.low, fvg_high=fvg.high,
            rationale=(f"chop_breakout: {side} displacement close {d.close} outside "
                       f"chop [{self.chop_low}-{self.chop_high}], iFVG "
                       f"{fvg.low}-{fvg.high} at boundary, target 15m swing {target}"),
            sweep_bar_range=d.high - d.low,
        )
```

- [ ] `pytest tests/test_chop_breakout.py -q` → PASS. Commit.

### Task 4: Post-entry exit rules + determinism (TDD)

**Files:** `app/strategy/chop_breakout.py`, `tests/test_chop_breakout.py`

- [ ] Failing tests:

```python
class TestExitRules:
    def _entered(self, vwap_invalidation=False):
        det = _in_chop(ChopBreakoutDetector(_cfg(vwap_invalidation=vwap_invalidation)))
        det._trade = {"side": "long", "entry": Decimal("21030"), "r": Decimal("28"),
                      "bars": 0, "trail_armed": False,
                      "chop_high": Decimal("21010"), "chop_low": Decimal("20990")}
        return det

    def test_failed_breakout_at_bar5_exits(self):
        det = self._entered()
        for i in range(4):  # bars 1-4 stay outside the range
            det._manage_trade(bar(300 + i, "21020", "21040"))
            assert det.exit_request is None
        det._manage_trade(bar(304, "20995", "21008", c="21000"))  # bar 5: back inside
        assert det.exit_request == "failed_breakout"

    def test_no_forced_exit_at_bar7(self):
        det = self._entered()
        for i in range(6):  # bars 1-6 outside
            det._manage_trade(bar(300 + i, "21020", "21040"))
        det._manage_trade(bar(306, "20995", "21008", c="21000"))  # bar 7: inside
        assert det.exit_request is None

    def test_vwap_invalidation_long_close_below(self):
        det = self._entered(vwap_invalidation=True)
        det._vwap.on_bar(bar(299, "21050", "21060", c="21055"))  # vwap ≈ 21055
        det._manage_trade(bar(300, "21020", "21040", c="21030"))  # close < vwap
        assert det.exit_request == "vwap_invalidation"


class TestDeterminism:
    def test_same_bars_identical_signals(self):
        def run():
            det = ChopBreakoutDetector(_cfg())
            out = []
            for i in range(400):
                # deterministic pseudo-chop: wide then tight then breakout-ish
                if i < 200:
                    base = 21000 + (30 if i % 2 else -30)
                    b = bar(i, str(base - 5), str(base + 5))
                elif i < 320:
                    b = tight(i)
                else:
                    base = 21000 + (i - 320) * 3
                    b = bar(i, str(base - 4), str(base + 8), c=str(base + 6))
                sig = det.on_bar(b)
                if sig is not None:
                    out.append(repr(sig))
                if det.exit_request:
                    out.append(det.exit_request)
                    det.exit_request = None
            return out
        assert run() == run()
```

- [ ] Implement `_manage_trade`:

```python
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

        # 2. VWAP invalidation — wrong side of session VWAP
        if self.config.vwap_invalidation:
            v = self._vwap.vwap
            if v is not None:
                wrong = close < v if t["side"] == "long" else close > v
                if wrong:
                    self.exit_request = "vwap_invalidation"
                    self._trade = None
                    return

        # 3. Optional SMA21 trail (default OFF): armed at +1.5R, exits on a
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
```

- [ ] `pytest tests/test_chop_breakout.py -q` → PASS. Commit.

### Task 5: Runner + engine exit channel + selection

**Files:** `app/strategy/chop_breakout.py`, `app/execution/engine.py` (~line 696, after the `runner.on_bar` try/except), `app/backtest/runner.py`, `app/main.py`, `app/bot_config.py`, `tests/test_chop_breakout.py`

- [ ] `ChopBreakoutRunner` (same shim shape as ORB/VWAP, plus the exit handoff):

```python
class _NoopComposer:
    def on_stop_loss(self) -> None:
        pass


@dataclass
class ChopBreakoutRunner:
    """Duck-type of the StrategyRunner surface ExecutionEngine touches."""

    instrument: str
    timeframe: str
    detector: ChopBreakoutDetector
    strategy_cfg: StrategyParams
    vp: None = None
    signal_instrument: str = ""
    last_reject: None = field(default=None, init=False)
    exit_request: str | None = field(default=None, init=False)
    composer: _NoopComposer = field(default_factory=_NoopComposer)
    grader: SetupGrader = field(default_factory=SetupGrader)

    def on_bar(self, bar: Bar) -> Optional[Signal]:
        sig = self.detector.on_bar(bar)
        if self.detector.exit_request is not None:
            self.exit_request = self.detector.exit_request
            self.detector.exit_request = None
        return sig
```

- [ ] Engine exit channel — in `app/execution/engine.py`, immediately after the `signal = runner.on_bar(bar)` try/except:

```python
        # Strategy-initiated exit channel (e.g. chop_breakout failed-breakout
        # / VWAP invalidation). Consumed every bar; only acts when a position
        # is open — a request while flat is a no-op by design.
        exit_req = getattr(runner, "exit_request", None)
        if exit_req is not None:
            runner.exit_request = None
            if self.risk_state.open_contracts != 0:
                log.info("Strategy exit request '%s' — flattening %s",
                         exit_req, runner.instrument)
                try:
                    await self.broker.cancel_all(runner.instrument)
                    await self.broker.flatten(runner.instrument)
                except Exception:
                    log.exception("Strategy exit flatten failed for %s",
                                  runner.instrument)
```

- [ ] Engine-channel test (append to `tests/test_chop_breakout.py`):

```python
class TestEngineExitChannel:
    def test_exit_request_flattens_open_position(self):
        import asyncio
        from dataclasses import dataclass as dc, field as f
        from app.sim.paper import PaperBroker
        from app.execution.engine import ExecutionEngine
        from app.risk.config import fifty_k_combine
        from app.risk.state import RiskState

        @dc
        class _Stub:
            instrument: str = "MNQ"
            timeframe: str = "5min"
            strategy_cfg: object = None
            vp: object = None
            composer: object = f(default_factory=lambda: type(
                "C", (), {"on_stop_loss": lambda self: None})())
            grader: object = None
            signal_instrument: str = ""
            last_reject: object = None
            exit_request: object = None

            def on_bar(self, bar):
                return None

        async def run():
            br = PaperBroker(starting_balance=Decimal("50000"),
                             slippage_ticks_market=0)
            rs = RiskState(config=fifty_k_combine())
            stub = _Stub()
            eng = ExecutionEngine(broker=br, risk_state=rs, runners=[stub],
                                  replay_mode=True, contracts=1)
            await br.connect()
            await eng.start()
            ts = datetime(2026, 3, 4, 15, 0, tzinfo=timezone.utc)
            await br.inject_bar(bar(0, "20990", "21010", c="21000"))
            res = await br.place_bracket("MNQ", "long", 1,
                                         entry=Decimal("21000"),
                                         stop=Decimal("20980"),
                                         target=Decimal("21100"))
            assert res.success and len(br._open) == 1
            stub.exit_request = "failed_breakout"
            await br.inject_bar(bar(1, "20995", "21005", c="21000"))
            assert len(br._open) == 0  # flattened by the exit channel
            await eng.stop()
        asyncio.run(run())
```

- [ ] Selection: in both `_build_runner` sites add (next to the vwap branch):

```python
        if s.engine == "chop_breakout":
            from app.strategy.chop_breakout import (ChopBreakoutConfig,
                                                    ChopBreakoutDetector,
                                                    ChopBreakoutRunner)
            return ChopBreakoutRunner(
                instrument=cfg.instrument,
                timeframe=cfg.timeframe,
                detector=ChopBreakoutDetector(ChopBreakoutConfig(
                    instrument=cfg.instrument,
                    regime_metric=s.cb_regime_metric,
                    compression_lookback=s.cb_compression_lookback,
                    compression_percentile=s.cb_compression_percentile,
                    history_window=s.cb_history_window,
                    min_chop_bars=s.cb_min_chop_bars,
                    vwap_cross_min=s.cb_vwap_cross_min,
                    entry_mode=s.cb_entry_mode,
                    target_floor_r=s.cb_target_floor_r,
                    failed_breakout_bars=s.cb_failed_breakout_bars,
                    vwap_invalidation=s.cb_vwap_invalidation,
                    sma21_trail=s.cb_sma21_trail,
                    atr_period=s.atr_period,
                    body_atr_multiple=s.body_atr_multiple,
                    min_body_to_range_ratio=s.min_body_to_range_ratio,
                    min_absolute_body=s.min_absolute_body,
                    swing_lookback=s.swing_lookback,
                )),
                strategy_cfg=s,
            )
```

(`app/main.py` variant uses `instrument=instrument`, `timeframe=timeframe`, adds `signal_instrument=signal_instrument or ""`.)

- [ ] Selection test (append):

```python
class TestEngineSelection:
    def test_build_runner_returns_chop_runner(self):
        from app.backtest.runner import BacktestConfig, _build_runner
        from app.bot_config import StrategyParams
        from app.strategy.chop_breakout import ChopBreakoutRunner

        s = StrategyParams(engine="chop_breakout", min_absolute_body=Decimal("5.0"))
        cfg = BacktestConfig(instrument="MNQ", bars=iter([]),
                             timeframe="5min", strategy_params=s)
        runner = _build_runner(cfg)
        assert isinstance(runner, ChopBreakoutRunner)
        assert runner.detector.config.min_chop_bars == 12
        assert runner.detector.disp.config.min_absolute_body == Decimal("5.0")
```

- [ ] `pytest tests/test_chop_breakout.py tests/test_engine.py tests/test_backtest_runner.py tests/test_vwap.py tests/test_orb.py -q` → PASS. Commit.

### Task 6: Evaluation (spec §Evaluation — NO sweeps)

- [ ] Smoke: `--set engine=chop_breakout --window 12` on 2024 MNQ → signals fire, exit-channel log lines visible, no exceptions.
- [ ] Solo runs, fixed defaults, frontier sizing:

```
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_test_2025_2026.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set engine=chop_breakout --save-id chop_test --save-label "chop_breakout (test 17mo)"
python scripts/run_monthly_combine.py --bars bars/bars_MNQ_train_2024.csv --instrument MNQ --timeframe 5min --risk-pct 1.25 --set engine=chop_breakout --save-id chop_2024 --save-label "chop_breakout (2024)"
```

- [ ] Complementarity matrix (the decision metric): per-month net vs iFVG control (`ab_control_*` registry). Success = **PF ≥ 1.05 solo AND positive expectancy specifically in iFVG's <20-trade months** (test: 2025-01/03/04/05/06/08/11, 2026-01/03/04 + 2025-10; 2024: Jan/Feb/Apr/Jun/Jul/Aug/Sep/Nov). If it only earns in iFVG's good months → REJECT ("second mouth, not a diversifier").
- [ ] Only if success: combined stream through funded_sim (shared MLL) — pass rate vs either engine alone; note per-engine kill-switch requirement before any combined deployment. (Known prior: one-account combination destroyed value for ORB; expect the same unless the drought-month P&L is large.)
- [ ] Report `trade_analysis/2026-06-12_chop_breakout.md` (standard table + complementarity matrix + verdict per spec stop rule: reject, don't tune); memory update; commit.

## Self-review
- Spec coverage: state machine → T2/T3; both regime metrics → T2 (compression tested per spec; vwap_cross implemented, selectable); entry/stop/target+floor → T3; exits 1–3 + flatten note → T4/deviation 5; config block → flat cb_* (deviation 1); all 6 spec tests present (T2 ×2, T3 trigger ×2+floor ×2, T4 ×3, determinism ×1) plus engine-channel and selection tests; evaluation §1–3 → T6 with the spec's exact success/reject criteria.
- Type consistency: `cb_*` names match StrategyParams ↔ ChopBreakoutConfig wiring; `exit_request: str | None` on detector and runner; `state in {"idle","chop"}`.
- No placeholders: every code step is complete.
