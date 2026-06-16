# PO3 Feature A — Session-Anchored Sweep-Level Tagging (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Annotate every emitted iFVG/sweep `Signal` with `swept_level_type` — which time-anchored reference level(s) (daily open, weekly open, prior-Asian high/low) the sweep extreme is within tolerance of — as **measurement only**, default-off, zero behavior change.

**Architecture:** A new pure module `app/strategy/sweep_levels.py` (`SweepLevelTracker`) maintains no-lookahead reference levels from the streaming bars and exposes `tag(sweep_extreme, tick, tolerance_ticks)`. The composer instantiates it only when `sweep_levels_tag_enabled`, feeds it each bar, and tags the sweep extreme at signal emission. The tag is a new optional `Signal` field, threaded into the backtest `--trade-csv` exactly like the existing `displacement_ts`/`fvg_zone_pts` fields so Phase-2 analysis can group by it. This is Phase 1 only — no gate (Phase 3 is out of scope).

**Tech Stack:** Python 3 / dataclasses / zoneinfo (ET) / pytest.

**Spec:** `c:\Users\Lawrence\Downloads\PO3_FEATURES_SPEC.md` (Feature A, Phases 1–2). Feature B (news blackout) is explicitly out of scope — it conflicts with the live CPI/FOMC straddle and is deferred pending reconciliation.

**Design decisions (reusing repo session config, per spec):**
- `daily_open` = open of the first bar at/after **09:30 ET** (NQ RTH open) on the current ET date.
- `weekly_open` = open of the first bar at/after the most recent **Sunday 18:00 ET** (futures week open).
- `asian_high`/`asian_low` = high/low of the most recently **completed** Asia session (repo's `asia()` killzone, **19:00–22:00 ET**), locked at session close.
- All levels are only exposed once known → no lookahead. `swing_only` when the sweep matches none.

---

## File structure
- `app/bot_config.py` — 2 new flat `StrategyParams` fields (Phase 1 only; gate fields deferred to Phase 3).
- `app/strategy/sweep_levels.py` — **new** pure module: `SweepLevelTracker` (level maintenance + `tag()`).
- `app/strategy/composer.py` — add `swept_level_type` to `Signal`; instantiate + feed the tracker; tag at emission (composer.py:782).
- `scripts/equity_export.py` — add `swept_level_type` column to `--trade-csv` (mirror `fvg_zone_pts`).
- `tests/test_sweep_levels.py` — **new** unit tests.

---

### Task 1: Config fields (default-off)

**Files:**
- Modify: `app/bot_config.py` (add near the `ifvg_*` flat fields in `StrategyParams`, e.g. after `swing_stop_lookback`)
- Test: `tests/test_sweep_levels.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sweep_levels.py
from app.bot_config import StrategyParams


def test_sweep_level_config_defaults_off():
    sp = StrategyParams()
    assert sp.sweep_levels_tag_enabled is False
    assert sp.sweep_levels_tag_tolerance_ticks == 4
```

- [ ] **Step 2: Run, verify FAIL**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_sweep_levels.py -v`
Expected: FAIL — `AttributeError: 'StrategyParams' object has no attribute 'sweep_levels_tag_enabled'`

- [ ] **Step 3: Add the fields**

In `app/bot_config.py`, inside `class StrategyParams`, add after the `swing_stop_lookback` field:

```python
    # PO3 Feature A (Phase 1, default-off): tag each signal with which time-anchored
    # level (daily/weekly open, prior-Asian H/L) its sweep extreme is within
    # tolerance of. Measurement only — no behavior change. Phase-3 gate fields are
    # deferred until the tag report justifies a filter.
    sweep_levels_tag_enabled: bool = False
    sweep_levels_tag_tolerance_ticks: int = 4
```

- [ ] **Step 4: Run, verify PASS**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_sweep_levels.py -v`
Expected: PASS (1 passed)

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py tests/test_sweep_levels.py
git commit -m "feat: PO3 Feature A config fields (sweep-level tagging, default-off)"
```

---

### Task 2: SweepLevelTracker — no-lookahead level maintenance

**Files:**
- Create: `app/strategy/sweep_levels.py`
- Test: `tests/test_sweep_levels.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_sweep_levels.py
from datetime import datetime, timezone
from decimal import Decimal
from app.broker.events import Bar
from app.strategy.sweep_levels import SweepLevelTracker

def _bar(et_hour, et_min, o, h, l, c, day="2026-06-16"):
    # ET -> UTC (EDT = UTC-4 in June). Build a UTC-aware bar at the given ET time.
    from zoneinfo import ZoneInfo
    ts_et = datetime.fromisoformat(f"{day}T{et_hour:02d}:{et_min:02d}:00").replace(tzinfo=ZoneInfo("America/New_York"))
    return Bar(instrument="MNQ", ts=ts_et.astimezone(timezone.utc),
               open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c), volume=100)

def test_daily_open_set_at_rth_and_no_lookahead():
    t = SweepLevelTracker()
    # pre-RTH bar (09:00 ET): daily_open not yet known for today
    t.on_bar(_bar(9, 0, 21000, 21010, 20990, 21005))
    assert t.levels().get("daily_open") is None
    # first RTH bar (09:30 ET): daily_open = its open
    t.on_bar(_bar(9, 30, 21020, 21030, 21015, 21025))
    assert t.levels()["daily_open"] == Decimal("21020")

def test_asian_high_low_locked_after_session_close():
    t = SweepLevelTracker()
    # Asia session 19:00-22:00 ET (prior day) — feed two bars
    t.on_bar(_bar(19, 0, 21000, 21050, 20990, 21010, day="2026-06-15"))
    t.on_bar(_bar(21, 0, 21010, 21080, 21005, 21070, day="2026-06-15"))
    # not locked until a bar AFTER 22:00 ET arrives
    t.on_bar(_bar(22, 30, 21070, 21075, 21060, 21065, day="2026-06-15"))
    assert t.levels()["asian_high"] == Decimal("21080")
    assert t.levels()["asian_low"] == Decimal("20990")
```

- [ ] **Step 2: Run, verify FAIL**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_sweep_levels.py -k "daily_open or asian" -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.strategy.sweep_levels'`

- [ ] **Step 3: Implement the tracker**

Create `app/strategy/sweep_levels.py`:

```python
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

from app.broker.events import Bar

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
    _week: tuple[int, int] | None = field(default=None)  # (iso_year, iso_week)
    _asia_accum: "tuple[Decimal, Decimal] | None" = field(default=None)
    _asia_accum_day: date | None = field(default=None)

    def on_bar(self, bar: Bar) -> None:
        et = bar.ts.astimezone(ET)
        d = et.date()
        # Daily reset; daily_open locks on the first bar at/after RTH open.
        if d != self._day:
            self._day = d
            self.daily_open = None
        if self.daily_open is None and et.time() >= _RTH_OPEN:
            self.daily_open = bar.open
        # Weekly open: first bar of a new ISO week.
        wk = (et.isocalendar().year, et.isocalendar().week)
        if wk != self._week:
            self._week = wk
            self.weekly_open = bar.open
        # Asia session accumulate; lock H/L once a bar past the session arrives.
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
```

- [ ] **Step 4: Run, verify PASS**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_sweep_levels.py -k "daily_open or asian" -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/strategy/sweep_levels.py tests/test_sweep_levels.py
git commit -m "feat: SweepLevelTracker — no-lookahead session-anchored levels"
```

---

### Task 3: `tag()` — match a sweep extreme to levels

**Files:**
- Modify: `app/strategy/sweep_levels.py` (add `tag` method)
- Test: `tests/test_sweep_levels.py` (append)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_sweep_levels.py
def test_tag_matches_within_tolerance_else_swing_only():
    t = SweepLevelTracker()
    t.on_bar(_bar(9, 30, 21020, 21030, 21015, 21025))   # daily_open = 21020
    tick = Decimal("0.25")
    # sweep extreme 0.5pt (2 ticks) from daily_open, tolerance 4 ticks -> match
    assert t.tag(Decimal("21020.50"), tick, 4) == ["daily_open"]
    # far from any level -> swing_only
    assert t.tag(Decimal("20800"), tick, 4) == ["swing_only"]

def test_tag_determinism_same_input_same_output():
    t = SweepLevelTracker()
    t.on_bar(_bar(9, 30, 21020, 21030, 21015, 21025))
    tick = Decimal("0.25")
    assert t.tag(Decimal("21021"), tick, 4) == t.tag(Decimal("21021"), tick, 4)
```

- [ ] **Step 2: Run, verify FAIL**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_sweep_levels.py -k tag -v`
Expected: FAIL — `AttributeError: 'SweepLevelTracker' object has no attribute 'tag'`

- [ ] **Step 3: Add the method** to `SweepLevelTracker` in `app/strategy/sweep_levels.py`:

```python
    def tag(self, sweep_extreme: Decimal, tick: Decimal, tolerance_ticks: int) -> list[str]:
        """Which known levels the sweep extreme is within tolerance of.
        Returns sorted matched names, or ['swing_only'] if none. Pure/deterministic."""
        tol = tick * Decimal(tolerance_ticks)
        matched = sorted(
            name for name, price in self.levels().items()
            if price is not None and abs(sweep_extreme - price) <= tol
        )
        return matched or ["swing_only"]
```

- [ ] **Step 4: Run, verify PASS**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_sweep_levels.py -k tag -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/strategy/sweep_levels.py tests/test_sweep_levels.py
git commit -m "feat: SweepLevelTracker.tag — sweep-extreme to anchored-level matching"
```

---

### Task 4: Signal field + composer wiring (default-off)

**Files:**
- Modify: `app/strategy/composer.py` — add `swept_level_type` to `Signal` (after `fvg_zone_pts`, composer.py:103); instantiate tracker in composer `__init__` when enabled; feed it in `on_bar`; tag at emission (composer.py:782 `return Signal(...)`).
- Test: `tests/test_sweep_levels.py` (append integration test)

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_sweep_levels.py
from app.strategy.composer import Signal

def test_signal_has_swept_level_type_field_default_none():
    # Field exists with a safe default so existing constructions are unaffected.
    import dataclasses
    names = {f.name for f in dataclasses.fields(Signal)}
    assert "swept_level_type" in names
    f = next(f for f in dataclasses.fields(Signal) if f.name == "swept_level_type")
    assert f.default is None
```

- [ ] **Step 2: Run, verify FAIL**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_sweep_levels.py -k swept_level_type -v`
Expected: FAIL — `StopIteration`/`AssertionError` (field absent)

- [ ] **Step 3: Add the field + wiring**

(a) In `app/strategy/composer.py`, in `class Signal`, after the `fvg_zone_pts` field (composer.py:103):

```python
    # PO3 Feature A (Phase 1): time-anchored levels the sweep extreme matched
    # (e.g. ["daily_open"]), or ["swing_only"]. None when tagging disabled.
    swept_level_type: "list[str] | None" = None
```

(b) Add the import near the top of `composer.py` (with the other `from .` imports):

```python
from .sweep_levels import SweepLevelTracker
```

(c) Add the two fields to the `ComposerConfig` dataclass (`composer.py:106`), after `r_multiple` (mirrors the other flat config fields):

```python
    # PO3 Feature A (Phase 1): tag sweeps by anchored level. Default-off.
    sweep_levels_tag_enabled: bool = False
    sweep_levels_tag_tolerance_ticks: int = 4
```

(d) Map them from `StrategyParams` (`s`) at **every** `ComposerConfig(...)` construction site — add these two kwargs alongside the existing `swing_stop_lookback=s.swing_stop_lookback` mapping:

```python
                    sweep_levels_tag_enabled=s.sweep_levels_tag_enabled,
                    sweep_levels_tag_tolerance_ticks=s.sweep_levels_tag_tolerance_ticks,
```

Sites (confirmed by `grep -n "ComposerConfig(" app/`): `app/backtest/runner.py:429` and `:479` (backtest/equity_export path — **required** for Phase-2 analysis), `app/main.py:296` and `:359` (live path — needed only if you want live tagging), `app/backtest.py:79`. The dataclass defaults (False/4) mean any site you don't touch simply keeps tagging off — so for Phase-1 analysis, the two `backtest/runner.py` sites are the only mandatory ones; add the rest for completeness.

(e) In the composer `__init__` (where `self._zones = config.killzones ...` is set, ~composer.py:249), add:

```python
        self._sweep_levels = SweepLevelTracker() if config.sweep_levels_tag_enabled else None
```

(f) In the composer's public bar entry (`def on_bar` in composer.py — the method the runner calls each bar), at the top add:

```python
        if self._sweep_levels is not None:
            self._sweep_levels.on_bar(bar)
```

(g) At the `return Signal(` site (composer.py:782) — note `cfg` there is already the `ComposerConfig` (it reads `cfg.instrument`, `cfg.fib_target_ext`) — immediately before `return Signal(`:

```python
        swept_level_type = None
        if self._sweep_levels is not None:
            from app.broker.paper import TICK_SIZE
            tick = TICK_SIZE.get(cfg.instrument, Decimal("0.25"))
            swept_level_type = self._sweep_levels.tag(
                awaiting.sweep.sweep_extreme, tick, cfg.sweep_levels_tag_tolerance_ticks,
            )
```
and add to the `Signal(...)` kwargs:

```python
            swept_level_type=swept_level_type,
```

- [ ] **Step 4: Run, verify PASS + no regressions**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_sweep_levels.py -v && ./.venv/Scripts/python.exe -m pytest tests/test_composer.py -q`
Expected: new tests PASS; existing composer tests unchanged (field is optional/default-None, tracker is None when disabled → zero behavior change).

- [ ] **Step 5: Commit**

```bash
git add app/strategy/composer.py tests/test_sweep_levels.py
git commit -m "feat: tag signals with swept_level_type when enabled (default-off)"
```

---

### Task 5: Thread tag into backtest `--trade-csv` (Phase-2 analysis hook)

**Files:**
- Modify: `scripts/equity_export.py` — add `swept_level_type` column (mirror `fvg_zone_pts`); and wherever the trade dict is built from the signal (grep `fvg_zone_pts` / `displacement_ts` to find the mapping — likely in `equity_export.py` or `app/backtest/runner.py`), copy `signal.swept_level_type` into the trade record.
- Test: manual verification (analysis hook, not unit-critical).

- [ ] **Step 1: Locate the existing field-threading**

Run: `grep -rn "fvg_zone_pts" scripts/equity_export.py app/backtest/runner.py app/backtest/funded_sim.py`
This shows the 2 sites: where the trade dict gets `fvg_zone_pts` from the signal, and the `--trade-csv` header/row writer.

- [ ] **Step 2: Add to the trade dict**

At the site that builds the per-trade dict with `"fvg_zone_pts": signal.fvg_zone_pts` (mirror it):

```python
                "swept_level_type": "|".join(signal.swept_level_type) if signal.swept_level_type else "",
```
(join the list to a `|`-delimited string for a flat CSV cell.)

- [ ] **Step 3: Add the CSV column** in `scripts/equity_export.py` (header + row, mirroring `fvg_zone_pts` at lines ~116–127):

Header — append `"swept_level_type"` to the `tw.writerow([...])` header list.
Row — append `t.get("swept_level_type", "")` to the row `tw.writerow([...])`.

- [ ] **Step 4: Verify end-to-end (tagging on, tiny run)**

Run:
```bash
./.venv/Scripts/python.exe scripts/equity_export.py --bars bars/bars_MNQ_dbv_2021_2026.csv --instrument MNQ --timeframe 5min --exclude-years 2021,2022,2023,2024,2025 --set sweep_levels_tag_enabled=True --out /tmp/eq_tag_smoke.csv --trade-csv /tmp/trades_tag_smoke.csv
head -3 /tmp/trades_tag_smoke.csv
```
Expected: the trade CSV has a `swept_level_type` column populated with values like `daily_open`, `asian_high`, or `swing_only` (2026 only, fast).

- [ ] **Step 5: Commit**

```bash
git add scripts/equity_export.py app/backtest/runner.py
git commit -m "feat: emit swept_level_type in --trade-csv for Phase-2 tag analysis"
```

---

## Self-review notes
- **Spec coverage (Feature A Phase 1):** levels computed no-lookahead (T2), `swept_level_type` on each signal with list/`swing_only` semantics (T3/T4), config block default-off (T1), trade-output hook for Phase-2 grouping (T5). Phase-2 is analysis (no code). Phase-3 gate explicitly deferred (YAGNI) — config gate fields not added yet.
- **Spec tests covered:** level computation + no-lookahead (T2), tagging within/outside tolerance (T3), determinism (T3). Phase-3 gate test deferred with Phase-3.
- **Type consistency:** `SweepLevelTracker.on_bar(bar)` / `.levels() -> dict` / `.tag(sweep_extreme, tick, tolerance_ticks) -> list[str]` used identically in T2–T4. `swept_level_type: list[str] | None` consistent in Signal (T4) and CSV join (T5).
- **Safety:** entirely default-off — tracker is `None` and the field is `None` unless `sweep_levels_tag_enabled=True`; existing signal constructions and the live bot are byte-unaffected. Verified by the composer regression in T4 step 4.
- **Wiring confirmed:** `ComposerConfig` is a flat dataclass built from `StrategyParams` (`s`) at 5 sites (verified `grep -n "ComposerConfig("`); the two new fields are added to the dataclass (default-off) and mapped at the build sites — `backtest/runner.py:429/:479` mandatory for Phase-2 analysis, `main.py:296/:359` + `backtest.py:79` for completeness. The emission site's `cfg` is the `ComposerConfig`, so `cfg.sweep_levels_tag_tolerance_ticks` resolves.
