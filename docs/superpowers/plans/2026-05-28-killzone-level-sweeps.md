# Killzone Level Sweeps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add killzone session H/L levels (London high/low, NY AM high/low, etc.) as a parallel sweep-displacement signal source, tagged `source="kz_level"` so analytics can split performance by source.

**Architecture:** A new `KillzoneLevelTracker` accumulates each killzone's H/L while it's open, locks the range when it closes, then detects pattern-A/B sweeps of those levels. It feeds `SweepEvent`s into the existing `SweepDisplacementComposer` alongside the existing `LiquidityTracker`. The composer gains a `source` field on `_Awaiting` and `Signal` with default `"swing"` so all existing behavior is unchanged when `kz_levels_enabled=False`.

**Tech Stack:** Python 3.12, FastAPI, React/TypeScript/Tailwind, pytest. Reuses `SweepEvent`, `Swing`, `LiquidityConfig.min_penetration`, `LiquidityConfig.multi_bar_window` from existing modules.

---

## Background for the implementer (read once)

- **`SweepEvent`** (`app/strategy/liquidity.py:65`): frozen dataclass with `side: SweepSide`, `swept_swing: Swing`, `pattern: Literal["A_multi_bar","B_one_bar"]`, `sweep_extreme: Decimal`, `completed_at: datetime`. KZ levels construct a **synthetic** `Swing` (since levels come from session ranges, not point swings). `bar_ts == confirmed_ts` for synthetics.
- **`Signal`** (`app/strategy/composer.py:54`): frozen dataclass — adding a field requires `source: str = "swing"` at the END to avoid breaking the journal/CSV via keyword-arg default.
- **`_Awaiting`** (`app/strategy/composer.py:115`): mutable dataclass (not frozen); `source: str = "swing"` added as the last field.
- **`on_sweep()`** signature today (`composer.py:170`): `def on_sweep(self, bar, sweep)`. Caller (`StrategyRunner.on_bar`, `engine.py:119`) calls it as `runner.composer.on_sweep(bar, s)`. The `source` param is added as a keyword arg with default so the existing call site works unchanged.
- **`StrategyRunner`** (`engine.py:97`): a `@dataclass` — new fields go after `vp`. The field is `kz_levels: "KillzoneLevelTracker | None" = None` (string annotation avoids circular import since `kz_levels.py` will import from `liquidity.py` which `engine.py` also imports).
- **`_build_runner`** (`main.py:95`): constructs a `StrategyRunner`. Add `kz_levels=KillzoneLevelTracker() if s.kz_levels_enabled else None` and import the new class.
- **`PATCH /api/config` hot-apply** (`server.py:449`): the existing pattern iterates `_engine.runners.values()` to update per-runner state. Mirror how `runner.vp` is enabled/disabled in the reload handler.
- **Run tests with the venv python:** `.venv/Scripts/python.exe -m pytest ...` (plain `python` lacks pydantic).

---

## File structure

| File | Action | Responsibility |
|------|--------|---------------|
| `app/strategy/kz_levels.py` | **Create** | `KillzoneLevelTracker` — accumulate, finalize, detect sweeps |
| `tests/test_kz_levels.py` | **Create** | Unit tests for KZ level tracker |
| `app/strategy/composer.py` | Modify | Add `source` to `_Awaiting`, `Signal`, `on_sweep` signature |
| `tests/test_strategy.py` | Modify | Tests for `Signal.source` tagging |
| `app/execution/engine.py` | Modify | `kz_levels` field on `StrategyRunner`, wire into `on_bar` |
| `app/bot_config.py` | Modify | `kz_levels_enabled: bool = True` on `StrategyParams` |
| `app/main.py` | Modify | `_build_runner` constructs `KillzoneLevelTracker` |
| `app/api/server.py` | Modify | Hot-apply `kz_levels_enabled` in `PATCH /api/config` |
| `frontend/src/types.ts` | Modify | `kz_levels_enabled: boolean` on strategy type |
| `frontend/src/components/ConfigPanel.tsx` | Modify | Toggle row + form serialization |

---

## Task 1: `KillzoneLevelTracker` — pure module

**Files:**
- Create: `app/strategy/kz_levels.py`
- Create: `tests/test_kz_levels.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_kz_levels.py`:

```python
from datetime import datetime, timezone, time as dtime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from app.sim.events import Bar
from app.bot_config import StrategyParams
from app.strategy.kz_levels import KillzoneLevelTracker
from app.strategy.killzone import Killzone, london_open, ny_am

ET = ZoneInfo("America/New_York")


def _bar(ts: datetime, h, l, c) -> Bar:
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts,
        open=Decimal(str(c)), high=Decimal(str(h)),
        low=Decimal(str(l)), close=Decimal(str(c)), volume=1,
    )


def _ts(hour: int, minute: int = 0, tz=ET) -> datetime:
    """2026-05-28 at the given hour:minute in tz."""
    return datetime(2026, 5, 28, hour, minute, tzinfo=tz)


# London open zone: 02:00–05:00 ET
LONDON = london_open()
NY_AM  = ny_am()


def test_accumulates_high_low_during_zone():
    """H/L accumulates while the killzone is active."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams()
    zones = [LONDON]

    tracker.on_bar(_bar(_ts(2, 0), h=101, l=99, c=100), zones, cfg)
    tracker.on_bar(_bar(_ts(2, 1), h=103, l=98, c=101), zones, cfg)
    # Zone is still open — levels not finalized yet, no sweeps possible.
    assert not tracker._kz_ranges


def test_finalizes_level_after_zone_closes():
    """Once the killzone closes, H/L is locked into _kz_ranges."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams()

    tracker.on_bar(_bar(_ts(2, 0), h=101, l=99, c=100), [LONDON], cfg)
    tracker.on_bar(_bar(_ts(2, 1), h=103, l=98, c=101), [LONDON], cfg)
    # Bar after London ends (05:00 ET)
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)

    assert "London" in tracker._kz_ranges
    high, low = tracker._kz_ranges["London"]
    assert high == Decimal("103")
    assert low == Decimal("98")


def test_pattern_b_sweep_of_kz_high():
    """One-bar sweep: bar.high >= kz_high + min_pen AND bar.close < kz_high."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams(min_penetration=Decimal("0.20"))

    # Accumulate London H/L: high=103, low=98
    tracker.on_bar(_bar(_ts(2, 0), h=103, l=98, c=100), [LONDON], cfg)
    # Close London zone
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)

    # Pattern B: tag high (103.3 >= 103 + 0.2) and close below (102.5 < 103)
    sweeps = tracker.on_bar(_bar(_ts(9, 0), h=103.3, l=102, c=102.5), [LONDON, NY_AM], cfg)
    assert len(sweeps) == 1
    s = sweeps[0]
    assert s.side == "high"
    assert s.pattern == "B_one_bar"
    assert s.swept_swing.price == Decimal("103")


def test_pattern_b_sweep_of_kz_low():
    """One-bar sweep of a KZ low level."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams(min_penetration=Decimal("0.20"))

    tracker.on_bar(_bar(_ts(2, 0), h=103, l=98, c=100), [LONDON], cfg)
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)

    # Pattern B low: bar.low <= kz_low - min_pen AND bar.close > kz_low
    sweeps = tracker.on_bar(_bar(_ts(9, 0), h=98.5, l=97.7, c=98.3), [LONDON, NY_AM], cfg)
    assert len(sweeps) == 1
    assert sweeps[0].side == "low"
    assert sweeps[0].swept_swing.price == Decimal("98")


def test_pattern_a_sweep_multi_bar():
    """Multi-bar sweep: tag doesn't close back same bar, but closes back next bar."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams(min_penetration=Decimal("0.20"), multi_bar_window=3)

    tracker.on_bar(_bar(_ts(2, 0), h=103, l=98, c=100), [LONDON], cfg)
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)

    # Tag bar: high crosses (103.3 >= 103.2) but close DOES NOT confirm (close=103.1 >= 103)
    sweeps1 = tracker.on_bar(_bar(_ts(9, 0), h=103.3, l=102, c=103.1), [LONDON, NY_AM], cfg)
    assert len(sweeps1) == 0  # no sweep yet

    # Close-back bar: close falls back below kz_high
    sweeps2 = tracker.on_bar(_bar(_ts(9, 1), h=103.0, l=101, c=102.5), [LONDON, NY_AM], cfg)
    assert len(sweeps2) == 1
    assert sweeps2[0].pattern == "A_multi_bar"


def test_level_consumed_after_sweep():
    """Once swept, a KZ level is removed and does not produce a second sweep."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams(min_penetration=Decimal("0.20"))

    tracker.on_bar(_bar(_ts(2, 0), h=103, l=98, c=100), [LONDON], cfg)
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)

    tracker.on_bar(_bar(_ts(9, 0), h=103.3, l=102, c=102.5), [LONDON, NY_AM], cfg)
    # Second sweep attempt — level should be gone.
    sweeps = tracker.on_bar(_bar(_ts(9, 1), h=103.3, l=102, c=102.5), [LONDON, NY_AM], cfg)
    assert len(sweeps) == 0


def test_daily_reset_clears_all_state():
    """On a new UTC date, all ranges and pending state resets."""
    tracker = KillzoneLevelTracker()
    cfg = StrategyParams()

    # Accumulate and finalize London level on day 1.
    tracker.on_bar(_bar(_ts(2, 0), h=103, l=98, c=100), [LONDON], cfg)
    tracker.on_bar(_bar(_ts(5, 1), h=100, l=99, c=100), [LONDON], cfg)
    assert "London" in tracker._kz_ranges

    # Bar on next UTC day → reset.
    next_day = datetime(2026, 5, 29, 10, 0, tzinfo=timezone.utc)
    tracker.on_bar(_bar(next_day, h=100, l=99, c=100), [LONDON], cfg)
    assert not tracker._kz_ranges
```

- [ ] **Step 2: Run tests to confirm they fail with ImportError**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kz_levels.py -q`
Expected: `ModuleNotFoundError: No module named 'app.strategy.kz_levels'`

- [ ] **Step 3: Implement `KillzoneLevelTracker`**

Create `app/strategy/kz_levels.py`:

```python
"""
Killzone session H/L sweep detector.

Tracks the high and low of each named killzone session (London, NY AM,
etc.) while it is open, locks the range once the session closes, then
detects pattern-A and pattern-B sweeps of those levels. Feeds
SweepEvent objects to the composer alongside LiquidityTracker.

Design: pure module — bars in, SweepEvents out. No I/O, no broker.

Key differences from LiquidityTracker:
  - Levels come from session ranges, not point swings.
  - Each level fires at most once per session (consumed on first sweep).
  - No lookback lag — levels are confirmed as soon as the session closes.
  - Synthetic Swing: bar_ts == confirmed_ts (no lookback delay).
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import TYPE_CHECKING

from app.sim.events import Bar
from app.strategy.killzone import Killzone, in_killzone
from app.strategy.liquidity import Swing, SweepEvent

if TYPE_CHECKING:
    from app.bot_config import StrategyParams

log = logging.getLogger(__name__)


class KillzoneLevelTracker:
    """Per-instrument killzone H/L sweep detector."""

    def __init__(self) -> None:
        # Finalized (high, low) per killzone name for today.
        self._kz_ranges: dict[str, tuple[Decimal, Decimal]] = {}
        # H/L accumulating for currently-open killzones.
        self._active_accum: dict[str, tuple[Decimal, Decimal]] = {}
        # UTC date of last bar; triggers full reset on change.
        self._session_date: date | None = None
        # Pattern A state: level_key -> extreme price that tagged it.
        self._pending_a: dict[str, Decimal] = {}
        # Bars elapsed since tag (for window timeout).
        self._bars_since_tag: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def on_bar(
        self,
        bar: Bar,
        zones: list[Killzone],
        cfg: "StrategyParams",
    ) -> list[SweepEvent]:
        """Process a closed bar. Returns any new sweep events."""

        # 1. Daily reset on UTC date change.
        bar_date = bar.ts.astimezone(timezone.utc).date()
        if bar_date != self._session_date:
            self._kz_ranges = {}
            self._active_accum = {}
            self._pending_a = {}
            self._bars_since_tag = {}
            self._session_date = bar_date

        # 2. Accumulate H/L for active zones.
        for zone in zones:
            active = in_killzone(bar.ts, [zone]) is not None
            if active:
                prev = self._active_accum.get(zone.name)
                if prev is None:
                    self._active_accum[zone.name] = (bar.high, bar.low)
                else:
                    self._active_accum[zone.name] = (
                        max(prev[0], bar.high),
                        min(prev[1], bar.low),
                    )

        # 3. Finalize closed zones that haven't been finalized yet.
        for zone in zones:
            if (
                zone.name in self._active_accum
                and zone.name not in self._kz_ranges
                and in_killzone(bar.ts, [zone]) is None
            ):
                high, low = self._active_accum.pop(zone.name)
                self._kz_ranges[zone.name] = (high, low)
                log.debug(
                    "KZ level locked: %s high=%s low=%s",
                    zone.name, high, low,
                )

        # 4. Sweep detection.
        min_pen = cfg.min_penetration
        window = cfg.multi_bar_window
        events: list[SweepEvent] = []

        for name, (kz_high, kz_low) in list(self._kz_ranges.items()):
            high_key = f"{name}_high"
            low_key = f"{name}_low"

            # ----- Pattern B: one-bar sweep (high side) -----
            if bar.high >= kz_high + min_pen and bar.close < kz_high:
                events.append(self._make_sweep(bar, "high", kz_high, bar.high, "B_one_bar"))
                del self._kz_ranges[name]
                self._pending_a.pop(high_key, None)
                self._bars_since_tag.pop(high_key, None)
                continue  # level consumed; skip low check for this name

            # ----- Pattern B: one-bar sweep (low side) -----
            if bar.low <= kz_low - min_pen and bar.close > kz_low:
                events.append(self._make_sweep(bar, "low", kz_low, bar.low, "B_one_bar"))
                del self._kz_ranges[name]
                self._pending_a.pop(low_key, None)
                self._bars_since_tag.pop(low_key, None)
                continue

            # ----- Pattern A: multi-bar (high side) -----
            if high_key in self._pending_a:
                self._bars_since_tag[high_key] = self._bars_since_tag.get(high_key, 0) + 1
                if bar.close < kz_high:
                    extreme = self._pending_a.pop(high_key)
                    self._bars_since_tag.pop(high_key, None)
                    events.append(self._make_sweep(bar, "high", kz_high, extreme, "A_multi_bar"))
                    del self._kz_ranges[name]
                    continue
                elif self._bars_since_tag.get(high_key, 0) >= window:
                    self._pending_a.pop(high_key, None)
                    self._bars_since_tag.pop(high_key, None)
            elif bar.high >= kz_high + min_pen and bar.close >= kz_high:
                # Tag without close-back — start pattern A tracking.
                self._pending_a[high_key] = bar.high
                self._bars_since_tag[high_key] = 0

            # ----- Pattern A: multi-bar (low side) -----
            if low_key in self._pending_a:
                self._bars_since_tag[low_key] = self._bars_since_tag.get(low_key, 0) + 1
                if bar.close > kz_low:
                    extreme = self._pending_a.pop(low_key)
                    self._bars_since_tag.pop(low_key, None)
                    events.append(self._make_sweep(bar, "low", kz_low, extreme, "A_multi_bar"))
                    del self._kz_ranges[name]
                    continue
                elif self._bars_since_tag.get(low_key, 0) >= window:
                    self._pending_a.pop(low_key, None)
                    self._bars_since_tag.pop(low_key, None)
            elif bar.low <= kz_low - min_pen and bar.close <= kz_low:
                self._pending_a[low_key] = bar.low
                self._bars_since_tag[low_key] = 0

        return events

    @staticmethod
    def _make_sweep(
        bar: Bar,
        side: str,
        level: Decimal,
        extreme: Decimal,
        pattern: str,
    ) -> SweepEvent:
        synthetic_swing = Swing(
            kind=side,          # type: ignore[arg-type]
            price=level,
            bar_ts=bar.ts,
            confirmed_ts=bar.ts,
        )
        return SweepEvent(
            side=side,          # type: ignore[arg-type]
            swept_swing=synthetic_swing,
            pattern=pattern,    # type: ignore[arg-type]
            sweep_extreme=extreme,
            completed_at=bar.ts,
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kz_levels.py -q`
Expected: `7 passed`

- [ ] **Step 5: Commit**

```bash
git add app/strategy/kz_levels.py tests/test_kz_levels.py
git commit -m "feat: KillzoneLevelTracker — session H/L sweep detector"
```

---

## Task 2: `source` tagging in composer

**Files:**
- Modify: `app/strategy/composer.py` — `Signal`, `_Awaiting`, `on_sweep`, `_build_signal`
- Modify: `tests/test_strategy.py` (if it exists) or add to `tests/test_kz_levels.py`

- [ ] **Step 1: Write failing tests for source tagging**

Append to `tests/test_kz_levels.py` (reuse existing test setup; adapt helper names to actual file if needed):

```python
def test_signal_source_defaults_to_swing():
    """Existing callers that don't pass source get 'swing' tag — no regression."""
    from app.strategy.composer import Signal
    from decimal import Decimal
    from datetime import datetime, timezone
    sig = Signal(
        instrument="MGC", side="long",
        entry=Decimal("100"), stop=Decimal("98"), target=Decimal("105"),
        created_at=datetime.now(timezone.utc),
        killzone="NY AM", sweep_pattern="B_one_bar",
        sweep_extreme=Decimal("97.5"),
        fvg_low=Decimal("99"), fvg_high=Decimal("100.5"),
        rationale="test",
    )
    assert sig.source == "swing"


def test_signal_source_kz_level_tag():
    """source='kz_level' can be explicitly set."""
    from app.strategy.composer import Signal
    from decimal import Decimal
    from datetime import datetime, timezone
    sig = Signal(
        instrument="MGC", side="short",
        entry=Decimal("103"), stop=Decimal("105"), target=Decimal("98"),
        created_at=datetime.now(timezone.utc),
        killzone="London", sweep_pattern="B_one_bar",
        sweep_extreme=Decimal("103.5"),
        fvg_low=Decimal("102.5"), fvg_high=Decimal("103"),
        rationale="test",
        source="kz_level",
    )
    assert sig.source == "kz_level"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kz_levels.py::test_signal_source_defaults_to_swing tests/test_kz_levels.py::test_signal_source_kz_level_tag -q`
Expected: FAIL — `Signal.__init__() got an unexpected keyword argument 'source'`

- [ ] **Step 3: Add `source` to `Signal`, `_Awaiting`, `on_sweep`, `_build_signal`**

In `app/strategy/composer.py`:

**a) Add to `Signal` dataclass** — after `rationale: str` (line ~76), add as the LAST field so the frozen dataclass constructor still works with keyword args:

```python
    source: str = "swing"          # "swing" | "kz_level" — for per-source analytics
```

**b) Add to `_Awaiting` dataclass** — after `killzone_name: str` (line ~121):

```python
    source: str = "swing"
```

**c) Update `on_sweep` signature** — line 170, change:

```python
    def on_sweep(self, bar: Bar, sweep: SweepEvent) -> None:
```

to:

```python
    def on_sweep(self, bar: Bar, sweep: SweepEvent, source: str = "swing") -> None:
```

**d) Store `source` in `_Awaiting`** — in the `on_sweep` body, the `self._awaiting.append(...)` call (line ~182), add `source=source`:

```python
        self._awaiting.append(_Awaiting(
            sweep=sweep,
            bars_since_sweep=0,
            killzone_name=zone.name,
            source=source,
        ))
```

**e) Pass `source` through in `_build_signal`** — the `return Signal(...)` at line ~342, add `source=awaiting.source`:

```python
        return Signal(
            instrument=cfg.instrument,
            side=side,
            entry=entry,
            stop=stop,
            target=target,
            created_at=bar.ts,
            killzone=awaiting.killzone_name,
            sweep_pattern=awaiting.sweep.pattern,
            sweep_extreme=awaiting.sweep.sweep_extreme,
            fvg_low=fvg.low,
            fvg_high=fvg.high,
            rationale=rationale,
            source=awaiting.source,
        )
```

- [ ] **Step 4: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_kz_levels.py -q`
Expected: all pass (9 tests now).

Also run the full strategy suite to confirm no regressions:
`.venv/Scripts/python.exe -m pytest tests/test_engine.py tests/test_volume_profile.py tests/test_htf.py -q`
Expected: only the known pre-existing failure (`test_signal_denied_when_already_at_max_contracts`).

- [ ] **Step 5: Commit**

```bash
git add app/strategy/composer.py tests/test_kz_levels.py
git commit -m "feat: Signal.source tagging (swing | kz_level)"
```

---

## Task 3: Engine wiring

**Files:**
- Modify: `app/execution/engine.py` — `StrategyRunner`, `on_bar`, `try_signal_from_forming`
- Modify: `app/bot_config.py` — `StrategyParams.kz_levels_enabled`
- Modify: `app/main.py` — `_build_runner`

No new tests needed here — Task 1 tests the tracker, Task 2 tests source tagging; engine wiring is covered by integration if the existing engine tests still pass.

- [ ] **Step 1: Add `kz_levels_enabled` to `StrategyParams`**

In `app/bot_config.py`, after the `htf_swing_timeframe` line, add:

```python
    kz_levels_enabled: bool = True          # sweep KZ session H/L levels in parallel with swing sweeps
```

- [ ] **Step 2: Add `kz_levels` field to `StrategyRunner`**

In `app/execution/engine.py`, the `StrategyRunner` dataclass (line ~97), after `vp: VolumeProfileTracker | None = None`, add:

```python
    kz_levels: "KillzoneLevelTracker | None" = None
```

Add the import at the top of the file alongside the other strategy imports:

```python
from app.strategy.kz_levels import KillzoneLevelTracker
```

- [ ] **Step 3: Wire KZ levels into `StrategyRunner.on_bar`**

In `app/execution/engine.py`, the `StrategyRunner.on_bar` method (line ~115):

```python
    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """Run all detectors against one bar. Returns at most one Signal."""
        sweeps = self.liquidity.on_bar(bar, atr=self._prev_atr)
        for s in sweeps:
            self.composer.on_sweep(bar, s, source="swing")

        # KZ level sweeps — parallel signal source, runs only when enabled.
        if self.kz_levels is not None:
            for s in self.kz_levels.on_bar(bar, self.composer._zones, ...):
                self.composer.on_sweep(bar, s, source="kz_level")
```

Wait — `self.composer._zones` is a private attribute. Check the composer source: `_zones` is set in `__init__` as `self._zones = config.killzones or default_killzones()`. It's private but accessible within the same package. Use it as-is — accessing a private attribute within the same package is acceptable here since the alternative (adding a public property) is unnecessary complexity.

The full replacement for `on_bar` in `StrategyRunner`:

```python
    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """Run all detectors against one bar. Returns at most one Signal."""
        sweeps = self.liquidity.on_bar(bar, atr=self._prev_atr)
        for s in sweeps:
            self.composer.on_sweep(bar, s, source="swing")

        if self.kz_levels is not None:
            for s in self.kz_levels.on_bar(bar, self.composer._zones, self._strategy_cfg):
                self.composer.on_sweep(bar, s, source="kz_level")

        signal: Optional[Signal] = None
        disp = self.displacement.on_bar(bar)
        if disp is not None:
            signal = self.composer.on_displacement(bar, disp)

        self._prev_atr = self.displacement.atr
        self.composer.on_bar_close(bar)
        return signal
```

BUT — `StrategyRunner.on_bar` currently doesn't have `self._strategy_cfg`. The `StrategyParams` is available in `ExecutionEngine.strategy_cfg` but NOT in `StrategyRunner`. Two options:
1. Store `cfg` on `StrategyRunner` at build time (the `min_penetration` etc. are already baked into `LiquidityConfig`, so this is a second reference)
2. Pass `cfg` to `kz_levels.on_bar` from the engine's `on_bar` loop (where `self.strategy_cfg` IS available)

Option 2 is cleaner. Keep `StrategyRunner.on_bar` unchanged and wire KZ levels in the **engine's `on_bar` loop** instead, which already has `self.strategy_cfg`. The spec says the same — "After the existing sweep block" in the engine.

**Actual implementation:** in `ExecutionEngine.on_bar` (around line 307-311 where `signal = runner.on_bar(bar)` is called), add the KZ sweep block BEFORE calling `runner.on_bar`:

```python
        # Run KZ level sweep detector (parallel to LiquidityTracker inside runner.on_bar).
        if runner.kz_levels is not None and self.strategy_cfg is not None:
            for s in runner.kz_levels.on_bar(bar, runner.composer._zones, self.strategy_cfg):
                runner.composer.on_sweep(bar, s, source="kz_level")

        try:
            signal = runner.on_bar(bar)
        ...
```

Keep `StrategyRunner.on_bar` exactly as it is — just revert the `source="swing"` change inside it since the on_sweep call there already works without it (the default is "swing"). Actually, we DO want to add `source="swing"` to the existing call in `StrategyRunner.on_bar` to be explicit:

```python
    def on_bar(self, bar: Bar) -> Optional[Signal]:
        """Run all detectors against one bar. Returns at most one Signal."""
        sweeps = self.liquidity.on_bar(bar, atr=self._prev_atr)
        for s in sweeps:
            self.composer.on_sweep(bar, s, source="swing")   # explicit tag

        signal: Optional[Signal] = None
        disp = self.displacement.on_bar(bar)
        if disp is not None:
            signal = self.composer.on_displacement(bar, disp)

        self._prev_atr = self.displacement.atr
        self.composer.on_bar_close(bar)
        return signal
```

And in `ExecutionEngine.on_bar`, add the KZ block before `runner.on_bar()`:

```python
        # KZ level sweeps fire before runner.on_bar() so both sources feed the
        # composer in the same bar, and the composer picks up whichever is freshest.
        if runner.kz_levels is not None and self.strategy_cfg is not None:
            for s in runner.kz_levels.on_bar(
                bar, runner.composer._zones, self.strategy_cfg
            ):
                runner.composer.on_sweep(bar, s, source="kz_level")
```

- [ ] **Step 4: Update `_build_runner` in `main.py`**

Add the import near the top of `app/main.py` alongside other strategy imports:

```python
from app.strategy.kz_levels import KillzoneLevelTracker
```

In `_build_runner` (line ~95), change the `return StrategyRunner(...)` call to include `kz_levels`:

```python
    return StrategyRunner(
        instrument=instrument,
        timeframe=timeframe,
        liquidity=LiquidityTracker(LiquidityConfig(
            swing_lookback=s.swing_lookback,
            min_penetration=s.min_penetration,
            multi_bar_window=s.multi_bar_window,
            max_swings=50,
            min_penetration_atr_factor=s.min_penetration_atr_factor if s.min_penetration_atr_factor > 0 else None,
        )),
        displacement=DisplacementDetector(DisplacementConfig(
            atr_period=s.atr_period,
            body_atr_multiple=s.body_atr_multiple,
            min_body_to_range_ratio=s.min_body_to_range_ratio,
            min_absolute_body=s.min_absolute_body,
        )),
        composer=SweepDisplacementComposer(ComposerConfig(
            instrument=instrument,
            displacement_window_bars=s.displacement_window_bars,
            stop_buffer=s.stop_buffer,
            r_multiple=s.r_multiple,
            killzones=zones,
            trend_ema_period=s.trend_ema_period,
            cooldown_bars_after_stop=s.cooldown_bars_after_stop,
            min_atr_filter=s.min_atr_filter,
            max_atr_filter=s.max_atr_filter,
        )),
        vp=VolumeProfileTracker(),
        kz_levels=KillzoneLevelTracker() if s.kz_levels_enabled else None,
    )
```

- [ ] **Step 5: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_backtest.py --ignore=tests/test_analytics_loader.py --ignore=tests/test_analytics_tools.py`
Expected: only the 5 known pre-existing failures.

Also run a quick smoke check to confirm imports work:
`.venv/Scripts/python.exe -c "from app.strategy.kz_levels import KillzoneLevelTracker; from app.execution.engine import StrategyRunner; print('OK')"`

- [ ] **Step 6: Commit**

```bash
git add app/strategy/composer.py app/execution/engine.py app/bot_config.py app/main.py
git commit -m "feat: wire KillzoneLevelTracker into StrategyRunner bar loop"
```

---

## Task 4: Hot-apply + frontend

**Files:**
- Modify: `app/api/server.py` — `PATCH /api/config` hot-apply
- Modify: `frontend/src/types.ts` — add `kz_levels_enabled`
- Modify: `frontend/src/components/ConfigPanel.tsx` — toggle row + form serialization

- [ ] **Step 1: Add hot-apply to PATCH /api/config**

In `app/api/server.py`, add this import near the other strategy imports at the top:

```python
from app.strategy.kz_levels import KillzoneLevelTracker
```

In `patch_config` (around line 449), after the `_engine.strategy_cfg = body.strategy` line and the existing HTF rebuild block, add:

```python
        # Hot-apply kz_levels_enabled: wire or unwire per-runner.
        if _engine is not None:
            for runner in _engine.runners.values():
                if body.strategy.kz_levels_enabled and runner.kz_levels is None:
                    runner.kz_levels = KillzoneLevelTracker()
                elif not body.strategy.kz_levels_enabled and runner.kz_levels is not None:
                    runner.kz_levels = None
```

- [ ] **Step 2: Add `kz_levels_enabled` to the TS strategy type**

In `frontend/src/types.ts`, in the strategy interface (after `htf_swing_timeframe: string`), add:

```typescript
  kz_levels_enabled: boolean
```

- [ ] **Step 3: Add ConfigPanel toggle row**

In `frontend/src/components/ConfigPanel.tsx`, after the `htf_target_min_r` slider row (just before the closing `]` of the fields array, around line 172), add:

```typescript
  {
    key: 'kz_levels_enabled', label: 'KZ Level Sweeps', type: 'select', section: 'strategy',
    options: ['true', 'false'],
    hint: 'Detect sweeps of completed killzone session highs and lows (e.g. London high, NY AM low) as a parallel signal source tagged kz_level. Runs alongside the existing swing sweep detector.',
  },
```

- [ ] **Step 4: Add form serialization**

In `ConfigPanel.tsx`, in the `handleSave` strategy payload block (near `htf_target_min_r: form.htf_target_min_r || '2.0'`), add:

```typescript
      kz_levels_enabled: form.kz_levels_enabled !== 'false',
```

Note: `kz_levels_enabled` defaults TRUE (unlike the htf flags), so use the `!== 'false'` idiom (same as `vp_enabled`) rather than `=== 'true'`.

- [ ] **Step 5: Build frontend**

Run: `cd frontend && npm run build`
Expected: clean build, no TypeScript errors.

- [ ] **Step 6: Run full backend suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_backtest.py --ignore=tests/test_analytics_loader.py --ignore=tests/test_analytics_tools.py`
Expected: only 5 known pre-existing failures.

- [ ] **Step 7: Commit everything**

```bash
git add app/api/server.py app/bot_config.py frontend/src/types.ts frontend/src/components/ConfigPanel.tsx app/api/static/
git commit -m "feat: KZ level sweeps hot-apply + frontend toggle"
```

---

## Self-review notes (verified during planning)

**Spec coverage:**
- ✅ `KillzoneLevelTracker` with all 5 state attributes → Task 1
- ✅ `on_bar(bar, zones, cfg)` step-by-step (reset / accumulate / finalize / detect) → Task 1
- ✅ Pattern A (multi-bar) and Pattern B (one-bar) for both high and low → Task 1
- ✅ Synthetic `Swing` with `bar_ts == confirmed_ts` → Task 1 `_make_sweep`
- ✅ Level consumed after sweep (removed from `_kz_ranges`) → Task 1 test + implementation
- ✅ Daily UTC reset → Task 1 test + implementation
- ✅ `Signal.source` field with default `"swing"` → Task 2
- ✅ `_Awaiting.source` → Task 2
- ✅ `on_sweep(bar, sweep, source="swing")` keyword arg → Task 2
- ✅ `source` flows through `_build_signal` → Task 2
- ✅ `StrategyRunner.kz_levels` field → Task 3
- ✅ Engine bar loop wiring (KZ before `runner.on_bar`, both sources feed the same composer) → Task 3
- ✅ `kz_levels_enabled: bool = True` in `StrategyParams` → Task 3
- ✅ `_build_runner` constructs tracker when enabled → Task 3
- ✅ Hot-apply in `PATCH /api/config` → Task 4
- ✅ Frontend toggle + form serialization → Task 4
- ✅ `Signal.source` in journal/SSE stream (flows automatically via existing `Signal` serialization; no extra code needed)

**Type consistency:** `KillzoneLevelTracker` referenced in engine.py, main.py, server.py — all imported from `app.strategy.kz_levels`. `on_sweep(bar, sweep, source="swing")` signature matches everywhere. `kz_levels_enabled` uses `!== 'false'` idiom (default true) in frontend; field name is consistent across types.ts, ConfigPanel, bot_config.py, server.py.

**No placeholders found.**
