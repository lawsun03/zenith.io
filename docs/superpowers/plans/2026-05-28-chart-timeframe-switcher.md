# Chart Timeframe Switcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add TF selector buttons (1m 3m 5m 15m 30m 1h) to the price chart header that repopulate the chart with bars at the chosen timeframe, without touching the bot's trading configuration.

**Architecture:** Backend gains an optional `timeframe` query param on `GET /api/bars`. Frontend holds `viewTf` state local to `BarChart`; a dedicated `useEffect([viewTf])` owns all historical bar fetching; WebSocket bar updates and the forming-bar poll are gated behind a ref check so they only apply when viewing the bot's trading TF.

**Tech Stack:** Python/FastAPI (backend), React + TypeScript + Tailwind (frontend), lightweight-charts (chart library)

---

## File Map

| File | Change |
|------|--------|
| `app/api/server.py` | Add `timeframe: str = ""` param to `get_bars`; use it when non-empty |
| `frontend/src/components/BarChart.tsx` | viewTf state + ref, seriesRef, chartRef, TF buttons, gated WebSocket/forming-bar, new fetch effect |
| `tests/test_api.py` | Test that `/api/bars` accepts a `timeframe` query param and returns `{"bars": []}` in non-live mode regardless |

---

## Task 1: Backend — add `timeframe` param to `GET /api/bars`

**Files:**
- Modify: `app/api/server.py` (around line 532)
- Modify: `tests/test_api.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_api.py` after the existing `/api/bars` tests (or at the end of the file):

```python
async def test_bars_accepts_timeframe_param():
    """
    /api/bars must accept a ?timeframe= query param.
    In paper/non-live mode the broker is None so bars is always [].
    The test confirms the param is wired (no 422 Unprocessable Entity)
    and the response shape is correct regardless of TF.
    """
    app, _, _, _, _ = await make_app_with_state()
    client = TestClient(app)
    for tf in ["1min", "5min", "15min", "1h"]:
        r = client.get(f"/api/bars?timeframe={tf}&limit=10")
        assert r.status_code == 200, f"Expected 200 for timeframe={tf}, got {r.status_code}"
        body = r.json()
        assert "bars" in body, f"Missing 'bars' key for timeframe={tf}"
        assert isinstance(body["bars"], list)
```

- [ ] **Step 2: Run test to confirm it fails**

```
cd C:\Users\Lawrence\Documents\topstep-trader-bot\topstep-bot
.venv\Scripts\python -m pytest tests/test_api.py::test_bars_accepts_timeframe_param -v
```

Expected: `FAILED` — FastAPI returns 422 because `timeframe` is not a known param yet.

- [ ] **Step 3: Add `timeframe` param to `get_bars`**

In `app/api/server.py`, replace:

```python
    @app.get("/api/bars")
    async def get_bars(limit: int = 500) -> JSONResponse:
        """
        Recent historical bars for chart pre-population. Live mode only.
        Returns up to `limit` bars at the currently-configured timeframe.
        """
        if _mode != "live" or _broker is None:
            return JSONResponse({"bars": []})
        try:
            cfg = load_bot_config(_bot_config_path)
            tf = (cfg.timeframes or _effective_timeframes)[0]
            bars = await _broker.get_historical_bars(timeframe=tf, limit=limit)
```

With:

```python
    @app.get("/api/bars")
    async def get_bars(limit: int = 500, timeframe: str = "") -> JSONResponse:
        """
        Recent historical bars for chart pre-population. Live mode only.
        Returns up to `limit` bars at the requested timeframe, or the
        bot's configured timeframe if not specified.
        """
        if _mode != "live" or _broker is None:
            return JSONResponse({"bars": []})
        try:
            cfg = load_bot_config(_bot_config_path)
            tf = timeframe or (cfg.timeframes or _effective_timeframes)[0]
            bars = await _broker.get_historical_bars(timeframe=tf, limit=limit)
```

- [ ] **Step 4: Run test to confirm it passes**

```
.venv\Scripts\python -m pytest tests/test_api.py::test_bars_accepts_timeframe_param -v
```

Expected: `PASSED`

- [ ] **Step 5: Run full test suite to confirm nothing regressed**

```
.venv\Scripts\python -m pytest tests/ -x -q
```

Expected: all pass (or same failures as before this change).

- [ ] **Step 6: Commit**

```
git add app/api/server.py tests/test_api.py
git commit -m "feat: accept timeframe param on GET /api/bars"
```

---

## Task 2: Frontend — state, refs, and TF_LABELS constant

**Files:**
- Modify: `frontend/src/components/BarChart.tsx`

- [ ] **Step 1: Add `TF_LABELS` constant**

`TF_SECONDS` already exists at the top of the file. Add `TF_LABELS` immediately after it:

```tsx
const TF_SECONDS: Record<string, number> = {
  '1min': 60, '3min': 180, '5min': 300,
  '15min': 900, '30min': 1800, '1h': 3600,
}

const TF_LABELS: Record<string, string> = {
  '1min': '1m', '3min': '3m', '5min': '5m',
  '15min': '15m', '30min': '30m', '1h': '1h',
}
```

- [ ] **Step 2: Add `viewTf` state and refs inside `BarChart`**

In `BarChart`, the current state declarations are:

```tsx
  const containerRef = useRef<HTMLDivElement>(null)
  const lastBarTimeRef = useRef<number | null>(null)
  const [countdown, setCountdown] = useState<string | null>(null)
  const [setupState, setSetupState] = useState<SetupState | null>(null)
```

Replace with:

```tsx
  const containerRef = useRef<HTMLDivElement>(null)
  const lastBarTimeRef = useRef<number | null>(null)
  const [countdown, setCountdown] = useState<string | null>(null)
  const [setupState, setSetupState] = useState<SetupState | null>(null)
  const [viewTf, setViewTf] = useState<string>(timeframe ?? '1min')
  const viewTfRef = useRef<string>(timeframe ?? '1min')
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const seriesRef = useRef<any>(null)
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const chartRef = useRef<any>(null)
```

- [ ] **Step 3: Add a sync effect to keep `viewTfRef` current**

Add this effect immediately after the `pollSetupState` effect (around line 215):

```tsx
  // Keep viewTfRef in sync so the chart useEffect closure reads fresh values.
  useEffect(() => { viewTfRef.current = viewTf }, [viewTf])
```

- [ ] **Step 4: TypeScript check**

```
cd frontend
npm run build 2>&1 | Select-String -Pattern "error" -CaseSensitive
```

Expected: no TypeScript errors. If there are, fix them before continuing.

---

## Task 3: Frontend — wire `seriesRef`/`chartRef` into the chart effect, remove old bar fetch

**Files:**
- Modify: `frontend/src/components/BarChart.tsx`

The main chart `useEffect` (deps `[callbacksRef]`) currently creates the chart and series, then immediately fetches `/api/bars`. This task moves the fetch out (Task 4 owns it) and wires the refs.

- [ ] **Step 1: Set `seriesRef` and `chartRef` immediately after creation**

Inside the `useEffect`, find the lines that create `chart` and `series`:

```tsx
    const chart = createChart(el, { ... })

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const series = chart.addSeries(CandlestickSeries as any, { ... })
```

Add ref assignments immediately after each:

```tsx
    const chart = createChart(el, { ... })
    chartRef.current = chart

    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    const series = chart.addSeries(CandlestickSeries as any, { ... })
    seriesRef.current = series
```

- [ ] **Step 2: Remove the old historical-bar fetch from the chart effect**

Find and delete this entire block (around line 389):

```tsx
    // Pre-populate the chart with historical bars so it's not empty on connect.
    fetch('/api/bars?limit=500')
      .then(r => r.json())
      .then(d => {
        if (Array.isArray(d.bars) && d.bars.length > 0) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          series.setData(d.bars as any)
          chart.timeScale().fitContent()
        }
      })
      .catch(() => {})
```

The `[viewTf]` effect in Task 4 will own this responsibility.

- [ ] **Step 3: Clear refs in the cleanup function**

Inside the `useEffect` return (the cleanup), add before `chart.remove()`:

```tsx
    return () => {
      clearInterval(syncId)
      clearInterval(vpRefetchId)
      clearInterval(formingBarId)
      chart.timeScale().unsubscribeVisibleLogicalRangeChange(drawHistogram)
      if (el.contains(vpCanvas)) el.removeChild(vpCanvas)
      callbacksRef.current = {}
      seriesRef.current = null   // ← add
      chartRef.current = null    // ← add
      chart.remove()
    }
```

- [ ] **Step 4: TypeScript check**

```
cd frontend
npm run build 2>&1 | Select-String -Pattern "error" -CaseSensitive
```

Expected: no new errors.

---

## Task 4: Frontend — `[viewTf]` data fetch effect

**Files:**
- Modify: `frontend/src/components/BarChart.tsx`

- [ ] **Step 1: Add the fetch effect after the `viewTfRef` sync effect**

Place this effect directly after `useEffect(() => { viewTfRef.current = viewTf }, [viewTf])`:

```tsx
  // Re-populate the chart whenever the viewed timeframe changes.
  // This effect owns all historical bar loading (the chart useEffect no longer fetches).
  useEffect(() => {
    if (!seriesRef.current || !chartRef.current) return
    fetch(`/api/bars?timeframe=${viewTf}&limit=500`)
      .then(r => r.json())
      .then(d => {
        if (!seriesRef.current || !chartRef.current) return
        if (Array.isArray(d.bars) && d.bars.length > 0) {
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          seriesRef.current.setData(d.bars as any)
          chartRef.current.timeScale().fitContent()
        }
        // Reset forming-bar anchor so off-TF bars don't show stale data.
        lastBarTimeRef.current = null
      })
      .catch(() => {})
  }, [viewTf])
```

- [ ] **Step 2: TypeScript check**

```
cd frontend
npm run build 2>&1 | Select-String -Pattern "error" -CaseSensitive
```

Expected: no errors.

---

## Task 5: Frontend — gate WebSocket updates and forming-bar poll, fix countdown

**Files:**
- Modify: `frontend/src/components/BarChart.tsx`

- [ ] **Step 1: Gate `onBar` in `callbacksRef`**

Inside the main chart `useEffect`, find `callbacksRef.current = { onBar(bar) { ... }, ... }`. Modify only the `onBar` handler:

```tsx
      onBar(bar) {
        // Only update chart when viewing the bot's trading TF.
        if (viewTfRef.current !== timeframe) return
        lastBarTimeRef.current = bar.time
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        series.update({ time: bar.time as any, open: bar.open, high: bar.high, low: bar.low, close: bar.close })
      },
```

- [ ] **Step 2: Gate the forming-bar fetch**

Find the `fetchFormingBar` function inside the chart `useEffect`:

```tsx
    const fetchFormingBar = () => {
      fetch('/api/forming-bar')
        .then(r => r.json())
        .then((b: { time: number; open: number; high: number; low: number; close: number } | null) => {
          if (!b) return
          if (lastBarTimeRef.current !== null && b.time < lastBarTimeRef.current) return
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          series.update({ time: b.time as any, open: b.open, high: b.high, low: b.low, close: b.close })
        })
        .catch(() => {})
    }
```

Add an early return at the top:

```tsx
    const fetchFormingBar = () => {
      // Forming bar only makes sense at the bot's trading TF.
      if (viewTfRef.current !== timeframe) return
      fetch('/api/forming-bar')
        .then(r => r.json())
        .then((b: { time: number; open: number; high: number; low: number; close: number } | null) => {
          if (!b) return
          if (lastBarTimeRef.current !== null && b.time < lastBarTimeRef.current) return
          // eslint-disable-next-line @typescript-eslint/no-explicit-any
          series.update({ time: b.time as any, open: b.open, high: b.high, low: b.low, close: b.close })
        })
        .catch(() => {})
    }
```

- [ ] **Step 3: Fix countdown to use `viewTf` instead of `timeframe` prop**

Find the countdown effect. Currently:

```tsx
  useEffect(() => {
    const tfSecs = TF_SECONDS[timeframe ?? ''] ?? null
    if (!tfSecs) { setCountdown(null); return }
    const tick = () => {
      ...
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [timeframe])
```

Replace `timeframe ?? ''` with `viewTf` and change the deps array:

```tsx
  useEffect(() => {
    const tfSecs = TF_SECONDS[viewTf] ?? null
    if (!tfSecs) { setCountdown(null); return }
    const tick = () => {
      const nowSecs = Math.floor(Date.now() / 1000)
      const nextBoundary = (Math.floor(nowSecs / tfSecs) + 1) * tfSecs
      const remaining = nextBoundary - nowSecs
      const m = Math.floor(remaining / 60)
      const s = remaining % 60
      setCountdown(m > 0 ? `${m}:${String(s).padStart(2, '0')}` : `${s}s`)
    }
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [viewTf])
```

- [ ] **Step 4: TypeScript check**

```
cd frontend
npm run build 2>&1 | Select-String -Pattern "error" -CaseSensitive
```

Expected: no errors.

---

## Task 6: Frontend — TF selector buttons

**Files:**
- Modify: `frontend/src/components/BarChart.tsx`

- [ ] **Step 1: Add TF buttons to the chart header**

Find the header `<div>` inside the `return` statement. It currently looks like:

```tsx
      {/* Header bar */}
      <div className="px-4 py-2 border-b border-border flex items-center justify-between">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Price Chart</span>
        <div className="flex items-center gap-3">
          {formingHot && (
            ...
          )}
          {countdown !== null && (
            ...
          )}
        </div>
      </div>
```

Replace with:

```tsx
      {/* Header bar */}
      <div className="px-4 py-2 border-b border-border flex items-center justify-between">
        <span className="text-[10px] tracking-[0.3em] text-dim uppercase">Price Chart</span>
        <div className="flex items-center gap-3">
          {/* TF selector */}
          <div className="flex items-center gap-0.5">
            {Object.keys(TF_SECONDS).map(tf => (
              <button
                key={tf}
                onClick={() => setViewTf(tf)}
                className={[
                  'text-[10px] font-mono px-1.5 py-0.5 rounded-sm transition-colors',
                  viewTf === tf
                    ? 'text-accent bg-accent/10'
                    : 'text-dim hover:text-ink',
                ].join(' ')}
              >
                {TF_LABELS[tf]}{tf === timeframe ? '·' : ''}
              </button>
            ))}
          </div>
          {formingHot && (
            <span className="flex items-center gap-1 text-[10px] font-mono text-yellow-400 animate-pulse">
              <span className="inline-block w-1.5 h-1.5 rounded-full bg-yellow-400" />
              SETUP
            </span>
          )}
          {countdown !== null && (
            <span className="text-[10px] font-mono tabular-nums text-dim">
              next bar{' '}
              <span className={countdown === 'now' ? 'text-accent animate-pulse-soft' : 'text-ink'}>
                {countdown}
              </span>
            </span>
          )}
        </div>
      </div>
```

- [ ] **Step 2: Final TypeScript build check**

```
cd frontend
npm run build 2>&1 | Select-String -Pattern "error" -CaseSensitive
```

Expected: clean build, no TypeScript errors.

- [ ] **Step 3: Visual verification**

Start the bot (or use paper mode) and open the dashboard at `http://localhost:5173`. Verify:

1. Six TF buttons appear in the chart header: `1m· 3m 5m 15m 30m 1h` (dot on the bot's current TF)
2. Clicking `5m` repopulates the chart with 5-minute candles and the countdown shows the next 5-min boundary
3. The `·` dot stays on `1m` (the bot's TF) regardless of which TF is selected
4. Clicking back to `1m` restores live WebSocket bar updates (candle updates every minute)
5. `bot_config.json` is unchanged after clicking any button

- [ ] **Step 4: Commit**

```
git add frontend/src/components/BarChart.tsx
git commit -m "feat: TF selector buttons on price chart (view-only, bot TF unaffected)"
```

---

## Self-Review Checklist

**Spec coverage:**
- ✅ `GET /api/bars` accepts `timeframe` param → Task 1
- ✅ `viewTf` state defaults to bot TF → Task 2
- ✅ `viewTfRef` kept in sync → Task 2
- ✅ `seriesRef` / `chartRef` set in chart effect → Task 3
- ✅ Old bar fetch removed from chart effect → Task 3
- ✅ `[viewTf]` fetch effect owns data loading → Task 4
- ✅ `lastBarTimeRef` cleared on TF switch → Task 4
- ✅ WebSocket `onBar` gated by `viewTfRef` → Task 5
- ✅ Forming-bar poll gated by `viewTfRef` → Task 5
- ✅ Countdown uses `viewTf` → Task 5
- ✅ TF buttons rendered left of countdown → Task 6
- ✅ Active TF highlighted, bot TF marked with `·` → Task 6
- ✅ `npm run build` clean check → Tasks 2–6

**No placeholders:** all steps include exact code. ✅

**Type consistency:** `viewTfRef.current`, `seriesRef.current`, `chartRef.current` used consistently across Tasks 3–6. `TF_LABELS` defined in Task 2, used in Task 6. ✅
