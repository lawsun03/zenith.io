# News Straddle 1-Min Range Fix + Skip Alerting — Design

**Date:** 2026-06-17
**Status:** Approved design, ready for implementation plan
**Branch:** `fix/news-straddle-1min-range` (off `master`)
**Author:** Lawrence Sun (with Claude)

## Problem

The live news-straddle scheduler (`app/notifications/news_straddle_scheduler.py`)
never arms on the deployed config, so the CPI/FOMC straddle has not been placing
live. Today's FOMC (2026-06-17 18:00 UTC) was skipped:

```
news_straddle scheduler: 2026-06-17 18:00:00+00:00 skipped (< 5 pre-range bars in buffer)
```

**Root cause:** the scheduler buffers bars from `broker.on_bar`, which the broker
fans out at the configured timeframe (`tf_list[0]` = **5min**, see
`topstepx.py::_on_new_bar` → `_fanout`). To lock its range it requires
`min_range_bars=5` bars in a `range_minutes=15` window, but a 15-minute window
holds at most **3** five-minute bars → `_compute_range` returns `None` → the
event is marked `SKIPPED` and no OCO is placed. The scheduler was designed for
1-minute bars (its docstring says "buffers 1-min bars"; 15 one-min bars clears
the threshold). On the 5-min config it skips **every** event.

A transient `RemoteProtocolError`/`ConnectionTerminated` appeared near arm time
but is not the cause — the granularity mismatch guarantees the skip on its own.

## Goal

1. The live scheduler builds its pre-release range from real 1-minute data so it
   actually arms and places the OCO.
2. Skips are never silent: an **early warning at least 5 minutes before** the
   release (with time to react), plus a final alert if it still can't arm, plus a
   positive confirmation when it does arm.
3. The validated arm timing is preserved: arm at `release − 120s`, range =
   `[arm − 15min, arm)`.

## Non-goals

- No change to the arm-time window shift (documented follow-up; explicitly out).
- No change to the backtest `NewsStraddleDetector` (it is fed fine-grained bars in
  the oracle and is unaffected).
- No change to offsets, sizes, or `tp_r`.
- No "arm early" behavior — normal days arm at the validated `−120s`.

## Design

### Core fix (Approach A) — fetch 1-min bars at arm time

Replace the streaming buffer with an on-demand REST fetch in
`NewsStraddleScheduler`:

- Remove `_buffer`, the `on_bar` method, and the `broker.on_bar(_sch.on_bar)`
  registration in `main.py` — the scheduler no longer needs the stream.
- In `_arm_event` (at `arm_ts = release − arm_lead_seconds`):
  ```
  bars = await broker.get_historical_bars(
      timeframe="1min",
      start_time=arm_ts - timedelta(minutes=range_minutes),
      end_time=arm_ts,
      instrument=self.instrument,
  )
  window = [b for b in bars if arm_ts - 15min <= b.ts < arm_ts]
  if len(window) < min_range_bars: -> SKIPPED + alert
  rhigh = max(b.high for b in window); rlow = min(b.low for b in window)
  buy = rhigh + offset; sell = rlow - offset
  place_oco_stop_entries(instrument, buy, sell, stop_r=offset, tp_r=tp_r, size=size)
  ```
- `min_range_bars` stays 5 — now 5-of-~15 one-minute bars, which tolerates feed
  gaps. The range high/low is identical to the 1-min-validated oracle's.
- `get_historical_bars(timeframe, start_time, end_time, instrument)` already
  exists and is used in `_on_new_bar`.

### Per-event lifecycle (scheduler `_run` loop)

1. Sleep until `release − preflight_lead_seconds` (default 300 = 5 min).
2. **Pre-flight readiness check** — `_is_ready()`:
   - broker connected, AND
   - `>= min_range_bars` one-minute bars present in `[now − 15min, now)`
     (same fetch the arm will use, evaluated on data-so-far).
   - Healthy → log "on track"; fall through to step 4.
   - At-risk → fire **early alert** ("⚠ Straddle at risk — ~5 min to <event>:
     <reason>"); enter step 3.
3. **Retry-on-trouble** (only if at-risk): re-run `_is_ready()` every
   `retry_interval_seconds` (default 60) until arm time. If it clears, send a
   "recovered — on track" notice. Never arms early; the canonical arm is step 4.
4. **Arm at `release − arm_lead_seconds` (120s)** — `_arm_event` as above.
   - Success → status `ARMED` + **confirmation alert** ("✅ Armed: <event>/<inst>
     <size>×, range [low–high]").
   - Insufficient bars / fetch exception / `place_oco_stop_entries` failure →
     status `SKIPPED` + **final loud alert**.

The one-straddle-per-event guard (`status != _PENDING`) is preserved; every
failure path sets a terminal status and calls the alert function (never a silent
return).

### Alerting (reuse the watchdog path)

- The scheduler takes an injected `alert_fn: Callable[[dict], Awaitable[None]]`.
- It is called at three moments: early-warning (−5min), final-skip (−120s),
  arm-success (−120s).
- In `main.py`, `alert_fn` fans out exactly like the existing `_on_feed_status`
  watchdog callback:
  - `await discord.send_alert(title, body)` (push), and
  - `journal.publish_straddle_event(payload)` (new, mirrors
    `journal.publish_feed_watchdog`) → dashboard/SSE.
- Payload fields: `event_type`, `instrument`, `release_ts`, `kind`
  (`early_warning` | `skipped` | `armed`), `reason`, `bars_found`,
  `range_high`/`range_low` (when armed).

### Observability (Rule 13)

`NewsStraddleScheduler.state()` already exposes per-event status; add `reason`
and `last_check_ts` so the StrategyDebug panel shows why an event is at-risk or
skipped. No heavy computation on the hot path (state is a pure read).

## Files touched

- `app/notifications/news_straddle_scheduler.py` — core: REST-fetch range,
  preflight + retry lifecycle, `alert_fn`, drop buffer/`on_bar`, extend `state()`.
- `app/bot_config.py` — add `news_straddle_preflight_lead_seconds: int = 300`,
  `news_straddle_retry_interval_seconds: int = 60` (keep
  `news_straddle_arm_lead_seconds: int = 120`).
- `app/main.py` — build `alert_fn` (discord + journal), pass it +
  preflight/retry config into `build_news_straddle_schedulers`; remove the
  `broker.on_bar(_sch.on_bar)` registration.
- `app/api/journal.py` — add `publish_straddle_event(payload)`.
- `app/strategy/news_straddle.py` — `build_news_straddle_schedulers` signature:
  thread `alert_fn`, `preflight_lead_seconds`, `retry_interval_seconds` through.
- (Frontend StrategyDebug: render the new `reason`/`kind` — optional, in-scope per
  Rule 13 but small.)

## Error handling

- Fetch raises → `SKIPPED` + alert (`reason="fetch_error"`).
- `< min_range_bars` → `SKIPPED` + alert (`reason="insufficient_bars: N/5"`).
- `place_oco_stop_entries` raises or returns no ids → `SKIPPED` + alert
  (`reason="oco_failed"`).
- Broker not connected at pre-flight or arm → at-risk/skip + alert
  (`reason="broker_disconnected"`).

## Testing (Rule 9 — verify intent)

Unit tests in `tests/test_news_straddle_live.py` (mock broker):
1. **Arms with correct levels:** `get_historical_bars` returns 15 one-min bars;
   assert `place_oco_stop_entries` called once with `buy = max(high)+offset`,
   `sell = min(low)−offset`, correct `size`/`stop_r=offset`/`tp_r`, and a
   confirmation alert fired.
2. **Skip + alert on insufficient bars:** returns `< min_range_bars`; assert no
   OCO placed, status `SKIPPED`, alert fired with `reason` containing the count.
3. **Skip + alert on fetch failure:** `get_historical_bars` raises; assert
   `SKIPPED` + alert, no crash.
4. **Early-warning fires when unhealthy:** `_is_ready()` false at pre-flight →
   early-warning alert emitted with `kind="early_warning"`.
5. **No re-arm of a terminal event:** calling arm twice does not place a second
   OCO.

Each test asserts the *business outcome* (OCO placed at the right prices / a loud
alert), not just that a method was called.

## Live verification

Re-fetch today's real `2026-06-17 17:43–17:58 UTC` 1-minute window via
`get_historical_bars` and show the `range_high`/`range_low` (and resulting
buy/sell levels) the fixed scheduler would have locked for today's FOMC — proving
the fix produces a valid range against real data, before the next CPI
(2026-07-14).

## Success criteria

- With healthy 1-min data, the scheduler arms at `−120s` and places the OCO with
  correct levels from the 1-min range.
- A skip is never silent: early-warning ≥5 min out, final alert at arm, plus a
  positive confirmation on success — all via Discord + dashboard.
- The validated arm timing (`−120s`) and 15-min window are unchanged.
- All unit tests pass; the today's-window re-fetch yields a valid range.

## Open items (confirm during implementation)

- Exact broker "is connected" check to call from `_is_ready()` (e.g. a
  `broker.is_connected` property vs catching `_require_connected()`).
- Whether StrategyDebug frontend rendering is included now or deferred (small).
