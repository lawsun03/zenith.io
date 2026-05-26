# Intrabar Recorder — Design Spec

**Date:** 2026-05-26
**Status:** Approved design, pending implementation
**Author:** brainstormed with Claude

## Problem

Trade post-mortems are blind to what price did *inside* each minute. On 2026-05-26
the bot took 5 losing MGC trades (≈ −$896); the user watched some go slightly into
profit before reversing, but there is no recorded data to confirm how far each trade
ran (max favorable excursion) or whether a breakeven/partial-profit rule would have
helped. The existing `bars_MGC.csv` is a stale one-time export, and the live bot only
persists 1-minute completed bars to the strategy — never the sub-minute price path.

## Goal

Continuously record a sub-minute price path for MGC to a CSV while the bot runs, at
high enough resolution to measure how far a trade moved into profit before exit.

Non-goals: live partial-profit/breakeven execution, multi-instrument recording,
frontend config, tick-level recording.

## Approach

The `_on_quote_update` handler in `TopstepXBroker` already maintains
`self._forming_bar` — a running OHLC of the current minute where `close` is the latest
bid/ask mid. A background asyncio task samples that snapshot every 5 seconds and
appends a row to a CSV. It is a pure observer: it reads existing state and never
touches order flow or the quote hot path.

### Resolution & coverage (decided)

- **Interval:** 5 seconds (module constant `_INTRABAR_SAMPLE_SECONDS = 5`).
- **Coverage:** continuous, whenever the bot is subscribed.
- **Instrument:** MGC only — naturally satisfied; the broker is single-instrument and
  MGC is the primary subscription.

### File

- **Single rolling file** `intrabar_MGC.csv` in the project root (same directory as
  `trades.csv`). Append-forever; header written once if the file does not exist.
- Named from the broker's primary instrument symbol, so it is `intrabar_<primary>.csv`
  in code (MGC today).

### Columns

```
sample_ts, instrument, bar_minute, open, high, low, close, volume
```

| Column      | Meaning |
|-------------|---------|
| `sample_ts` | UTC ISO timestamp the snapshot was taken (~every 5s) |
| `instrument`| primary symbol (MGC) |
| `bar_minute`| the forming bar's minute timestamp (UTC ISO) |
| `open`      | minute open (first mid of the minute) |
| `high`      | running high so far this minute |
| `low`       | running low so far this minute |
| `close`     | latest mid at sample time → the 5-second price series |
| `volume`    | running quote-tick count this minute |

A sequence of rows reconstructs the intra-minute price path: `close` gives the 5s
price series, and `high`/`low` give the running extremes used to compute max favorable
excursion per trade.

### Lifecycle

- `self._intrabar_task: asyncio.Task | None = None` initialized in `__init__`.
- Started at the **end of `subscribe()`**, after the QUOTE_UPDATE handler is wired
  (so `self._forming_bar` can populate).
- Loop body: `await asyncio.sleep(_INTRABAR_SAMPLE_SECONDS)`, then if
  `self._forming_bar is not None`, append one row.
- Cancelled and awaited in `disconnect()`.

### Error handling (Rule 12)

The row append is wrapped in try/except: a disk/serialization error logs at `ERROR`
with the exception and the loop continues. The sampler never crashes the broker and
never dies silently. A `CancelledError` exits the loop cleanly on disconnect.

## Out of scope (Rule 2 — simplicity)

- No `BotConfig` field, no frontend toggle, no PATCH hot-apply. Interval is a constant;
  promote to config later only if live tuning is needed.
- No multi-instrument support (broker is single-instrument today).
- No tick-level recording (rejected as hot-path I/O risk).

## Operational notes

- The bot is currently running (PID 15824). This change takes effect only after a
  **restart**; recording begins at the next launch and does not capture the current
  session retroactively.
- During illiquid stretches with no quotes, consecutive snapshots repeat the same
  forming bar — expected and harmless; `bar_minute` makes it explicit.

## Files touched

| Action | Path | Change |
|--------|------|--------|
| Modify | `app/broker/topstepx.py` | `_intrabar_task` init; module constant; sampler loop; start in `subscribe()`; cancel in `disconnect()`; CSV append helper |

## Success criteria

- After a bot restart, `intrabar_MGC.csv` appears in the project root and grows by
  roughly one row every 5 seconds while quotes flow.
- Rows contain a sane forming-bar OHLC with `close` tracking the latest mid.
- Stopping the bot (disconnect) cancels the task with no error spew.
- No measurable added latency to order placement (sampler is off the quote/order path).
