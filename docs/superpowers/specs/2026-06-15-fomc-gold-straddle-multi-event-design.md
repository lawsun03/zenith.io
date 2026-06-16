# FOMC Gold Straddle — Multi-Event Straddle Support

**Date:** 2026-06-15
**Status:** Design — approved approach, pending spec review
**Target event:** FOMC rate decision, **Wed 2026-06-17 14:00 ET (18:00 UTC)**

## Goal

Run a 20-contract MGC (micro gold) news straddle on the **practice/shadow account**
for this Wednesday's FOMC decision, while leaving the existing live CPI straddle
(MNQ, first fire 2026-07-14) intact. This requires generalizing the single-event
straddle wiring into a **per-event spec list** so CPI→MNQ and FOMC→MGC coexist in
one process.

## Why this edge

The gold-FOMC straddle is a confirmed research edge (PF ~2.0, 4/5 years, both sides;
see `project_news_straddle` memory + `trade_analysis/2026-06-14_*`). It is **gold-only** —
index futures (MNQ) whipsaw on FOMC ("FOMC fakeout"), so the straddle must run on MGC,
not the live MNQ instrument. What B94 rejected was a *dedicated new live engine* for it;
the existing scheduler path (B92) is reused here.

## Current state (what exists)

- `NewsStraddleScheduler` (`app/notifications/news_straddle_scheduler.py`): arms one resting
  OCO stop-entry straddle per scheduled event, **single instrument**, **fixed `offset_ticks`**.
  Buffers 1-min bars for its one instrument, locks the 15-min pre-arm range, places OCO via
  `broker.place_oco_stop_entries`, attaches stop (= broken range boundary, R = offset) + TP (= tp_r·R).
- `app/strategy/cpi_day.py`: router that marks "event days" from `news_events.csv` for ONE
  `event_type` and suppresses the base engine on those days (`CPI_DAY_BLOCK` pretrade gate).
- `main.py` constructs ONE scheduler from `cfg.instrument` + the single
  `news_straddle_*` config fields when `cpi_day_router_enabled`.
- Multi-instrument bar subscription already works: `main.py:772` subscribes every symbol in
  `bot_cfg.instruments`.
- Live config today: `cpi_day_router_enabled=true`, `event_type="CPI"`, instrument MNQ,
  `offset_ticks=60`, `contracts=1`, `cpi_base_suppress=false` (additive).

## Design (Approach 1: per-event scheduler instances, fixed pre-calibrated offset)

### 1. Config — per-event spec list

Add `news_straddle_events: list[NewsStraddleEvent]` to `StrategyParams`. Each spec:

| field | CPI (unchanged) | FOMC (new) |
|-------|-----------------|------------|
| `event_type` | `"CPI"` | `"FOMC"` |
| `instrument` | `"MNQ"` | `"MGC"` |
| `offset_ticks` | `60` | `20` |
| `tp_r` | `3.0` | `3.0` |
| `contracts` | `1` | `20` |
| `suppress_base` | `false` (additive — income lever) | `true` (base MNQ off on FOMC day) |

**Back-compat:** when `news_straddle_events` is empty/absent, fall back to the existing
single-event fields (`news_straddle_event_type`, `news_straddle_offset_ticks`,
`news_straddle_contracts`, instrument = `cfg.instrument`). This guarantees the current live
CPI behavior is unchanged if the list is misconfigured — fail safe, not silently broken.

### 2. Data

Add `FOMC,2026-06-17T18:00:00+00:00` to `data/news_events.csv` (14:00 ET = 18:00 UTC in EDT;
matches the existing summer FOMC rows). **Re-verify against federalreserve.gov before the run.**

### 3. Subscription

Add `MGC` to `bot_config.json` `instruments` (→ `["MNQ", "MGC"]`) so MGC 1-min bars flow to
the FOMC scheduler. Base execution engine continues to run on MNQ (`cfg.instrument`).

### 4. Scheduler wiring

`main.py` builds **one `NewsStraddleScheduler` per enabled event spec**, each constructed with
that spec's instrument / `offset_ticks` / `tp_r` / `contracts` / `tick` (from `TICK_SIZE`) and
the event times filtered to that spec's `event_type`. The scheduler class is unchanged (already
single-instrument and instrument-filters its bar intake at `scheduler.py:83`). The
`strategy_state` publisher takes the list of schedulers; the StrategyDebug panel renders each.

### 5. Router / base suppression (per-event)

Base suppression is decided **per event spec** via `suppress_base`, not the global
`cpi_base_suppress` flag (which is all-or-nothing and would also suppress the base on CPI days,
reverting the additive income lever). Generalize `cpi_day.py` so the engine's suppress-date set
is the **union of dates whose matching spec has `suppress_base=true`**:

- FOMC 06-17 → `suppress_base=true` → MNQ base blocked (`CPI_DAY_BLOCK`) for that ET day,
  isolating the gold-straddle experiment (and avoiding the MNQ base trading the FOMC whipsaw).
- CPI 07-14 → `suppress_base=false` → base MNQ keeps trading (additive), straddle on MNQ on top —
  unchanged from today.

The global `cpi_base_suppress` flag is left as-is and ignored when `news_straddle_events` is
populated (per-event `suppress_base` governs). Document this precedence in the back-compat note.

### 6. Observability (Rule 13)

`strategy_state` SSE already emits scheduler `.state()`; with N schedulers it emits a list, one
entry per event (instrument, offset, tp_r, size, per-event status + locked range). StrategyDebug
panel shows each. Backend logs each arm at INFO (`scheduler.py:111`).

## Offset calibration

Method (matches research `news_multi.py`): `offset = 0.5 × ATR(5-min, pre-event)`, then
`offset_ticks = offset / 0.10` (MGC tick).

Computed from `bars/bars_GC_1s_fomc_windows.csv` (31 FOMC events, 1s → 5-min, pre-release
[release−20m, release−2m]):

- 5y pre-FOMC 5-min ATR: median 1.33 pts, mean 1.78 pts, p75 1.95 pts.
- **2026 regime is ~3× higher**: Jan 7.8, Mar 4.2, Apr 3.8 pts.
- 0.5×ATR: 5y median ≈ 7 ticks; **2026 regime ≈ 19–21 ticks**.

**Chosen: `offset_ticks = 20`** (current-regime 0.5×ATR). The 5y median (7) is rejected as too
tight for the 2026 gold regime — it would be whipsawed through the boundary instantly.

> **REGIME-FRAGILITY CAVEAT (load-bearing).** This is a *fixed* offset; it does not adapt to
> Wednesday's actual pre-2pm volatility. This is the known cost of Approach 1 over live-ATR
> (cf. Lesson 3, "fixed-point thresholds are regime-fragile"). If gold vol on the 17th is far
> from the ~4-pt recent norm, the straddle will be mis-sized. Live-ATR offset is the documented
> follow-up (out of scope here).

## Risk callouts (practice account — notional only, but mechanically real)

- **20-lot bypasses the pretrade DLL/MLL gate** (B92 behavior): the straddle is not risk-checked,
  so this is not a "clean" shadow-combine day. Suppressing the MNQ base (§5) keeps the account from
  being double-exposed.
- **B92 naked-leg residual at 20 lots:** on simultaneous dual fills the client-side OCO can briefly
  leave a naked 2nd leg (→ up to 40 lots gross) before the cancel lands.
- **First-ever live gold straddle**, fixed (non-adaptive) offset.

## Testing

- Unit: `news_straddle_events` parsing + back-compat fallback (empty list → legacy single-event).
- Unit: `main.py` builds N schedulers with correct per-spec instrument/offset/size; each filters
  its own instrument's bars and arms only its own `event_type` dates.
- Unit: router suppress-date set = union of dates whose spec has `suppress_base=true`; FOMC 06-17
  (suppress_base=true) blocks MNQ base, CPI 07-14 (suppress_base=false) does not.
- Regression: with `news_straddle_events` empty, existing CPI-only behavior is byte-identical
  (offset 60, MNQ, 1 contract, 07-14 arming).
- Pre-flight (Wed, before 13:58 ET): startup log shows both schedulers ("CPI…MNQ" + "FOMC…MGC 20
  contract"), MGC subscribed, FOMC date loaded, base suppressed for the ET date.

## Out of scope

- Live/adaptive ATR offset (documented follow-up).
- Multi-instrument *base* engine (base stays MNQ).
- Any change to the confirmed straddle mechanism (range, stop=boundary, TP=3R).

## Rollback

Set `news_straddle_events` to `[]` (reverts to legacy single CPI/MNQ path) or
`cpi_day_router_enabled=false` + restart. Remove `MGC` from `instruments`. No schema migration —
the new field is additive and defaults empty.
