# CPI-day router — design spec

**Date:** 2026-06-15
**Status:** Approved (brainstorming), pending implementation plan
**Author:** Lawrence + Claude (brainstorming session)

## Problem

The CPI breakout straddle (B89 engine, B92 live resting-OCO order path) is validated
and shipped default-off, but it can only run as a standalone `engine="news_straddle"`,
which is mutually exclusive with the base `combined` engine. We want, in **one
persistent process on one account**:

- **CPI trading days** → the CPI straddle runs; the base engine takes **no new entries**.
- **Every other day** → the base `combined` engine runs normally; the straddle is dormant.

Mutually exclusive *by day*, not simultaneous. No engine reconstruction, no daily
restart, no mid-session hot-swap.

## Non-goals (YAGNI)

- No FOMC / gold-FOMC routing (B94 rejected gold-FOMC: marginal $/mo, raises busts, PF 1.99).
- No mid-session engine hot-swap.
- No additive same-day overlap (base + straddle both live the same day).
- No auto-fetch of the BLS calendar; the events file is maintained manually.

## Decisions (locked during brainstorming)

| # | Decision |
|---|----------|
| a | New config flag name: **`cpi_day_router_enabled`** (default `False`). |
| b | Keep `engine: "combined"`; the router does **not** swap the engine field. |
| c | Phase-agnostic: works identically in `combine` and `shadow` phases. Rollout is shadow-first by *sequencing* (enable on the practice account first), not by code restriction. |
| — | On a CPI day the base engine is suppressed for the **whole trading day** (not just a window around the release). |
| — | Mechanism: **in-process day-gate** (one persistent process; pretrade gate blocks base entries; scheduler self-arms the straddle only at CPI events). |

## Architecture — five pieces

### 1. CPI-day determination (`is_cpi_day`)
A pure helper: given the current bar timestamp and the set of CPI dates loaded from
`data/news_events.csv`, return whether "today" is a CPI day. Date comparison in
**America/New_York** — CPI prints 08:30 ET, so a CPI day = the ET calendar date of the
release. Reuses `app.strategy.news_straddle.load_event_times(...)` (the same source the
scheduler uses) filtered to `event_type=CPI`; no second list of dates.

### 2. Base-engine suppression (new pretrade check)
Add one composable check to `app/risk/pretrade.py`:
`if cpi_day_active and order.is_entry → Deny("CPI_DAY_BLOCK", <message>)`.
`check()` is pure (no I/O), so the execution engine computes `cpi_day_active` once per
bar from the CPI date set and feeds it into the gate (via `RiskState` or a check
parameter — implementation plan picks one). Exits/flattens (`is_entry=False`) still
pass, so an open base position can always be managed/flattened — only **new base
entries** are blocked on a CPI day.

### 3. Straddle scheduler, decoupled from the engine field
Today (B92) the `NewsStraddleScheduler` is only constructed when
`engine == "news_straddle"`. Add `cpi_day_router_enabled` (default `False`). When `True`:
keep `engine: "combined"` (base runs), **also** construct the `NewsStraddleScheduler`,
and feed `cpi_day_active` into the gate. The scheduler self-arms an OCO only at CPI
event times, so it is inert on non-CPI days by construction. The straddle bypasses
pretrade (scheduler → `broker.place_oco_stop_entries` directly), so the base-entry
block never touches it.

### 4. Day rollover
The process lives across midnight, so `cpi_day_active` is recomputed each bar from the
bar's ET date — no extra scheduler/timer; it flips automatically the moment the day rolls.

### 5. Observability (Rule 13)
`strategy_state` already carries `news_straddle` (from `scheduler.state()`). Add a
sibling `cpi_day_router` field — `{today_is_cpi_day, next_cpi_date, base_entries_suppressed}`
— rendered in `frontend/src/components/StrategyDebug.tsx`. Lawrence can open the
dashboard and confirm "today is a CPI day → base suppressed, straddle armed" without
reading logs. State changes also logged at INFO when the day-gate engages/disengages.

## Known limitations (flagged, not blockers)

1. **Stale events file (prerequisite).** `data/news_events.csv` CPI rows end
   **2026-06-10** (past). Until the next real CPI date (from the official BLS calendar —
   not fabricated) is appended, the router is a correct no-op: no day matches, base runs
   every day. The feature can ship and sit harmless until the date is added.
2. **Straddle bypasses the risk gate** (daily-loss limit, max-contracts). Pre-existing
   B92 behavior, not introduced here — the resting OCO goes straight to the broker.
   Acceptable for a 1-contract shadow; a follow-up to route it through pretrade is worth
   doing before sizing up.
3. **OCO is client-side.** If both stop legs fill in the same instant, the
   sibling-cancel cannot prevent a brief naked second position (inherent to client-side
   OCO; exchange-native OCO is the only full fix). Acceptable at 1 contract.

## Testing (Rule 9 — encode *why*, not just *what*)

- `is_cpi_day`: a CPI date → True; the day before/after → False; ET-vs-UTC boundary (a
  00:30-UTC event belongs to the *previous* ET day — the test fails if naive UTC date is used).
- Pretrade: on a CPI day a base **entry is Denied**; an **exit the same day is Allowed**;
  on a non-CPI day a base entry is **Allowed** (the gate must not over-block normal
  trading — this test fails if the router leaks into non-CPI days).
- Router wiring: `cpi_day_router_enabled=True` constructs the scheduler with
  `engine="combined"`; `False` constructs neither (no regression to current behavior).
- Phase-agnostic: the gate behaves identically for `account_phase` `combine` vs `shadow`.

## Rollout

1. Ship default-off (`cpi_day_router_enabled: False`). Full suite + frontend build green.
2. Append the next real CPI date(s) to `data/news_events.csv` from the BLS calendar.
3. Enable on the practice/shadow account (`PRAC-…`, `phase_shadow: true`); observe one
   CPI cycle in the dashboard (day-gate engages, straddle arms, base suppressed).
4. When satisfied, enable on the combine phase — same flag, same code path, no change.
