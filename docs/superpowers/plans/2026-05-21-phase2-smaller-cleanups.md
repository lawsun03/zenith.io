# Phase 2 Smaller Cleanups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix three accumulated quality issues: VP tolerance is 10× too loose in `bot_config.json`; the bot has no post-stop cooldown so it can immediately re-enter against a sweep that already stopped it out; `min_penetration` is a fixed price value when it should optionally scale with ATR.

**Architecture:** Three isolated changes that don't affect each other's code paths. (1) One-line JSON fix for VP tolerance. (2) `Fill.is_stop` field → PaperBroker pass-through → composer cooldown counter → engine wiring in `_handle_fill`. (3) `LiquidityConfig.min_penetration_atr_factor` → `LiquidityTracker.on_bar(atr=)` optional param → `StrategyRunner` caches previous-bar ATR and threads it through.

**Tech Stack:** Python dataclasses (frozen and mutable), asyncio, existing pytest suite. No new dependencies.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `bot_config.json` | Fix vp_filter_tolerance 20.0 → 2.0 |
| Modify | `app/sim/events.py` | Add `is_stop: bool = False` to `Fill` |
| Modify | `app/sim/paper.py` | Pass `is_stop=is_stop` when constructing Fill in `_close_bracket` |
| Modify | `app/strategy/composer.py` | Add `cooldown_bars_after_stop` to `ComposerConfig`; add `_cooldown_remaining` counter; add `on_stop_loss()` method; check cooldown in `on_displacement`; decrement in `on_bar_close` |
| Modify | `app/execution/engine.py` | In `_handle_fill`, call `runner.composer.on_stop_loss()` on stop exits; add `_prev_atr` cache to `StrategyRunner`; pass `atr=self._prev_atr` to `liquidity.on_bar` |
| Modify | `app/strategy/liquidity.py` | Add `min_penetration_atr_factor: Decimal \| None = None` to `LiquidityConfig`; change `on_bar(bar, atr=None)` signature; compute effective penetration |
| Create | `tests/test_phase2_cleanups.py` | Tests for cooldown and ATR-scaled penetration |

---

## Task 1: VP Tolerance Fix

**Files:**
- Modify: `bot_config.json`

The default in `app/bot_config.py` is correctly `Decimal("2.0")`, but `bot_config.json` overrides it with `"20.0"` — 20 price points on MGC = $200, making the filter nearly useless.

- [ ] **Step 1: Fix the JSON**

In `bot_config.json`, change:
```json
"vp_filter_tolerance": "20.0"
```
to:
```json
"vp_filter_tolerance": "2.0"
```

- [ ] **Step 2: Run existing VP tests to verify no regression**

```bash
.venv/Scripts/python.exe -m pytest tests/test_volume_profile.py -v
```

Expected: all tests pass.

- [ ] **Step 3: Commit**

```bash
git add bot_config.json
git commit -m "fix: VP tolerance was 20.0 (useless) — corrected to 2.0 price points"
```

---

## Task 2: Post-Stop Cooldown

**Files:**
- Modify: `app/sim/events.py`
- Modify: `app/sim/paper.py`
- Modify: `app/strategy/composer.py`
- Modify: `app/execution/engine.py`
- Create: `tests/test_phase2_cleanups.py`

Without this, the bot can re-enter on the next bar after a stop — possibly entering the same pattern that just stopped it out in a choppy market.

- [ ] **Step 1: Write failing tests**

Create `tests/test_phase2_cleanups.py`:

```python
"""Tests for Phase 2 smaller cleanups: cooldown and ATR-scaled penetration."""
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from app.sim.events import Bar, Fill
from app.strategy.composer import ComposerConfig, SweepDisplacementComposer


def _bar(i: int = 0) -> Bar:
    ts = datetime(2026, 1, 2, 10, i, tzinfo=timezone.utc)
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts,
        open=Decimal("100"), high=Decimal("101"),
        low=Decimal("99"), close=Decimal("100"),
        volume=100,
    )


# ── Cooldown tests ──────────────────────────────────────────────────────────

def test_cooldown_off_by_default():
    """Default cooldown is 0 (disabled); _cooldown_remaining stays 0 after stop."""
    cfg = ComposerConfig(instrument="MGC")
    composer = SweepDisplacementComposer(cfg)
    composer.on_stop_loss()
    assert composer._cooldown_remaining == 0


def test_cooldown_set_on_stop_loss():
    """on_stop_loss() sets _cooldown_remaining to cooldown_bars_after_stop."""
    cfg = ComposerConfig(instrument="MGC", cooldown_bars_after_stop=3)
    composer = SweepDisplacementComposer(cfg)
    composer.on_stop_loss()
    assert composer._cooldown_remaining == 3


def test_cooldown_decrements_on_bar_close():
    """on_bar_close() decrements _cooldown_remaining toward 0."""
    cfg = ComposerConfig(instrument="MGC", cooldown_bars_after_stop=3)
    composer = SweepDisplacementComposer(cfg)
    composer.on_stop_loss()
    composer.on_bar_close(_bar(0))
    assert composer._cooldown_remaining == 2
    composer.on_bar_close(_bar(1))
    assert composer._cooldown_remaining == 1
    composer.on_bar_close(_bar(2))
    assert composer._cooldown_remaining == 0


def test_cooldown_does_not_go_below_zero():
    """Calling on_bar_close after cooldown expires doesn't go negative."""
    cfg = ComposerConfig(instrument="MGC", cooldown_bars_after_stop=1)
    composer = SweepDisplacementComposer(cfg)
    composer.on_stop_loss()
    composer.on_bar_close(_bar(0))
    composer.on_bar_close(_bar(1))
    assert composer._cooldown_remaining == 0


def test_fill_has_is_stop_field():
    """Fill dataclass has is_stop field defaulting to False."""
    f = Fill(
        ts=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        instrument="MGC",
        side="long",
        fill_price=Decimal("100"),
        size=1,
        is_entry=True,
        realized_pnl_delta=Decimal("0"),
        contracts_delta=1,
        broker_order_id="test-1",
    )
    assert f.is_stop is False


def test_fill_is_stop_can_be_set():
    """Fill.is_stop can be True for stop exits."""
    f = Fill(
        ts=datetime(2026, 1, 2, 10, 0, tzinfo=timezone.utc),
        instrument="MGC",
        side="short",
        fill_price=Decimal("98"),
        size=1,
        is_entry=False,
        realized_pnl_delta=Decimal("-20"),
        contracts_delta=-1,
        broker_order_id="test-2",
        is_stop=True,
    )
    assert f.is_stop is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_phase2_cleanups.py -v
```

Expected: failures on `cooldown_bars_after_stop` (not in ComposerConfig), `on_stop_loss()` (method missing), `_cooldown_remaining` (attribute missing), and `is_stop` (not in Fill).

- [ ] **Step 3: Add `is_stop` to Fill**

In `app/sim/events.py`, modify the `Fill` dataclass (add `is_stop` as the last field with a default so all existing call sites remain valid):

```python
@dataclass(frozen=True)
class Fill:
    ts: datetime
    instrument: str
    side: Side
    fill_price: Decimal
    size: int
    is_entry: bool
    realized_pnl_delta: Decimal
    contracts_delta: int
    broker_order_id: str
    is_stop: bool = False  # True when this exit was a stop-loss hit
```

- [ ] **Step 4: Pass `is_stop` to Fill in PaperBroker**

In `app/sim/paper.py`, find `_close_bracket`. The `Fill(...)` constructor call is inside it. Add `is_stop=is_stop` to that call:

```python
await self._fanout(
    self._fill_handlers,
    Fill(
        ts=ts,
        instrument=bracket.instrument,
        side="short" if bracket.side == "long" else "long",
        fill_price=exit_price,
        size=bracket.size,
        is_entry=False,
        realized_pnl_delta=pnl,
        contracts_delta=-bracket.size if bracket.side == "long" else bracket.size,
        broker_order_id=f"{bracket.order_id}-X",
        is_stop=is_stop,
    ),
)
```

- [ ] **Step 5: Add cooldown to ComposerConfig and SweepDisplacementComposer**

In `app/strategy/composer.py`, add to `ComposerConfig`:

```python
@dataclass
class ComposerConfig:
    instrument: str
    displacement_window_bars: int = 5
    stop_buffer: Decimal = Decimal("0.30")
    r_multiple: Decimal = Decimal("2.0")
    killzones: list[Killzone] | None = None
    trend_ema_period: int = 50
    cooldown_bars_after_stop: int = 0  # 0 = disabled; N = block signals for N bars after a stop
```

In `SweepDisplacementComposer.__init__`, add the counter:

```python
def __init__(self, config: ComposerConfig) -> None:
    self.config = config
    self._zones = config.killzones or default_killzones()
    self._awaiting: list[_Awaiting] = []
    self._ema: Decimal | None = None
    self._ema_bars: int = 0
    self._cooldown_remaining: int = 0  # bars until signals are allowed again
```

Add the new method (place it after `awaiting` property, before `on_sweep`):

```python
def on_stop_loss(self) -> None:
    """Notify the composer that the last exit was a stop-loss. Starts cooldown if configured."""
    bars = self.config.cooldown_bars_after_stop
    if bars > 0:
        self._cooldown_remaining = bars
        log.info("Post-stop cooldown started: blocking signals for %d bars", bars)
```

In `on_displacement`, add an early return check (immediately after `if event.fvg is None: return None`):

```python
def on_displacement(self, bar: Bar, event: DisplacementEvent) -> Signal | None:
    if event.fvg is None:
        return None

    if self._cooldown_remaining > 0:
        log.info(
            "Signal blocked: post-stop cooldown (%d bars remaining)",
            self._cooldown_remaining,
        )
        return None

    # ... rest unchanged
```

In `on_bar_close`, add cooldown decrement (at the beginning of the method, before the window bookkeeping):

```python
def on_bar_close(self, bar: Bar) -> None:
    if self._cooldown_remaining > 0:
        self._cooldown_remaining -= 1

    window = self.config.displacement_window_bars
    # ... rest unchanged
```

- [ ] **Step 6: Wire cooldown in engine `_handle_fill`**

In `app/execution/engine.py`, in `_handle_fill`, add the cooldown notification after the risk state update:

```python
async def _handle_fill(self, fill: Fill) -> None:
    self.risk_state.record_fill(
        realized_pnl_delta=fill.realized_pnl_delta,
        contracts_delta=fill.contracts_delta,
        ts=fill.ts,
    )
    log.info(
        "Fill: %s %s %d @ %s pnl=%s contracts_now=%d",
        fill.instrument,
        "ENTRY" if fill.is_entry else "EXIT",
        fill.size,
        fill.fill_price,
        fill.realized_pnl_delta,
        self.risk_state.open_contracts,
    )
    # Notify composer of stop exits so it can start cooldown.
    if not fill.is_entry and fill.is_stop:
        runner = self.runners.get(fill.instrument)
        if runner is not None:
            runner.composer.on_stop_loss()

    # If a reversal was pending and this fill just brought us flat, execute it.
    if self.risk_state.open_contracts == 0:
        pending = self._pending_reversal.pop(fill.instrument, None)
        if pending is not None:
            log.info(
                "Flat after reversal flatten — entering: %s", pending.rationale,
            )
            asyncio.create_task(self._execute_reversal(pending))
```

- [ ] **Step 7: Run tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_phase2_cleanups.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Run full suite to catch regressions**

```bash
.venv/Scripts/python.exe -m pytest tests/ -v -x
```

Expected: all pass. If any test constructs a `Fill` without `is_stop`, it still works because of the default value.

- [ ] **Step 9: Commit**

```bash
git add app/sim/events.py app/sim/paper.py app/strategy/composer.py app/execution/engine.py tests/test_phase2_cleanups.py
git commit -m "feat: post-stop cooldown — Fill.is_stop field, composer cooldown counter, engine wiring"
```

---

## Task 3: min_penetration ATR Scaling

**Files:**
- Modify: `app/strategy/liquidity.py`
- Modify: `app/execution/engine.py`
- Modify: `tests/test_phase2_cleanups.py` (add tests)

In low-volatility environments, a fixed 0.20-point penetration (2 ticks on MGC) is meaningful. In high-volatility sessions, price routinely moves 5–10 points per bar, and 0.20 is nearly invisible. Scaling by ATR adapts to the regime automatically.

- [ ] **Step 1: Add tests for ATR-scaled penetration**

Append to `tests/test_phase2_cleanups.py`:

```python
# ── ATR penetration scaling tests ──────────────────────────────────────────

from app.strategy.liquidity import LiquidityConfig, LiquidityTracker


def test_effective_pen_fixed_when_no_factor():
    """Without min_penetration_atr_factor, fixed min_penetration is used."""
    cfg = LiquidityConfig(min_penetration=Decimal("0.20"))
    tracker = LiquidityTracker(cfg)
    assert tracker._effective_pen(atr=Decimal("2.0")) == Decimal("0.20")
    assert tracker._effective_pen(atr=None) == Decimal("0.20")


def test_effective_pen_scales_with_atr():
    """With factor=0.25, effective pen = 0.25 × ATR."""
    cfg = LiquidityConfig(min_penetration=Decimal("0.20"), min_penetration_atr_factor=Decimal("0.25"))
    tracker = LiquidityTracker(cfg)
    assert tracker._effective_pen(atr=Decimal("2.0")) == Decimal("0.50")
    assert tracker._effective_pen(atr=Decimal("0.4")) == Decimal("0.10")


def test_effective_pen_falls_back_to_fixed_when_atr_none():
    """If factor is set but ATR is None (not yet warmed up), use fixed min_penetration."""
    cfg = LiquidityConfig(min_penetration=Decimal("0.20"), min_penetration_atr_factor=Decimal("0.25"))
    tracker = LiquidityTracker(cfg)
    assert tracker._effective_pen(atr=None) == Decimal("0.20")
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv/Scripts/python.exe -m pytest tests/test_phase2_cleanups.py::test_effective_pen_fixed_when_no_factor tests/test_phase2_cleanups.py::test_effective_pen_scales_with_atr tests/test_phase2_cleanups.py::test_effective_pen_falls_back_to_fixed_when_atr_none -v
```

Expected: `AttributeError: LiquidityConfig has no attribute 'min_penetration_atr_factor'` and `AttributeError: LiquidityTracker has no method '_effective_pen'`.

- [ ] **Step 3: Add `min_penetration_atr_factor` to `LiquidityConfig`**

In `app/strategy/liquidity.py`, modify `LiquidityConfig`:

```python
@dataclass
class LiquidityConfig:
    swing_lookback: int = 3
    min_penetration: Decimal = Decimal("0.20")
    min_penetration_atr_factor: Decimal | None = None  # if set, effective pen = factor × ATR
    multi_bar_window: int = 3
    max_swings: int = 50
```

- [ ] **Step 4: Add `_effective_pen` helper and update `on_bar` signature**

In `app/strategy/liquidity.py`, add the helper method to `LiquidityTracker` (place after the `recent_low_swings` property, before `on_bar`):

```python
def _effective_pen(self, atr: Decimal | None) -> Decimal:
    """Compute the effective min_penetration for this bar's sweep detection."""
    if self.config.min_penetration_atr_factor is not None and atr is not None:
        return atr * self.config.min_penetration_atr_factor
    return self.config.min_penetration
```

Change `on_bar` signature and compute `self._pen` at the start:

```python
def on_bar(self, bar: Bar, atr: Decimal | None = None) -> list[SweepEvent]:
    """
    Process a closed bar. Returns any sweep events that completed
    on this bar.

    `atr` is the current ATR from the displacement detector (previous
    bar's value, since displacement runs after liquidity in the pipeline).
    Used only when `min_penetration_atr_factor` is set in config.

    Order of operations matters:
      1. Buffer the bar.
      2. Try to confirm a new swing in the middle of the buffer.
      3. Check for Pattern B (one-bar) sweeps against existing swings.
      4. Update Pattern A pending sweeps; emit any that closed back;
         expire any that ran past the window.
      5. Check for new Pattern A tags on existing swings.
    """
    self._pen = self._effective_pen(atr)
    self._bar_idx += 1
    self._bars.append(bar)
    events: list[SweepEvent] = []

    new_swing = self._maybe_confirm_swing()
    if new_swing is not None:
        self._swings.append(new_swing)

    events.extend(self._detect_pattern_b(bar))
    events.extend(self._resolve_pending(bar))
    self._register_new_tags(bar)

    return events
```

Also add `self._pen: Decimal = self.config.min_penetration` to `__init__` (so it's defined before the first bar arrives):

In `LiquidityTracker.__init__`, after `self._bar_idx = 0`, add:
```python
self._pen: Decimal = self.config.min_penetration
```

- [ ] **Step 5: Replace `self.config.min_penetration` with `self._pen` in detection methods**

In `_detect_pattern_b`, replace:
```python
pen = self.config.min_penetration
```
with:
```python
pen = self._pen
```

In `_register_new_tags`, replace:
```python
pen = self.config.min_penetration
```
with:
```python
pen = self._pen
```

- [ ] **Step 6: Cache previous-bar ATR in `StrategyRunner` and pass to liquidity**

In `app/execution/engine.py`, add `_prev_atr` to `StrategyRunner`:

```python
@dataclass
class StrategyRunner:
    instrument: str
    timeframe: str
    liquidity: LiquidityTracker
    displacement: DisplacementDetector
    composer: SweepDisplacementComposer
    vp: VolumeProfileTracker | None = None
    _prev_atr: Decimal | None = field(default=None, init=False, repr=False)
```

Modify `on_bar` to pass the cached ATR and update it after displacement:

```python
def on_bar(self, bar: Bar) -> Optional[Signal]:
    """Run all detectors against one bar. Returns at most one Signal."""
    sweeps = self.liquidity.on_bar(bar, atr=self._prev_atr)
    for s in sweeps:
        self.composer.on_sweep(bar, s)

    signal: Optional[Signal] = None
    disp = self.displacement.on_bar(bar)
    if disp is not None:
        signal = self.composer.on_displacement(bar, disp)

    # Cache ATR for the NEXT bar's liquidity call (one-bar lag is acceptable;
    # ATR doesn't change sharply bar-to-bar and liquidity runs before displacement).
    self._prev_atr = self.displacement.atr

    # Bookkeeping AFTER signal evaluation — see composer docstring.
    self.composer.on_bar_close(bar)
    return signal
```

Add the `field` import if not already present — check: `from dataclasses import dataclass, field` is already in engine.py at line 39.

- [ ] **Step 7: Run all new tests**

```bash
.venv/Scripts/python.exe -m pytest tests/test_phase2_cleanups.py -v
```

Expected: all tests PASS.

- [ ] **Step 8: Run full test suite**

```bash
.venv/Scripts/python.exe -m pytest tests/ -v -x
```

Expected: all pass. The `on_bar(bar)` callers in the engine still work because `atr` has a default of `None`.

- [ ] **Step 9: Commit**

```bash
git add app/strategy/liquidity.py app/execution/engine.py tests/test_phase2_cleanups.py
git commit -m "feat: ATR-scaled min_penetration — LiquidityConfig factor, StrategyRunner ATR cache"
```

---

## Self-Review

**Spec coverage:**
- [x] VP tolerance 20.0 → 2.0 → Task 1
- [x] `Fill.is_stop` field → Task 2, Step 3
- [x] PaperBroker passes `is_stop` → Task 2, Step 4
- [x] `ComposerConfig.cooldown_bars_after_stop` → Task 2, Step 5
- [x] `on_stop_loss()` + `_cooldown_remaining` → Task 2, Step 5
- [x] `on_displacement` checks cooldown → Task 2, Step 5
- [x] `on_bar_close` decrements cooldown → Task 2, Step 5
- [x] Engine wires stop fills → composer → Task 2, Step 6
- [x] `LiquidityConfig.min_penetration_atr_factor` → Task 3, Step 3
- [x] `_effective_pen()` helper → Task 3, Step 4
- [x] `on_bar(bar, atr=None)` signature → Task 3, Step 4
- [x] `_detect_pattern_b` and `_register_new_tags` use `self._pen` → Task 3, Step 5
- [x] `StrategyRunner._prev_atr` cache → Task 3, Step 6
- [x] `liquidity.on_bar(bar, atr=self._prev_atr)` → Task 3, Step 6

**Placeholder scan:** None. All code blocks are complete.

**Type consistency:**
- `Fill.is_stop: bool = False` — default False, existing call sites unaffected
- `_effective_pen(atr: Decimal | None) -> Decimal` — returns Decimal in all branches
- `StrategyRunner._prev_atr: Decimal | None` — matches `DisplacementDetector.atr` return type
- `LiquidityTracker.on_bar(bar: Bar, atr: Decimal | None = None)` — backward-compatible; all existing callers pass no `atr` and get fixed penetration as before
