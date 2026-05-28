# Chart Timeframe Switcher — Design Spec

**Date:** 2026-05-28
**Status:** Approved

---

## Problem

The price chart always displays bars at the bot's configured trading timeframe. There is no way to switch to a different timeframe for visual context without restarting the bot or editing `bot_config.json`. The switcher must be purely cosmetic — it must not change the bot's trading timeframe or affect any strategy logic.

---

## Scope

Two files change:

| File | Change |
|------|--------|
| `app/api/server.py` | Add optional `timeframe` query param to `GET /api/bars` |
| `frontend/src/components/BarChart.tsx` | View TF state, TF buttons, fetch-on-switch, WebSocket gate |

No other files are touched. `bot_config.json`, the strategy, risk, broker, and all other API endpoints are unaffected.

---

## Backend

### `GET /api/bars`

**Before:** `async def get_bars(limit: int = 500)` — always fetches at `cfg.timeframes[0]`.

**After:** `async def get_bars(limit: int = 500, timeframe: str = "")` — if `timeframe` is non-empty, passes it to `get_historical_bars(timeframe=timeframe, limit=limit)`; otherwise falls back to `cfg.timeframes[0]` exactly as today.

No other changes to the endpoint. The bot's configured timeframe is never read from this param for any trading purpose.

---

## Frontend

### New state and refs in `BarChart`

| Name | Type | Purpose |
|------|------|---------|
| `viewTf` | `string` state | Currently displayed timeframe; defaults to `timeframe` prop (bot TF) on mount |
| `viewTfRef` | `MutableRefObject<string>` | Kept in sync with `viewTf` via a one-liner effect; read inside WebSocket closure to avoid stale captures |
| `seriesRef` | `MutableRefObject<CandlestickSeries \| null>` | Set inside the chart `useEffect`; lets the fetch effect call `setData()` without triggering chart remount |
| `chartRef` | `MutableRefObject<IChartApi \| null>` | Same pattern; used to call `timeScale().fitContent()` after loading new data |

### Effect: chart data on TF switch (`useEffect([viewTf])`)

Fires on mount and whenever the user picks a different TF. Fetches `/api/bars?timeframe=${viewTf}&limit=500`, then:
1. Calls `seriesRef.current.setData(bars)` to replace all candles
2. Calls `chartRef.current.timeScale().fitContent()` to re-fit the view
3. Clears `lastBarTimeRef` so forming-bar logic re-anchors correctly

The existing historical-bar fetch inside the main chart `useEffect` is removed — this new effect owns all chart data loading.

### WebSocket bar gate

Inside `callbacksRef.current.onBar`, add a guard:

```ts
if (viewTfRef.current !== timeframe) return
```

`timeframe` is the bot's trading TF (prop). When the user is viewing a different TF, live bar events are discarded for the chart. The bot still receives and processes them normally — only the chart display is gated.

### Forming-bar poll gate

Same guard: only call `fetchFormingBar` when `viewTf === timeframe`. When off-TF, the poll interval is still set up but the fetch is skipped with an early return.

### Countdown timer

Change the existing `useEffect([timeframe])` countdown to `useEffect([viewTf])` so the "next bar" countdown reflects the timeframe being viewed, not the bot's TF.

### TF selector buttons

Rendered in the existing chart header bar, to the left of the countdown. Buttons: **1m 3m 5m 15m 30m 1h** (matching the `TF_SECONDS` keys already in the file).

**Active TF:** accent green text (`text-accent`) with a subtle underline or solid background chip.

**Bot TF indicator:** a small `·` dot suffix on whichever button matches the bot's trading TF (the `timeframe` prop). This makes it unambiguous which TF the bot is running on even when you're viewing a different one.

**Style:** `text-[10px] font-mono uppercase tracking-widest` — consistent with the rest of the header. No border on individual buttons; consistent with the project's Tailwind border pattern.

---

## Data flow summary

```
User clicks "5m"
  → viewTf = "5min"
  → viewTfRef.current = "5min"
  → fetch /api/bars?timeframe=5min&limit=500
  → seriesRef.current.setData(bars)
  → chartRef.current.timeScale().fitContent()
  → countdown now counts to next 5-min boundary
  → WebSocket "bar" events at 1min are discarded for chart
  → forming-bar poll skipped

User clicks "1m" (bot TF)
  → viewTf = "1min"
  → fetch /api/bars?timeframe=1min&limit=500
  → WebSocket "bar" events resume updating chart
  → forming-bar poll resumes
```

---

## What does not change

- `bot_config.json` — never written
- Bot's trading timeframe — never altered
- Strategy, risk, broker — untouched
- `/api/config` PATCH — no new field
- All other API endpoints — unaffected
- VP histogram overlay — continues to draw from `/api/vp/profile`, unaffected by view TF
- Signal conditions panel — continues to poll `/api/setup_state`, unaffected

---

## Success criteria

- Clicking a TF button repopulates the chart with bars at that timeframe
- The bot's trading TF is always identifiable via the dot marker
- Switching TF does not trigger a restart, config change, or any broker call beyond the historical bar fetch
- Returning to the bot's TF re-enables live WebSocket bar updates
- `npm run build` passes with no TypeScript errors
