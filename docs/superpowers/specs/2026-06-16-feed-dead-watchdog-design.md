# Feed-Dead Watchdog — Design Spec

**Date:** 2026-06-16
**Status:** Approved (pending spec review)
**Goal:** Detect when the live market-data feed goes silent during expected-bar hours and alert the operator (Discord + email) once on death and once on recovery — so a silent feed failure (e.g. the SignalR eviction storm that ran unnoticed for hours) is caught in minutes, not hours.

---

## Problem

The live bot drives everything off incoming bars. If the data feed dies (SignalR eviction, broker disconnect, network), the engine simply stops receiving bars — no error, no trades, no signal. This has happened and run silently for hours. There is currently no in-process detector: the only signal is the operator noticing the dashboard is stale. We need an in-process watchdog that distinguishes a *dead feed* from the market being *legitimately quiet* (overnight break, weekend, holiday) and alerts on real death.

## Decisions (from brainstorming)

1. **Suppression:** full session-aware. Only evaluate the gap when bars are genuinely expected (active CME hours), suppressing the daily maintenance break, weekends, and holiday/early-close periods.
2. **Threshold:** timeframe-relative — `max(3 × timeframe, 10 min)` with no bar (≈3 missed bars at 5min). Self-adjusts if the timeframe changes; tolerates one slow bar.
3. **Alerting:** both Discord and email, alert-once on death + once on recovery (no re-alert while DEAD, no spam per tick).
4. **Observability (Rule 13):** push watchdog state on the SSE stream (independent of bar arrival) and render it in the StrategyDebug panel.

## Non-goals (YAGNI)

- No per-instrument watchdog — one feed status for the process (all instruments share one feed).
- No auto-reconnect — detect + alert only; reconnection is the broker's responsibility.
- No dashboard header health-dot — possible follow-up once the backend state exists.
- No change to trading/risk logic — this is pure observation + notification.

---

## Architecture

The watchdog lives in `ExecutionEngine`, mirroring the existing wall-clock `_flatten_clock` task. Three pieces:

### 1. Last-bar stamp
`ExecutionEngine._handle_bar` (engine.py:637) records `self._last_bar_at` = wall-clock `datetime.now(tz=UTC)` on every bar processed. (Wall-clock arrival time, NOT `bar.ts` — we care when data last *arrived*, not the bar's market timestamp.)

### 2. Watchdog clock task
A new `_watchdog_clock` coroutine, created in `start()` when `feed_watchdog_enabled` (guarded like `_flatten_task`), cancelled in `stop()`. Loop: every 30s,
1. Compute `gap_s = (now - self._last_bar_at).total_seconds()` (if `_last_bar_at` is None — no bar yet since start — treat as not-yet-armed: status `LIVE`, no alert).
2. Determine `expected = bars_expected(now)` (Section "Session gate").
3. Run the state machine (Section "State machine").
4. Push the watchdog state onto the SSE queue (Section "Observability").

The 30s cadence reuses the flatten clock's interval; threshold detection at 30s granularity is fine for a minutes-scale gap.

### 3. Alert callback (decoupled)
The engine exposes an optional `on_feed_status: Callable[[FeedStatusEvent], Awaitable[None]] | None` (same decoupling pattern as `on_signal`). The engine never imports the notifiers; `main.py` wires `on_feed_status` to call `DiscordNotifier` + `EmailNotifier`. `FeedStatusEvent` carries `{status: "dead"|"recovered", last_bar_at: datetime, gap_seconds: float}`.

---

## Session gate — `bars_expected(ts) -> bool`

Pure function in `app/risk/flatten.py` (reuses `_EARLY_CLOSE_DATES`, `_SESSION_OPEN = 17:00 CT`, `trading_day_ct`). CME NQ/GC trade Sun 17:00 CT → Fri 16:00 CT with a daily maintenance break 16:00–17:00 CT. Bars are NOT expected when any of:

- **Daily maintenance break:** 16:00 ≤ CT-time < 17:00.
- **Weekend gap:** from Fri 16:00 CT to Sun 17:00 CT. (Saturday all day; Friday ≥16:00; Sunday <17:00.)
- **Early-close days:** on a `trading_day_ct(ts) in _EARLY_CLOSE_DATES`, no bars are expected from the noon close (12:00 CT) to the 17:00 CT reopen.

Otherwise `True`. DST-correct via the existing `CT` zone. This helper is independent and unit-testable in isolation.

---

## State machine

Three states tracked as `self._feed_status` ∈ {`LIVE`, `QUIET_EXPECTED`, `DEAD`}, initialized `LIVE`.

Per tick, given `gap_s`, `expected`, `threshold_s = max(3 * _tf_seconds(timeframe), 600)`:

| Current | Condition | Next | Side effect |
|---------|-----------|------|-------------|
| LIVE / QUIET_EXPECTED | `not expected` | QUIET_EXPECTED | none (suppressed) |
| LIVE / QUIET_EXPECTED | `expected and gap_s > threshold_s` | DEAD | log WARNING; `on_feed_status(dead)` once |
| LIVE / QUIET_EXPECTED | `expected and gap_s <= threshold_s` | LIVE | none |
| DEAD | a bar has arrived since (gap_s small, i.e. `gap_s <= threshold_s`) | LIVE | log INFO; `on_feed_status(recovered)` once |
| DEAD | still no bar | DEAD | none (no re-alert) |

Transition side effects fire **only on the edge**, giving alert-once + recovery-once. `QUIET_EXPECTED` never alerts; it exists so the UI can show "quiet (expected)" distinctly from a live feed.

Recovery is keyed on a real bar arriving (which updates `_last_bar_at` via `_handle_bar`), so `DEAD→LIVE` cannot be spoofed by the clock alone. Note a death that begins while expected and persists into a break: it stays DEAD (we already alerted); when the break starts we do NOT downgrade to QUIET_EXPECTED (avoid implying recovery). Refinement: only `LIVE/QUIET_EXPECTED → QUIET_EXPECTED` on `not expected`; once `DEAD`, only a real bar clears it. (Captured in the table's first row applying to LIVE/QUIET_EXPECTED only.)

---

## Observability (Rule 13)

**Deliberate exception to bar-driven `strategy_state`:** because the watchdog must report status when bars have stopped, the `_watchdog_clock` tick pushes its own small block onto the same SSE queue every tick (not inside the bar handler):

```json
{
  "type": "feed_watchdog",
  "status": "live",            // "live" | "quiet" | "dead"
  "last_bar_at": "2026-06-16T20:31:00+00:00",
  "seconds_since": 47.0,
  "threshold_s": 900,
  "expected": true
}
```

`frontend/src/components/StrategyDebug.tsx` subscribes and renders a labeled row: status (green `live` / grey `quiet` / red `DEAD`), seconds-since-last-bar, and threshold. The `useStream` hook already multiplexes SSE event types; add `feed_watchdog` handling alongside `strategy_state`.

**Backend log lines (Rule 13 layer 1):** WARNING on `→DEAD` (`"FEED DEAD: no bar for %ds (threshold %ds), last bar %s"`), INFO on `→LIVE recovery` (`"FEED RECOVERED: bar arrived after %ds dark"`).

---

## Config

- `BotConfig.feed_watchdog_enabled: bool = True`. Single flag; the threshold is derived from the timeframe, not separately configured (YAGNI). Persisted in `save_bot_config`, returned in `GET /api/config`, hot-applied in `PATCH /api/config` (start/stop the task on toggle, mirroring `flatten_wallclock_enabled`), surfaced in the ConfigPanel toggle + frontend `types.ts`.

---

## Error handling

- The watchdog never raises into the engine loop: the tick body is wrapped in try/except logging at ERROR (mirrors `_poll_forming_bars` discipline). A watchdog failure must never stop trading or flatten.
- `on_feed_status` callback exceptions are caught and logged (notifier down ≠ watchdog down).
- If `feed_watchdog_enabled` is False, the task is never created (log says so at startup, mirroring the email-notifier-disabled line).

---

## Testing (Rule 9)

`tests/test_feed_watchdog.py`, deterministic via an injected `now` (no real sleeping):

- **`bars_expected`** — true mid-session; false during 16:00–17:00 CT break; false Sat/Fri-evening/Sun-pre-17:00; false after noon on an `_EARLY_CLOSE_DATES` day; DST boundary (CDT vs CST) correctness.
- **Threshold** — `max(3×tf, 10min)`: 5min→900s, 1min→600s (floor).
- **State machine** — LIVE→DEAD when expected & gap>threshold (asserts one `on_feed_status(dead)`); staying DEAD does not re-alert; DEAD→LIVE on bar arrival (asserts one recovery); QUIET_EXPECTED suppresses alerts during the break even with a large gap; a not-yet-armed engine (`_last_bar_at is None`) never alerts.
- **Callback decoupling** — a stub `on_feed_status` records calls; assert exactly one death + one recovery across a full cycle.
- **Engine integration** — `_handle_bar` updates `_last_bar_at`; toggling `feed_watchdog_enabled` off means no task (mirrors `test_flatten_wallclock_disabled_no_task`).

The SSE-push and frontend render are verified by the acceptance check (paper run: kill/resume the bar stream, confirm the panel flips DEAD→recovered and alerts arrive), not unit tests.

---

## Risks

- **False alarms** if `bars_expected` is wrong at a session edge → mitigated by dedicated DST + edge unit tests and the 3-missed-bar tolerance.
- **Missed death** if a holiday/early-close is missing from `_EARLY_CLOSE_DATES` → the watchdog would (correctly) expect bars and alert; that's a safe failure (an extra alert), not a silent miss. Keep the date list current (already a documented yearly task).
- **Clock vs market time confusion** — explicitly uses wall-clock arrival for the gap and CT market time only for `bars_expected`; the two are never mixed.
