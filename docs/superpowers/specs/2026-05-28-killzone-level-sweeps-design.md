# Killzone Level Sweeps — Design Spec

**Date:** 2026-05-28
**Status:** Approved

---

## Problem

The existing strategy detects sweeps of generic swing highs/lows. Killzone session highs and lows (e.g. London's high and low, NY AM's high and low) are well-known pivot levels that often act as strong support/resistance. Price sweeping these levels and then reversing — or breaking through cleanly — with displacement confirmation is a high-quality setup. Adding this as a parallel signal source, tagged separately, lets performance be analyzed independently.

---

## What This Is

A second, parallel sweep-displacement signal source anchored to completed killzone H/L levels rather than generic swing highs/lows. The same sweep (pattern A/B) → displacement + FVG → signal state machine applies. Both sources run simultaneously; each can independently fire a signal. Signals carry a `source` tag so performance can be split in analytics.

---

## Scope

| File | Change |
|------|--------|
| `app/strategy/kz_levels.py` | **New** — `KillzoneLevelTracker` |
| `app/strategy/composer.py` | Add `source` to `_Awaiting`, `on_sweep()`, `Signal` |
| `app/execution/engine.py` | Wire `KillzoneLevelTracker` into `StrategyRunner` bar loop |
| `app/bot_config.py` | Add `kz_levels_enabled: bool = True` to `StrategyParams` |
| `app/api/server.py` | Hot-apply `kz_levels_enabled` in `PATCH /api/config` |
| `frontend/src/components/ConfigPanel.tsx` | Toggle for `kz_levels_enabled` |
| `tests/test_kz_levels.py` | **New** — unit tests for `KillzoneLevelTracker` |
| `tests/test_strategy.py` | Tests for `Signal.source` tagging through composer |

No changes to: `LiquidityTracker`, `DisplacementDetector`, broker, pretrade gates, risk, execution engine order placement.

---

## Architecture

```
Bar stream
  │
  ├─ LiquidityTracker.on_bar()  →  SweepEvent(s)  →  composer.on_sweep(..., source="swing")
  │
  └─ KillzoneLevelTracker.on_bar()  →  SweepEvent(s)  →  composer.on_sweep(..., source="kz_level")
                                                                    │
                                                       SweepDisplacementComposer
                                                       (unchanged state machine)
                                                                    │
                                                            Signal(source="swing"|"kz_level")
                                                                    │
                                                          Execution engine (unchanged)
```

---

## `KillzoneLevelTracker` (`app/strategy/kz_levels.py`)

### State

| Attribute | Type | Purpose |
|-----------|------|---------|
| `_kz_ranges` | `dict[str, tuple[Decimal, Decimal]]` | Finalized (high, low) per killzone name for today |
| `_active_accum` | `dict[str, tuple[Decimal, Decimal]]` | H/L being accumulated for currently-open killzones |
| `_session_date` | `date \| None` | UTC date of last bar; resets all state on change |
| `_pending_a` | `dict[str, Decimal]` | Pattern A: price that tagged a level, keyed by level key `"<name>_high"` / `"<name>_low"` |
| `_bars_since_tag` | `dict[str, int]` | Bars elapsed since pattern A tag (for window timeout) |

### `on_bar(bar: Bar, zones: list[Killzone], cfg: StrategyParams) → list[SweepEvent]`

1. **Daily reset**: if `bar.ts.date()` (UTC) differs from `_session_date`, clear all state and update `_session_date`.
2. **Accumulate active killzones**: for each `zone` in `zones`, if the bar's ET time is inside `[zone.start, zone.end)`, update `_active_accum[zone.name]` with `bar.high` / `bar.low`.
3. **Finalize completed killzones**: for any zone in `_active_accum` that is no longer active (bar's ET time is past `zone.end`), move it from `_active_accum` to `_kz_ranges`. Only finalize once per zone per day.
4. **Sweep detection** against all entries in `_kz_ranges` (see below).
5. Return accumulated `SweepEvent`s.

### Sweep detection

Uses the same `min_penetration` and `multi_bar_window` from `StrategyParams`. Mirrors `LiquidityTracker` patterns:

**Pattern B (one-bar):** `bar.high >= kz_high + min_penetration AND bar.close < kz_high` → emit `SweepEvent(side="high", ...)`. Mirror for lows.

**Pattern A (multi-bar):** bar tags level (high >= kz_high + min_penetration) but close does not confirm → record in `_pending_a`. On subsequent bars within `multi_bar_window`, if close < kz_high → emit sweep. If window expires, discard pending.

Once a level is swept (either pattern), **remove it from `_kz_ranges`** — each level fires at most once per day.

### `SweepEvent` construction

KZ H/L levels don't have a `Swing` struct (they come from session ranges, not point swings). Construct a synthetic `Swing` to pass to `SweepEvent`:

```python
Swing(
    kind="high",          # or "low"
    price=kz_high,
    bar_ts=bar.ts,        # bar that swept it
    confirmed_ts=bar.ts,
)
```

This keeps `SweepEvent` unchanged — it still wraps a `Swing`, the Swing just has `bar_ts == confirmed_ts` (no lookback lag for KZ levels, unlike structural swings).

---

## `Signal` source tagging (`app/strategy/composer.py`)

### `Signal` dataclass

Add one field:

```python
source: str = "swing"   # "swing" | "kz_level"
```

Default `"swing"` preserves backward compatibility with existing backtest CSVs and journal entries.

### `_Awaiting` dataclass

Add:

```python
source: str = "swing"
```

### `on_sweep()` signature

```python
def on_sweep(self, bar: Bar, sweep: SweepEvent, source: str = "swing") -> None:
```

Stores `source` in the new `_Awaiting` entry.

### Signal emission

When building the `Signal` in `on_displacement()`, pass `source=awaiting.source`.

---

## Engine wiring (`app/execution/engine.py`)

### `StrategyRunner`

Add attribute:

```python
kz_levels: KillzoneLevelTracker | None = None
```

Set in `_build_runner()` when `bot_cfg.strategy.kz_levels_enabled` is True.

### Bar processing loop

After the existing sweep block:

```python
# Existing
sweeps = runner.liquidity.on_bar(bar)
for s in sweeps:
    runner.composer.on_sweep(bar, s, source="swing")

# New — runs only when enabled
if runner.kz_levels is not None and self.strategy_cfg is not None:
    for s in runner.kz_levels.on_bar(bar, runner.composer._zones, self.strategy_cfg):
        runner.composer.on_sweep(bar, s, source="kz_level")
```

---

## Config

### `StrategyParams` (`app/bot_config.py`)

```python
kz_levels_enabled: bool = True
```

### `PATCH /api/config` hot-apply (`app/api/server.py`)

When `kz_levels_enabled` changes:
- If now enabled: set `runner.kz_levels = KillzoneLevelTracker()`
- If now disabled: set `runner.kz_levels = None`

Same hot-apply pattern used for `vp_enabled`.

### Frontend `ConfigPanel.tsx`

Add a boolean toggle for `kz_levels_enabled`, alongside the existing VP and HTF toggles.

---

## Analytics / journal

`Signal.source` flows through to:
- SSE stream `signal` event payload
- Journal entries (already serialise full Signal fields)
- Trades CSV (via existing backtest runner)

No schema changes needed — the field is additive. Backtester output can be filtered by `source` column to compare P&L, win rate, and average R by strategy source.

---

## What does not change

- `LiquidityTracker` — no modifications
- `DisplacementDetector` — no modifications
- `SweepEvent` struct — no modifications
- Broker, order placement, pretrade gates, risk — untouched
- Existing `"swing"` signals — behaviour identical, just now explicitly tagged

---

## Success criteria

- `kz_levels_enabled: true` → KZ level sweeps fire alongside swing sweeps; both appear in journal with correct `source` tag
- `kz_levels_enabled: false` → behaviour identical to current (no regression)
- A KZ level that is swept is not re-used that day
- Levels reset at UTC midnight (new trading day)
- `Signal.source` is present in backtest CSV output, filterable for per-source analytics
- Existing tests pass unchanged
