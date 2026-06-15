# Forbes Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backtest-only ICT "Forbes Model" engine (`engine="forbes"`) for NQ/MNQ 1-min that trades the NY open toward session liquidity via opening-range FVGs, then measure its real edge (vs the 75–80% claim) with ablations + the funded harness.

**Architecture:** A new `ForbesComposer` (`app/strategy/forbes.py`) reuses existing detectors (`DisplacementDetector`, `LiquidityTracker`, `ORBDetector`, `KillzoneLevelTracker`) on a single 1-min feed, aggregates 1m→15m internally for swing POIs, and adds Forbes-specific glue: the 09:30–10:30 ET killzone, the OR-must-contain-FVG gate, entry-priority orchestration, and a nearest-unswept-session-liquidity target + min-RR gate. Selected via `_build_runner` like the other engines. Default-off.

**Tech Stack:** Python 3.12, pydantic (`StrategyParams`), `zoneinfo` ET, pytest. Run tests with `.venv/Scripts/python.exe -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-15-forbes-model-design.md`

---

## File structure

- **Modify** `app/bot_config.py` — add `forbes_*` params to `StrategyParams`.
- **Create** `app/strategy/forbes.py` — `ForbesConfig`, `_FifteenMinAggregator`, `ForbesPOIMap`, `ForbesDetector`, `ForbesRunner`. One file (the engine modules in this repo, e.g. `orb.py`, are single-file engine+runner; follow that pattern).
- **Modify** `app/main.py` (`_build_runner`) — dispatch `engine == "forbes"`.
- **Modify** `app/strategy/kz_levels.py` — ensure Asia + prior-day-NY session ranges are tracked (extend the zone set the tracker consumes).
- **Create** `tests/test_forbes.py` — unit/defining-behavior tests.
- **Create** `scripts/run_forbes_backtest.py` — 5y backtest + ablations + funded harness, report metrics.

Reference interfaces (read before coding): `app/strategy/composer.py` (`Signal` dataclass, fields: instrument, side, entry, stop, target, created_at, killzone, sweep_pattern, sweep_extreme, fvg_low, fvg_high, rationale), `app/strategy/orb.py` (Detector→`on_bar(bar)->Signal|None`, `state()`; Runner pattern), `app/strategy/liquidity.py` (`LiquidityTracker.on_bar(bar, atr)->list[SweepEvent]`, `.swings()`, `.recent_high_swings()`, `.recent_low_swings()`, `Swing`, `SweepEvent`), `app/strategy/displacement.py` (`DisplacementDetector.on_bar`, `.active_fvgs`, `.peek_displacement()`, `.atr`, `FairValueGap`), `app/strategy/kz_levels.py` (`KillzoneLevelTracker.on_bar`, `_kz_ranges`), `app/strategy/killzone.py` (`asia()`, `london_open()`, `ny_am()`, `ET`, `in_killzone`), `app/broker/events.py` (`Bar`: instrument, timeframe, ts, open, high, low, close, volume).

---

## Task 1: Config params

**Files:** Modify `app/bot_config.py` (`StrategyParams`)

- [ ] **Step 1: Add fields.** After the `news_straddle_*` / `cpi_day_router_enabled` block in `StrategyParams`, add:

```python
    # Forbes Model (ICT session-liquidity engine; engine="forbes"). Backtest-only,
    # default-off. All discretionary rules are params (spec 2026-06-15-forbes-model).
    forbes_killzone_et: str = "09:30-10:30"      # active window (ET); outside it: no trades
    forbes_or_open_et: str = "09:30"             # opening-range start (ET) = 06:30 PST
    forbes_or_minutes: int = 15                  # OR length (first 15 1-min candles)
    forbes_or_min_fvgs: int = 1                  # OR must hold >= this many FVGs, else stand aside
    forbes_poi_swing_tf_min: int = 15            # timeframe (min) for swing-POI detection
    forbes_target_mode: str = "liquidity"        # "liquidity" | "or_top" | "midway_poi"
    forbes_min_rr: Decimal = Decimal("1.4")      # skip setups whose RR is below this
    forbes_stop_mode: str = "beyond_wick"        # "beyond_wick" | "beyond_or"
    forbes_max_trades_per_day: int = 1
    # session windows (ET) for the liquidity map; comma "HH:MM-HH:MM" per session
    forbes_asia_et: str = "18:00-00:00"
    forbes_london_et: str = "02:00-05:00"
    forbes_prior_ny_et: str = "09:30-16:00"
```

- [ ] **Step 2: Verify load + defaults.** Run: `.venv/Scripts/python.exe -c "from app.bot_config import StrategyParams; p=StrategyParams(); print(p.engine, p.forbes_target_mode, p.forbes_min_rr, p.forbes_max_trades_per_day)"`
Expected: `ifvg liquidity 1.4 1`

- [ ] **Step 3: Commit.**
```bash
git add app/bot_config.py
git commit -m "feat(forbes): add forbes_* config params (default-off)"
```

---

## Task 2: 15-min aggregator

**Files:** Create `app/strategy/forbes.py` (start the file); Test: `tests/test_forbes.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_forbes.py
from datetime import datetime, timezone
from decimal import Decimal
from app.broker.events import Bar
from app.strategy.forbes import _FifteenMinAggregator


def _b(minute, o, h, l, c):
    return Bar(instrument="MNQ", timeframe="1min",
               ts=datetime(2026, 5, 11, 13, minute, tzinfo=timezone.utc),
               open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=100)


def test_aggregator_emits_one_15m_bar_per_15_one_min_bars():
    agg = _FifteenMinAggregator(minutes=15)
    out = []
    for m in range(15):  # 13:00..13:14 -> closes the 13:00-13:15 bucket on the 15th
        r = agg.on_bar(_b(m, "100", str(100 + m), str(100 - 1), str(100)))
        if r is not None:
            out.append(r)
    # The 15m bar finalizes when a bar in the NEXT bucket arrives:
    r = agg.on_bar(_b(15, "101", "101", "101", "101"))
    assert r is not None
    assert r.high == Decimal("114")   # max of highs 100..114
    assert r.low == Decimal("99")
    assert r.timeframe == "15min"
```

- [ ] **Step 2: Run to fail.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k aggregator -v` → ImportError / fails.

- [ ] **Step 3: Implement.** Create `app/strategy/forbes.py` with the file header + aggregator:

```python
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
```

- [ ] **Step 4: Run to pass.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k aggregator -v` → PASS.

- [ ] **Step 5: Commit.**
```bash
git add app/strategy/forbes.py tests/test_forbes.py
git commit -m "feat(forbes): 1m->15m aggregator for swing POIs"
```

---

## Task 3: POI / liquidity map

**Files:** Modify `app/strategy/forbes.py`; Test: `tests/test_forbes.py`

The map holds liquidity levels (15m swing H/L + session H/L) each tagged swept/unswept, and answers "nearest unswept opposing level" for a given side + price.

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_forbes.py (append)
from app.strategy.forbes import ForbesPOIMap, _Level


def test_poi_map_nearest_unswept_opposing():
    m = ForbesPOIMap()
    m.add(_Level(price=Decimal("110"), kind="session_high", swept=False))
    m.add(_Level(price=Decimal("120"), kind="swing_high", swept=False))
    m.add(_Level(price=Decimal("105"), kind="session_high", swept=True))  # already swept
    # Long from 100: nearest UNSWEPT level ABOVE = 110 (105 is swept, 120 is farther).
    lvl = m.nearest_unswept_opposing(side="long", price=Decimal("100"))
    assert lvl is not None and lvl.price == Decimal("110")
    # Short from 100: no unswept level below -> None.
    assert m.nearest_unswept_opposing(side="short", price=Decimal("100")) is None


def test_poi_map_mark_swept_when_price_trades_through():
    m = ForbesPOIMap()
    m.add(_Level(price=Decimal("110"), kind="session_high", swept=False))
    m.update_swept(bar_high=Decimal("111"), bar_low=Decimal("108"))
    assert m.nearest_unswept_opposing(side="long", price=Decimal("100")) is None  # 110 now swept
```

- [ ] **Step 2: Run to fail.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k poi_map -v`

- [ ] **Step 3: Implement.** Append to `forbes.py`:

```python
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
```

- [ ] **Step 4: Run to pass.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k poi_map -v` → PASS.

- [ ] **Step 5: Commit.**
```bash
git add app/strategy/forbes.py tests/test_forbes.py
git commit -m "feat(forbes): liquidity/POI map (nearest unswept opposing + sweep tagging)"
```

---

## Task 4: Session-range extension (Asia + prior-NY)

**Files:** Modify `app/strategy/kz_levels.py`; Test: `tests/test_forbes.py` (or extend `tests/test_kz_levels.py`)

`KillzoneLevelTracker` tracks ranges for the killzones it's given. Forbes needs Asia, London, and prior-day NY ranges available as POIs. Verify the tracker accepts the Asia/London/prior-NY zones (they exist: `killzone.asia()`, `london_open()`, `ny_am()`), and expose a `locked_ranges()` accessor returning `{name: (high, low)}` for the composer to consume.

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_forbes.py (append)
from app.strategy.kz_levels import KillzoneLevelTracker
from app.strategy import killzone as kz


def test_kz_tracker_exposes_locked_session_ranges():
    t = KillzoneLevelTracker()
    assert hasattr(t, "locked_ranges")
    assert t.locked_ranges() == {}   # empty before any session locks
```

- [ ] **Step 2: Run to fail.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k locked_session -v`

- [ ] **Step 3: Implement.** In `app/strategy/kz_levels.py`, add a read-only accessor to `KillzoneLevelTracker`:

```python
    def locked_ranges(self) -> "dict[str, tuple[Decimal, Decimal]]":
        """Session ranges that have closed/locked this day. {name: (high, low)}. Pure read."""
        return dict(self._kz_ranges)
```

Confirm by reading `kz_levels.py` that `_kz_ranges` is populated on session close (it is — see `_kz_ranges[zone.name] = (high, low)` in `on_bar`). No behavior change; additive accessor only.

- [ ] **Step 4: Run to pass.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k locked_session -v` → PASS. Then `pytest tests/test_kz_levels.py -q` → still green (no regression).

- [ ] **Step 5: Commit.**
```bash
git add app/strategy/kz_levels.py tests/test_forbes.py
git commit -m "feat(forbes): expose locked_ranges() on KillzoneLevelTracker"
```

---

## Task 5: ForbesDetector — killzone + OR-FVG gate + day-state

**Files:** Modify `app/strategy/forbes.py`; Test: `tests/test_forbes.py`

`ForbesDetector` owns the reused detectors and per-day state: is `ts` in the killzone; is the OR locked and did it hold ≥ `or_min_fvgs` FVGs; trades-taken count. This task builds the GATES (no entries yet).

- [ ] **Step 1: Write the failing tests.**

```python
# tests/test_forbes.py (append)
from app.bot_config import StrategyParams
from app.strategy.forbes import ForbesConfig, ForbesDetector


def _cfg(**kw):
    s = StrategyParams(engine="forbes", **kw)
    return ForbesConfig.from_params("MNQ", s)


def test_killzone_gate_blocks_outside_window():
    d = ForbesDetector(_cfg())
    # 11:00 ET (15:00 UTC summer) is past the 09:30-10:30 window.
    bar = _b(0, "100", "100", "100", "100")  # 13:00 UTC = 09:00 ET -> before window
    assert d.in_killzone(bar.ts) is False


def test_or_fvg_gate_stands_aside_when_no_fvg():
    d = ForbesDetector(_cfg())
    # After OR closes with 0 FVGs, the day is stood-aside.
    d._or_locked = True
    d._or_fvg_count = 0
    assert d.day_eligible() is False
    d._or_fvg_count = 1
    assert d.day_eligible() is True
```

- [ ] **Step 2: Run to fail.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k "killzone_gate or or_fvg_gate" -v`

- [ ] **Step 3: Implement.** Append to `forbes.py`. `ForbesConfig` holds parsed params + the ET windows; `ForbesDetector` wires reused detectors and the gates. Use `app.strategy.killzone.ET` and parse `"HH:MM-HH:MM"`.

```python
from datetime import time as _time
from app.strategy.killzone import ET
from app.strategy.displacement import DisplacementConfig, DisplacementDetector
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
from app.strategy.kz_levels import KillzoneLevelTracker


def _parse_window(s: str) -> "tuple[_time, _time]":
    a, b = s.split("-")
    h1, m1 = (int(x) for x in a.split(":")); h2, m2 = (int(x) for x in b.split(":"))
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
```

- [ ] **Step 4: Run to pass.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k "killzone_gate or or_fvg_gate" -v` → PASS.

- [ ] **Step 5: Commit.**
```bash
git add app/strategy/forbes.py tests/test_forbes.py
git commit -m "feat(forbes): ForbesConfig + detector gates (killzone, OR-FVG eligibility)"
```

---

## Task 6: ForbesDetector.on_bar — entry models, stop, liquidity target, min-RR

**Files:** Modify `app/strategy/forbes.py`; Test: `tests/test_forbes.py`

This is the core. `on_bar(bar) -> Signal | None`: feed reused detectors; build the OR (lock at OR end, count FVGs); on each 15m close, refresh swing POIs; refresh session POIs from `kz_levels.locked_ranges()`; mark POIs swept; if eligible + in killzone + under max-trades, search triggers in priority order; on a trigger build the Signal (stop per `stop_mode`, target = nearest unswept opposing per `target_mode`, skip if RR < `min_rr`).

- [ ] **Step 1: Write the failing tests** (defining behavior — encode WHY):

```python
# tests/test_forbes.py (append)
from app.strategy.composer import Signal


def test_min_rr_gate_skips_low_rr_setup(monkeypatch):
    # WHY: a setup whose nearest-liquidity target is too close (RR < min_rr) must NOT trade.
    d = ForbesDetector(_cfg(forbes_min_rr="3.0"))
    sig = d._build_signal(side="long", entry=Decimal("100"), stop=Decimal("90"),
                          target=Decimal("110"), bar=_b(0,"100","100","100","100"),
                          pattern="ifvg", sweep_level=Decimal("90"))
    assert sig is None  # RR = (110-100)/(100-90) = 1.0 < 3.0


def test_build_signal_uses_liquidity_target_and_passes_min_rr():
    # WHY: a valid setup targets the liquidity level and emits a Signal with correct R geometry.
    d = ForbesDetector(_cfg(forbes_min_rr="1.4"))
    sig = d._build_signal(side="long", entry=Decimal("100"), stop=Decimal("90"),
                          target=Decimal("125"), bar=_b(0,"100","100","100","100"),
                          pattern="ifvg", sweep_level=Decimal("90"))
    assert sig is not None
    assert sig.side == "long" and sig.target == Decimal("125") and sig.stop == Decimal("90")


def test_max_trades_per_day_enforced():
    # WHY: with max_trades_per_day=1, a second eligible trigger the same day is suppressed.
    d = ForbesDetector(_cfg(forbes_max_trades_per_day=1))
    d._trades_today = 1
    assert d._can_trade() is False
```

- [ ] **Step 2: Run to fail.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k "min_rr or build_signal or max_trades" -v`

- [ ] **Step 3: Implement.** Append the signal-builder + helpers, then the `on_bar` orchestration. Build `Signal` with the real fields (Task ref: `composer.Signal`). The entry-trigger detection delegates to the reused detectors: iFVG via `self.displacement.active_fvgs` + inversion check; sweep+displacement via `self.liquidity.on_bar(...)` SweepEvents + `self.displacement.peek_displacement()`; breakout+retest via OR levels. Read `composer.py`'s existing iFVG inversion logic and reuse its approach for the inversion test.

```python
    def _can_trade(self) -> bool:
        return self._trades_today < self.config.max_trades_per_day

    def _build_signal(self, *, side, entry, stop, target, bar, pattern, sweep_level):
        stop_dist = abs(entry - stop)
        if stop_dist == 0:
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
        # or_top / midway_poi handled in Task 7's ablation wiring; default None -> skip.
        return None
```

The `on_bar` body (orchestration — wire the reused detectors; pseudocode-to-real, no placeholders: each branch calls a real method shown above / on the reused detectors):

```python
    def on_bar(self, bar):
        # new ET day -> reset
        et_date = self._et(bar.ts).date()
        if et_date != self._day:
            self._day = et_date
            self._or_locked = False; self._or_fvg_count = 0; self._trades_today = 0
            self.poi.reset_day()
        atr = self.displacement.atr
        self.displacement.on_bar(bar)
        self.liquidity.on_bar(bar, atr)
        self.kz_levels.on_bar(bar, _zones_for_forbes())  # zones built in Task 7 wiring
        # refresh session POIs
        for name, (hi, lo) in self.kz_levels.locked_ranges().items():
            self.poi.add(_Level(hi, "session_high")); self.poi.add(_Level(lo, "session_low"))
        # refresh 15m swing POIs
        m15 = self.agg.on_bar(bar)
        if m15 is not None:
            for sw in self.liquidity.recent_high_swings():
                self.poi.add(_Level(sw.price, "swing_high"))
            for sw in self.liquidity.recent_low_swings():
                self.poi.add(_Level(sw.price, "swing_low"))
        self.poi.update_swept(bar.high, bar.low)
        # OR window: lock at end, count FVGs inside
        self._maybe_lock_or(bar)
        if not (self.in_killzone(bar.ts) and self.day_eligible() and self._can_trade()):
            return None
        sig = self._search_triggers(bar)   # returns Signal|None; uses _build_signal/_select_target
        if sig is not None:
            self._trades_today += 1
        return sig
```

Implement `_maybe_lock_or` (track OR high/low over the `[or_open, or_open+or_minutes)` window; at first bar past the window set `_or_locked=True` and `_or_fvg_count = len(self.displacement.active_fvgs)`), `_search_triggers` (priority: iFVG inversion of an active FVG after a POI sweep → sweep+displacement off a session level → OR breakout+retest; each computes side/entry/stop via `stop_mode` and target via `_select_target`, then `_build_signal`), and a module `_zones_for_forbes()` helper returning the Asia/London/NY `Killzone` objects from `app.strategy.killzone`. **Read `app/strategy/composer.py` `_Awaiting`/inversion logic and `app/strategy/orb.py` OR-window handling and mirror them** — do not invent new FVG/inversion math.

- [ ] **Step 4: Run to pass.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -v` → all PASS. If a trigger branch is hard to unit-test in isolation, add a small bar-sequence integration test that drives `on_bar` through a constructed sweep→displacement→FVG→inversion sequence and asserts exactly one Signal with the expected side/target.

- [ ] **Step 5: Commit.**
```bash
git add app/strategy/forbes.py tests/test_forbes.py
git commit -m "feat(forbes): on_bar entry models + stop/liquidity-target/min-RR signal build"
```

---

## Task 7: Engine wiring (ForbesRunner + _build_runner)

**Files:** Modify `app/strategy/forbes.py` (add `ForbesRunner`), `app/main.py` (`_build_runner`); Test: `tests/test_forbes.py`

- [ ] **Step 1: Write the failing test.**

```python
# tests/test_forbes.py (append)
def test_build_runner_dispatches_forbes():
    from app.main import _build_runner
    from app.bot_config import StrategyParams
    r = _build_runner("MNQ", StrategyParams(engine="forbes"), None, "1min", None)
    assert r.__class__.__name__ == "ForbesRunner"
    assert r.instrument == "MNQ"
```

- [ ] **Step 2: Run to fail.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k build_runner_dispatches -v`

- [ ] **Step 3: Implement.** Add `ForbesRunner` to `forbes.py` mirroring `ORBRunner` (`app/strategy/orb.py:253`) — it holds the detector, exposes `instrument`, `timeframe`, `signal_instrument`, and `on_bar(bar)->Signal|None` delegating to the detector. Then in `app/main.py:_build_runner`, add before the final `ifvg` fallthrough:

```python
    if s.engine == "forbes":
        from app.strategy.forbes import ForbesConfig, ForbesDetector, ForbesRunner
        return ForbesRunner(
            instrument=instrument,
            timeframe=timeframe,
            detector=ForbesDetector(ForbesConfig.from_params(instrument, s)),
            signal_instrument=signal_instrument or "",
        )
```

- [ ] **Step 4: Run to pass.** `.venv/Scripts/python.exe -m pytest tests/test_forbes.py -k build_runner_dispatches -v` → PASS. Then `.venv/Scripts/python.exe -c "import app.main"` clean.

- [ ] **Step 5: Commit.**
```bash
git add app/strategy/forbes.py app/main.py tests/test_forbes.py
git commit -m "feat(forbes): ForbesRunner + engine=forbes dispatch in _build_runner"
```

---

## Task 8: Backtest harness + ablations + report

**Files:** Create `scripts/run_forbes_backtest.py`

- [ ] **Step 1: Write the harness.** Model it on an existing engine backtest script (read `scripts/run_b90_pipeline.py` or the ORB equity-export path for the exact `run_backtest`/equity-export API). It must:
  - load `bars/bars_MNQ_dbv_2021_2026.csv`, EXCLUDE 2022 from headline;
  - run `engine="forbes"` defaults → collect per-trade results (entry, exit, side, R, $);
  - print **win rate, RR distribution (p25/50/75), expectancy ($ and R), max drawdown, trade count**, per year;
  - run ablations: (a) `forbes_killzone_et="00:00-23:59"` (killzone off) vs default; (b) FVG-present vs FVG-absent days (group by `_or_fvg_count`); (c) `forbes_target_mode` in {liquidity, or_top, midway_poi}; (d) `forbes_min_rr` in {1.0, 1.4, 2.0};
  - run the funded harness (`funded_sim`, haircut 0/200/400) for combine pass-rate + XFA payout (copy the call pattern from `scripts/run_b99_*`/`run_b90_pipeline.py`);
  - state the measured win rate plainly vs the author's 75–80% claim.

- [ ] **Step 2: Run it.** `.venv/Scripts/python.exe scripts/run_forbes_backtest.py 2>&1 | tee research/forbes_backtest.txt`
Expected: a metrics table prints; trade count > 0 (if 0, the entry logic isn't firing — debug Task 6 before proceeding).

- [ ] **Step 3: Write up.** Create `trade_analysis/2026-06-15_forbes_model.md` with the headline table, ablation results, the funded verdict, and the discretionary assumptions used. State GO/NO-GO vs a fixed-R / existing-engine baseline.

- [ ] **Step 4: Commit.**
```bash
git add scripts/run_forbes_backtest.py trade_analysis/2026-06-15_forbes_model.md
git commit -m "feat(forbes): backtest harness + ablations + write-up (measure real win rate)"
```

---

## Task 9: Full verification

- [ ] **Step 1: Full suite.** `.venv/Scripts/python.exe -m pytest -q` → all pass (816+ baseline + new forbes tests; 0 failures).
- [ ] **Step 2: Default-off no-op.** `.venv/Scripts/python.exe -c "from app.bot_config import StrategyParams; assert StrategyParams().engine=='ifvg'; print('forbes default-off OK')"`
- [ ] **Step 3: Commit (if anything uncommitted).**
```bash
git add -A && git commit -m "test(forbes): full suite green"
```

---

## Notes / known limitations (carry from spec — do not silently fix)
- Intrabar fidelity: same-bar stop-vs-target uses **stop-first** (conservative); the harness must implement this in its fill logic.
- The 15m feed is internal aggregation (Task 2), NOT the runner HTF feed — deliberate (avoids the `run_backtest` HTF-feed gap, [[project_backtest_no_htf_feed]]).
- Expect the measured win rate to be far below the 75–80% claim (annotated-winner selection bias). A confirmatory "no edge" is a valid result.
- Forbes ships **default-off, backtest-only** (`engine="forbes"`); no live wiring in this plan.
