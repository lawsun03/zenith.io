# KZ Level Sweeps — Observability Design Spec

**Date:** 2026-05-28
**Status:** Approved

---

## Problem

The `KillzoneLevelTracker` runs silently inside the engine. There is no way to verify from the dashboard that it is accumulating session H/L correctly, locking levels after killzones close, or detecting sweeps. Without visibility, a misconfiguration or edge case is indistinguishable from "no setups today."

---

## Goal

Add the three Rule 13 observability layers to the KZ level sweep feature:

1. **Log** key state transitions in `kz_levels.py`
2. **SSE** emit a `strategy_state` event each bar via the existing WebSocket stream
3. **Frontend** render a collapsible `StrategyDebug` panel showing live KZ state

---

## Scope

| File | Change |
|------|--------|
| `app/strategy/kz_levels.py` | Add `log.info` for sweep detected and Pattern A tag started |
| `app/execution/engine.py` | Add `on_bar_done` callback + `_snapshot_strategy_state` helper |
| `app/api/journal.py` | Add `publish_strategy_state(instrument, state)` |
| `app/main.py` | Wire `engine.on_bar_done` to journal |
| `frontend/src/hooks/useStream.ts` | Handle `strategy_state` kind, expose `strategyState` |
| `frontend/src/types.ts` | Add `StrategyStatePayload` type |
| `frontend/src/components/StrategyDebug.tsx` | **New** — collapsible debug panel |
| `app/api/static/` | Rebuilt frontend assets |

No changes to: `LiquidityTracker`, `SweepDisplacementComposer` internals, broker, risk, order placement, backtest runner.

---

## Layer 1 — Logging (`kz_levels.py`)

Two new log lines, both `log.info` (INFO so they appear in normal log tails without `--debug`):

**Pattern A tag started** (level touched, price not closed back yet):
```
KZ pattern A tag: London high @ 103.0 (bar extreme: 103.3)
```

**Sweep detected** (Pattern A or B completed):
```
KZ sweep: London high B_one_bar (extreme: 103.3 → level: 103.0)
```

The existing `log.debug("KZ level locked: ...")` stays as-is (DEBUG is appropriate — happens every session close, not a trading event).

---

## Layer 2 — SSE event (`strategy_state`)

### Event shape

```json
{
  "kind": "strategy_state",
  "ts": "2026-05-28T09:01:00+00:00",
  "payload": {
    "instrument": "MGC",
    "kz_ranges": {
      "London": {"high": "103.0", "low": "98.0"}
    },
    "kz_pending_a": ["London_high"],
    "awaiting_sweeps": [
      {
        "side": "high",
        "source": "kz_level",
        "price": "103.0",
        "bars_elapsed": 2,
        "killzone": "London"
      }
    ]
  }
}
```

**Fields:**
- `kz_ranges` — finalized (high, low) per killzone name for today. Empty dict if no killzone has closed yet.
- `kz_pending_a` — list of level keys (`"<name>_high"` / `"<name>_low"`) where a Pattern A tag has started but price has not closed back. Used to render an `[A]` badge in the panel.
- `awaiting_sweeps` — sweep events sitting in the composer waiting for a displacement bar. Includes both `"swing"` and `"kz_level"` sources.

### Emission point

`ExecutionEngine._handle_bar`, immediately after `runner.on_bar(bar)` returns (before the stale-bar early-return). Fires on every bar including warmup bars so the panel updates during backtest replay.

### New engine callback

```python
on_bar_done: Callable[[str, dict], None] | None = None
```

Synchronous (not `Awaitable`) — `journal._publish` is sync and the snapshot is a pure read. Called as:

```python
if self.on_bar_done is not None:
    self.on_bar_done(bar.instrument, _snapshot_strategy_state(runner))
```

### `_snapshot_strategy_state` helper (engine.py)

Module-level function, not a method — pure read, no mutation:

```python
def _snapshot_strategy_state(runner: StrategyRunner) -> dict:
    kz_ranges = {}
    kz_pending_a: list[str] = []
    if runner.kz_levels is not None:
        kz_ranges = {
            name: {"high": str(high), "low": str(low)}
            for name, (high, low) in runner.kz_levels._kz_ranges.items()
        }
        kz_pending_a = list(runner.kz_levels._pending_a.keys())
    awaiting = [
        {
            "side": a.sweep.side,
            "source": a.source,
            "price": str(a.sweep.swept_swing.price),
            "bars_elapsed": a.bars_since_sweep,
            "killzone": a.killzone_name,
        }
        for a in runner.composer._awaiting
    ]
    return {
        "instrument": runner.instrument,
        "kz_ranges": kz_ranges,
        "kz_pending_a": kz_pending_a,
        "awaiting_sweeps": awaiting,
    }
```

### `publish_strategy_state` (journal.py)

Sync method, same pattern as `publish_bar` — publishes without storing:

```python
def publish_strategy_state(self, instrument: str, state: dict) -> None:
    entry = JournalEntry(
        ts=datetime.now(timezone.utc),
        kind="strategy_state",
        payload=state,
    )
    self._publish(entry)
```

### Wiring (main.py)

One line added near the engine construction:

```python
engine.on_bar_done = lambda instr, state: journal.publish_strategy_state(instr, state)
```

---

## Layer 3 — Frontend

### `StrategyStatePayload` (types.ts)

```typescript
export interface StrategyStatePayload {
  instrument: string
  kz_ranges: Record<string, { high: string; low: string }>
  kz_pending_a: string[]
  awaiting_sweeps: Array<{
    side: string
    source: string
    price: string
    bars_elapsed: number
    killzone: string
  }>
}
```

### `useStream.ts`

Add `strategyState: StrategyStatePayload | null` state. Handle `kind === "strategy_state"` in `ws.onmessage`:

```typescript
if (msg.kind === 'strategy_state') {
  setStrategyState(msg.payload)
  return
}
```

Return `strategyState` from the hook alongside existing fields.

### `StrategyDebug.tsx`

New component. Collapsible — collapsed by default. A single `▶ Strategy State` header row toggles it open.

**When open, two sections:**

**KZ Levels** (from `kz_ranges`):
- Empty state: dim text `"No levels locked today"`
- Otherwise: one row per zone — `name | high | low`. If `kz_pending_a` includes `"<name>_high"`, show a small `[A]` badge beside the high value. Same for low.

**Awaiting Sweeps** (from `awaiting_sweeps`):
- Empty state: dim text `"No pending sweeps"`
- Otherwise: one row per entry — `side | source | price | bars`. Source shown as `kz` or `sw` (abbreviated) to save space.

Style: same hacker/matrix theme (`text-ink`, `text-dim`, `text-accent`, `border-border`). The component carries its own `border border-border` — it is a direct child of `<main>`, not inside the 3-column gap-px grid.

### `App.tsx`

Add between `BarChart` and the 3-column feed grid:

```tsx
<StrategyDebug state={strategyState} />
```

`strategyState` comes from `useStream`. The component handles `null` (renders nothing or collapsed placeholder before the first bar arrives).

---

## What does not change

- `LiquidityTracker` — no modifications
- `SweepDisplacementComposer` internals — no modifications
- Broker, risk, execution — untouched
- Journal storage buffers — `strategy_state` is not stored, only streamed
- Backtest runner — not wired (backtest doesn't use the WebSocket journal)

---

## Success criteria

- During a paper run, the StrategyDebug panel updates every bar
- After London closes (05:00 ET), `KZ Levels` shows the London high/low
- When a KZ level is swept, it disappears from `KZ Levels` on the next bar
- When a sweep fires but no displacement yet, `Awaiting Sweeps` shows the entry
- Log output shows "KZ sweep:" and "KZ pattern A tag:" lines at INFO level
- `kz_levels_enabled: false` → `kz_ranges` and `kz_pending_a` are empty dicts; `awaiting_sweeps` may still show swing-source entries
