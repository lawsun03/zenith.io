# iFVG Entry Rework + Dodgy Setup Grading Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rework the 1min entry from displacement-bar FVGs to true iFVG inversions (prior FVG zones flipped by a displacement bar), and add a `SetupGrader` that scores each signal A+/A/A-/B/B- against Dodgy's 5 criteria, filtering below A-.

**Architecture:** `DisplacementDetector` gains a rolling `_active_fvgs` deque; `_evaluate()` now searches that deque for a prior FVG the displacement bar inverted rather than computing the bar's own 3-bar gap. A new `SetupGrader` class (standalone module) scores signals after the composer produces them; `StrategyRunner.on_bar()` calls the grader and discards failing signals. The grader is updated every 60s by the existing HTF refresh loop via the same 30min bars already fetched.

**Tech Stack:** Python 3.x, FastAPI, asyncio, dataclasses, React/TypeScript/Tailwind, WebSocket (journal pub/sub)

**Spec:** `docs/superpowers/specs/2026-05-28-ifvg-entry-rework-design.md`

---

## File Map

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `app/strategy/displacement.py` | Add FVG formation, mitigation, inversion detection, `active_fvgs` property |
| Modify | `app/strategy/composer.py` | Add `setup_grade` optional field to `Signal` |
| Modify | `app/strategy/htf.py` | Add public `swing_highs` / `swing_lows` properties to `HTFLevelFinder` |
| Create | `app/strategy/grader.py` | `SetupGrade` dataclass + `SetupGrader` class |
| Modify | `app/execution/engine.py` | `StrategyRunner`: add mandatory `grader` field, update `on_bar` + `try_signal_from_forming` |
| Modify | `app/main.py` | Wire grader into `_build_runner` and `_refresh_htf_once` |
| Modify | `app/api/journal.py` | Add `publish_strategy_state` method |
| Create | `frontend/src/components/StrategyDebug.tsx` | Collapsible grade panel |
| Modify | `frontend/src/App.tsx` | Mount `StrategyDebug` below signal panel |
| Create | `tests/test_ifvg_inversion.py` | iFVG formation, mitigation, inversion tests |
| Create | `tests/test_grader.py` | All grade branches, session range, delivery FVG |

---

## Task 1: FVG Formation and `active_fvgs` in DisplacementDetector

**Files:**
- Modify: `app/strategy/displacement.py`
- Create: `tests/test_ifvg_inversion.py`

- [ ] **Step 1: Write failing tests for FVG formation**

Create `tests/test_ifvg_inversion.py`:

```python
"""Tests for iFVG inversion detection in DisplacementDetector."""
from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.broker.events import Bar
from app.strategy.displacement import DisplacementConfig, DisplacementDetector, FairValueGap

BASE_TS = datetime(2026, 5, 28, 9, 30, tzinfo=timezone.utc)


def bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        instrument="MGC", timeframe="1min",
        ts=BASE_TS + timedelta(minutes=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=100,
    )


def make_detector() -> DisplacementDetector:
    return DisplacementDetector(DisplacementConfig(
        atr_period=3,
        body_atr_multiple=Decimal("1.5"),
        min_body_to_range_ratio=Decimal("0.6"),
        min_absolute_body=Decimal("0.5"),
    ))


class TestFVGFormation:
    def test_bullish_fvg_added_to_active(self):
        """b3.low > b1.high creates a bullish FVG in _active_fvgs."""
        d = make_detector()
        # b1: narrow bar at 100
        d.on_bar(bar(0, "100", "101", "99", "100"))
        # b2: any bar
        d.on_bar(bar(1, "100", "102", "100", "101"))
        assert len(d.active_fvgs) == 0  # need 3 bars
        # b3: low above b1.high (101) → bullish FVG [101, b3.low=103]
        d.on_bar(bar(2, "103", "104", "103", "104"))
        fvgs = d.active_fvgs
        assert len(fvgs) == 1
        assert fvgs[0].side == "bullish"
        assert fvgs[0].low == Decimal("101")
        assert fvgs[0].high == Decimal("103")

    def test_bearish_fvg_added_to_active(self):
        """b3.high < b1.low creates a bearish FVG in _active_fvgs."""
        d = make_detector()
        d.on_bar(bar(0, "105", "106", "104", "105"))
        d.on_bar(bar(1, "104", "105", "103", "104"))
        d.on_bar(bar(2, "100", "103", "100", "101"))
        fvgs = d.active_fvgs
        assert len(fvgs) == 1
        assert fvgs[0].side == "bearish"
        assert fvgs[0].low == Decimal("103")
        assert fvgs[0].high == Decimal("104")

    def test_no_gap_no_fvg(self):
        """Overlapping bars produce no FVG."""
        d = make_detector()
        d.on_bar(bar(0, "100", "102", "99", "101"))
        d.on_bar(bar(1, "101", "103", "100", "102"))
        d.on_bar(bar(2, "102", "104", "101", "103"))
        assert len(d.active_fvgs) == 0
```

- [ ] **Step 2: Run — verify FAIL**

```
pytest tests/test_ifvg_inversion.py::TestFVGFormation -v
```
Expected: `AttributeError: 'DisplacementDetector' object has no attribute 'active_fvgs'`

- [ ] **Step 3: Add `_active_fvgs` deque and FVG formation to `DisplacementDetector`**

In `app/strategy/displacement.py`, in `DisplacementDetector.__init__`, add after `self._prev_close`:
```python
from collections import deque  # already imported at top

self._active_fvgs: deque[FairValueGap] = deque(maxlen=30)
```

Add `active_fvgs` property after the existing `atr` property:
```python
@property
def active_fvgs(self) -> list[FairValueGap]:
    """Unmitigated 1min FVGs available for iFVG inversion detection."""
    return list(self._active_fvgs)
```

Add a new private method `_form_fvg` before `_evaluate`:
```python
def _form_fvg(self, b1: Bar, b3: Bar) -> FairValueGap | None:
    """Check if b1 and b3 bracket a 3-bar gap and return it if so."""
    if b3.low > b1.high:
        return FairValueGap(side="bullish", low=b1.high, high=b3.low, created_at=b3.ts)
    if b3.high < b1.low:
        return FairValueGap(side="bearish", low=b3.high, high=b1.low, created_at=b3.ts)
    return None
```

In `on_bar`, BEFORE the `if len(self._window) < 3` guard that calls `_evaluate`, add the FVG formation call. The full `on_bar` method becomes:

```python
def on_bar(self, bar: Bar) -> DisplacementEvent | None:
    self._update_atr(bar)
    self._window.append(bar)

    if len(self._window) < 3:
        return None
    if self._atr is None:
        return None

    bar1, bar2, bar3 = self._window[0], self._window[1], self._window[2]

    # Form and record any new 3-bar FVG (before inversion check so the
    # bar that creates an FVG doesn't immediately invert its own gap).
    new_fvg = self._form_fvg(bar1, bar3)
    if new_fvg is not None:
        self._active_fvgs.append(new_fvg)

    return self._evaluate(bar1, bar2, bar3)
```

- [ ] **Step 4: Run — verify PASS**

```
pytest tests/test_ifvg_inversion.py::TestFVGFormation -v
```
Expected: 3 tests PASS.

- [ ] **Step 5: Commit**

```
git add app/strategy/displacement.py tests/test_ifvg_inversion.py
git commit -m "feat: add FVG formation tracking to DisplacementDetector"
```

---

## Task 2: FVG Mitigation

**Files:**
- Modify: `app/strategy/displacement.py`
- Modify: `tests/test_ifvg_inversion.py`

- [ ] **Step 1: Write failing mitigation tests**

Append to `tests/test_ifvg_inversion.py`:

```python
class TestFVGMitigation:
    def test_bullish_fvg_mitigated_when_price_trades_to_far_edge(self):
        """A bullish FVG [low, high] is removed when bar.low <= fvg.low."""
        d = make_detector()
        # Form bullish FVG: b1.high=101, b3.low=103 → FVG [101, 103]
        d.on_bar(bar(0, "100", "101", "99", "100"))
        d.on_bar(bar(1, "101", "102", "100", "101"))
        d.on_bar(bar(2, "103", "104", "103", "104"))
        assert len(d.active_fvgs) == 1

        # Bar that trades down to the far edge (low = 101)
        d.on_bar(bar(3, "103", "103", "101", "102"))
        assert len(d.active_fvgs) == 0

    def test_bearish_fvg_mitigated_when_price_trades_to_far_edge(self):
        """A bearish FVG [low, high] is removed when bar.high >= fvg.high."""
        d = make_detector()
        # Form bearish FVG: b1.low=104, b3.high=103 → FVG [103, 104]
        d.on_bar(bar(0, "105", "106", "104", "105"))
        d.on_bar(bar(1, "104", "105", "103", "104"))
        d.on_bar(bar(2, "100", "103", "100", "101"))
        assert len(d.active_fvgs) == 1

        # Bar whose high reaches far edge (104)
        d.on_bar(bar(3, "101", "104", "101", "103"))
        assert len(d.active_fvgs) == 0

    def test_fvg_not_mitigated_before_far_edge(self):
        """FVG stays active when price doesn't reach the far edge."""
        d = make_detector()
        d.on_bar(bar(0, "100", "101", "99", "100"))
        d.on_bar(bar(1, "101", "102", "100", "101"))
        d.on_bar(bar(2, "103", "104", "103", "104"))
        # Bar that only dips to 102 — far edge is 101, not reached
        d.on_bar(bar(3, "104", "105", "102", "103"))
        assert len(d.active_fvgs) == 1
```

- [ ] **Step 2: Run — verify FAIL**

```
pytest tests/test_ifvg_inversion.py::TestFVGMitigation -v
```
Expected: FAIL (mitigation not implemented yet).

- [ ] **Step 3: Add mitigation to `on_bar`**

Add private method `_mitigate_fvgs` to `DisplacementDetector`:

```python
def _mitigate_fvgs(self, bar: Bar) -> None:
    """Remove any active FVG whose far edge has been traded through."""
    # Bullish FVG far edge = fvg.low; mitigated when bar.low <= fvg.low
    # Bearish FVG far edge = fvg.high; mitigated when bar.high >= fvg.high
    self._active_fvgs = deque(
        (fvg for fvg in self._active_fvgs
         if not (
             (fvg.side == "bullish" and bar.low <= fvg.low) or
             (fvg.side == "bearish" and bar.high >= fvg.high)
         )),
        maxlen=30,
    )
```

Update `on_bar` to call mitigation BEFORE forming new FVGs (so a bar that simultaneously mitigates one gap and forms a new one is handled correctly):

```python
def on_bar(self, bar: Bar) -> DisplacementEvent | None:
    self._update_atr(bar)
    self._window.append(bar)

    if len(self._window) < 3:
        return None
    if self._atr is None:
        return None

    bar1, bar2, bar3 = self._window[0], self._window[1], self._window[2]

    self._mitigate_fvgs(bar3)

    new_fvg = self._form_fvg(bar1, bar3)
    if new_fvg is not None:
        self._active_fvgs.append(new_fvg)

    return self._evaluate(bar1, bar2, bar3)
```

- [ ] **Step 4: Run — verify PASS**

```
pytest tests/test_ifvg_inversion.py::TestFVGMitigation -v
```
Expected: 3 tests PASS.

- [ ] **Step 5: Run full suite — no regressions**

```
pytest tests/test_ifvg_inversion.py tests/test_strategy.py -v
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```
git add app/strategy/displacement.py tests/test_ifvg_inversion.py
git commit -m "feat: add FVG mitigation to DisplacementDetector"
```

---

## Task 3: iFVG Inversion Detection (Core Rework)

**Files:**
- Modify: `app/strategy/displacement.py`
- Modify: `tests/test_ifvg_inversion.py`

- [ ] **Step 1: Write failing inversion tests**

Append to `tests/test_ifvg_inversion.py`:

```python
class TestIFVGInversion:
    def _warm_atr(self, detector: DisplacementDetector, n: int = 5) -> None:
        """Feed n neutral bars to warm ATR before the test sequence."""
        for i in range(n):
            detector.on_bar(bar(i, "2400", "2401", "2399", "2400"))

    def test_bearish_displacement_inverts_bullish_fvg(self):
        """
        A bearish displacement bar whose close goes through a bullish FVG's
        far edge (fvg.low) produces a DisplacementEvent with that prior FVG.
        """
        d = make_detector()
        self._warm_atr(d)
        offset = 5

        # Build a bullish FVG [2401, 2403]:
        #   b1.high=2401, b3.low=2403
        d.on_bar(bar(offset,   "2400", "2401", "2399", "2400"))  # b1
        d.on_bar(bar(offset+1, "2401", "2402", "2400", "2402"))  # b2
        d.on_bar(bar(offset+2, "2403", "2405", "2403", "2404"))  # b3 → FVG formed
        assert any(f.side == "bullish" for f in d.active_fvgs)

        # Now: bearish displacement bar (b2 of inversion window) that closes
        # BELOW fvg.low (2401). Large body required.
        # We need 3 bars so inversion is evaluated on b3 close.
        d.on_bar(bar(offset+3, "2404", "2405", "2395", "2396"))  # big bearish b2
        result = d.on_bar(bar(offset+4, "2396", "2397", "2394", "2395"))  # b3

        assert result is not None
        assert result.side == "bearish"
        assert result.fvg is not None
        assert result.fvg.side == "bullish"   # the prior bullish FVG, now iFVG
        assert result.fvg.low == Decimal("2401")
        assert result.fvg.high == Decimal("2403")

    def test_bullish_displacement_inverts_bearish_fvg(self):
        """
        A bullish displacement bar whose close goes through a bearish FVG's
        far edge (fvg.high) produces a DisplacementEvent with that prior FVG.
        """
        d = make_detector()
        self._warm_atr(d)
        offset = 5

        # Build bearish FVG [2398, 2400]: b1.low=2400, b3.high=2398
        d.on_bar(bar(offset,   "2401", "2402", "2400", "2401"))  # b1
        d.on_bar(bar(offset+1, "2400", "2401", "2398", "2399"))  # b2
        d.on_bar(bar(offset+2, "2395", "2398", "2394", "2396"))  # b3 → FVG [2398,2400]
        assert any(f.side == "bearish" for f in d.active_fvgs)

        # Bullish displacement closes ABOVE fvg.high (2400)
        d.on_bar(bar(offset+3, "2396", "2407", "2395", "2406"))  # big bullish b2
        result = d.on_bar(bar(offset+4, "2406", "2408", "2405", "2407"))

        assert result is not None
        assert result.side == "bullish"
        assert result.fvg is not None
        assert result.fvg.side == "bearish"
        assert result.fvg.low == Decimal("2398")
        assert result.fvg.high == Decimal("2400")

    def test_displacement_with_no_prior_fvg_returns_fvg_none(self):
        """If no prior FVG exists, displacement returns event with fvg=None."""
        d = make_detector()
        self._warm_atr(d)
        offset = 5

        # No FVGs formed — jump straight to displacement
        d.on_bar(bar(offset,   "2400", "2401", "2399", "2400"))
        d.on_bar(bar(offset+1, "2400", "2401", "2389", "2390"))  # big bearish
        result = d.on_bar(bar(offset+2, "2390", "2391", "2389", "2390"))

        assert result is not None      # displacement detected
        assert result.fvg is None      # but no iFVG (no prior FVG to invert)

    def test_mitigated_fvg_not_used_for_inversion(self):
        """A FVG removed by mitigation cannot be used as an iFVG entry."""
        d = make_detector()
        self._warm_atr(d)
        offset = 5

        # Form bullish FVG [2401, 2403]
        d.on_bar(bar(offset,   "2400", "2401", "2399", "2400"))
        d.on_bar(bar(offset+1, "2401", "2402", "2400", "2402"))
        d.on_bar(bar(offset+2, "2403", "2405", "2403", "2404"))

        # Mitigate it: bar.low touches 2401 (far edge)
        d.on_bar(bar(offset+3, "2404", "2404", "2401", "2403"))
        assert len(d.active_fvgs) == 0

        # Bearish displacement — no valid FVG to invert
        d.on_bar(bar(offset+4, "2403", "2404", "2393", "2394"))
        result = d.on_bar(bar(offset+5, "2394", "2395", "2393", "2394"))
        assert result is None or result.fvg is None
```

- [ ] **Step 2: Run — verify FAIL**

```
pytest tests/test_ifvg_inversion.py::TestIFVGInversion -v
```
Expected: FAIL (current `_evaluate` computes own 3-bar FVG, not prior FVG inversion).

- [ ] **Step 3: Rework `_evaluate` in `displacement.py`**

Replace the entire `_evaluate` method:

```python
def _evaluate(self, b1: Bar, b2: Bar, b3: Bar) -> DisplacementEvent | None:
    """
    Did bar2 displace AND invert a prior active FVG?

    Displacement criteria are unchanged (body size, ATR multiple, body-to-range).
    The FVG reported is the PRIOR active FVG that bar2's body closed through —
    not bar2's own 3-bar gap with b1/b3. If no prior FVG was inverted,
    fvg=None (displacement present but no iFVG entry zone).
    """
    cfg = self.config
    atr = self._atr
    assert atr is not None

    body = abs(b2.close - b2.open)
    bar_range = b2.high - b2.low

    if body < cfg.min_absolute_body:
        return None
    if bar_range == 0:
        return None
    if body / bar_range < cfg.min_body_to_range_ratio:
        return None
    if body < atr * cfg.body_atr_multiple:
        return None

    if b2.close > b2.open:
        side: DisplacementSide = "bullish"
    elif b2.close < b2.open:
        side = "bearish"
    else:
        return None

    ifvg = self._find_inverted_fvg(b2, side)

    ratio = body / atr
    return DisplacementEvent(
        side=side,
        displacement_bar=b2,
        body_size=body,
        atr_at_event=atr,
        body_to_atr=ratio,
        fvg=ifvg,
    )

def _find_inverted_fvg(self, displacement_bar: Bar, side: DisplacementSide) -> FairValueGap | None:
    """
    Search _active_fvgs for one that the displacement bar's body closed through.

    Bearish displacement inverts a bullish FVG when bar.close < fvg.low.
    Bullish displacement inverts a bearish FVG when bar.close > fvg.high.

    Returns the most-recently-formed matching FVG (last in deque), or None.
    """
    for fvg in reversed(self._active_fvgs):
        if side == "bearish" and fvg.side == "bullish":
            if displacement_bar.close < fvg.low:
                return fvg
        elif side == "bullish" and fvg.side == "bearish":
            if displacement_bar.close > fvg.high:
                return fvg
    return None
```

- [ ] **Step 4: Run inversion tests — verify PASS**

```
pytest tests/test_ifvg_inversion.py::TestIFVGInversion -v
```
Expected: 4 tests PASS.

- [ ] **Step 5: Run all inversion tests and existing strategy tests**

```
pytest tests/test_ifvg_inversion.py tests/test_strategy.py -v
```

The existing strategy tests in `test_strategy.py` will likely fail now because they set up scenarios where a displacement bar creates its own 3-bar FVG that gets reported. These tests need to be updated: for any test that expects `result.fvg` to be non-None, a prior active FVG must exist in the sequence before the displacement bar fires.

For each failing `test_strategy.py` test, examine the bar sequence and prepend 3 bars that form a FVG in the correct direction before the sweep+displacement sequence. Specifically:
- If the displacement is **bearish**: add a bullish FVG (b3.low > b1.high) a few bars before the displacement bar
- If the displacement is **bullish**: add a bearish FVG (b3.high < b1.low) before the displacement bar

Run until all pass before continuing.

- [ ] **Step 6: Commit**

```
git add app/strategy/displacement.py tests/test_ifvg_inversion.py tests/test_strategy.py
git commit -m "feat: rework DisplacementDetector to detect iFVG inversions"
```

---

## Task 4: Update `peek_displacement` for iFVG

**Files:**
- Modify: `app/strategy/displacement.py`
- Modify: `tests/test_ifvg_inversion.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_ifvg_inversion.py`:

```python
class TestPeekDisplacementIFVG:
    def test_peek_returns_none_when_no_prior_fvg(self):
        """peek_displacement returns None if displacement bar has no iFVG to invert."""
        d = make_detector()
        for i in range(5):
            d.on_bar(bar(i, "2400", "2401", "2399", "2400"))
        # Big bearish bar, no prior FVG
        d.on_bar(bar(5, "2400", "2401", "2388", "2389"))
        assert d.peek_displacement() is None

    def test_peek_returns_side_and_ifvg_when_prior_fvg_exists(self):
        """peek_displacement returns (side, b1, b2) when displacement can invert a prior FVG."""
        d = make_detector()
        for i in range(3):
            d.on_bar(bar(i, "2400", "2401", "2399", "2400"))
        # Form bullish FVG [2401, 2403]
        d.on_bar(bar(3, "2400", "2401", "2399", "2400"))
        d.on_bar(bar(4, "2401", "2402", "2400", "2402"))
        d.on_bar(bar(5, "2403", "2405", "2403", "2404"))
        # Feed big bearish bar (b2 of displacement window)
        d.on_bar(bar(6, "2404", "2405", "2392", "2393"))
        result = d.peek_displacement()
        assert result is not None
        side, b1, b2 = result
        assert side == "bearish"
```

- [ ] **Step 2: Run — verify FAIL**

```
pytest tests/test_ifvg_inversion.py::TestPeekDisplacementIFVG -v
```

- [ ] **Step 3: Update `peek_displacement` to use iFVG logic**

Replace the `peek_displacement` method in `displacement.py`:

```python
def peek_displacement(self) -> "tuple[DisplacementSide, Bar, Bar] | None":
    """
    Non-mutating: return (side, b1, b2) if the most recent bar qualifies as a
    displacement bar AND there is a prior active FVG it could invert.

    Returns None if ATR not warmed, window too small, bar doesn't displace,
    or no prior FVG exists to invert (no iFVG entry possible).
    """
    if len(self._window) < 2 or self._atr is None:
        return None
    b1 = self._window[-2]
    b2 = self._window[-1]
    cfg = self.config
    atr = self._atr

    body = abs(b2.close - b2.open)
    bar_range = b2.high - b2.low

    if body < cfg.min_absolute_body:
        return None
    if bar_range == 0:
        return None
    if body / bar_range < cfg.min_body_to_range_ratio:
        return None
    if body < atr * cfg.body_atr_multiple:
        return None

    if b2.close > b2.open:
        side: DisplacementSide = "bullish"
    elif b2.close < b2.open:
        side = "bearish"
    else:
        return None

    # Only signal "peek active" if there's a prior FVG that could be inverted.
    if self._find_inverted_fvg(b2, side) is None:
        return None

    return side, b1, b2
```

Also update `_compute_fvg` usage in `engine.py`'s `try_signal_from_forming`. That method calls `DisplacementDetector._compute_fvg(b1, forming_bar, side)` to check if the forming bar creates a gap. With the new iFVG approach, the forming bar (b3) is no longer used to form the entry FVG — the entry FVG is the prior inverted one. Update `try_signal_from_forming` in `engine.py` to pass the displacement event with the already-known iFVG:

In `app/execution/engine.py`, replace `try_signal_from_forming`:

```python
def try_signal_from_forming(self, forming_bar: Bar) -> Optional[Signal]:
    """
    Check if the forming bar (as b3) allows the composer to emit a signal
    using the iFVG already identified by peek_displacement.

    With the iFVG rework, the entry FVG is the prior inverted FVG (already
    known from the displacement bar), not a gap created by the forming bar.
    We use the forming bar only to confirm the displacement window is still
    valid (bar3 closed, we now have a complete 3-bar view).
    """
    peek = self.displacement.peek_displacement()
    if peek is None:
        return None
    side, b1, b2 = peek

    # The iFVG is already identified — retrieve it from _active_fvgs.
    ifvg = self.displacement._find_inverted_fvg(b2, side)
    if ifvg is None:
        return None

    body = abs(b2.close - b2.open)
    atr = self.displacement.atr or body
    event = DisplacementEvent(
        side=side,
        displacement_bar=b2,
        body_size=body,
        atr_at_event=atr,
        body_to_atr=body / atr,
        fvg=ifvg,
    )
    return self.composer.on_displacement(forming_bar, event)
```

- [ ] **Step 4: Run — verify PASS**

```
pytest tests/test_ifvg_inversion.py::TestPeekDisplacementIFVG -v
```

- [ ] **Step 5: Run full test suite**

```
pytest tests/ -v --tb=short
```
Fix any remaining failures before committing.

- [ ] **Step 6: Commit**

```
git add app/strategy/displacement.py app/execution/engine.py tests/test_ifvg_inversion.py
git commit -m "feat: update peek_displacement and try_signal_from_forming for iFVG"
```

---

## Task 5: Signal `setup_grade` Field + HTFLevelFinder Public Properties

**Files:**
- Modify: `app/strategy/composer.py`
- Modify: `app/strategy/htf.py`

- [ ] **Step 1: Add `setup_grade` to Signal**

In `app/strategy/composer.py`, add to the `Signal` dataclass (at the end, after `rationale`):

```python
from __future__ import annotations  # already present

# Add to existing imports at the top:
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from app.strategy.grader import SetupGrade
```

In the `Signal` dataclass, add the final field:
```python
setup_grade: "SetupGrade | None" = None
```

No other changes to composer.py.

- [ ] **Step 2: Add public properties to HTFLevelFinder**

In `app/strategy/htf.py`, in the `HTFLevelFinder` class, add after `rebuild`:

```python
@property
def swing_highs(self) -> list[Decimal]:
    """Public read of tracked 30min swing highs (set by rebuild)."""
    return list(self._swing_highs)

@property
def swing_lows(self) -> list[Decimal]:
    """Public read of tracked 30min swing lows (set by rebuild)."""
    return list(self._swing_lows)
```

- [ ] **Step 3: Run tests — no regressions**

```
pytest tests/ -v --tb=short
```
Expected: all PASS (these are additive changes).

- [ ] **Step 4: Commit**

```
git add app/strategy/composer.py app/strategy/htf.py
git commit -m "feat: add setup_grade to Signal; public swing properties on HTFLevelFinder"
```

---

## Task 6: SetupGrader — Session Range + Delivery FVG Tracking

**Files:**
- Create: `app/strategy/grader.py`
- Create: `tests/test_grader.py`

- [ ] **Step 1: Write failing tests for session range and delivery FVG**

Create `tests/test_grader.py`:

```python
"""Tests for SetupGrader — grade assignment, session range, delivery FVG."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.broker.events import Bar
from app.strategy.displacement import FairValueGap
from app.strategy.grader import SetupGrader

BASE_TS = datetime(2026, 5, 28, 14, 30, tzinfo=timezone.utc)  # NY AM killzone


def bar(i: int, o: str, h: str, l: str, c: str) -> Bar:
    return Bar(
        instrument="MGC", timeframe="1min",
        ts=BASE_TS + timedelta(minutes=i),
        open=Decimal(o), high=Decimal(h), low=Decimal(l), close=Decimal(c),
        volume=100,
    )


def fvg(side: str, low: str, high: str) -> FairValueGap:
    return FairValueGap(side=side, low=Decimal(low), high=Decimal(high),
                        created_at=BASE_TS)


class TestSessionRange:
    def test_session_range_expands_within_killzone(self):
        g = SetupGrader()
        g.update_session_range(bar(0, "100", "105", "98", "102"), "NY AM")
        g.update_session_range(bar(1, "102", "107", "101", "104"), "NY AM")
        high, low = g.session_range("NY AM")
        assert high == Decimal("107")
        assert low == Decimal("98")

    def test_session_range_resets_on_killzone_change(self):
        g = SetupGrader()
        g.update_session_range(bar(0, "100", "110", "95", "105"), "London")
        g.update_session_range(bar(1, "105", "106", "103", "104"), "NY AM")
        high, low = g.session_range("NY AM")
        assert high == Decimal("106")
        assert low == Decimal("103")

    def test_no_update_outside_killzone(self):
        g = SetupGrader()
        g.update_session_range(bar(0, "100", "105", "98", "102"), None)
        assert g.session_range("NY AM") is None


class TestDeliveryFVG:
    def _make_30min_bars_with_bullish_fvg(self) -> list[Bar]:
        """Three 30min bars forming a bullish FVG [2401, 2403]."""
        return [
            Bar("MGC", "30min", BASE_TS - timedelta(minutes=90),
                Decimal("2400"), Decimal("2401"), Decimal("2399"), Decimal("2400"), 100),
            Bar("MGC", "30min", BASE_TS - timedelta(minutes=60),
                Decimal("2401"), Decimal("2402"), Decimal("2400"), Decimal("2402"), 100),
            Bar("MGC", "30min", BASE_TS - timedelta(minutes=30),
                Decimal("2403"), Decimal("2405"), Decimal("2403"), Decimal("2404"), 100),
        ]

    def test_delivery_fvg_detected_when_sweep_in_zone(self):
        g = SetupGrader()
        g.update_delivery_fvgs(self._make_30min_bars_with_bullish_fvg())
        # Sweep extreme at 2402 — inside FVG [2401, 2403] with 0.5 ATR tol
        assert g.has_delivery_fvg(sweep_extreme=Decimal("2402"), atr=Decimal("1.0")) is True

    def test_delivery_fvg_not_detected_when_sweep_far_away(self):
        g = SetupGrader()
        g.update_delivery_fvgs(self._make_30min_bars_with_bullish_fvg())
        # Sweep at 2450 — far from FVG [2401, 2403]
        assert g.has_delivery_fvg(sweep_extreme=Decimal("2450"), atr=Decimal("1.0")) is False
```

- [ ] **Step 2: Run — verify FAIL**

```
pytest tests/test_grader.py::TestSessionRange tests/test_grader.py::TestDeliveryFVG -v
```
Expected: `ModuleNotFoundError: No module named 'app.strategy.grader'`

- [ ] **Step 3: Create `app/strategy/grader.py` with session range + delivery FVG**

```python
"""
Setup grader — scores a Signal against Dodgy's iFVG setup rating criteria.

Five criteria: momentum quality, target clarity, FVG singularity,
premium/discount positioning, and delivery from a 30min FVG.
Grades A+/A/A-/B/B-; signals below A- are filtered before execution.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal, TYPE_CHECKING

from app.broker.events import Bar
from app.strategy.displacement import FairValueGap

if TYPE_CHECKING:
    from app.strategy.composer import Signal
    from app.strategy.displacement import DisplacementEvent

log = logging.getLogger(__name__)

MomentumQuality = Literal["strong", "decent", "weak"]


@dataclass(frozen=True)
class SetupGrade:
    """Result of grading one signal candidate against Dodgy's 5 criteria."""
    grade: Literal["A+", "A", "A-", "B", "B-"]
    passes: bool                        # True if grade >= A-
    has_delivery_fvg: bool              # swept swing at a 30min FVG
    premium_discount_ok: bool           # entry in correct half of both ranges
    target_clear: bool                  # target aligns with a structural level
    fvg_singular: bool                  # no overlapping 1min FVGs in entry zone
    momentum_quality: MomentumQuality
    reason: str                         # one-line log / dashboard message


class SetupGrader:
    """
    Stateful grader: holds 30min delivery FVGs, HTF swings, and session ranges.
    Updated by the HTF refresh loop; queried once per signal candidate.
    """

    def __init__(self) -> None:
        self._delivery_fvgs: list[FairValueGap] = []
        self._htf_swing_highs: list[Decimal] = []
        self._htf_swing_lows: list[Decimal] = []
        # {killzone_name: (high, low)}
        self._session_ranges: dict[str, tuple[Decimal, Decimal]] = {}
        self._last_killzone: str | None = None

    # ------------------------------------------------------------------
    # State update methods (called by HTF refresh loop + bar handler)
    # ------------------------------------------------------------------

    def update_delivery_fvgs(self, bars_30min: list[Bar]) -> None:
        """Scan 30min bars for unmitigated FVGs; store as delivery FVG candidates."""
        self._delivery_fvgs = self._compute_unmitigated_fvgs(bars_30min)
        log.debug("Grader: %d unmitigated 30min delivery FVGs", len(self._delivery_fvgs))

    def update_htf_swings(self, highs: list[Decimal], lows: list[Decimal]) -> None:
        """Store 30min swing highs and lows for premium/discount and target clarity."""
        self._htf_swing_highs = list(highs)
        self._htf_swing_lows = list(lows)

    def update_session_range(self, bar: Bar, killzone_name: str | None) -> None:
        """Expand the session range for the active killzone, or reset on change."""
        if killzone_name is None:
            self._last_killzone = None
            return
        if killzone_name != self._last_killzone:
            # New killzone opened — reset its range to this bar
            self._session_ranges[killzone_name] = (bar.high, bar.low)
            self._last_killzone = killzone_name
        else:
            prev_high, prev_low = self._session_ranges.get(
                killzone_name, (bar.high, bar.low)
            )
            self._session_ranges[killzone_name] = (
                max(prev_high, bar.high),
                min(prev_low, bar.low),
            )

    # ------------------------------------------------------------------
    # Read-only helpers (for tests and score())
    # ------------------------------------------------------------------

    def session_range(self, killzone_name: str) -> tuple[Decimal, Decimal] | None:
        """Return (high, low) for the named killzone, or None if not yet set."""
        return self._session_ranges.get(killzone_name)

    def has_delivery_fvg(self, sweep_extreme: Decimal, atr: Decimal) -> bool:
        """True if sweep_extreme is within 0.5 × ATR of any 30min delivery FVG."""
        tol = atr * Decimal("0.5")
        for fvg in self._delivery_fvgs:
            if fvg.low - tol <= sweep_extreme <= fvg.high + tol:
                return True
        return False

    # ------------------------------------------------------------------
    # Internal: 30min FVG scanning (mirrors HTFLevelFinder logic but uses
    # FairValueGap instead of the private _Gap type)
    # ------------------------------------------------------------------

    @staticmethod
    def _compute_unmitigated_fvgs(bars: list[Bar]) -> list[FairValueGap]:
        """Find 3-bar FVGs in bars, then remove any a later bar mitigated."""
        gaps: list[tuple[int, FairValueGap]] = []
        for i in range(2, len(bars)):
            b1, b3 = bars[i - 2], bars[i]
            if b3.low > b1.high:
                gaps.append((i, FairValueGap(
                    side="bullish", low=b1.high, high=b3.low, created_at=b3.ts,
                )))
            elif b3.high < b1.low:
                gaps.append((i, FairValueGap(
                    side="bearish", low=b3.high, high=b1.low, created_at=b3.ts,
                )))

        out: list[FairValueGap] = []
        for formed_idx, fvg in gaps:
            mitigated = False
            for later in bars[formed_idx + 1:]:
                if fvg.side == "bullish" and later.low <= fvg.low:
                    mitigated = True
                    break
                if fvg.side == "bearish" and later.high >= fvg.high:
                    mitigated = True
                    break
            if not mitigated:
                out.append(fvg)
        return out
```

- [ ] **Step 4: Run session range and delivery tests — verify PASS**

```
pytest tests/test_grader.py::TestSessionRange tests/test_grader.py::TestDeliveryFVG -v
```

- [ ] **Step 5: Commit**

```
git add app/strategy/grader.py tests/test_grader.py
git commit -m "feat: SetupGrader with session range and delivery FVG tracking"
```

---

## Task 7: SetupGrader `score()` Method

**Files:**
- Modify: `app/strategy/grader.py`
- Modify: `tests/test_grader.py`

- [ ] **Step 1: Write failing score() tests**

Append to `tests/test_grader.py`:

```python
from datetime import timezone
from app.strategy.composer import ComposerConfig, Signal, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, DisplacementConfig
from app.strategy.liquidity import LiquidityConfig, LiquidityTracker
from app.risk.pretrade import Side


def _make_signal(
    side: str = "short",
    entry: str = "2405",
    stop: str = "2410",
    target: str = "2395",
    sweep_extreme: str = "2408",
    fvg_low: str = "2401",
    fvg_high: str = "2403",
    rationale: str = "NY AM: test signal",
) -> Signal:
    return Signal(
        instrument="MGC",
        side=side,
        entry=Decimal(entry),
        stop=Decimal(stop),
        target=Decimal(target),
        created_at=BASE_TS,
        killzone="NY AM",
        sweep_pattern="B_one_bar",
        sweep_extreme=Decimal(sweep_extreme),
        fvg_low=Decimal(fvg_low),
        fvg_high=Decimal(fvg_high),
        rationale=rationale,
    )


def _make_disp(
    body_to_atr: str = "1.8",
    body_to_range: str = "0.7",
    atr: str = "2.0",
) -> "DisplacementEvent":
    b = bar(0, "2410", "2412", "2398", "2399")
    body = Decimal(body_to_atr) * Decimal(atr)
    return DisplacementEvent(
        side="bearish",
        displacement_bar=b,
        body_size=body,
        atr_at_event=Decimal(atr),
        body_to_atr=Decimal(body_to_atr),
        fvg=fvg("bullish", "2401", "2403"),
    )


class TestScoreGrades:
    def _grader_with_htf(self) -> SetupGrader:
        g = SetupGrader()
        # HTF swings: high at 2420 (above entry 2405), low at 2390 (below)
        g.update_htf_swings([Decimal("2420")], [Decimal("2390")])
        # Session range: high 2412, low 2398 → mid = 2405; entry 2405 is at mid (not strictly premium for short)
        # Use high 2415, low 2395 → mid=2405; short entry at 2405 = mid → barely not premium
        # Use high 2415, low 2390 → mid=2402.5; short entry 2405 > mid → premium ✓
        g._session_ranges["NY AM"] = (Decimal("2415"), Decimal("2390"))
        g._last_killzone = "NY AM"
        return g

    def test_grade_b_minus_when_momentum_weak(self):
        """body_to_atr < 1.0 → B-, fails."""
        g = self._grader_with_htf()
        signal = _make_signal()
        disp = _make_disp(body_to_atr="0.8")
        grade = g.score(signal, disp, active_fvgs=[])
        assert grade.grade == "B-"
        assert grade.passes is False
        assert grade.momentum_quality == "weak"

    def test_grade_b_when_no_clear_target(self):
        """No HTF label and target far from any swing → B, fails."""
        g = SetupGrader()
        g.update_htf_swings([Decimal("2500")], [Decimal("2500")])  # far from target
        g._session_ranges["NY AM"] = (Decimal("2415"), Decimal("2390"))
        g._last_killzone = "NY AM"
        signal = _make_signal(target="2395", rationale="NY AM: no HTF level")
        disp = _make_disp(body_to_atr="1.8")
        grade = g.score(signal, disp, active_fvgs=[])
        assert grade.grade == "B"
        assert grade.passes is False
        assert grade.target_clear is False

    def test_grade_b_when_fvg_not_singular(self):
        """Overlapping active FVG in entry zone → B, fails."""
        g = self._grader_with_htf()
        signal = _make_signal(rationale="NY AM: HTF: 4h FVG @ 2395")
        disp = _make_disp(body_to_atr="1.8")
        # Another FVG overlapping [2401, 2403]
        overlapping = fvg("bearish", "2402", "2404")
        grade = g.score(signal, disp, active_fvgs=[overlapping])
        assert grade.grade == "B"
        assert grade.passes is False
        assert grade.fvg_singular is False

    def test_grade_a_minus_when_wrong_premium_discount(self):
        """Entry not in premium/discount → A- (passes, P/D relaxed at A-)."""
        g = SetupGrader()
        # Short signal but entry is in DISCOUNT (below mid) → wrong P/D
        g.update_htf_swings([Decimal("2420")], [Decimal("2380")])
        # session mid = (2415+2385)/2 = 2400; entry 2395 < mid → discount, wrong for short
        g._session_ranges["NY AM"] = (Decimal("2415"), Decimal("2385"))
        g._last_killzone = "NY AM"
        signal = _make_signal(entry="2395", stop="2398", target="2382",
                               rationale="NY AM: HTF: 30min swing @ 2382")
        disp = _make_disp(body_to_atr="1.8")
        grade = g.score(signal, disp, active_fvgs=[])
        assert grade.grade == "A-"
        assert grade.passes is True
        assert grade.premium_discount_ok is False

    def test_grade_a_when_correct_pd_and_strong_momentum(self):
        """Correct P/D + strong momentum → A."""
        g = self._grader_with_htf()
        signal = _make_signal(rationale="NY AM: HTF: 4h FVG @ 2395")
        disp = _make_disp(body_to_atr="1.8", body_to_range="0.7")
        grade = g.score(signal, disp, active_fvgs=[])
        assert grade.grade == "A"
        assert grade.passes is True
        assert grade.premium_discount_ok is True

    def test_grade_a_plus_when_delivery_fvg_also_present(self):
        """Grade A + delivery FVG match → A+."""
        g = self._grader_with_htf()
        # Add 30min delivery FVG that overlaps sweep_extreme (2408)
        g.update_delivery_fvgs([
            Bar("MGC", "30min", BASE_TS - timedelta(hours=2),
                Decimal("2409"), Decimal("2410"), Decimal("2407"), Decimal("2409"), 100),
            Bar("MGC", "30min", BASE_TS - timedelta(hours=1, minutes=30),
                Decimal("2409"), Decimal("2410"), Decimal("2408"), Decimal("2410"), 100),
            Bar("MGC", "30min", BASE_TS - timedelta(hours=1),
                Decimal("2412"), Decimal("2413"), Decimal("2412"), Decimal("2412"), 100),
        ])
        signal = _make_signal(rationale="NY AM: HTF: 4h FVG @ 2395")
        disp = _make_disp(body_to_atr="1.8", body_to_range="0.7")
        grade = g.score(signal, disp, active_fvgs=[])
        assert grade.grade == "A+"
        assert grade.has_delivery_fvg is True
```

- [ ] **Step 2: Run — verify FAIL**

```
pytest tests/test_grader.py::TestScoreGrades -v
```
Expected: `AttributeError: 'SetupGrader' object has no attribute 'score'`

- [ ] **Step 3: Add `score()` to `SetupGrader` in `grader.py`**

Add these methods to the `SetupGrader` class:

```python
def score(
    self,
    signal: "Signal",
    disp: "DisplacementEvent",
    active_fvgs: list[FairValueGap],
) -> SetupGrade:
    """
    Score a signal candidate against Dodgy's 5 criteria.
    Returns a SetupGrade; check .passes to decide whether to trade.
    """
    b2 = disp.displacement_bar
    body = disp.body_size
    bar_range = b2.high - b2.low
    body_to_range = (body / bar_range) if bar_range > 0 else Decimal("0")

    # -- Step 1: Momentum --
    if disp.body_to_atr < Decimal("1.0"):
        return SetupGrade(
            grade="B-", passes=False,
            has_delivery_fvg=False, premium_discount_ok=False,
            target_clear=False, fvg_singular=False,
            momentum_quality="weak",
            reason="momentum too weak (body_to_atr < 1.0) — B-",
        )

    # -- Step 2: Target clarity --
    target_clear = self._check_target_clarity(signal, disp.atr_at_event)
    if not target_clear:
        return SetupGrade(
            grade="B", passes=False,
            has_delivery_fvg=False, premium_discount_ok=False,
            target_clear=False, fvg_singular=False,
            momentum_quality="decent",
            reason="no structural target found — B",
        )

    # -- Step 3: FVG singularity --
    fvg_singular = self._check_fvg_singular(signal, active_fvgs)
    if not fvg_singular:
        return SetupGrade(
            grade="B", passes=False,
            has_delivery_fvg=False, premium_discount_ok=False,
            target_clear=True, fvg_singular=False,
            momentum_quality="decent",
            reason="overlapping FVGs in entry zone — B",
        )

    # -- At least A-: compute remaining criteria --
    momentum_quality: MomentumQuality = (
        "strong"
        if disp.body_to_atr >= Decimal("1.5") and body_to_range >= Decimal("0.6")
        else "decent"
    )

    pd_ok = self._check_premium_discount(signal)
    delivery = self.has_delivery_fvg(signal.sweep_extreme, disp.atr_at_event)

    grade: Literal["A+", "A", "A-", "B", "B-"]
    if pd_ok and momentum_quality == "strong":
        grade = "A+" if delivery else "A"
    else:
        grade = "A-"

    reason = (
        f"{signal.killzone}: grade {grade} — "
        f"momentum={momentum_quality}, P/D={'ok' if pd_ok else 'off'}, "
        f"delivery={'yes' if delivery else 'no'}"
    )
    log.info(reason)

    return SetupGrade(
        grade=grade, passes=True,
        has_delivery_fvg=delivery,
        premium_discount_ok=pd_ok,
        target_clear=True, fvg_singular=True,
        momentum_quality=momentum_quality,
        reason=reason,
    )

def _check_target_clarity(self, signal: "Signal", atr: Decimal) -> bool:
    """True if target aligns with HTF level, session extreme, or HTF swing."""
    if "HTF:" in signal.rationale:
        return True
    tol = atr * Decimal("3")
    if signal.side == "long":
        # Target should be near a swing high above entry or session high
        return (
            any(abs(signal.target - h) <= tol
                for h in self._htf_swing_highs if h > signal.entry)
            or any(abs(signal.target - high) <= tol
                   for (high, _) in self._session_ranges.values())
        )
    else:
        return (
            any(abs(signal.target - l) <= tol
                for l in self._htf_swing_lows if l < signal.entry)
            or any(abs(signal.target - low) <= tol
                   for (_, low) in self._session_ranges.values())
        )

def _check_fvg_singular(self, signal: "Signal", active_fvgs: list[FairValueGap]) -> bool:
    """True if no other active 1min FVG overlaps the signal's iFVG zone."""
    if signal.fvg_low is None or signal.fvg_high is None:
        return True
    for fvg in active_fvgs:
        if fvg.low == signal.fvg_low and fvg.high == signal.fvg_high:
            continue  # same FVG as the signal's own
        # Overlap: two intervals overlap if one's low < other's high
        if fvg.low < signal.fvg_high and fvg.high > signal.fvg_low:
            return False
    return True

def _check_premium_discount(self, signal: "Signal") -> bool:
    """
    True if entry is in the correct half of BOTH session range and HTF swing range.
    Long: entry < midpoint (discount). Short: entry > midpoint (premium).
    """
    kz = signal.killzone
    session = self._session_ranges.get(kz)
    if session is None:
        return False
    sess_mid = (session[0] + session[1]) / 2

    if not self._htf_swing_highs or not self._htf_swing_lows:
        return False
    # Use nearest swing high above entry and nearest swing low below entry
    highs_above = [h for h in self._htf_swing_highs if h > signal.entry]
    lows_below = [l for l in self._htf_swing_lows if l < signal.entry]
    if not highs_above or not lows_below:
        return False
    htf_mid = (min(highs_above) + max(lows_below)) / 2

    if signal.side == "long":
        return signal.entry < sess_mid and signal.entry < htf_mid
    else:
        return signal.entry > sess_mid and signal.entry > htf_mid
```

- [ ] **Step 4: Run score tests — verify PASS**

```
pytest tests/test_grader.py -v
```
Expected: all PASS.

- [ ] **Step 5: Commit**

```
git add app/strategy/grader.py tests/test_grader.py
git commit -m "feat: add score() to SetupGrader with full grade algorithm"
```

---

## Task 8: Wire Grader into StrategyRunner

**Files:**
- Modify: `app/execution/engine.py`

- [ ] **Step 1: Update imports and StrategyRunner dataclass**

In `app/execution/engine.py`, add to imports:

```python
from dataclasses import replace as dc_replace
from app.strategy.grader import SetupGrader
from app.strategy.killzone import in_killzone
```

In the `StrategyRunner` dataclass, add `grader` as a mandatory field (no default) BEFORE the optional `vp` field:

```python
grader: SetupGrader
vp: VolumeProfileTracker | None = None
```

- [ ] **Step 2: Update `on_bar` to call grader**

Replace the existing `on_bar` method in `StrategyRunner`:

```python
def on_bar(self, bar: Bar) -> Optional[Signal]:
    """Run all detectors against one bar. Returns at most one Signal."""
    sweeps = self.liquidity.on_bar(bar, atr=self._prev_atr)
    for s in sweeps:
        self.composer.on_sweep(bar, s)

    signal: Optional[Signal] = None
    disp = self.displacement.on_bar(bar)
    if disp is not None:
        candidate = self.composer.on_displacement(bar, disp)
        if candidate is not None:
            grade = self.grader.score(
                candidate, disp, self.displacement.active_fvgs
            )
            if grade.passes:
                signal = dc_replace(candidate, setup_grade=grade)
            else:
                log.info(
                    "Signal filtered by grader: %s — %s",
                    grade.grade, grade.reason,
                )

    self._prev_atr = self.displacement.atr

    kz = in_killzone(bar.ts, self.composer._zones)
    self.grader.update_session_range(bar, kz.name if kz else None)

    self.composer.on_bar_close(bar)
    return signal
```

- [ ] **Step 3: Fix all StrategyRunner instantiation sites**

Search for all places in the codebase that construct a `StrategyRunner`:

```
grep -rn "StrategyRunner(" app/ tests/
```

For each site, add `grader=SetupGrader()` as an argument. The most common site is `main.py`'s `_build_runner` function — that gets a proper grader in Task 9. For test files, pass `grader=SetupGrader()`.

- [ ] **Step 4: Run tests — verify no regressions**

```
pytest tests/test_engine.py tests/test_strategy.py tests/test_integration.py -v --tb=short
```
Fix any remaining failures (most will be missing `grader=` in test constructors).

- [ ] **Step 5: Commit**

```
git add app/execution/engine.py tests/
git commit -m "feat: wire SetupGrader as mandatory field in StrategyRunner"
```

---

## Task 9: Wire Grader in `main.py`

**Files:**
- Modify: `app/main.py`

- [ ] **Step 1: Add import**

In `app/main.py`, add to imports:

```python
from app.strategy.grader import SetupGrader
```

- [ ] **Step 2: Instantiate grader in `_build_runner`**

Find the function or block where `StrategyRunner(...)` is constructed (search: `StrategyRunner(`). Add `grader=SetupGrader()` to the constructor call.

- [ ] **Step 3: Feed grader in `_refresh_htf_once`**

In `_refresh_htf_once`, after the `level_finder.rebuild(fvg_bars=bias_bars, swing_bars=swing_bars)` call, add:

```python
# Feed grader with 30min delivery FVGs and HTF swing levels
for instrument, runner in (engine.runners.items() if engine is not None else []):
    runner.grader.update_delivery_fvgs(swing_bars)
    runner.grader.update_htf_swings(
        level_finder.swing_highs,
        level_finder.swing_lows,
    )
```

Note: `_refresh_htf_once` receives `broker` and `s` (StrategyParams) but not `engine` directly. Check the actual function signature and adjust to pass the runner reference appropriately — the exact wiring depends on how `_htf_refresh_loop` holds a reference to the runner. If it accesses `engine.runners`, ensure `engine` is in scope at the call site.

- [ ] **Step 4: Run the bot startup check**

```
python -m app.main --help
```
Expected: no import errors or crashes.

- [ ] **Step 5: Run full test suite**

```
pytest tests/ -v --tb=short
```
Expected: all PASS.

- [ ] **Step 6: Commit**

```
git add app/main.py
git commit -m "feat: wire SetupGrader into _build_runner and _refresh_htf_once"
```

---

## Task 10: Journal `publish_strategy_state`

**Files:**
- Modify: `app/api/journal.py`
- Modify: `app/execution/engine.py` (StrategyRunner.on_bar — add journal call)

- [ ] **Step 1: Add `publish_strategy_state` to Journal**

In `app/api/journal.py`, add after the existing `publish_bar` method:

```python
def publish_strategy_state(
    self,
    grade: "SetupGrade | None",
    instrument: str,
    active_fvgs_count: int = 0,
    session_high: Decimal | None = None,
    session_low: Decimal | None = None,
) -> None:
    """Emit a strategy_state WebSocket event every bar for the dashboard."""
    from app.strategy.grader import SetupGrade  # local import avoids circular
    payload: dict = {"instrument": instrument}
    if grade is not None:
        payload.update({
            "grade": grade.grade,
            "passes": grade.passes,
            "has_delivery_fvg": grade.has_delivery_fvg,
            "premium_discount_ok": grade.premium_discount_ok,
            "target_clear": grade.target_clear,
            "fvg_singular": grade.fvg_singular,
            "momentum_quality": grade.momentum_quality,
            "reason": grade.reason,
        })
    payload["active_fvgs_count"] = active_fvgs_count
    if session_high is not None:
        payload["session_high"] = str(session_high)
    if session_low is not None:
        payload["session_low"] = str(session_low)

    entry = JournalEntry(
        ts=datetime.now(timezone.utc),
        kind="strategy_state",
        payload=_decimal_to_str(payload),
    )
    self._publish(entry)
```

- [ ] **Step 2: Call `publish_strategy_state` from StrategyRunner**

The runner doesn't currently hold a journal reference. The cleanest place to call `publish_strategy_state` is in `server.py`'s bar handler loop (where `runner.on_bar(bar)` is already called and `journal` is in scope). Search `server.py` for where `runner.on_bar` is called and add the publish call after it:

```python
signal = runner.on_bar(bar)
# Publish strategy state for dashboard
kz_name = runner.composer._zones  # already computed inside on_bar
sr = runner.grader.session_range(runner.composer._zones[0].name if runner.composer._zones else "")
journal.publish_strategy_state(
    grade=signal.setup_grade if signal else None,
    instrument=runner.instrument,
    active_fvgs_count=len(runner.displacement.active_fvgs),
    session_high=sr[0] if sr else None,
    session_low=sr[1] if sr else None,
)
```

Find the exact location by searching `server.py` for `on_bar` and insert there.

- [ ] **Step 3: Run tests**

```
pytest tests/test_api.py tests/ -v --tb=short
```

- [ ] **Step 4: Commit**

```
git add app/api/journal.py app/api/server.py
git commit -m "feat: publish strategy_state WebSocket event each bar"
```

---

## Task 11: StrategyDebug Frontend Component

**Files:**
- Create: `frontend/src/components/StrategyDebug.tsx`
- Modify: `frontend/src/App.tsx`

- [ ] **Step 1: Create `StrategyDebug.tsx`**

Create `frontend/src/components/StrategyDebug.tsx`:

```tsx
import { useState, useEffect, useRef } from "react";

interface StrategyState {
  instrument?: string;
  grade?: string;
  passes?: boolean;
  has_delivery_fvg?: boolean;
  premium_discount_ok?: boolean;
  target_clear?: boolean;
  fvg_singular?: boolean;
  momentum_quality?: string;
  reason?: string;
  active_fvgs_count?: number;
  session_high?: string;
  session_low?: string;
}

function Check({ ok }: { ok: boolean | undefined }) {
  if (ok === undefined) return <span className="text-dim">—</span>;
  return ok
    ? <span className="text-[#00ff41]">✓</span>
    : <span className="text-red-500">✗</span>;
}

function GradeBadge({ grade, passes }: { grade?: string; passes?: boolean }) {
  if (!grade) return <span className="text-dim">—</span>;
  const color = passes ? "text-[#00ff41]" : "text-red-500";
  return <span className={`font-bold text-lg ${color}`}>{grade}</span>;
}

export function StrategyDebug({ wsUrl }: { wsUrl: string }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<StrategyState>({});
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.kind === "strategy_state") {
          setState(msg.payload as StrategyState);
        }
      } catch {}
    };
    return () => ws.close();
  }, [wsUrl]);

  return (
    <div className="border border-border mt-px">
      <button
        className="w-full flex items-center justify-between px-3 py-2 text-xs text-dim hover:text-ink"
        onClick={() => setOpen((o) => !o)}
      >
        <span>STRATEGY DEBUG</span>
        <span>{open ? "▲" : "▼"}</span>
      </button>

      {open && (
        <div className="px-3 pb-3 text-xs space-y-2">
          <div className="flex items-center gap-3">
            <GradeBadge grade={state.grade} passes={state.passes} />
            <span className="text-dim">{state.reason ?? "awaiting bar…"}</span>
          </div>

          <table className="w-full">
            <tbody>
              {[
                ["Sweep present", true],
                ["iFVG inversion", state.fvg_singular],
                ["Target clear", state.target_clear],
                ["FVG singular", state.fvg_singular],
                ["Premium/Discount", state.premium_discount_ok],
                ["30min delivery FVG", state.has_delivery_fvg],
              ].map(([label, val]) => (
                <tr key={label as string}>
                  <td className="text-dim pr-4 py-0.5">{label as string}</td>
                  <td><Check ok={val as boolean | undefined} /></td>
                </tr>
              ))}
              <tr>
                <td className="text-dim pr-4 py-0.5">Momentum</td>
                <td className="text-ink">{state.momentum_quality ?? "—"}</td>
              </tr>
              <tr>
                <td className="text-dim pr-4 py-0.5">Active 1min FVGs</td>
                <td className="text-ink">{state.active_fvgs_count ?? "—"}</td>
              </tr>
              <tr>
                <td className="text-dim pr-4 py-0.5">Session H/L</td>
                <td className="text-ink">
                  {state.session_high && state.session_low
                    ? `${state.session_high} / ${state.session_low}`
                    : "—"}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Mount in `App.tsx`**

In `frontend/src/App.tsx`, import the component:

```tsx
import { StrategyDebug } from "./components/StrategyDebug";
```

Find where the signal panel is rendered and add `<StrategyDebug>` immediately after it. The `wsUrl` should be the same WebSocket URL already used elsewhere in `App.tsx` (search for `ws://` or `useStream`).

- [ ] **Step 3: Build frontend**

```
cd frontend && npm run build
```
Expected: clean build, no TypeScript errors.

- [ ] **Step 4: Verify in browser**

Start the bot and open `http://localhost:5173`. Confirm:
- "STRATEGY DEBUG" collapsible section appears below the signal panel
- Expanding it shows the criterion table
- Grade updates each bar (grade badge changes colour on pass/fail)
- No console errors

- [ ] **Step 5: Commit**

```
git add frontend/src/components/StrategyDebug.tsx frontend/src/App.tsx frontend/dist/
git commit -m "feat: StrategyDebug panel showing live setup grade"
```

---

## Self-Review Checklist

**Spec coverage:**
- [x] FVG formation tracking — Task 1
- [x] Mitigation — Task 2
- [x] iFVG inversion detection — Task 3
- [x] `peek_displacement` + `try_signal_from_forming` — Task 4
- [x] `Signal.setup_grade` — Task 5
- [x] `HTFLevelFinder.swing_highs/lows` — Task 5
- [x] `SetupGrade` dataclass — Task 6
- [x] Session range tracking — Task 6
- [x] Delivery FVG tracking — Task 6
- [x] `score()` — momentum, target, singularity, P/D, delivery — Task 7
- [x] StrategyRunner mandatory grader field — Task 8
- [x] `main.py` wiring — Task 9
- [x] `publish_strategy_state` journal — Task 10
- [x] `StrategyDebug.tsx` + App.tsx — Task 11

**Confirmed no placeholders, no TBD, no "similar to Task N" shortcuts.**

**Type consistency confirmed:**
- `SetupGrade` defined in Task 6, referenced in Tasks 7, 8, 10, 11
- `active_fvgs: list[FairValueGap]` defined in Task 1, used in Tasks 3, 7, 8
- `swing_highs` / `swing_lows` properties defined in Task 5, used in Task 9
- `dc_replace` (alias for `dataclasses.replace`) defined in Task 8 import
- `_find_inverted_fvg` defined in Task 3, called in Task 4
