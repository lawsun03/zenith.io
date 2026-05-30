# Backtests Page — StrategyGrader Display + Databento Integration

**Date:** 2026-05-29  
**Status:** Approved

---

## Problem

1. The backtest runner already calls `SetupGrader` per signal but throws away the grade. There is no way to see whether trades came from A+ setups vs B- setups in the UI.
2. The Databento fetch script (`scripts/fetch_bars_databento.py`) exists but has no UI entry point. Fetching more historical data requires dropping to a terminal.

---

## Goals

- Surface grade data (A+/A/A-/B/B-) and per-criterion pass/fail in the backtest results view.
- Let Lawrence filter the trade list by grade tier to compare A+ performance vs A- etc.
- Add a Databento data-source toggle directly inside the Run Backtest panel, with cost estimate and cache awareness.

---

## Out of Scope

- Grader logic changes (grader.py is not touched).
- Forming-bar replay fidelity (separate sub-project per 2026-05-27 scope doc).
- Streaming fetch progress via SSE (fetch is fast enough for a synchronous response).

---

## Design

### 1. Backend — `backtest.py`: emit grade per signal

The grader already runs inside `run_backtest()` and its result is used to filter signals. We store two additional fields on each signal dict before writing the output JSON:

```python
signal_dict["grade"]    = grade.grade      # str: "A+", "A", "A-", "B", "B-"
signal_dict["criteria"] = {                # which of the 5 axes passed
    "mom": grade.momentum_ok,   # verify exact field names against SetupGrade in grader.py
    "tgt": grade.target_ok,
    "fvg": grade.fvg_ok,
    "pd":  grade.pd_ok,
    "del": grade.delivery_ok,
}
```

`SetupGrade` already has these boolean fields — we just serialize them. No logic change. **Implementation note:** read `grader.py` to confirm the exact attribute names on `SetupGrade` before writing the serialization code.

The `Trade` object (paired entry/exit) inherits its grade from the corresponding signal. When reconstructing trades, copy `grade` and `criteria` from the signal onto the trade dict.

### 2. Backend — `server.py`: `/api/databento/fetch` endpoint

```
POST /api/databento/fetch
Body: { "start": "YYYY-MM-DD", "end": "YYYY-MM-DD", "symbol": "MGC", "dry_run": false }
Returns: { "ok": true, "days_fetched": 5, "cached_through": "2026-05-29", "cost_estimate": 0.12 }
         { "ok": false, "reason": "DATABENTO_API_KEY not set" }

When dry_run=true: skips actual download, returns cost_estimate + cached_through only.
```

**Cache check:** read the existing `bars_{symbol}.csv` (if present), find its max timestamp date. If that date >= `end`, return `ok: true, days_fetched: 0` (full cache hit — no fetch needed). Otherwise fetch only the missing tail.

**Subprocess:** calls `scripts/fetch_bars_databento.py` with `--symbol`, `--start`, `--end` flags, captured synchronously (the script typically completes in < 5s for a few days of 1min bars).

**Cost estimate:** returned by the script via stdout JSON; endpoint passes it through to the frontend.

**Key guard:** if `DATABENTO_API_KEY` is absent from env, return `{ ok: false, reason: "DATABENTO_API_KEY not set" }` immediately.

### 3. Frontend — `BacktestsPage.tsx`: data source toggle

**New state:**
```ts
type DataSource = 'local' | 'databento'
const [dataSource, setDataSource] = useState<DataSource>('local')
const [bentoMeta, setBentoMeta] = useState<{ cost: number; cachedThrough: string | null; willFetch: number } | null>(null)
```

**New UI row** — inserted between the date range inputs and the Run button:

```
DATA SOURCE   [ Local CSV ]  [ Databento ]
              (when Databento selected):
              est. cost: ~$0.12 · cached through: 2026-05-28 · will fetch 1 new day
```

Switching to Databento immediately calls `POST /api/databento/fetch` with `{ ..., dry_run: true }` to get the cost estimate without downloading anything. On Run:

1. If `dataSource === 'databento'`: call `POST /api/databento/fetch` first, await response.
2. If `ok: false`: show error, abort.
3. Then call `POST /api/backtest/run` as normal (it will find the updated CSV).

### 4. Frontend — `BacktestsPage.tsx`: grade scorecard + filtered trade list

**New types:**
```ts
interface GradeCriteria { mom: boolean; tgt: boolean; fvg: boolean; pd: boolean; del: boolean }

interface Trade {          // extends existing
  grade?: string
  criteria?: GradeCriteria
}
```

**Grade scorecard** — rendered at the top of the backtest detail panel when a result is selected. One tile per tier (A+, A, A-, B, B-), each showing:
- Tier label
- Trade count for that tier
- Win rate (wins / count)
- Profit factor (gross_win / gross_loss) for that tier

Clicking a tile sets `gradeFilter: string | null`. Clicking the active tile clears the filter (shows all). No multi-select — one grade at a time.

**Trade list** — existing rows gain two additions:
- Grade badge (colored chip: A+ = bright green, A = green, A- = yellow-green, B = orange, B- = red)
- Criteria row below entry/exit: `mom✓ tgt✓ fvg✓ p/d✓ del✓` — failed criteria render in red with `✗`

Filter is applied client-side: `trades.filter(t => gradeFilter === null || t.grade === gradeFilter)`.

**Header label** updates when filtered: `TRADES · A+ only (4 of 16)` vs `TRADES (16)`.

---

## File Inventory

| File | Change |
|---|---|
| `app/backtest.py` | Add `grade` + `criteria` to each signal dict; copy to trade dict |
| `app/api/server.py` | Add `POST /api/databento/fetch` endpoint |
| `frontend/src/components/BacktestsPage.tsx` | Data source toggle, grade scorecard tiles, grade badge + criteria on trade rows, grade filter state |

No new files. No changes to `grader.py`, `bot_config.py`, or any other backend module.

---

## Success Criteria

- Running a backtest produces JSON with `grade` and `criteria` on every signal/trade.
- The grade scorecard renders in the detail view with correct counts, win rates, and profit factors per tier.
- Clicking a grade tile filters the trade list; clicking again clears.
- Selecting "Databento" in the run panel shows a cost/cache line and triggers a fetch before the backtest runs.
- `POST /api/databento/fetch` returns fast (< 10s) and skips the network call on a cache hit.
- No console errors, `npm run build` clean.
