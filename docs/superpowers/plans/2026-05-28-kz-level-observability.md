# KZ Level Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three observability layers to the KZ level sweep feature: INFO log lines at key transitions, a `strategy_state` WebSocket event emitted each bar, and a collapsible `StrategyDebug` panel on the dashboard.

**Architecture:** A new `on_bar_done` callback on `ExecutionEngine` fires after each bar with a pure-read snapshot of the runner's strategy state. `Journal.publish_strategy_state` pushes this snapshot over the WebSocket as a `strategy_state` event (not stored). The frontend `useStream` hook captures it and passes it to a new `StrategyDebug` component.

**Tech Stack:** Python 3.12, FastAPI, React/TypeScript/Tailwind. Run tests with `.venv/Scripts/python.exe -m pytest`. Build frontend with `cd frontend && npm run build`.

---

## Background for the implementer

- **WebSocket stream** (`/api/stream`): pushes `JournalEntry` objects. Each has `kind`, `ts`, `payload`. `journal.publish_bar` is the pattern for fire-and-forget publishing (no storage). We replicate this with `publish_strategy_state`.
- **`ExecutionEngine._handle_bar`** (`app/execution/engine.py`): called on each bar. After `runner.on_bar(bar)` returns, we call a new optional `on_bar_done` callback with the instrument name and a state snapshot dict.
- **`_snapshot_strategy_state`**: a module-level helper function (not a method) that reads `runner.kz_levels._kz_ranges`, `runner.kz_levels._pending_a`, and `runner.composer._awaiting`. Pure read — no mutation. Safe on the hot path.
- **`useStream.ts`**: the frontend hook that owns the WebSocket connection. Dispatches on `msg.kind`. Add a branch for `"strategy_state"`.
- **`StrategyDebug.tsx`**: collapsible panel, collapsed by default. Two tables: KZ Levels and Awaiting Sweeps.
- **Run tests:** `.venv/Scripts/python.exe -m pytest tests/test_engine.py tests/test_kz_levels.py -q`
- **Build frontend:** `cd frontend && npm run build`

---

## File structure

| File | Action | Responsibility |
|------|--------|---------------|
| `app/strategy/kz_levels.py` | Modify | Add INFO logs for Pattern A tag + sweep detected |
| `app/execution/engine.py` | Modify | Add `on_bar_done` callback + `_snapshot_strategy_state` helper |
| `app/api/journal.py` | Modify | Add `publish_strategy_state` method |
| `app/main.py` | Modify | Wire `engine.on_bar_done` to journal |
| `tests/test_engine.py` | Modify | Tests for `_snapshot_strategy_state` |
| `frontend/src/types.ts` | Modify | Add `StrategyStatePayload` interface |
| `frontend/src/hooks/useStream.ts` | Modify | Handle `strategy_state` kind, expose `strategyState` |
| `frontend/src/components/StrategyDebug.tsx` | **Create** | Collapsible debug panel |
| `frontend/src/App.tsx` | Modify | Import + render `StrategyDebug` |

---

## Task 1: Backend — logging, snapshot, event, wiring

**Files:**
- Modify: `app/strategy/kz_levels.py`
- Modify: `app/execution/engine.py`
- Modify: `app/api/journal.py`
- Modify: `app/main.py`
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests for `_snapshot_strategy_state`**

Append to `tests/test_engine.py`. Add this import at the top of the file alongside the existing engine imports:

```python
from app.execution.engine import _snapshot_strategy_state
```

Then append these tests at the bottom of `tests/test_engine.py`:

```python
def test_snapshot_empty_when_kz_levels_none():
    """Returns empty collections when runner has no KZ tracker."""
    runner = make_runner()
    state = _snapshot_strategy_state(runner)
    assert state["instrument"] == "MGC"
    assert state["kz_ranges"] == {}
    assert state["kz_pending_a"] == []
    assert state["awaiting_sweeps"] == []


def test_snapshot_kz_ranges_serialized_as_strings():
    """Finalized KZ ranges appear as string decimals."""
    from app.strategy.kz_levels import KillzoneLevelTracker
    runner = make_runner()
    runner.kz_levels = KillzoneLevelTracker()
    runner.kz_levels._kz_ranges["London"] = (Decimal("103"), Decimal("98"))
    state = _snapshot_strategy_state(runner)
    assert state["kz_ranges"] == {"London": {"high": "103", "low": "98"}}


def test_snapshot_pending_a_keys_included():
    """Pattern A tags in progress appear in kz_pending_a."""
    from app.strategy.kz_levels import KillzoneLevelTracker
    runner = make_runner()
    runner.kz_levels = KillzoneLevelTracker()
    runner.kz_levels._kz_ranges["London"] = (Decimal("103"), Decimal("98"))
    runner.kz_levels._pending_a["London_high"] = Decimal("103.3")
    state = _snapshot_strategy_state(runner)
    assert "London_high" in state["kz_pending_a"]


def test_snapshot_awaiting_sweeps_empty_by_default():
    """No awaiting sweeps when composer has nothing pending."""
    runner = make_runner()
    state = _snapshot_strategy_state(runner)
    assert state["awaiting_sweeps"] == []
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py::test_snapshot_empty_when_kz_levels_none tests/test_engine.py::test_snapshot_kz_ranges_serialized_as_strings tests/test_engine.py::test_snapshot_pending_a_keys_included tests/test_engine.py::test_snapshot_awaiting_sweeps_empty_by_default -q`
Expected: `ImportError: cannot import name '_snapshot_strategy_state'`

- [ ] **Step 3: Add INFO logs to `kz_levels.py`**

In `app/strategy/kz_levels.py`, add a log line inside the Pattern A tag-start block (after `self._pending_a[high_key] = bar.high`):

```python
            elif bar.high >= kz_high + min_pen and bar.close >= kz_high:
                # Tag without close-back — start pattern A tracking.
                self._pending_a[high_key] = bar.high
                self._bars_since_tag[high_key] = 0
                log.info("KZ pattern A tag: %s high @ %s (bar extreme: %s)", name, kz_high, bar.high)
```

And for the low-side Pattern A tag (after `self._pending_a[low_key] = bar.low`):

```python
            elif bar.low <= kz_low - min_pen and bar.close <= kz_low:
                self._pending_a[low_key] = bar.low
                self._bars_since_tag[low_key] = 0
                log.info("KZ pattern A tag: %s low @ %s (bar extreme: %s)", name, kz_low, bar.low)
```

Also add log lines for each sweep emission. There are four sweep sites — Pattern B high, Pattern B low, Pattern A high completion, Pattern A low completion. Add `log.info` immediately before each `events.append(self._make_sweep(...))` call:

Pattern B high (after the `if bar.high >= kz_high + min_pen and bar.close < kz_high:` line):
```python
                log.info("KZ sweep: %s high B_one_bar (extreme: %s → level: %s)", name, bar.high, kz_high)
                events.append(self._make_sweep(bar, "high", kz_high, bar.high, "B_one_bar"))
```

Pattern B low:
```python
                log.info("KZ sweep: %s low B_one_bar (extreme: %s → level: %s)", name, bar.low, kz_low)
                events.append(self._make_sweep(bar, "low", kz_low, bar.low, "B_one_bar"))
```

Pattern A high completion (the `if bar.close < kz_high:` branch inside `if high_key in self._pending_a:`):
```python
                    log.info("KZ sweep: %s high A_multi_bar (extreme: %s → level: %s)", name, extreme, kz_high)
                    events.append(self._make_sweep(bar, "high", kz_high, extreme, "A_multi_bar"))
```

Pattern A low completion:
```python
                    log.info("KZ sweep: %s low A_multi_bar (extreme: %s → level: %s)", name, extreme, kz_low)
                    events.append(self._make_sweep(bar, "low", kz_low, extreme, "A_multi_bar"))
```

- [ ] **Step 4: Add `_snapshot_strategy_state` and `on_bar_done` to `engine.py`**

In `app/execution/engine.py`, add the helper function as a module-level function just before the `ExecutionEngine` class definition (after `StrategyRunner`):

```python
def _snapshot_strategy_state(runner: "StrategyRunner") -> dict:
    """Read-only snapshot of strategy state for the debug panel. No mutation."""
    kz_ranges: dict = {}
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

Add `on_bar_done` to `ExecutionEngine.__init__` — add the parameter after `strategy_cfg`:

```python
    def __init__(
        self,
        broker: Broker,
        risk_state: RiskState,
        runners: list[StrategyRunner],
        on_signal: SignalEmitted | None = None,
        on_order_placed: Callable[[], None] | None = None,
        on_pre_place: PrePlaceCallback | None = None,
        replay_mode: bool = False,
        contracts: int = 1,
        risk_per_trade_pct: Decimal = Decimal("0"),
        strategy_cfg: "StrategyParams | None" = None,
        on_bar_done: "Callable[[str, dict], None] | None" = None,
    ) -> None:
```

And store it in `__init__` (alongside `self.strategy_cfg = strategy_cfg`):

```python
        self.on_bar_done = on_bar_done
```

In `_handle_bar`, add the callback call immediately after the `try/except` block for `runner.on_bar(bar)`:

```python
        try:
            signal = runner.on_bar(bar)
        except Exception:
            log.exception("Strategy raised on bar %s", bar.ts)
            return

        if self.on_bar_done is not None:
            self.on_bar_done(bar.instrument, _snapshot_strategy_state(runner))

        if signal is None or is_stale:
```

- [ ] **Step 5: Add `publish_strategy_state` to `journal.py`**

In `app/api/journal.py`, add this method immediately after `publish_bar`:

```python
    def publish_strategy_state(self, instrument: str, state: dict) -> None:
        """Stream strategy state to WebSocket subscribers without storing it."""
        entry = JournalEntry(
            ts=datetime.now(timezone.utc),
            kind="strategy_state",
            payload=state,
        )
        self._publish(entry)
```

- [ ] **Step 6: Wire `on_bar_done` in `main.py`**

In `app/main.py`, find the `ExecutionEngine(...)` constructor call (around line 1007). Add `on_bar_done` as the last argument:

```python
    engine = ExecutionEngine(
        broker=broker,
        risk_state=risk_state,
        runners=[runner],
        on_signal=_make_signal_journaler(journal, notifier, config_path=cfg.bot_config_path, discord=discord),
        on_order_placed=reconciler.notify_order_placed,
        on_pre_place=_make_pre_place(config_path=cfg.bot_config_path),
        contracts=bot_cfg.contracts,
        risk_per_trade_pct=bot_cfg.risk_per_trade_pct,
        strategy_cfg=bot_cfg.strategy,
        on_bar_done=lambda instr, state: journal.publish_strategy_state(instr, state),
    )
```

- [ ] **Step 7: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py tests/test_kz_levels.py -q`
Expected: all pass (4 new tests + existing tests). The 5 pre-existing failures in other test files are unchanged.

Also smoke-test the import:
`.venv/Scripts/python.exe -c "from app.execution.engine import _snapshot_strategy_state; print('OK')"`

- [ ] **Step 8: Commit**

```bash
git add app/strategy/kz_levels.py app/execution/engine.py app/api/journal.py app/main.py tests/test_engine.py
git commit -m "feat: strategy_state SSE event — backend logging, snapshot, wiring"
```

---

## Task 2: Frontend — types, stream hook, debug panel, App wiring

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/hooks/useStream.ts`
- Create: `frontend/src/components/StrategyDebug.tsx`
- Modify: `frontend/src/App.tsx`

No new tests (visual feature; verified by frontend build + manual paper run).

- [ ] **Step 1: Add `StrategyStatePayload` to `types.ts`**

In `frontend/src/types.ts`, append this interface before the final closing line of the file:

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

- [ ] **Step 2: Update `useStream.ts` to handle `strategy_state`**

In `frontend/src/hooks/useStream.ts`, add the import for `StrategyStatePayload` at the top:

```typescript
import type { BarEvent, JournalItem, StatusPayload, StrategyStatePayload } from '../types'
```

Add `strategyState` to the state declarations (alongside `status`, `signals`, etc.):

```typescript
  const [strategyState, setStrategyState] = useState<StrategyStatePayload | null>(null)
```

Add a handler in `ws.onmessage` immediately before the `const item: JournalItem` line:

```typescript
        if (msg.kind === 'strategy_state') {
          setStrategyState(msg.payload as StrategyStatePayload)
          return
        }
```

Add `strategyState` to the return value:

```typescript
  return { status, signals, fills, reconciles, connState, strategyState }
```

- [ ] **Step 3: Create `StrategyDebug.tsx`**

Create `frontend/src/components/StrategyDebug.tsx`:

```tsx
import { useState } from 'react'
import type { StrategyStatePayload } from '../types'

interface Props {
  state: StrategyStatePayload | null
}

export function StrategyDebug({ state }: Props) {
  const [open, setOpen] = useState(false)

  return (
    <div className="border border-border font-mono text-sm">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full flex items-center justify-between px-4 py-2 text-dim hover:text-ink text-xs tracking-widest uppercase"
      >
        <span>Strategy State</span>
        <span className="text-accent">{open ? '▼' : '▶'}</span>
      </button>

      {open && (
        <div className="border-t border-border p-4 flex flex-col gap-6">

          {/* KZ Levels */}
          <div>
            <div className="text-dim text-xs tracking-widest uppercase mb-2">KZ Levels</div>
            {!state || Object.keys(state.kz_ranges).length === 0 ? (
              <div className="text-dim text-xs">No levels locked today</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-dim">
                    <th className="text-left pr-6 pb-1">Zone</th>
                    <th className="text-right pr-6 pb-1">High</th>
                    <th className="text-right pb-1">Low</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(state.kz_ranges).map(([name, { high, low }]) => (
                    <tr key={name}>
                      <td className="text-ink pr-6 py-0.5">{name}</td>
                      <td className="text-right pr-6 py-0.5">
                        <span className="text-ink">{high}</span>
                        {state.kz_pending_a.includes(`${name}_high`) && (
                          <span className="ml-1 text-accent text-[10px]">[A]</span>
                        )}
                      </td>
                      <td className="text-right py-0.5">
                        <span className="text-ink">{low}</span>
                        {state.kz_pending_a.includes(`${name}_low`) && (
                          <span className="ml-1 text-accent text-[10px]">[A]</span>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

          {/* Awaiting Sweeps */}
          <div>
            <div className="text-dim text-xs tracking-widest uppercase mb-2">Awaiting Sweeps</div>
            {!state || state.awaiting_sweeps.length === 0 ? (
              <div className="text-dim text-xs">No pending sweeps</div>
            ) : (
              <table className="w-full text-xs">
                <thead>
                  <tr className="text-dim">
                    <th className="text-left pr-4 pb-1">Side</th>
                    <th className="text-left pr-4 pb-1">Src</th>
                    <th className="text-right pr-4 pb-1">Price</th>
                    <th className="text-right pr-4 pb-1">Bars</th>
                    <th className="text-left pb-1">Zone</th>
                  </tr>
                </thead>
                <tbody>
                  {state.awaiting_sweeps.map((s, i) => (
                    <tr key={i}>
                      <td className={`pr-4 py-0.5 ${s.side === 'high' ? 'text-red-400' : 'text-accent'}`}>
                        {s.side}
                      </td>
                      <td className="text-dim pr-4 py-0.5">
                        {s.source === 'kz_level' ? 'kz' : 'sw'}
                      </td>
                      <td className="text-ink text-right pr-4 py-0.5">{s.price}</td>
                      <td className="text-dim text-right pr-4 py-0.5">{s.bars_elapsed}</td>
                      <td className="text-dim py-0.5">{s.killzone}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </div>

        </div>
      )}
    </div>
  )
}
```

- [ ] **Step 4: Wire `StrategyDebug` into `App.tsx`**

In `frontend/src/App.tsx`, add the import at the top:

```typescript
import { StrategyDebug } from './components/StrategyDebug'
```

Update the `useStream` destructure to include `strategyState`:

```typescript
  const { status, signals, fills, reconciles, connState, strategyState } = useStream(chartCbRef)
```

Add the component between `<BarChart ... />` and the 3-column feed grid:

```tsx
        <BarChart callbacksRef={chartCbRef} timeframe={config?.timeframes?.[0]} />
        <StrategyDebug state={strategyState} />
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-px bg-border border border-border">
```

- [ ] **Step 5: Build frontend**

Run: `cd frontend && npm run build`
Expected: clean build, no TypeScript errors.

- [ ] **Step 6: Run full backend suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_backtest.py --ignore=tests/test_analytics_loader.py --ignore=tests/test_analytics_tools.py`
Expected: only the 5 known pre-existing failures.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/types.ts frontend/src/hooks/useStream.ts frontend/src/components/StrategyDebug.tsx frontend/src/App.tsx app/api/static/
git commit -m "feat: StrategyDebug panel — live KZ levels and awaiting sweeps"
```

---

## Self-review notes

**Spec coverage:**
- ✅ `log.info` for Pattern A tag started → Task 1 Step 3
- ✅ `log.info` for sweep detected (all 4 patterns × 2 sides) → Task 1 Step 3
- ✅ `on_bar_done` callback on engine → Task 1 Step 4
- ✅ `_snapshot_strategy_state` helper — kz_ranges, kz_pending_a, awaiting_sweeps → Task 1 Step 4
- ✅ `publish_strategy_state` on journal (sync, no storage) → Task 1 Step 5
- ✅ `engine.on_bar_done` wired in main.py → Task 1 Step 6
- ✅ `StrategyStatePayload` in types.ts → Task 2 Step 1
- ✅ `useStream` handles `strategy_state` kind → Task 2 Step 2
- ✅ `StrategyDebug` panel — KZ Levels table with `[A]` badge → Task 2 Step 3
- ✅ `StrategyDebug` panel — Awaiting Sweeps table → Task 2 Step 3
- ✅ Collapsed by default → Task 2 Step 3
- ✅ Wired in App.tsx between BarChart and feed grid → Task 2 Step 4
- ✅ `kz_levels_enabled: false` → kz_ranges and kz_pending_a are empty dicts; panel still renders → Task 2 Step 3 (handles empty state)

**Type consistency:**
- `_snapshot_strategy_state` returns `dict` with keys `instrument`, `kz_ranges`, `kz_pending_a`, `awaiting_sweeps` — matches `StrategyStatePayload` shape exactly.
- `publish_strategy_state(instrument: str, state: dict)` — `state` already contains `instrument`; the journal entry `payload` is `state` (which includes it). Frontend reads `msg.payload` and casts to `StrategyStatePayload`. ✓
- `[A]` badge check: `state.kz_pending_a.includes(\`${name}_high\`)` — key format matches `high_key = f"{name}_high"` in `kz_levels.py`. ✓
- `on_bar_done` type: `Callable[[str, dict], None]` — lambda in main.py is `lambda instr, state: journal.publish_strategy_state(instr, state)`. ✓
