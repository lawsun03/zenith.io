# Research Backlog — ranked. Claim the top `pending` item; one per session.

Status values: pending | in-progress — session <ts> | done — <verdict>
Research/ideation sessions (every ~3rd) APPEND new items as mini-specs.

---

## B1 — Funded-objective re-scoring of the rejected bench  [pending]
The whole bench was rejected on COMBINE pass-rate grounds; several had the best
PF we ever measured. Score them on the XFA payout metric instead.
- Variants (fixed configs, no sweeps): control (iFVG frontier, risk 1.25);
  frozen-ATR (`--set atr_ref_lag_bars=12`); trail-1R (`--trail-1r`);
  stop-cap (`--set max_stop_atr=6`); ORB (`--set engine=orb --set
  orb_r_multiple=2.5`); inverted regime-switch (`--set engine=regime_switch
  --set orb_r_multiple=2.5 --set rs_invert=True`).
- Each: equity_export over `bars/bars_MNQ_dbv_2021_2026.csv` (EXCLUDE 2022 —
  pass a date-filtered CSV or run per-year files and concatenate; simplest:
  run on the 5y file but report 2022 rows only in the final confirmatory pass
  of the chosen winner) → funded_sim at haircut 0/200/400.
- ALSO probe sizing for the top-2: risk 0.5 / 0.75 / 1.0 / 1.25 (sizing is the
  bust-rate lever; this is the one sanctioned "sweep" because the objective is
  new). First datapoint on record: ORB r2.5, 2024 only, risk 1.25 → XFA net
  $23.6k but 8/9 accounts busted (haircut 200).
- Deliverable: payout-frontier table (net payouts vs busts vs sizing),
  trade_analysis doc, findings entries, candidate recommendation.
- Success: any variant with materially positive net payouts and bust rate low
  enough that account replacement (via Combine passes) sustains it. Think in
  pipeline terms: passes feed accounts; payouts must outrun bust+reset costs.

## B2 — MFE/MAE excursion ladder  [pending]
Standing #1 from the monitoring backlog. Capture per-trade max favorable /
adverse excursion in the backtest, then derive exit levels from data.
- Step 1 (dataset): additive fields on `BacktestResult.trades` — track per
  open bracket the running max-favorable/max-adverse price in PaperBroker
  (cheap: update in inject_bar loop), expose as `mfe`/`mae` per trade.
  Defining-behavior test required.
- Step 2 (analysis): distributions of MFE in R-units for winners vs losers on
  the control + ORB configs over 2021/2023/2024/2025-26 (NOT 2022).
- Step 3 (candidates): from the distributions pick at most TWO exit-ladder
  designs (e.g., partial at p50-of-winner-MFE, trail beyond p75) with fixed
  parameters derived from data; implement as default-off exit mode; benchmark
  BOTH objectives.
- Success: beats control on either objective per stop rules.

## B3 — Two-stage phase policy (pass config + milk config)  [pending]
The bot already switches behavior via `account_phase`. Formalize: config A
optimized to PASS (Combine objective winner), config B optimized for XFA
payouts (B1 winner). Simulate the full pipeline: attempts at A → funded at B.
- Needs B1 done. Deliverable: recommended per-phase pair + pipeline numbers
  (expected $/month including reset costs), trade_analysis doc.

## B4 — Weekly live-data forensics  [pending]
Inputs: this week's `logs/*.log`, `trades/*.csv`, `bars` via free TopstepX
fetch (`scripts/fetch_bars.py --symbol MNQ --days 7`).
- Run `scripts/parity_check.py` per live day (engine matching what was live:
  ifvg before 6-12 ~07:22 PT, combined after).
- Measure REAL entry slippage on the week's live fills vs replay; compare to
  the modeled 1 tick; recommend `slippage_ticks_market` setting with evidence.
- `scripts/gate_trace.py` any human-flagged or large missed moves.
- Deliverable: trade_analysis weekly forensics doc + findings entries +
  archived session bars under `bars/live_archive/` (new dir ok).

## B5 — ORB prior-day-range qualifier  [pending]
The inverted regime gate (ORB-on-large-range-days) printed 2024 PF 1.45 — as a
qualifier for ORB'S OWN trades (skip ORB on below-median prior-day range), not
as an account router. Implement as default-off ORB config flag (one fixed rule:
prior ET-day range %-of-close >= trailing 60-day median → ORB may trade).
Benchmark ORB-solo both objectives vs ORB baseline.

## B6 — ATR-normalized displacement thresholds  [pending]
The 2022-23 drought mechanism: fixed-point min_absolute_body (5.0) / stop_buffer
(3.0) are ~2x relatively stricter at NQ 11-16k than at 21k+. Add default-off
alternative: thresholds specified as %-of-price or ATR-multiples (pick ONE
formulation, fixed constants chosen to MATCH today's behavior at current price
levels — i.e., 5.0pts at 21k = 0.024% — so 2024+ behavior is unchanged by
construction and only the low-price years change). Evaluate on 2021/2023.
Success: 2021/2023 months wake up (trades/mo, PF >= 1) without changing 2024+.

## B7 — kz_levels master-branch benchmark  [pending]
Last untested engine candidate (memory: project-combined-strategy-bot). Cheap:
check out the master branch strategy code read-only (git show master:<paths>),
assess what it would take to run through the harness; if a port is < 1 session,
do it; else write up the assessment and close the item. Same sweep DNA as iFVG
— expect correlated droughts; the question is whether it adds ANY new months.

## B8 — Wall-clock flatten fix  [pending]  (code quality, live-risk)
Bar-driven `_enforce_flatten` never fires on CME early-close days (~5-7/yr) —
flatten-rule violation risk found by cross-engine validation. Add a wall-clock
asyncio task in the engine (default-OFF flag `flatten_wallclock_enabled` in
StrategyParams or engine ctor) that enforces the flatten window by clock, plus
an early-close calendar note. Tests: simulated clock crossing with open
position; no-op when flag off. Ship default-off + recommendation.

## B9 — ORB Rule-13 UI wiring  [pending]  (observability)
ORB is LIVE (combined engine) with no dashboard state. Add OR-range/fired state
to the `strategy_state` SSE event + a StrategyDebug section (CLAUDE.md Rule 13
defines the three layers). Pure additive; frontend build required
(`npm run build` in frontend/). Note: server changes need a bot restart — do it
only while market closed AND flat, per run-bot procedure.

## B10 — funded_sim --save-id registry output  [pending]  (infra)
Give funded_sim (or equity_export) a `--save-id` that writes a UI-registry
JSON (shape like run_monthly_combine._save_ui_result, with the funded metrics
in a `funded_pipeline` block) so payout-frontier results render in the
dashboard alongside backtests.

---
(Research sessions append new items below this line.)
