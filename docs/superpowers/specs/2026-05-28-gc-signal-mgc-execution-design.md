# GC Signal / MGC Execution — Design Spec

**Date:** 2026-05-28
**Status:** Approved

---

## Problem

MGC (Micro Gold, 10 oz) is the execution instrument — right size for the account. But MGC has thin volume. The structural levels that actually matter (swing highs/lows that attract liquidity) are set by GC (full Gold, 100 oz) because that's where the institutional participants trade. Reading structure off MGC means reacting to less-meaningful swings. Reading it off GC means reacting to the levels the market actually respects.

---

## Goal

Stream GC bars into the strategy pipeline (for signal generation) while placing all orders on MGC. From the user's perspective — dashboard, journal, signals — everything says MGC. GC is an internal implementation detail.

---

## Scope

| File | Change |
|------|--------|
| `app/bot_config.py` | Add `signal_instrument: str \| None = None` to `BotConfig`; update `save_bot_config` |
| `app/execution/engine.py` | Add `signal_instrument: str` to `StrategyRunner`; add `_bar_router` to `ExecutionEngine`; update `_handle_bar` routing; update `_snapshot_strategy_state` |
| `app/main.py` | Update `_build_runner`, `_run_live` subscription, `_make_bar_journaler` |
| `app/api/journal.py` | Add `display_instrument` param to `publish_bar` |
| `app/api/server.py` | `GET /api/bars` uses execution instrument; `GET /api/config` and `PATCH /api/config` include `signal_instrument` |
| `frontend/src/types.ts` | Add `signal_instrument: string \| null` to `BotConfig` |
| `frontend/src/components/ConfigPanel.tsx` | Add `signal_instrument` field |

No changes to: strategy pipeline, pretrade gates, risk sizing, order placement, broker protocol, backtest runner.

---

## Architecture

```
GC bar stream (TopstepX SDK)
        │
        ▼
ExecutionEngine._handle_bar
  _bar_router: {"GC": "MGC"}        ← maps signal instrument → execution instrument
        │
        ▼
StrategyRunner (keyed "MGC")
  LiquidityTracker → SweepEvent
  DisplacementDetector → DisplacementEvent
  KillzoneLevelTracker → SweepEvent
  SweepDisplacementComposer
        │
        ▼
  Signal(instrument="MGC", ...)      ← execution instrument, not GC
        │
        ▼
  broker.place_bracket(instrument="MGC", ...)
        │
        ▼
  MGC Fill (fill.instrument="MGC")
  _handle_fill → runners.get("MGC") ← routes correctly, unchanged
```

```
Chart display:
  GC bar arrives → journal.publish_bar(bar, display_instrument="MGC")
  → WebSocket "bar" event with instrument="MGC"
  → BarChart renders MGC-labeled bar

  GET /api/bars?timeframe=4h
  → broker.get_historical_bars(instrument="MGC", ...)  ← explicit execution instrument
  → chart pre-load shows MGC history
```

---

## Config

### `BotConfig` (`app/bot_config.py`)

```python
signal_instrument: str | None = None
# None = use same instrument as execution (existing behavior, fully backward compatible).
# Set to "GC" to stream GC bars for signal generation while trading the configured instrument.
```

`save_bot_config` serializes it: `"signal_instrument": config.signal_instrument`.

### `GET /api/config` and `PATCH /api/config`

Both include `signal_instrument` in their response/request payload. No hot-apply — changing `signal_instrument` requires a bot restart (it changes what the SDK subscribes to).

### Frontend

`types.ts`: `signal_instrument: string | null` on `BotConfig`.

`ConfigPanel.tsx`: text input field (same as `htf_bias_timeframe` — plain string, not a dropdown, since valid values depend on what TopstepX has). Empty string serialized as `null` in `handleSave`.

---

## Engine changes (`app/execution/engine.py`)

### `StrategyRunner`

Add field after `kz_levels`:

```python
signal_instrument: str = ""  # set by _build_runner; empty string = same as instrument
```

The effective signal instrument is `self.signal_instrument or self.instrument`. Used in logs and the debug panel snapshot.

### `ExecutionEngine._bar_router`

Built in `__init__` from the runners list:

```python
# Maps signal instrument → execution instrument for cross-instrument routing.
self._bar_router: dict[str, str] = {
    r.signal_instrument: r.instrument
    for r in runners
    if r.signal_instrument and r.signal_instrument != r.instrument
}
```

### `_handle_bar` routing

```python
exec_instrument = self._bar_router.get(bar.instrument, bar.instrument)
runner = self.runners.get(exec_instrument)
```

This replaces the current `runner = self.runners.get(bar.instrument)`. When no routing is configured (normal MGC-only setup), `_bar_router` is empty and the default `bar.instrument` is used — fully backward compatible.

### `_snapshot_strategy_state`

Add `signal_instrument` to the snapshot payload:

```python
"signal_instrument": runner.signal_instrument or runner.instrument,
```

---

## Broker subscription (`app/main.py`)

### `_run_live`

Change subscription instrument from `cfg.instrument` to the signal instrument:

```python
signal_instr = bot_cfg.signal_instrument or cfg.instrument
await broker.subscribe([signal_instr], cfg.timeframes)
```

The broker SDK streams GC bars when `signal_instrument = "GC"`. Order placement uses `signal.instrument` (which is always the execution instrument — set by `ComposerConfig.instrument`), so orders still go to MGC.

### `_build_runner`

Pass `signal_instrument` to the runner:

```python
return StrategyRunner(
    instrument=instrument,            # execution instrument: "MGC"
    signal_instrument=signal_instrument or instrument,  # "GC" or "MGC"
    ...
)
```

`_build_runner` gains a `signal_instrument: str | None = None` parameter.

### `_make_bar_journaler`

The bar journaler re-labels GC bars as the execution instrument before publishing to the chart WebSocket:

```python
def _make_bar_journaler(journal: Journal, execution_instrument: str):
    async def on_bar(bar: Bar) -> None:
        journal.publish_bar(bar, display_instrument=execution_instrument)
    return on_bar
```

Callers pass the execution instrument (MGC). When `signal_instrument == instrument` (default), `display_instrument == bar.instrument` — no change.

---

## Journal (`app/api/journal.py`)

`publish_bar` gains an optional `display_instrument` parameter:

```python
def publish_bar(self, bar: Bar, display_instrument: str | None = None) -> None:
    entry = JournalEntry(
        ts=bar.ts,
        kind="bar",
        payload={
            "instrument": display_instrument or bar.instrument,
            "open": str(bar.open),
            "high": str(bar.high),
            "low": str(bar.low),
            "close": str(bar.close),
            "volume": bar.volume,
        },
    )
    self._publish(entry)
```

Existing callers (`publish_bar(bar)`) are unaffected — `display_instrument=None` uses `bar.instrument`.

---

## Historical bars (`app/api/server.py`)

`GET /api/bars` always fetches from the **execution instrument**, not the signal instrument:

```python
cfg = load_bot_config(_bot_config_path)
exec_instr = cfg.instrument or _effective_instrument
tf = timeframe or (cfg.timeframes or _effective_timeframes)[0]
_days = {"4h": 60, "1d": 90}.get(tf, 5)
bars = await _broker.get_historical_bars(
    timeframe=tf, limit=limit, days=_days,
    instrument=exec_instr,
)
```

This requires `get_historical_bars` to accept an explicit `instrument` parameter (currently it always uses `self._instruments[0]`). If `instrument` is provided and differs from the subscribed instrument, it performs a one-off fetch for the specified symbol. The chart pre-load always shows MGC history.

**`get_historical_bars` change in `topstepx.py`:** Add `instrument: str | None = None` parameter. When provided, uses it instead of `self._instruments[0]` for the symbol lookup.

---

## Hot-reload

When the bot hot-reloads the runner (config change that rebuilds the strategy stack), the engine's `runners` dict is replaced. The `_bar_router` must be rebuilt at the same time. In `main.py`'s reload handler (where `engine.runners = {cfg.instrument: new_runner}` is set), also update:

```python
engine._bar_router = {
    new_runner.signal_instrument: new_runner.instrument
} if new_runner.signal_instrument and new_runner.signal_instrument != new_runner.instrument else {}
```

---

## Paper/backtest mode

`signal_instrument` is respected in paper mode — the `_bar_router` is built the same way. However, paper mode replays bars from a CSV file, and those bars carry whatever instrument is in the file (typically MGC). Since `_bar_router = {"GC": "MGC"}` and paper bars have `instrument="MGC"`, the router default (`bar.instrument`) applies and bars route to the MGC runner unchanged. Paper mode works correctly regardless of `signal_instrument` — it just doesn't get the GC-structure benefit since it replays MGC bars.

---

## Logging

At startup in `_run_live`, when `signal_instrument` differs from the execution instrument:

```
INFO  Using GC bars for signal generation, executing on MGC
```

---

## Backward compatibility

- `signal_instrument: None` (default) → `_bar_router` is empty → `_handle_bar` routes by `bar.instrument` → identical to current behavior
- All existing paper runs, backtests, and single-instrument live setups are unaffected
- `save_bot_config` will write `"signal_instrument": null` for existing configs; `load_bot_config` defaults it to `None`

---

## Success criteria

- GC bars arrive in the strategy pipeline; swing levels are detected on GC price action
- Signals emit with `instrument="MGC"` — dashboard, journal, SSE stream all show MGC
- Orders are placed on MGC
- Chart shows MGC-labeled bars (GC prices re-labeled) with no visual difference
- Historical chart pre-load fetches MGC bars
- `signal_instrument: null` (default) produces identical behavior to the current codebase
- Strategy debug panel shows `signal_instrument: GC` when configured
