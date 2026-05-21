# Phase 2: Volatility Regime Filter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add optional ATR-based min/max volatility gates so the bot only enters when the market is in an acceptable volatility regime — not too quiet (insufficient movement to justify risk) and not too chaotic (whipsaw/slippage risk).

**Architecture:** `DisplacementEvent.atr_at_event` already carries the ATR at signal time. Add `min_atr_filter` and `max_atr_filter` to `ComposerConfig` (0 = disabled). Check in `on_displacement()` after the existing cooldown check. Wire through `StrategyParams → main.py → ComposerConfig`. Add sliders to the frontend ConfigPanel and `StrategyConfig` type. Requires strategy reload (not hot-applied) — consistent with all other `ComposerConfig` params.

**Tech Stack:** Python dataclasses, Pydantic, React/TypeScript, Tailwind.

**Convention for disabled:** `Decimal("0")` means disabled (same pattern as `trend_ema_period: int = 0` meaning off). `min_atr_filter=0` → no floor; `max_atr_filter=0` → no ceiling.

---

## File Map

| Action | Path | Responsibility |
|---|---|---|
| Modify | `app/strategy/composer.py` | Add `min_atr_filter`, `max_atr_filter` to `ComposerConfig`; check in `on_displacement()` |
| Modify | `app/bot_config.py` | Add `min_atr_filter`, `max_atr_filter` to `StrategyParams` |
| Modify | `app/main.py` | Wire new fields into `ComposerConfig(...)` in `_build_runner()` |
| Modify | `frontend/src/types.ts` | Add to `StrategyConfig` interface |
| Modify | `frontend/src/components/ConfigPanel.tsx` | Add two slider rows to `STRATEGY_FIELDS` |
| Create | `tests/test_vol_regime_filter.py` | Unit tests for ATR gate in composer |

---

## Task 1: Composer ATR Gate

**Files:**
- Modify: `app/strategy/composer.py`
- Create: `tests/test_vol_regime_filter.py`

- [ ] **Step 1: Write failing tests**

Create `tests/test_vol_regime_filter.py`:

```python
"""Tests for volatility regime filter in SweepDisplacementComposer."""
from datetime import datetime, timezone, timedelta
from decimal import Decimal
import pytest

from app.strategy.composer import ComposerConfig, SweepDisplacementComposer
from app.strategy.displacement import DisplacementEvent, FairValueGap
from app.broker.events import Bar


def _bar(i: int = 0) -> Bar:
    ts = datetime(2026, 1, 2, 10, i, tzinfo=timezone.utc)
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts,
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("98"), close=Decimal("101"),
        volume=200,
    )


def _disp(atr: str, side: str = "bullish") -> DisplacementEvent:
    """Construct a minimal DisplacementEvent with a valid FVG."""
    bar = _bar()
    fvg = FairValueGap(
        side=side,
        low=Decimal("99.0"),
        high=Decimal("100.5"),
        created_at=bar.ts,
    )
    return DisplacementEvent(
        side=side,
        displacement_bar=bar,
        body_size=Decimal("1.5"),
        atr_at_event=Decimal(atr),
        body_to_atr=Decimal("1.5") / Decimal(atr),
        fvg=fvg,
    )


def _composer_with_sweep(cfg: ComposerConfig) -> SweepDisplacementComposer:
    """Return a composer that already has a pending sweep registered."""
    from app.strategy.liquidity import Swing, SweepEvent
    composer = SweepDisplacementComposer(cfg)
    sweep = SweepEvent(
        side="low",
        swept_swing=Swing(
            kind="low",
            price=Decimal("99.0"),
            bar_ts=datetime(2026, 1, 2, 9, 55, tzinfo=timezone.utc),
            confirmed_ts=datetime(2026, 1, 2, 9, 58, tzinfo=timezone.utc),
        ),
        pattern="B_one_bar",
        sweep_extreme=Decimal("98.5"),
        completed_at=datetime(2026, 1, 2, 9, 59, tzinfo=timezone.utc),
    )
    bar = _bar(0)
    # Simulate the bar being inside a killzone by calling on_sweep directly
    # with a bar whose timestamp is in NY AM (12:00 UTC = 08:00 ET).
    ny_am_bar = Bar(
        instrument="MGC", timeframe="1min",
        ts=datetime(2026, 1, 2, 13, 0, tzinfo=timezone.utc),  # 08:00 ET — in NY AM killzone
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("98"), close=Decimal("101"),
        volume=200,
    )
    composer.on_sweep(ny_am_bar, sweep)
    return composer


def test_no_filter_emits_signal():
    """With no filter (0 = disabled), any ATR passes."""
    cfg = ComposerConfig(instrument="MGC", min_atr_filter=Decimal("0"), max_atr_filter=Decimal("0"))
    composer = _composer_with_sweep(cfg)
    ny_am_bar = Bar(
        instrument="MGC", timeframe="1min",
        ts=datetime(2026, 1, 2, 13, 1, tzinfo=timezone.utc),
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("98"), close=Decimal("101"),
        volume=200,
    )
    signal = composer.on_displacement(ny_am_bar, _disp("1.5"))
    assert signal is not None, "Expected signal with no filter"


def test_min_atr_blocks_low_vol():
    """min_atr_filter > 0 blocks signals when ATR is below the floor."""
    cfg = ComposerConfig(instrument="MGC", min_atr_filter=Decimal("2.0"), max_atr_filter=Decimal("0"))
    composer = _composer_with_sweep(cfg)
    ny_am_bar = Bar(
        instrument="MGC", timeframe="1min",
        ts=datetime(2026, 1, 2, 13, 1, tzinfo=timezone.utc),
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("98"), close=Decimal("101"),
        volume=200,
    )
    signal = composer.on_displacement(ny_am_bar, _disp("1.5"))  # ATR 1.5 < floor 2.0
    assert signal is None, "Expected no signal: ATR below min_atr_filter"


def test_min_atr_allows_at_or_above_floor():
    """min_atr_filter passes signals when ATR equals or exceeds the floor."""
    cfg = ComposerConfig(instrument="MGC", min_atr_filter=Decimal("1.5"), max_atr_filter=Decimal("0"))
    composer = _composer_with_sweep(cfg)
    ny_am_bar = Bar(
        instrument="MGC", timeframe="1min",
        ts=datetime(2026, 1, 2, 13, 1, tzinfo=timezone.utc),
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("98"), close=Decimal("101"),
        volume=200,
    )
    signal = composer.on_displacement(ny_am_bar, _disp("1.5"))  # ATR 1.5 == floor 1.5
    assert signal is not None, "Expected signal: ATR at exactly min_atr_filter"


def test_max_atr_blocks_high_vol():
    """max_atr_filter > 0 blocks signals when ATR exceeds the ceiling."""
    cfg = ComposerConfig(instrument="MGC", min_atr_filter=Decimal("0"), max_atr_filter=Decimal("2.0"))
    composer = _composer_with_sweep(cfg)
    ny_am_bar = Bar(
        instrument="MGC", timeframe="1min",
        ts=datetime(2026, 1, 2, 13, 1, tzinfo=timezone.utc),
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("98"), close=Decimal("101"),
        volume=200,
    )
    signal = composer.on_displacement(ny_am_bar, _disp("3.0"))  # ATR 3.0 > ceiling 2.0
    assert signal is None, "Expected no signal: ATR above max_atr_filter"


def test_max_atr_allows_at_or_below_ceiling():
    """max_atr_filter passes signals when ATR equals or is below the ceiling."""
    cfg = ComposerConfig(instrument="MGC", min_atr_filter=Decimal("0"), max_atr_filter=Decimal("3.0"))
    composer = _composer_with_sweep(cfg)
    ny_am_bar = Bar(
        instrument="MGC", timeframe="1min",
        ts=datetime(2026, 1, 2, 13, 1, tzinfo=timezone.utc),
        open=Decimal("100"), high=Decimal("102"),
        low=Decimal("98"), close=Decimal("101"),
        volume=200,
    )
    signal = composer.on_displacement(ny_am_bar, _disp("3.0"))  # ATR 3.0 == ceiling 3.0
    assert signal is not None, "Expected signal: ATR at exactly max_atr_filter"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
.venv\Scripts\python.exe -m pytest tests/test_vol_regime_filter.py -v
```

Expected: `TypeError` or `AttributeError` because `min_atr_filter` doesn't exist on `ComposerConfig`.

- [ ] **Step 3: Add `min_atr_filter` and `max_atr_filter` to `ComposerConfig`**

In `app/strategy/composer.py`, modify `ComposerConfig` (add after `cooldown_bars_after_stop`):

```python
@dataclass
class ComposerConfig:
    instrument: str
    displacement_window_bars: int = 5
    stop_buffer: Decimal = Decimal("0.30")
    r_multiple: Decimal = Decimal("2.0")
    killzones: list[Killzone] | None = None
    trend_ema_period: int = 50
    cooldown_bars_after_stop: int = 0
    # Volatility regime gates. 0 = disabled. min_atr_filter > 0 skips
    # dead-market sessions; max_atr_filter > 0 skips high-whipsaw regimes.
    min_atr_filter: Decimal = Decimal("0")
    max_atr_filter: Decimal = Decimal("0")
```

- [ ] **Step 4: Add ATR gate check in `on_displacement()`**

In `app/strategy/composer.py`, in `on_displacement()`, add the ATR gate after the cooldown check (after the `if self._cooldown_remaining > 0: return None` block, before the `wanted` dict):

```python
# Volatility regime filter: block signals outside configured ATR range.
if self.config.min_atr_filter > 0 and event.atr_at_event < self.config.min_atr_filter:
    log.info(
        "Signal blocked: ATR %s below min_atr_filter %s (low-vol regime)",
        event.atr_at_event, self.config.min_atr_filter,
    )
    return None
if self.config.max_atr_filter > 0 and event.atr_at_event > self.config.max_atr_filter:
    log.info(
        "Signal blocked: ATR %s above max_atr_filter %s (high-vol regime)",
        event.atr_at_event, self.config.max_atr_filter,
    )
    return None
```

- [ ] **Step 5: Run tests**

```bash
.venv\Scripts\python.exe -m pytest tests/test_vol_regime_filter.py -v
```

Expected: all 5 tests PASS.

- [ ] **Step 6: Run full suite to catch regressions**

```bash
.venv\Scripts\python.exe -m pytest tests/ -v -x
```

Expected: no new failures. (Existing 5 pre-existing failures are unrelated.)

- [ ] **Step 7: Commit**

```bash
git add app/strategy/composer.py tests/test_vol_regime_filter.py
git commit -m "feat: ATR volatility regime filter in composer (min_atr_filter, max_atr_filter)"
```

---

## Task 2: Wire Through Config Stack

**Files:**
- Modify: `app/bot_config.py`
- Modify: `app/main.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/ConfigPanel.tsx`

No tests needed for this task — the config round-trip is tested by the existing `tests/test_volume_profile.py` pattern.

- [ ] **Step 1: Add to `StrategyParams` in `app/bot_config.py`**

In `app/bot_config.py`, add two fields to `StrategyParams` (after `trend_ema_period`):

```python
class StrategyParams(BaseModel):
    swing_lookback: int = 2
    min_penetration: Decimal = Decimal("0.20")
    multi_bar_window: int = 3
    atr_period: int = 14
    body_atr_multiple: Decimal = Decimal("1.0")
    min_body_to_range_ratio: Decimal = Decimal("0.6")
    min_absolute_body: Decimal = Decimal("1.0")
    displacement_window_bars: int = 5
    stop_buffer: Decimal = Decimal("0.30")
    r_multiple: Decimal = Decimal("2.5")
    trend_ema_period: int = 50  # 0 = disabled; N = only take signals with the N-bar EMA trend
    min_atr_filter: Decimal = Decimal("0")  # 0 = no floor; N = require ATR >= N before entering
    max_atr_filter: Decimal = Decimal("0")  # 0 = no ceiling; N = require ATR <= N before entering

    # Volume profile filter + target
    vp_enabled: bool = True
    vp_tick_size: Decimal = Decimal("0.10")
    vp_value_area_pct: float = 0.70
    vp_filter_tolerance: Decimal = Decimal("2.0")
    vp_hvn_threshold: float = 1.5
    vp_min_target_r: Decimal = Decimal("1.0")
```

- [ ] **Step 2: Wire into `_build_runner()` in `app/main.py`**

In `app/main.py`, find the `ComposerConfig(...)` construction in `_build_runner()` (around line 116). Add the two new fields:

```python
composer=SweepDisplacementComposer(ComposerConfig(
    instrument=instrument,
    displacement_window_bars=s.displacement_window_bars,
    stop_buffer=s.stop_buffer,
    r_multiple=s.r_multiple,
    killzones=zones,
    trend_ema_period=s.trend_ema_period,
    min_atr_filter=s.min_atr_filter,
    max_atr_filter=s.max_atr_filter,
)),
```

- [ ] **Step 3: Verify `save_bot_config` already handles new fields**

`save_bot_config` uses `config.strategy.model_dump()` which includes all fields automatically. No change needed.

Run to confirm:
```bash
.venv\Scripts\python.exe -c "
from app.bot_config import BotConfig, save_bot_config
from pathlib import Path
import json, tempfile, os
cfg = BotConfig()
p = Path(tempfile.mktemp(suffix='.json'))
save_bot_config(cfg, p)
d = json.loads(p.read_text())
assert 'min_atr_filter' in d['strategy'], 'missing min_atr_filter'
assert 'max_atr_filter' in d['strategy'], 'missing max_atr_filter'
print('OK — both fields present in saved config')
p.unlink()
"
```

Expected: `OK — both fields present in saved config`

- [ ] **Step 4: Add to `StrategyConfig` in `frontend/src/types.ts`**

In `frontend/src/types.ts`, add two lines to `StrategyConfig` (after `trend_ema_period`):

```typescript
export interface StrategyConfig {
  swing_lookback: number
  min_penetration: string
  multi_bar_window: number
  atr_period: number
  body_atr_multiple: string
  min_body_to_range_ratio: string
  min_absolute_body: string
  displacement_window_bars: number
  stop_buffer: string
  r_multiple: string
  trend_ema_period: number
  min_atr_filter: string   // "0" = disabled
  max_atr_filter: string   // "0" = disabled
  // Volume profile
  vp_enabled: boolean
  vp_tick_size: string
  vp_value_area_pct: number
  vp_filter_tolerance: string
  vp_hvn_threshold: number
  vp_min_target_r: string
}
```

- [ ] **Step 5: Add sliders to ConfigPanel**

In `frontend/src/components/ConfigPanel.tsx`, find `STRATEGY_FIELDS` (the array of config field descriptors). Add two entries after the `trend_ema_period` entry:

```typescript
  {
    key: 'min_atr_filter', label: 'Min ATR Filter', type: 'slider', section: 'strategy',
    min: 0, max: 3.0, step: 0.1,
    hint: 'Minimum ATR to enter a trade. 0 = disabled. Raise to skip low-volatility dead markets (try 0.5–1.0 on MGC).',
  },
  {
    key: 'max_atr_filter', label: 'Max ATR Filter', type: 'slider', section: 'strategy',
    min: 0, max: 6.0, step: 0.1,
    hint: 'Maximum ATR to enter a trade. 0 = disabled. Lower to skip high-volatility whipsaw sessions (try 3.0–4.0 on MGC).',
  },
```

Make sure the `onSave` handler in ConfigPanel includes these fields — it currently spreads all strategy keys from the form, so this is automatic.

- [ ] **Step 6: Run existing tests**

```bash
.venv\Scripts\python.exe -m pytest tests/ -v -x
```

Expected: no new failures.

- [ ] **Step 7: Commit**

```bash
git add app/bot_config.py app/main.py frontend/src/types.ts frontend/src/components/ConfigPanel.tsx
git commit -m "feat: wire ATR regime filter through StrategyParams, main.py, and frontend ConfigPanel"
```

---

## Self-Review

**Spec coverage:**
- [x] `ComposerConfig.min_atr_filter` and `max_atr_filter` (0 = disabled) → Task 1, Step 3
- [x] ATR gate in `on_displacement()` → Task 1, Step 4
- [x] `StrategyParams` fields → Task 2, Step 1
- [x] `_build_runner()` wiring → Task 2, Step 2
- [x] `frontend/src/types.ts` → Task 2, Step 4
- [x] ConfigPanel sliders → Task 2, Step 5
- [x] `save_bot_config` handles new fields automatically → Task 2, Step 3

**Placeholder scan:** None.

**Type consistency:**
- `min_atr_filter: Decimal = Decimal("0")` in both `ComposerConfig` and `StrategyParams`
- Frontend sends string `"0"` or `"1.5"`; Pydantic parses to `Decimal` — same as all other Decimal fields
- ATR check uses `Decimal` comparison, not float — no precision issues
- Existing callers of `ComposerConfig(...)` that don't pass `min_atr_filter`/`max_atr_filter` get 0 (disabled) — no breakage
