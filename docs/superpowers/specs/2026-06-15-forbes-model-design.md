# Forbes Model — design spec

**Date:** 2026-06-15
**Status:** Approved (brainstorming), pending implementation plan
**Author:** Lawrence + Claude (brainstorming session)

## Problem / goal

Build and **rigorously backtest** an ICT-style intraday strategy ("Forbes Model") on
Nasdaq futures (NQ/MNQ), **1-minute execution**. Core idea: during the NY open, trade
*toward* session liquidity (session highs/lows) using opening-range FVGs as entry
triggers. The author claims a 75–80% win rate "if taken correctly"; the examples are
post-hoc annotated winners, so the primary deliverable is an **objective, selection-bias-free
measurement** of the real edge, plus ablations, and whether it helps the combine/XFA goals.

All session/clock times in the spec are **US/Pacific**; internally the bot works in
**ET** (`America/New_York`) to match the existing killzone stack. 6:30 PST = 09:30 ET.

## Decisions locked during brainstorming

| Topic | Decision |
|---|---|
| Build approach | **Reuse existing detectors.** A new `ForbesComposer` wires `DisplacementDetector` (FVGs), `LiquidityTracker` (sweeps/swings), `ORBDetector` (opening range), `KillzoneLevelTracker` (session H/L), and adds only the Forbes-specific glue. No from-scratch FVG/sweep code. |
| Target / RR | **Liquidity target + min-RR gate.** target = nearest unswept opposing session H/L; RR is derived (`target_dist / stop_dist`); **skip the setup if RR < `forbes_min_rr`** (default 1.4). `forbes_target_mode` also offers `or_top` / `midway_poi` safe alternatives. |
| POI definition | **POI = prior swing / session level** for this method (Forbes). (POIs can generally be HTF FVG/OB, but here they are structural swing/session levels.) POIs and liquidity *targets* are the **same objects** — one session/swing liquidity map serves both. |
| Timeframe | **POIs (swings) identified on 15m; everything else (FVG/iFVG/sweep/entry/execution) on 1m.** Single 1-min feed; the composer **aggregates 1m→15m internally** for swing detection (avoids the known broken HTF-feed-in-backtest path, see Limitations). |

## Architecture

A new composer module **`app/strategy/forbes.py`** exposing `ForbesComposer` (same
`on_bar(bar) -> Signal | None` interface as `SweepDisplacementComposer`), selected via
`StrategyParams.engine == "forbes"` and built in `app/main.py:_build_runner`. It owns
instances of the reused detectors and the new Forbes logic. Engine-facing surface
(`displacement`, `grader`, etc.) delegates or no-ops as needed so `ExecutionEngine` /
`server.py` / `strategy_state` need no special-casing.

### Units (each: what it does / inputs / deps)

**Reused (consume 1m bars):**
- `DisplacementDetector` → FVGs (`active_fvgs`) + displacement events. Used for the OR-FVG
  gate and the iFVG/displacement entry triggers.
- `LiquidityTracker` → swings + sweep events on 1m. Used for the sweep+displacement trigger.
- `ORBDetector` (config: `orb_open_et=09:30`, `orb_range_minutes=15`) → the opening range.
- `KillzoneLevelTracker` → session range high/low, locked at session close. **Extended** to
  cover Asia / London / prior-day NY (today it covers London / NY-AM etc.). Provides the
  session liquidity map (POIs + targets).

**New (in `forbes.py`):**
- `ForbesKillzone` — gate: only act inside `forbes_killzone` (default 09:30–10:30 ET). Outside
  it, A+ only (post-MVP; v1 = hard gate to the window).
- `_FifteenMinAggregator` — buffers 1m bars into 15m OHLC for swing-POI detection.
- `ForbesPOIMap` — the unified liquidity map: 15m swing highs/lows + Asia/London/prior-NY
  session H/L, each tagged swept/unswept as price interacts. Serves POIs (entry context) and
  targets (nearest unswept opposing level).
- `ForbesComposer` — orchestrates: OR-FVG gate → entry-priority trigger search → Signal build
  (stop + liquidity target + min-RR gate). Enforces `forbes_max_trades_per_day`.

## Flow (per 1m bar)

1. Update reused detectors + the 15m aggregator + the POI map (mark levels swept/unswept).
2. If `ts` not in `forbes_killzone` → return None (v1).
3. At OR close (06:45 PST / 09:45 ET): lock OR; record whether it held ≥ `forbes_or_min_fvgs`
   (default 1) FVGs. If not → **stand aside the rest of the day** (choppy-day filter).
4. While in-window and OR held an FVG and under `forbes_max_trades_per_day`, search entry
   triggers in `forbes_entry_priority` order:
   - **(1) iFVG:** price took a POI (swept a swing/session level), left an FVG, then inverted
     it (closed back through) → enter at the iFVG zone.
   - **(2) sweep + displacement:** a wick swept a session H/L, then displaced back in → enter
     at the swept level.
   - **(3) breakout + retest:** break an OR level, retest, close back through the gap → enter.
5. On a trigger, build a `Signal`:
   - **side** = the displacement/inversion direction (toward the opposing liquidity).
   - **stop** = `forbes_stop_mode`: `beyond_wick` (default, just past the swept wick) | `beyond_or`.
   - **target** = `forbes_target_mode`: `liquidity` (default — nearest **unswept opposing**
     session H/L) | `or_top` | `midway_poi`.
   - **RR** = `target_dist / stop_dist`. If `RR < forbes_min_rr` → **skip** (no trade).
6. `ExecutionEngine` places the bracket (entry/stop/target) via the existing path. Flat EOD.

## Config params (every discretionary rule — added to `StrategyParams`, all `forbes_*`)

`forbes_killzone_et` ("09:30-10:30"), `forbes_or_open_et` ("09:30"), `forbes_or_minutes` (15),
`forbes_require_or_fvg` (True) + `forbes_or_min_fvgs` (1), `forbes_session_windows_et`
(Asia 18:00-00:00, London 02:00-05:00, prior-NY 09:30-16:00 — ET defaults, configurable),
`forbes_poi_swing_tf_min` (15), `forbes_target_mode` ("liquidity"), `forbes_min_rr` ("1.4"),
`forbes_stop_mode` ("beyond_wick"), `forbes_max_trades_per_day` (1),
`forbes_entry_priority` (["ifvg","sweep_disp","breakout_retest"]). All default-off overall via
`engine` (Forbes only runs when `engine == "forbes"`); no change to deployed behavior.

## Backtest & ablations (the deliverable)

Data: `bars/bars_MNQ_dbv_2021_2026.csv` (5y, 1-min, 24h Globex — Asia/London present). EXCLUDE
2022 (frozen holdout) from headline numbers.
- **Headline:** win rate, RR distribution, expectancy ($/trade and R), max drawdown, trade count
  — per year (2021/2023/2024/2025-26) for stability. Explicitly compare to the author's 75–80%
  claim and state the measured rate plainly.
- **Ablations:** (a) killzone filter ON vs OFF; (b) FVG-present vs FVG-absent OR days;
  (c) aggressive (liquidity) vs safe (or_top / midway_poi) targets; (d) `forbes_min_rr` sweep.
- **Funded check:** run the combine-pass + XFA-payout harness (`funded_sim`, haircut 0/200/400)
  to answer "does it help the combine/funded goals," same as B99.

## Testing (Rule 9 — encode *why*)

Defining-behavior unit tests:
- OR-FVG gate: an OR with 0 FVGs → no trades that day; with ≥1 → eligible.
- Killzone: a valid trigger at 10:31 ET → suppressed; at 10:00 ET → allowed.
- Session-liquidity target: target picks the **nearest unswept opposing** level; a swept level is
  skipped.
- min-RR skip: a setup whose nearest-liquidity RR < `forbes_min_rr` → no Signal.
- Entry priority: when two triggers coexist on a bar, the higher-priority one wins.
- iFVG inversion: price taps a POI, leaves an FVG, closes back through → exactly one entry.

## Known limitations / risks (fail loud)

- **Intrabar fidelity:** sweeps are wick events and same-bar stop-vs-target order is unresolvable
  at 1m granularity. Use a conservative **stop-first-on-same-bar** rule; flag it (matches ORB sims).
- **15m feed:** done by internal 1m→15m aggregation in the composer, NOT the runner HTF feed —
  deliberately, because `run_backtest` historically did not feed HTF data ([[project_backtest_no_htf_feed]]).
- **Selection-bias expectation:** the 75–80% claim is from annotated winners; the empirical rate
  will almost certainly be much lower. A confirmatory "no edge" is a valid, publishable result.
- **Discretionary assumptions** (all configurable, listed above): POI = 15m swings + session
  levels; ICT-default session windows; default stop beyond wick; default target nearest liquidity;
  one trade/day; OR needs ≥1 FVG.

## Non-goals (YAGNI)

No live deployment in this project (backtest/measurement only; `engine="forbes"` ships default-off).
No HTF (30m–4h) FVG/OB POIs in v1 (POIs = swings/session levels per the method). No "A+ outside
the window" discretionary layer in v1 (hard killzone gate). No multi-instrument (MNQ/NQ only).
