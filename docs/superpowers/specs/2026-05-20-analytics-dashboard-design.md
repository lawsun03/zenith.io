# Analytics Dashboard + Claude Advisor — Design Spec

**Date:** 2026-05-20

---

## Goal

Add a `/analytics` page to the dashboard that displays historical performance metrics derived from trade CSVs, backtest results, and logs — plus an on-demand Claude advisor that uses agentic tool-calling to investigate the data and produce specific parameter recommendations with reasoning.

## Architecture Overview

Two new subsystems bolt onto the existing FastAPI backend and React frontend without touching the trading path:

1. **`app/analytics/`** — backend data pipeline and Claude agentic loop
2. **`frontend/src/pages/Analytics.tsx` + supporting components** — the analytics UI

The existing `app/api/server.py` gets two new routes. The existing `App.tsx` nav gets an `ANALYTICS` link and a `/analytics` route.

---

## Data Sources

All data is read from files already on disk. No new database or cache.

| Source | Files | Notes |
|---|---|---|
| Live trades | `trades*.csv` in project root | UTF-8. All files merged. Older files (`trades.csv`) lack `stop`, `target`, `killzone`, `rationale` columns — loader must handle missing columns gracefully (treat as `None`). |
| Backtest summaries | `backtest_results/summary_run*.txt` | Plain text, structured format. 72+ runs. |
| Backtest equity | `backtest_results/equity_run*.csv` | Fields: `ts, equity, drawdown, drawdown_pct` |
| Current config | `bot_config.json` | Strategy params, entry mode, contracts, killzones |
| Logs | `logs/YYYY-MM-DD.log` | UTF-16 encoded (Windows PowerShell artifact). Last 2 days. |

---

## Backend — `app/analytics/`

### `loader.py`

Reads raw files and returns clean Python structures. No caching — the dataset is small enough that cold reads are instant.

**Functions:**
- `load_all_trades() -> list[dict]` — merges all `trades*.csv` files, normalises column names, parses timestamps and decimals
- `load_backtest_summaries() -> list[dict]` — parses all `summary_run*.txt` files into structured dicts (params, metrics, killzone breakdown)
- `load_log_events(days: int = 2) -> list[dict]` — reads last N daily log files with `encoding='utf-16'`, extracts log level, logger, message, timestamp
- `load_config() -> dict` — reads `bot_config.json`

### `tools.py`

Six tool functions Claude can call. Each is a plain Python function — no I/O side effects, no state mutation.

| Tool name | Input params | Returns |
|---|---|---|
| `get_performance_summary` | _(none)_ | Win rate, net P&L, gross profit, gross loss, profit factor, expectancy, avg winner, avg loser, max drawdown, total trades across all sessions |
| `get_killzone_breakdown` | _(none)_ | Per-killzone dict: trade count, win rate, net P&L, avg hold time |
| `get_recent_trades` | `limit: int = 50` | Last N trades: ts, side, killzone, fill price, size, realized P&L |
| `get_backtest_runs` | `sort_by: str = "profit_factor"`, `limit: int = 10` | Top backtest summaries with their generating params (swing_lookback, r_multiple, body_atr_multiple, etc.) and metrics |
| `get_current_config` | _(none)_ | Full strategy params from `bot_config.json` |
| `get_log_summary` | `days: int = 2` | Counts and examples of: VP filter rejections, signal denials by reason, errors, warnings |

Each tool is described to Claude with a JSON schema so Claude can call it with typed arguments.

### `advisor.py`

The agentic loop. Takes an optional user question string; if omitted, defaults to a general performance analysis prompt.

**Flow:**
1. Build initial system prompt: role (trading strategy advisor for TopstepX futures bot), context (instrument, account type, current date), and instruction to use tools before drawing conclusions.
2. Send to `claude-sonnet-4-6` with all six tool definitions via `anthropic` SDK.
3. Loop: if response contains tool calls, execute each via `tools.py`, send results back, repeat.
4. Cap at **10 tool rounds** to bound API cost per session.
5. Yield SSE events throughout:
   - `{"type": "tool_call", "name": "<tool>", "input": {...}}` — when Claude invokes a tool
   - `{"type": "tool_result", "name": "<tool>", "preview": "<first 200 chars of result>"}` — after execution
   - `{"type": "text_delta", "delta": "<chunk>"}` — as Claude streams its final text
   - `{"type": "done"}` — when complete
   - `{"type": "error", "message": "<msg>"}` — on API error or missing key

**Model:** `claude-sonnet-4-6`
**API key:** Read from `ANTHROPIC_API_KEY` env var. If missing, yield `{"type": "error", "message": "ANTHROPIC_API_KEY not set in .env"}` immediately.
**Streaming:** Use `anthropic` SDK's streaming API (`client.messages.stream()`).

---

## API Routes (additions to `server.py`)

### `GET /api/analytics/stats`

Returns pre-computed stats for initial page load. No Claude involved.

```json
{
  "performance": { "net_pnl": 272, "win_rate": 0.553, "profit_factor": 1.76, "expectancy": 16.6, "total_trades": 106, "max_drawdown": -321 },
  "killzones": {
    "london": { "trades": 47, "win_rate": 0.638, "net_pnl": 1000 },
    "ny_am":  { "trades": 35, "win_rate": 0.486, "net_pnl": 544 },
    "ny_pm":  { "trades": 21, "win_rate": 0.476, "net_pnl": 162 }
  },
  "recent_trades": [ ... ]
}
```

### `POST /api/analytics/ask-claude`

SSE endpoint. Request body: `{"question": "optional user question"}`. Streams events from `advisor.py` until `done` or `error`.

---

## Frontend

### Routing

`App.tsx` adds `/analytics` route. Nav header gets `ANALYTICS` link alongside `LIVE` and `CONFIG`, using the existing nav tab pattern.

### New files

**`frontend/src/pages/Analytics.tsx`**
Top-level page component. Fetches `/api/analytics/stats` on mount. Owns the stacked layout: stats row → killzone table → recent trades → Claude advisor. All existing Tailwind + hacker theme conventions apply.

**`frontend/src/components/StatsRow.tsx`**
Five stat cards in a grid: Net P&L, Win Rate, Profit Factor, Expectancy, Total Trades. P&L shown green if positive, red if negative. Subtext shows scope ("all sessions" or "today").

**`frontend/src/components/KillzoneTable.tsx`**
Grid showing per-killzone: trade count, win rate (coloured by performance), net P&L. Only shows killzones with at least 1 trade.

**`frontend/src/components/TradesTable.tsx`**
Scrollable list of last 50 trades. Columns: time (PT), side (green/red), killzone, size, P&L. Matches existing fill table style in the live dashboard.

**`frontend/src/components/ClaudeAdvisor.tsx`**
The Claude panel. Three internal states:

- **`idle`** — "Ask Claude" button visible. Optional text input for a custom question (placeholder: "e.g. Why is NY AM underperforming?"). If no question entered, sends a general analysis request.
- **`investigating`** — Button disabled. Investigation log appears: each `tool_call` event adds a line (`▶ get_killzone_breakdown() · running...`), updated to `· done` on `tool_result`. Uses `fetch` + `ReadableStream` to consume the SSE stream (not `EventSource` — `EventSource` only supports GET; the advisor endpoint is POST).
- **`responding`** — Tool log collapses to a summary line. Claude's recommendation text streams in, rendered as preformatted monospace. Cursor blink while streaming.
- **`done`** — Full response visible. "Ask again" button resets to idle.
- **`error`** — Error message displayed (including the friendly ANTHROPIC_API_KEY not set message).

---

## `.env` Change Required

```
ANTHROPIC_API_KEY=sk-ant-api03-...
```

Get key from console.anthropic.com. Add to `.env` alongside existing `TOPSTEP_*` vars. Never committed.

---

## What Claude Does NOT Have Access To

- Real-time market data or live prices
- The ability to place orders or change config directly (read-only tools only)
- More than the last 2 days of logs (to keep prompt size bounded)
- Backtest equity curves (summaries only — equity CSVs are too large for tool responses)

---

## Success Criteria

- `/analytics` renders with stats populated from trade CSVs on page load
- "Ask Claude" fires the SSE stream; tool calls appear in the investigation log in real time
- Claude's recommendation streams in as text after tool calls complete
- If `ANTHROPIC_API_KEY` is missing, a clear error message appears in the Claude panel (not a broken spinner)
- No changes to trading path, broker, engine, or risk modules
- `npm run build` clean, no TS errors

---

## Out of Scope

- Persistent storage of past Claude recommendations
- Scheduling automatic EOD Claude runs
- Equity curve chart (SVG/canvas) — stats only for now
- Backtest parameter sweep UI
- Editing config from the analytics page
