# GC Signal / MGC Execution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stream GC bars into the strategy pipeline for signal generation while placing all orders on MGC — GC is internal only, MGC appears in all user-facing output.

**Architecture:** A `signal_instrument` config field (default `None` = same as execution instrument) drives a `_bar_router` dict on `ExecutionEngine` that maps GC bar events to the MGC runner. The broker subscribes to the signal instrument's bar stream; fills and order placement always use the execution instrument. The chart re-labels streamed bars as the execution instrument; historical chart fetches always request the execution instrument explicitly.

**Tech Stack:** Python 3.12, FastAPI, Pydantic, React/TypeScript/Tailwind. Run tests with `.venv/Scripts/python.exe -m pytest`. Build frontend with `cd frontend && npm run build`.

---

## Background for the implementer

- **Working directory:** `C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot`
- **`StrategyRunner`** (`app/execution/engine.py:98`): `@dataclass` with `instrument` (execution, e.g. "MGC") and a new `signal_instrument` field to add. The `runners` dict in `ExecutionEngine` is keyed by `runner.instrument` (execution instrument).
- **`_handle_bar`** (`engine.py`): currently does `runner = self.runners.get(bar.instrument)`. For GC bars this returns `None` — the new `_bar_router` fixes this.
- **`_handle_fill`** (`engine.py`): routes by `fill.instrument` which is always "MGC" (the execution instrument). This is unchanged.
- **`_act_on_signal`** (`engine.py`): uses `signal.instrument` for order placement. `signal.instrument` comes from `ComposerConfig.instrument` which is always the execution instrument. Unchanged.
- **`_make_bar_journaler`** (`main.py:453`): `broker.on_bar(_make_bar_journaler(journal))` — the single bar journaler call is at `main.py:1021`. This needs the execution instrument to re-label GC bars for the chart.
- **`get_historical_bars`** (`topstepx.py:1074`): currently uses `self._instruments[0]` for the symbol. Adding an explicit `instrument` param lets the `/api/bars` endpoint request MGC history even when the broker is subscribed to GC.
- **`publish_bar`** (`journal.py:279`): currently `publish_bar(self, bar: Bar)` — needs `display_instrument: str | None = None` param so the chart always shows MGC.
- **Run tests:** `.venv/Scripts/python.exe -m pytest tests/test_engine.py tests/test_kz_levels.py -q`
- **Existing pre-existing failures (do not fix):** `test_signal_denied_when_already_at_max_contracts`, `test_journal_subscriber_drops_old_under_backpressure`, `test_paper_mode_full_run`, and two reconciler tests.

---

## File structure

| File | Action | Responsibility |
|------|--------|----------------|
| `app/bot_config.py` | Modify | Add `signal_instrument: str \| None = None` to `BotConfig`; update `save_bot_config` |
| `app/broker/topstepx.py` | Modify | Add `instrument: str \| None` param to `get_historical_bars` |
| `app/execution/engine.py` | Modify | Add `signal_instrument` to `StrategyRunner`; add `_bar_router` to `ExecutionEngine`; update `_handle_bar`; update `_snapshot_strategy_state` |
| `app/api/journal.py` | Modify | Add `display_instrument` param to `publish_bar` |
| `app/main.py` | Modify | `_build_runner` gets `signal_instrument`; `_run_live` subscribes to signal instrument; `_make_bar_journaler` re-labels; hot-reload path updates `_bar_router` |
| `app/api/server.py` | Modify | `GET /api/config` and `PATCH /api/config` include `signal_instrument`; `GET /api/bars` uses execution instrument |
| `frontend/src/types.ts` | Modify | Add `signal_instrument: string \| null` to `BotConfig` |
| `frontend/src/components/ConfigPanel.tsx` | Modify | Add `signal_instrument` text field + form wiring |
| `tests/test_engine.py` | Modify | Tests for `_bar_router` and GC→MGC bar routing |

---

## Task 1: Config model + broker instrument param

**Files:**
- Modify: `app/bot_config.py`
- Modify: `app/broker/topstepx.py`
- Modify: `tests/test_engine.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_engine.py`. No new imports needed — existing imports already cover `ExecutionEngine`, `StrategyRunner`, `PaperBroker`, `RiskState`, `Decimal`.

```python
def test_bar_router_empty_when_no_signal_instrument():
    """Default runner (signal_instrument empty) produces empty bar router."""
    from app.risk.config import fifty_k_combine
    from app.broker.paper import PaperBroker
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    # signal_instrument defaults to "" — no routing needed
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True)
    assert engine._bar_router == {}


def test_bar_router_maps_signal_to_execution_instrument():
    """Runner with signal_instrument='GC' builds {'GC': 'MGC'} router."""
    from app.risk.config import fifty_k_combine
    from app.broker.paper import PaperBroker
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    runner.signal_instrument = "GC"
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True)
    assert engine._bar_router == {"GC": "MGC"}
```

- [ ] **Step 2: Run tests to confirm failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py::test_bar_router_empty_when_no_signal_instrument tests/test_engine.py::test_bar_router_maps_signal_to_execution_instrument -q`
Expected: `AttributeError: 'StrategyRunner' object has no attribute 'signal_instrument'`

- [ ] **Step 3: Add `signal_instrument` to `BotConfig`**

In `app/bot_config.py`, add after `enabled_killzones`:

```python
    signal_instrument: str | None = None
    # None = use same instrument as execution (backward compatible).
    # Set to "GC" to stream GC bars for signal generation while trading the configured instrument.
```

In `save_bot_config`, add `"signal_instrument": config.signal_instrument,` to the `data` dict (after `"enabled_killzones"`):

```python
    data = {
        "instrument": config.instrument,
        "timeframes": config.timeframes,
        "replay_delay_ms": config.replay_delay_ms,
        "replay_start_delay_s": config.replay_start_delay_s,
        "account_name": config.account_name,
        "entry_mode": config.entry_mode,
        "contracts": config.contracts,
        "risk_per_trade_pct": _conv(config.risk_per_trade_pct),
        "partial_profit_r": _conv(config.partial_profit_r),
        "enabled_killzones": config.enabled_killzones,
        "signal_instrument": config.signal_instrument,
        "strategy": {k: _conv(v) for k, v in config.strategy.model_dump().items()},
    }
```

- [ ] **Step 4: Add `signal_instrument` to `StrategyRunner`**

In `app/execution/engine.py`, add after the `kz_levels` field in `StrategyRunner`:

```python
    signal_instrument: str = ""
    # Empty string = same as instrument. Set to "GC" when using GC bars for MGC execution.
```

- [ ] **Step 5: Add `_bar_router` to `ExecutionEngine.__init__`**

In `app/execution/engine.py`, in `ExecutionEngine.__init__`, add after `self.on_bar_done = on_bar_done`:

```python
        # Maps signal instrument → execution instrument (e.g. {"GC": "MGC"}).
        # Empty when all runners use the same instrument for signal and execution.
        self._bar_router: dict[str, str] = {
            r.signal_instrument: r.instrument
            for r in runners
            if r.signal_instrument and r.signal_instrument != r.instrument
        }
```

- [ ] **Step 6: Run tests**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py::test_bar_router_empty_when_no_signal_instrument tests/test_engine.py::test_bar_router_maps_signal_to_execution_instrument -q`
Expected: 2 passed.

- [ ] **Step 7: Add explicit `instrument` param to `get_historical_bars` in `topstepx.py`**

In `app/broker/topstepx.py`, update `get_historical_bars` signature and body. The current signature is:

```python
async def get_historical_bars(
    self,
    timeframe: str = "1min",
    limit: int = 500,
    days: int = 5,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
) -> list[Bar]:
```

Change to:

```python
async def get_historical_bars(
    self,
    timeframe: str = "1min",
    limit: int = 500,
    days: int = 5,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    instrument: str | None = None,
) -> list[Bar]:
```

Inside the method body, change the `primary` assignment from:

```python
        primary = self._instruments[0] if self._instruments else ""
```

to:

```python
        primary = instrument or (self._instruments[0] if self._instruments else "")
```

- [ ] **Step 8: Run full suite to confirm no regressions**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_backtest.py --ignore=tests/test_analytics_loader.py --ignore=tests/test_analytics_tools.py`
Expected: 2 new tests pass; only the 5 known pre-existing failures remain.

- [ ] **Step 9: Commit**

```bash
git add app/bot_config.py app/broker/topstepx.py app/execution/engine.py tests/test_engine.py
git commit -m "feat: signal_instrument config + bar router + get_historical_bars instrument param"
```

---

## Task 2: Engine bar routing + journal re-labeling

**Files:**
- Modify: `app/execution/engine.py` — `_handle_bar`, `_snapshot_strategy_state`
- Modify: `app/api/journal.py` — `publish_bar`
- Modify: `tests/test_engine.py` — routing test

- [ ] **Step 1: Write failing routing test**

Append to `tests/test_engine.py`:

```python
@pytest.mark.asyncio
async def test_gc_bar_routes_to_mgc_runner():
    """GC bars are processed by the MGC runner when signal_instrument='GC'."""
    from app.risk.config import fifty_k_combine
    from app.broker.paper import PaperBroker
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    runner = make_runner()
    runner.signal_instrument = "GC"
    engine = ExecutionEngine(broker, state, [runner], replay_mode=True)
    await broker.connect()
    await engine.start()

    processed = []
    original_on_bar = runner.on_bar
    def tracking_on_bar(b):
        processed.append(b)
        return original_on_bar(b)
    runner.on_bar = tracking_on_bar

    # Inject a GC bar — should route to the MGC runner
    gc_bar = bar(in_ny_am(0), '100', '101', '99', '100', instrument="GC")
    await broker.inject_bar(gc_bar)
    await asyncio.sleep(0)
    assert len(processed) == 1
    assert processed[0].instrument == "GC"
```

- [ ] **Step 2: Run to confirm failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py::test_gc_bar_routes_to_mgc_runner -q`
Expected: FAIL — `assert len(processed) == 1` fails (0 processed, GC bar had no matching runner).

- [ ] **Step 3: Update `_handle_bar` to use `_bar_router`**

In `app/execution/engine.py`, find `_handle_bar`. Change the runner lookup line from:

```python
        runner = self.runners.get(bar.instrument)
```

to:

```python
        exec_instrument = self._bar_router.get(bar.instrument, bar.instrument)
        runner = self.runners.get(exec_instrument)
```

- [ ] **Step 4: Run routing test**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py::test_gc_bar_routes_to_mgc_runner -q`
Expected: 1 passed.

- [ ] **Step 5: Update `_snapshot_strategy_state` to include `signal_instrument`**

In `app/execution/engine.py`, in `_snapshot_strategy_state`, add `signal_instrument` to the returned dict:

```python
    return {
        "instrument": runner.instrument,
        "signal_instrument": runner.signal_instrument or runner.instrument,
        "kz_ranges": kz_ranges,
        "kz_pending_a": kz_pending_a,
        "awaiting_sweeps": awaiting,
    }
```

- [ ] **Step 6: Add `display_instrument` param to `journal.publish_bar`**

In `app/api/journal.py`, change `publish_bar` from:

```python
    def publish_bar(self, bar: Bar) -> None:
        """Stream a bar to WebSocket subscribers without storing it."""
        entry = JournalEntry(
            ts=bar.ts,
            kind="bar",
            payload={
                "instrument": bar.instrument,
                "open": str(bar.open),
                "high": str(bar.high),
                "low": str(bar.low),
                "close": str(bar.close),
                "volume": bar.volume,
            },
        )
        self._publish(entry)
```

to:

```python
    def publish_bar(self, bar: Bar, display_instrument: str | None = None) -> None:
        """Stream a bar to WebSocket subscribers without storing it."""
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

- [ ] **Step 7: Run full engine test suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py -q`
Expected: all pass except the 1 known pre-existing failure (`test_signal_denied_when_already_at_max_contracts`).

- [ ] **Step 8: Commit**

```bash
git add app/execution/engine.py app/api/journal.py tests/test_engine.py
git commit -m "feat: GC bar routing via _bar_router, publish_bar display_instrument"
```

---

## Task 3: `main.py` wiring

**Files:**
- Modify: `app/main.py` — `_build_runner`, `_make_bar_journaler`, `_run_live`, hot-reload path

- [ ] **Step 1: Update `_build_runner` to accept `signal_instrument`**

In `app/main.py`, change `_build_runner` signature from:

```python
def _build_runner(
    instrument: str,
    s: StrategyParams,
    enabled_killzones: list[str] | None = None,
    timeframe: str = "1min",
) -> StrategyRunner:
```

to:

```python
def _build_runner(
    instrument: str,
    s: StrategyParams,
    enabled_killzones: list[str] | None = None,
    timeframe: str = "1min",
    signal_instrument: str | None = None,
) -> StrategyRunner:
```

In the `return StrategyRunner(...)` call inside `_build_runner`, add `signal_instrument` after `kz_levels`:

```python
        kz_levels=KillzoneLevelTracker() if s.kz_levels_enabled else None,
        signal_instrument=signal_instrument or "",
```

- [ ] **Step 2: Update `_make_bar_journaler` to accept `execution_instrument`**

In `app/main.py`, change `_make_bar_journaler` from:

```python
def _make_bar_journaler(journal: Journal):
    """Build the on_bar subscriber that streams bars to the chart."""
    from app.broker.events import Bar as BarEvent

    async def on_bar(bar: BarEvent) -> None:
        journal.publish_bar(bar)

    return on_bar
```

to:

```python
def _make_bar_journaler(journal: Journal, execution_instrument: str = ""):
    """Build the on_bar subscriber that streams bars to the chart."""
    from app.broker.events import Bar as BarEvent

    async def on_bar(bar: BarEvent) -> None:
        # Re-label signal instrument bars (e.g. GC) as the execution instrument (MGC)
        # for the chart — prices are identical, only the label differs.
        journal.publish_bar(bar, display_instrument=execution_instrument or None)

    return on_bar
```

- [ ] **Step 3: Update `_run_live` to subscribe to signal instrument**

In `app/main.py`, `_run_live` currently starts with:

```python
    await broker.subscribe([cfg.instrument], cfg.timeframes)
```

The function signature already receives `bot_cfg: "BotConfig | None" = None`. Change the subscription line to:

```python
    signal_instr = (bot_cfg.signal_instrument if bot_cfg and bot_cfg.signal_instrument else None) or cfg.instrument
    if signal_instr != cfg.instrument:
        log.info("Signal instrument: %s — execution instrument: %s", signal_instr, cfg.instrument)
    await broker.subscribe([signal_instr], cfg.timeframes)
```

- [ ] **Step 4: Update the main engine construction to pass `signal_instrument` to `_build_runner` and `_make_bar_journaler`**

Find the `runner = _build_runner(...)` call before `ExecutionEngine(...)` construction (around line 990). It currently looks like:

```python
    runner = _build_runner(
        instrument=effective_instrument,
        s=bot_cfg.strategy,
        enabled_killzones=bot_cfg.enabled_killzones,
        timeframe=effective_timeframes[0],
    )
```

Add `signal_instrument`:

```python
    runner = _build_runner(
        instrument=effective_instrument,
        s=bot_cfg.strategy,
        enabled_killzones=bot_cfg.enabled_killzones,
        timeframe=effective_timeframes[0],
        signal_instrument=bot_cfg.signal_instrument,
    )
```

Find the `broker.on_bar(_make_bar_journaler(journal))` call (line 1021). Change it to:

```python
    broker.on_bar(_make_bar_journaler(journal, execution_instrument=effective_instrument))
```

- [ ] **Step 5: Update the paper-mode hot-reload path**

In `app/main.py`, the hot-reload block (around line 557) currently:

```python
            new_runner = _build_runner(
                cfg.instrument, new_cfg.strategy, new_cfg.enabled_killzones,
                timeframe=new_cfg.timeframes[0] if new_cfg.timeframes else "1min",
            )
            engine.runners = {cfg.instrument: new_runner}
            engine.strategy_cfg = new_cfg.strategy
```

Change to:

```python
            new_runner = _build_runner(
                cfg.instrument, new_cfg.strategy, new_cfg.enabled_killzones,
                timeframe=new_cfg.timeframes[0] if new_cfg.timeframes else "1min",
                signal_instrument=new_cfg.signal_instrument,
            )
            engine.runners = {cfg.instrument: new_runner}
            engine._bar_router = {
                new_runner.signal_instrument: new_runner.instrument
            } if new_runner.signal_instrument and new_runner.signal_instrument != new_runner.instrument else {}
            engine.strategy_cfg = new_cfg.strategy
```

- [ ] **Step 6: Run full suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_backtest.py --ignore=tests/test_analytics_loader.py --ignore=tests/test_analytics_tools.py`
Expected: only the 5 known pre-existing failures.

- [ ] **Step 7: Commit**

```bash
git add app/main.py
git commit -m "feat: wire signal_instrument through _build_runner, _run_live, bar journaler"
```

---

## Task 4: Server config endpoints + frontend

**Files:**
- Modify: `app/api/server.py`
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/ConfigPanel.tsx`

- [ ] **Step 1: Add `signal_instrument` to `GET /api/config` response**

In `app/api/server.py`, find the `GET /api/config` response dict (around line 436). It currently ends with:

```python
            "enabled_killzones": cfg.enabled_killzones,
            "mode": _mode,
            "strategy": _decimal_to_str(cfg.strategy.model_dump()),
```

Add `signal_instrument` after `enabled_killzones`:

```python
            "enabled_killzones": cfg.enabled_killzones,
            "signal_instrument": cfg.signal_instrument,
            "mode": _mode,
            "strategy": _decimal_to_str(cfg.strategy.model_dump()),
```

- [ ] **Step 2: Add `signal_instrument` to `PATCH /api/config` response**

Find the `PATCH /api/config` return (around line 473). It mirrors the same fields. Add `"signal_instrument": body.signal_instrument,` in the same position:

```python
            "enabled_killzones": body.enabled_killzones,
            "signal_instrument": body.signal_instrument,
            "mode": _mode,
            "strategy": _decimal_to_str(body.strategy.model_dump()),
```

- [ ] **Step 3: Update `GET /api/bars` to fetch with explicit execution instrument**

In `app/api/server.py`, find `GET /api/bars` (line 540). The current body after the mode check is:

```python
        try:
            cfg = load_bot_config(_bot_config_path)
            tf = timeframe or (cfg.timeframes or _effective_timeframes)[0]
            _days = {"4h": 60, "1d": 90}.get(tf, 5)
            bars = await _broker.get_historical_bars(timeframe=tf, limit=limit, days=_days)
```

Change the `get_historical_bars` call to pass the execution instrument explicitly:

```python
        try:
            cfg = load_bot_config(_bot_config_path)
            tf = timeframe or (cfg.timeframes or _effective_timeframes)[0]
            _days = {"4h": 60, "1d": 90}.get(tf, 5)
            exec_instr = cfg.instrument or _effective_instrument
            bars = await _broker.get_historical_bars(
                timeframe=tf, limit=limit, days=_days, instrument=exec_instr,
            )
```

Note: `_effective_instrument` is already available in scope via the `build_app` closure.

- [ ] **Step 4: Add `signal_instrument` to frontend `BotConfig` type**

In `frontend/src/types.ts`, find the `BotConfig` interface and add `signal_instrument` after `enabled_killzones`:

```typescript
export interface BotConfig {
  instrument: string
  timeframes: string[]
  replay_delay_ms: number
  replay_start_delay_s: number
  account_name: string | null
  entry_mode: string
  enabled_killzones: string[]
  signal_instrument: string | null
  contracts: number
  risk_per_trade_pct: number
  partial_profit_r: number
  mode?: string
  strategy: StrategyConfig
}
```

- [ ] **Step 5: Add `signal_instrument` to `ConfigPanel.tsx` form initialization**

In `frontend/src/components/ConfigPanel.tsx`, find the `useEffect` that sets form state from config (around line 205). Add `signal_instrument` to the spread:

```typescript
    setForm({
      instrument:           config.instrument,
      timeframes:           config.timeframes[0] ?? '1min',
      replay_delay_ms:      String(config.replay_delay_ms ?? 0),
      replay_start_delay_s: String(config.replay_start_delay_s ?? 5),
      account_name:         config.account_name ?? '',
      entry_mode:           config.entry_mode ?? 'market',
      contracts:            String(config.contracts ?? 1),
      risk_per_trade_pct:   String(config.risk_per_trade_pct ?? 0.25),
      partial_profit_r:     String(config.partial_profit_r ?? 0),
      signal_instrument:    config.signal_instrument ?? '',
      ...Object.fromEntries(
        Object.entries(config.strategy).map(([k, v]) => [k, String(v)])
      ),
    })
```

- [ ] **Step 6: Add `signal_instrument` to `handleSave` in `ConfigPanel.tsx`**

In `handleSave`, find the `await onSave({...})` call (around line 263). Add `signal_instrument` after `enabled_killzones`:

```typescript
    await onSave({
      instrument:           form.instrument?.trim().toUpperCase() || 'MGC',
      timeframes:           [form.timeframes || '1min'],
      replay_delay_ms:      parseInt(form.replay_delay_ms)      || 0,
      replay_start_delay_s: parseInt(form.replay_start_delay_s) || 5,
      account_name:         form.account_name?.trim() || null,
      entry_mode:           form.entry_mode || 'market',
      contracts:            parseInt(form.contracts) || 1,
      risk_per_trade_pct:   parseFloat(form.risk_per_trade_pct) || 0,
      partial_profit_r:     parseFloat(form.partial_profit_r) || 0,
      enabled_killzones:    enabledKillzones,
      signal_instrument:    form.signal_instrument?.trim().toUpperCase() || null,
      strategy,
    })
```

- [ ] **Step 7: Add `signal_instrument` input field to ConfigPanel UI**

In `frontend/src/components/ConfigPanel.tsx`, find where the Instrument input field is rendered (look for `form.instrument`). Add a Signal Instrument field immediately below it in the same general / top section. The pattern to follow is the same text input used for `instrument`. Insert after the instrument field's closing `</div>`:

```tsx
              <div>
                <label className="text-dim text-xs tracking-widest uppercase block mb-1">Signal Instrument</label>
                <input
                  className={inputClass}
                  value={form.signal_instrument ?? ''}
                  onChange={e => set('signal_instrument', e.target.value)}
                  placeholder="blank = same as instrument (e.g. GC)"
                />
                <div className="text-dim text-[10px] mt-1">
                  Leave blank to use the same instrument for signals and execution. Set to GC to read structure off full Gold while trading MGC. Requires restart.
                </div>
              </div>
```

- [ ] **Step 8: Build frontend**

Run: `cd frontend && npm run build`
Expected: clean build, no TypeScript errors.

- [ ] **Step 9: Run full backend suite**

Run: `.venv/Scripts/python.exe -m pytest tests/ -q --ignore=tests/test_backtest.py --ignore=tests/test_analytics_loader.py --ignore=tests/test_analytics_tools.py`
Expected: only the 5 known pre-existing failures.

- [ ] **Step 10: Commit**

```bash
git add app/api/server.py frontend/src/types.ts frontend/src/components/ConfigPanel.tsx app/api/static/
git commit -m "feat: signal_instrument in config API + frontend field"
```

---

## Self-review

**Spec coverage:**
- ✅ `signal_instrument: str | None = None` in `BotConfig` → Task 1 Step 3
- ✅ `save_bot_config` serializes it → Task 1 Step 3
- ✅ `signal_instrument: str` on `StrategyRunner` → Task 1 Step 4
- ✅ `_bar_router` on `ExecutionEngine` → Task 1 Step 5
- ✅ `get_historical_bars` explicit `instrument` param → Task 1 Step 7
- ✅ `_handle_bar` uses `_bar_router` for runner lookup → Task 2 Step 3
- ✅ `_snapshot_strategy_state` includes `signal_instrument` → Task 2 Step 5
- ✅ `publish_bar` `display_instrument` param → Task 2 Step 6
- ✅ `_build_runner` gets `signal_instrument` → Task 3 Step 1
- ✅ `_make_bar_journaler` re-labels with execution instrument → Task 3 Step 2
- ✅ `_run_live` subscribes to `signal_instrument` → Task 3 Step 3
- ✅ Engine construction passes `signal_instrument` → Task 3 Step 4
- ✅ Hot-reload path updates `_bar_router` → Task 3 Step 5
- ✅ `GET /api/config` includes `signal_instrument` → Task 4 Step 1
- ✅ `PATCH /api/config` includes `signal_instrument` → Task 4 Step 2
- ✅ `GET /api/bars` uses execution instrument explicitly → Task 4 Step 3
- ✅ `BotConfig` TypeScript type updated → Task 4 Step 4
- ✅ ConfigPanel form init and save wired → Task 4 Steps 5-6
- ✅ ConfigPanel UI field added → Task 4 Step 7
- ✅ Backward compat: `signal_instrument=None` → empty `_bar_router` → unchanged routing → Task 1 Step 5, Task 2 Step 3
- ✅ Paper mode hot-reload updates `_bar_router` → Task 3 Step 5
- ✅ Log line when `signal_instrument` differs from `instrument` → Task 3 Step 3

**Type consistency:**
- `signal_instrument: str = ""` on `StrategyRunner` (empty string = not set)
- `signal_instrument: str | None = None` on `BotConfig` (None = not set)
- `_build_runner(signal_instrument=bot_cfg.signal_instrument)` passes None; `signal_instrument=signal_instrument or ""` converts to empty string for the runner field. ✓
- `_bar_router` built with `if r.signal_instrument and r.signal_instrument != r.instrument` — empty string is falsy, so runner with `signal_instrument=""` is excluded. ✓
- `signal_instrument: string | null` in TypeScript; form stores `""` when blank; `handleSave` converts `""` to `null` via `|| null`. ✓
- `_effective_instrument` used in `GET /api/bars` — this variable is already in scope inside the `build_app` closure (it's set near the top of `build_app`). Verify before implementing.
