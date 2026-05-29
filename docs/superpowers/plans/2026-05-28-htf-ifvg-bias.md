# HTF IFVG Bias Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the swing-structure HTF bias detector with an Inversed Fair Value Gap (IFVG) detector — bias flips when a 4h FVG is closed through from the wrong side.

**Architecture:** `HTFBiasTracker.rebuild()` is rewritten to scan bars chronologically, tracking the most recent 3-bar FVG. When a bar closes through the far edge of that FVG (inversion), bias flips. The interface (`rebuild()`, `bias()`, `diagnostics()`) is unchanged so no callers need updating. The `_Gap` dataclass already in `htf.py` is reused for the tracked FVG.

**Tech Stack:** Python 3.12. Run tests with `.venv/Scripts/python.exe -m pytest`. No frontend or engine changes.

---

## Background for the implementer

- **Working directory:** `C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot`
- **`HTFBiasTracker`** is in `app/strategy/htf.py`. Its `rebuild(bars)` method is called by `_refresh_htf_once` in `main.py` every 60 seconds with the last 90 days of 4h bars. Its `bias()` method is called by the engine's `_apply_confluence` to gate signals. **The interface must not change.**
- **`_Gap`** is a frozen dataclass already in `htf.py`: `side: Literal["bullish","bearish"]`, `low: Decimal`, `high: Decimal`. Reuse it for the tracked FVG.
- **FVG definition (3-bar pattern):**
  - Bearish FVG: `bars[i-2].low > bars[i].high` → gap zone = `low=bars[i].high, high=bars[i-2].low`
  - Bullish FVG: `bars[i-2].high < bars[i].low` → gap zone = `low=bars[i-2].high, high=bars[i].low`
- **Inversion (IFVG) rule:**
  - Bearish FVG inversed when `bar.close > fvg.high` → bias = `"bullish"`
  - Bullish FVG inversed when `bar.close < fvg.low` → bias = `"bearish"`
- **"Most recent FVG":** when a new FVG forms, it replaces the tracked one (even if the old one was never inversed). Only one FVG tracked at a time.
- **Order of operations per bar:** (1) check inversion of tracked FVG, then (2) check if this bar completes a new FVG.
- **Bias persists:** once set by an inversion, bias stays until the next inversion. Starts `"neutral"` until the first inversion occurs.
- **`lookback` param:** kept on `__init__` for API compatibility (callers pass it) but unused in IFVG logic.
- **Pre-existing test failures (do not fix):** `test_signal_denied_when_already_at_max_contracts`, `test_journal_subscriber_drops_old_under_backpressure`, `test_paper_mode_full_run`, and two reconciler tests.

---

## File structure

| File | Action |
|------|--------|
| `app/strategy/htf.py` | Rewrite `HTFBiasTracker` internals |
| `tests/test_htf.py` | Replace old bias tests with IFVG tests |

---

## Task 1: Replace HTFBiasTracker with IFVG logic

**Files:**
- Modify: `app/strategy/htf.py`
- Modify: `tests/test_htf.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_htf.py`, **replace** the four existing `HTFBiasTracker` tests (lines containing `test_bias_neutral_when_insufficient_data`, `test_bias_bullish_on_higher_highs`, `test_bias_bearish_on_lower_highs`, `test_bias_neutral_on_mixed_structure`) with these six IFVG tests. Keep all `HTFLevelFinder` tests unchanged.

The `_bar` helper already exists in the file — reuse it.

```python
def test_ifvg_bias_neutral_with_fewer_than_3_bars():
    """Fewer than 3 bars → cannot form a FVG → neutral."""
    t = HTFBiasTracker()
    t.rebuild([_bar(0, 10, 11, 9, 10), _bar(1, 10, 11, 9, 10)])
    assert t.bias() == "neutral"


def test_ifvg_bias_neutral_before_any_inversion():
    """FVG exists but has not been inversed yet → neutral."""
    t = HTFBiasTracker()
    # Bearish FVG: b1.low=10 > b3.high=9 → gap [9, 10]
    # b4 close=9.5 stays inside gap, does not invert
    t.rebuild([
        _bar(0, 11, 12, 10, 11),   # b1: low=10
        _bar(1, 10, 10,  8,  9),   # b2
        _bar(2,  9,  9,  7,  8),   # b3: high=9 → bearish FVG [9, 10]
        _bar(3,  8,  9,  7,  9),   # b4: close=9 — does NOT cross high=10
    ])
    assert t.bias() == "neutral"


def test_ifvg_bias_bullish_after_bearish_fvg_inversion():
    """Bearish FVG inversed (close > fvg.high) → bias = bullish."""
    t = HTFBiasTracker()
    # Bearish FVG: b1.low=10 > b3.high=9 → gap [9, 10]
    # b4 closes at 10.5 > fvg.high=10 → IFVG → bullish
    t.rebuild([
        _bar(0, 11, 12, 10, 11),   # b1: low=10
        _bar(1, 10, 10,  8,  9),   # b2
        _bar(2,  9,  9,  7,  8),   # b3: high=9 → bearish FVG [9, 10]
        _bar(3,  8, 11,  8, 10.5), # b4: close=10.5 > 10 → inversion → bullish
    ])
    assert t.bias() == "bullish"


def test_ifvg_bias_bearish_after_bullish_fvg_inversion():
    """Bullish FVG inversed (close < fvg.low) → bias = bearish."""
    t = HTFBiasTracker()
    # Bullish FVG: b3.low=11 > b1.high=10 → gap [10, 11]
    # b4 closes at 9.5 < fvg.low=10 → IFVG → bearish
    t.rebuild([
        _bar(0,  9, 10,  8,  9),   # b1: high=10
        _bar(1, 10, 12, 10, 11),   # b2
        _bar(2, 11, 12, 11, 11.5), # b3: low=11 → bullish FVG [10, 11]
        _bar(3, 11, 11,  9,  9.5), # b4: close=9.5 < 10 → inversion → bearish
    ])
    assert t.bias() == "bearish"


def test_ifvg_most_recent_fvg_replaces_old_unmitigated_fvg():
    """When a new FVG forms before the tracked one is inversed, the new one is tracked."""
    t = HTFBiasTracker()
    # First: bearish FVG [9, 10] forms but is never inversed.
    # Then: bullish FVG [12, 13] forms and IS inversed → bearish.
    t.rebuild([
        _bar(0, 11, 12, 10, 11),   # b1 for FVG1
        _bar(1, 10, 10,  8,  9),   # b2 for FVG1
        _bar(2,  9,  9,  7,  8),   # b3 for FVG1: bearish FVG [9, 10]
        _bar(3, 11, 13, 11, 12),   # b1 for FVG2 (does NOT invert bearish FVG [9,10])
        _bar(4, 12, 14, 12, 13),   # b2 for FVG2
        _bar(5, 13, 14, 13, 13.5), # b3 for FVG2: low=13 > b1.high=13? No.
        # Let's build a clean bullish FVG: b1.high=10, b3.low=12 → gap [10, 12]
    ])
    # The above doesn't cleanly produce FVG2. Use a clean sequence instead:
    t2 = HTFBiasTracker()
    t2.rebuild([
        # Bearish FVG [9, 10] — never inverted
        _bar(0, 11, 12, 10, 11),
        _bar(1, 10, 10,  8,  9),
        _bar(2,  9,  9,  7,  8),   # bearish FVG [9, 10]
        # Next 3 bars form bullish FVG [10, 12]
        _bar(3,  8,  9,  8,  9),   # b4 (no inversion of bearish, close=9 not > 10)
        _bar(4,  9, 11,  9, 10),   # b5
        _bar(5, 12, 13, 12, 12.5), # b6: low=12 > b4.high=9 → bullish FVG [9, 12]
        # Now invert the bullish FVG: close < low=9
        _bar(6, 12, 12,  8,  8.5), # close=8.5 < 9 → bearish
    ])
    assert t2.bias() == "bearish"


def test_ifvg_bias_persists_after_inversion():
    """Bias stays at the last inversion value; does not revert to neutral."""
    t = HTFBiasTracker()
    # Inversion at bar 3, then 3 more bars with no new FVG → bias stays bullish.
    t.rebuild([
        _bar(0, 11, 12, 10, 11),
        _bar(1, 10, 10,  8,  9),
        _bar(2,  9,  9,  7,  8),   # bearish FVG [9, 10]
        _bar(3,  8, 11,  8, 10.5), # inversion → bullish
        _bar(4, 10, 11,  9, 10),   # no new FVG
        _bar(5, 10, 11,  9, 10),   # no new FVG
        _bar(6, 10, 11,  9, 10),   # no new FVG
    ])
    assert t.bias() == "bullish"
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_htf.py -k "ifvg" -q`
Expected: collection errors or `AttributeError` — tests reference no changed behavior yet.

- [ ] **Step 3: Rewrite `HTFBiasTracker` in `htf.py`**

Replace the entire `HTFBiasTracker` class with this implementation. Keep everything else in `htf.py` unchanged (`_Gap`, `HTFLevelFinder`, module-level imports, docstring).

```python
class HTFBiasTracker:
    """4h FVG inversion (IFVG) → directional bias.

    Scans bars chronologically for 3-bar Fair Value Gaps. Tracks the most
    recent FVG. When a bar closes through the far edge of that FVG (inversion),
    flips bias:
      - Bearish FVG inversed (close > fvg.high) → bullish
      - Bullish FVG inversed (close < fvg.low)  → bearish

    Bias persists at the last inversion until the next inversion. Starts
    neutral until the first inversion is observed.

    FVG definition (3-bar pattern, bars b1/b2/b3 in order):
      - Bearish: b1.low > b3.high  → gap zone [b3.high, b1.low]
      - Bullish: b1.high < b3.low  → gap zone [b1.high, b3.low]
    """

    def __init__(self, lookback: int = 3) -> None:
        # lookback retained for API compatibility; unused in IFVG logic.
        self._bias: Bias = "neutral"
        self._last_bar_count: int = 0
        self._tracked_fvg: "_Gap | None" = None
        self._last_inversion_ts = None  # datetime | None

    def rebuild(self, bars: list[Bar]) -> None:
        """Scan bars chronologically for FVG inversions; update bias."""
        old = self._bias
        self._last_bar_count = len(bars)

        if len(bars) < 3:
            self._bias = "neutral"
            self._tracked_fvg = None
            self._last_inversion_ts = None
            return

        ordered = sorted(bars, key=lambda b: b.ts)
        bias: Bias = "neutral"
        tracked: "_Gap | None" = None
        last_inv_ts = None

        for i, bar in enumerate(ordered):
            # 1. Check if this bar inverts the tracked FVG (close through far edge).
            if tracked is not None:
                if tracked.side == "bearish" and bar.close > tracked.high:
                    bias = "bullish"
                    last_inv_ts = bar.ts
                    log.debug(
                        "IFVG: bearish [%s–%s] inversed at %s → bullish",
                        tracked.low, tracked.high, bar.ts,
                    )
                    tracked = None
                elif tracked.side == "bullish" and bar.close < tracked.low:
                    bias = "bearish"
                    last_inv_ts = bar.ts
                    log.debug(
                        "IFVG: bullish [%s–%s] inversed at %s → bearish",
                        tracked.low, tracked.high, bar.ts,
                    )
                    tracked = None

            # 2. Check if bar i completes a new 3-bar FVG.
            if i >= 2:
                b1, b3 = ordered[i - 2], bar
                if b3.low > b1.high:       # bullish FVG: gap between b1.high and b3.low
                    tracked = _Gap("bullish", b1.high, b3.low)
                elif b3.high < b1.low:     # bearish FVG: gap between b3.high and b1.low
                    tracked = _Gap("bearish", b3.high, b1.low)

        if bias != old:
            log.info("HTFBias (IFVG): %s -> %s", old, bias)
        self._bias = bias
        self._tracked_fvg = tracked
        self._last_inversion_ts = last_inv_ts

    def bias(self) -> Bias:
        return self._bias

    def diagnostics(self) -> dict:
        return {
            "bias": self._bias,
            "bars_fed": self._last_bar_count,
            "tracked_fvg": {
                "side": self._tracked_fvg.side,
                "low": str(self._tracked_fvg.low),
                "high": str(self._tracked_fvg.high),
            } if self._tracked_fvg else None,
            "last_inversion_ts": (
                self._last_inversion_ts.isoformat()
                if self._last_inversion_ts else None
            ),
        }
```

- [ ] **Step 4: Run IFVG tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_htf.py -q`
Expected: all HTF tests pass (IFVG bias tests + unchanged level finder tests).

Note: `test_ifvg_most_recent_fvg_replaces_old_unmitigated_fvg` creates two trackers (`t` and `t2`) — only `t2` is asserted. This is fine; `t` is unused scaffolding that can be ignored.

- [ ] **Step 5: Run full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_backtest.py --ignore=tests/test_analytics_loader.py --ignore=tests/test_analytics_tools.py`
Expected: only the 5 known pre-existing failures.

- [ ] **Step 6: Commit**

```bash
git add app/strategy/htf.py tests/test_htf.py
git commit -m "feat: replace HTFBiasTracker with IFVG-based bias detection"
```

---

## Self-review

**Spec coverage:**
- ✅ Bearish FVG inversed (close > high) → bullish → `test_ifvg_bias_bullish_after_bearish_fvg_inversion`
- ✅ Bullish FVG inversed (close < low) → bearish → `test_ifvg_bias_bearish_after_bullish_fvg_inversion`
- ✅ Most recent FVG tracked (not all) → single `tracked` variable, overwritten when new FVG forms → `test_ifvg_most_recent_fvg_replaces_old_unmitigated_fvg`
- ✅ Neutral until first inversion → `test_ifvg_bias_neutral_before_any_inversion`
- ✅ Bias persists after inversion → `test_ifvg_bias_persists_after_inversion`
- ✅ Interface unchanged (`rebuild`, `bias`, `diagnostics`) → no callers need updating
- ✅ `_Gap` reused for tracked FVG — already defined in `htf.py`
- ✅ `lookback` param kept for API compat

**Type consistency:**
- `_Gap("bullish", b1.high, b3.low)` — `_Gap.side` is `Literal["bullish","bearish"]`, `low`/`high` are `Decimal`. `bar.high` and `bar.low` are `Decimal` (from `Bar` dataclass). ✓
- `self._tracked_fvg: "_Gap | None"` — string annotation avoids forward-ref issue since `_Gap` is defined before `HTFBiasTracker`. Actually `_Gap` IS defined before `HTFBiasTracker` in the file, so the annotation can be `_Gap | None` without quotes. Either works. ✓
- `diagnostics()` returns `dict` consistent with engine's existing diagnostic display. ✓

**One issue found:** `test_ifvg_most_recent_fvg_replaces_old_unmitigated_fvg` creates unused `t` before `t2`. The test is valid (only `t2` is asserted) but the unused `t` block is dead code. The dead block should be deleted to keep the test clean — the comment explains the intent, and `t2` tests it.

Fixing inline — the test as written in Step 1 should have the unused `t` block removed. Here is the corrected version of that test (replace the full function in Step 1):

```python
def test_ifvg_most_recent_fvg_replaces_old_unmitigated_fvg():
    """When a new FVG forms before the tracked one is inversed, the new one is tracked."""
    t = HTFBiasTracker()
    # Bearish FVG [9, 10] forms. Before it's inverted, bullish FVG [9, 12] forms.
    # Bullish FVG [9, 12] is then inverted (close < 9) → bearish.
    t.rebuild([
        _bar(0, 11, 12, 10, 11),   # b1 of FVG1
        _bar(1, 10, 10,  8,  9),   # b2 of FVG1
        _bar(2,  9,  9,  7,  8),   # b3 of FVG1: bearish FVG [9, 10]
        _bar(3,  8,  9,  8,  9),   # b4: close=9, NOT > 10 (no inversion); b1 of FVG2
        _bar(4,  9, 11,  9, 10),   # b5: b2 of FVG2
        _bar(5, 12, 13, 12, 12.5), # b6: b3 of FVG2: low=12 > b4.high=9 → bullish FVG [9, 12]
        _bar(6, 12, 12,  8,  8.5), # b7: close=8.5 < fvg.low=9 → inversion → bearish
    ])
    assert t.bias() == "bearish"
```
