# Research Backlog — ranked. Claim the top `pending` item; one per session.

Status values: pending | in-progress — session <ts> | done — <verdict>
Research/ideation sessions (every ~3rd) APPEND new items as mini-specs.

---

## B1 — Funded-objective re-scoring of the rejected bench  [done — candidate: ORB r0.75 is funded Phase-B recommendation; doc trade_analysis/2026-06-13_B1_funded_objective_scoring.md]
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

## B2 — MFE/MAE excursion ladder  [done — infrastructure shipped; be_trail_r=1.0 rejected (iFVG: 7/61 passes 11% PF 0.97; ORB: 8/61 13% PF 1.057 — both worse than baseline); MFE/MAE per-trade data in BacktestResult.trades + research/*.csv]
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

## B3 — Two-stage phase policy (pass config + milk config)  [done — candidate: iFVG r1.25 (Combine) + ORB r1.0 (Funded) = $393/mo, sust 1.26x; conservative: iFVG + ORB r0.75 = $377/mo, sust 2.43x; doc trade_analysis/2026-06-13_B3_phase_policy.md]
The bot already switches behavior via `account_phase`. Formalize: config A
optimized to PASS (Combine objective winner), config B optimized for XFA
payouts (B1 winner). Simulate the full pipeline: attempts at A -> funded at B.
- Needs B1 done. Deliverable: recommended per-phase pair + pipeline numbers
  (expected $/month including reset costs), trade_analysis doc.

## B4 — Weekly live-data forensics  [done — dataset; doc trade_analysis/2026-06-12_weekly_forensics.md]
(was claimed out of rank order: B1 concurrently claimed by a duplicate wrapper
session at 23:35Z; B2 would mutate backtest code under B1's running variant
comparison; B3 depends on B1. B4 was the top concurrency-safe item.)
Outcome: exec slippage ~2 ticks (recommend slippage_ticks_market=2); the 111.5pt
"slippage" was 0.5pt real (column = plan deviation); 06-12 signal parity 1/1;
3 integrity bugs filed as B11-B13; M26 week bars archived in bars/live_archive/.
Inputs: this week's `logs/*.log`, `trades/*.csv`, `bars` via free TopstepX
fetch (`scripts/fetch_bars.py --symbol MNQ --days 7`).
- Run `scripts/parity_check.py` per live day (engine matching what was live:
  ifvg before 6-12 ~07:22 PT, combined after).
- Measure REAL entry slippage on the week's live fills vs replay; compare to
  the modeled 1 tick; recommend `slippage_ticks_market` setting with evidence.
- `scripts/gate_trace.py` any human-flagged or large missed moves.
- Deliverable: trade_analysis weekly forensics doc + findings entries +
  archived session bars under `bars/live_archive/` (new dir ok).

## B5 — ORB prior-day-range qualifier  [done — rejected: combine identical (12/61, PF 1.10 both ways); funded net -61% ($63k vs $163k h200) from 50% trade-volume cut with only +2.4% PF gain; prior-day range does not predict ORB quality]

## B6 — ATR-normalized displacement thresholds  [done — rejected: iFVG 12/61 (20%) PF 1.10 vs baseline 13/61 (21%) PF 1.18; 2022 drought unchanged (2-18 trades/mo); drought is structure-poverty, not threshold-sensitivity; feature default-off]
The 2022-23 drought mechanism: fixed-point min_absolute_body (5.0) / stop_buffer
(3.0) are ~2x relatively stricter at NQ 11-16k than at 21k+. Add default-off
alternative: thresholds specified as %-of-price or ATR-multiples (pick ONE
formulation, fixed constants chosen to MATCH today's behavior at current price
levels — i.e., 5.0pts at 21k = 0.024% — so 2024+ behavior is unchanged by
construction and only the low-price years change). Evaluate on 2021/2023.
Success: 2021/2023 months wake up (trades/mo, PF >= 1) without changing 2024+.

## B7 — kz_levels master-branch benchmark  [done — rejected: 0/61 passes (0%) vs baseline 21%; PF 1.18 matches iFVG but trade frequency ~7/mo is too sparse to compound into a combine pass; session ranges lock once per session → 10x fewer signals than swing-based sweeps]

## B8 — Wall-clock flatten fix  [done — shipped: flatten_wallclock_enabled flag + early-close calendar note + 2 defining-behavior tests; wall-clock task was already present unconditionally]  (code quality, live-risk)
Bar-driven `_enforce_flatten` never fires on CME early-close days (~5-7/yr) —
flatten-rule violation risk found by cross-engine validation. Add a wall-clock
asyncio task in the engine (default-OFF flag `flatten_wallclock_enabled` in
StrategyParams or engine ctor) that enforces the flatten window by clock, plus
an early-close calendar note. Tests: simulated clock crossing with open
position; no-op when flag off. Ship default-off + recommendation.

## B9 — ORB Rule-13 UI wiring  [done — shipped: ORBDetector.state() + strategy_state orb_state field + StrategyDebug ORB section; 6 tests; bot restarted]  (observability)
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

## B11 — Excursion tracker instrument filter  [pending]  (data integrity, small)
From B4 forensics: `ExcursionTracker.on_bar` (app/execution/excursion.py:87-95)
updates every open window with every bar — no instrument filter, and
`ExcursionWindow` has no instrument field. Multi-instrument days (06-07..06-11)
produced garbage mfe/mae/outcome (MES window scored `win` off an MNQ high).
Fix: add `instrument` to ExcursionWindow, pass at all `open()` call sites
(app/journaling.py:136/344/373 — normalize `CON.F.US.MNQ.M26` → `MNQ` root),
skip foreign bars in `on_bar`. Defining-behavior test: two open windows on
different instruments, inject a bar for one, assert the other's mfe/mae/flags
untouched. Note in trade_analysis that pre-06-12 multi-instrument rows stay
unusable (no backfill possible).

## B12 — Tracked runtime-ledger policy (git-wipe hazard)  [pending]  (decision for Lawrence)
From B4 forensics: 22 rows of trades/trades.csv (06-10T01:32Z→06-12T14:59Z)
were destroyed by a git tree-restore (reflog: `reset: moving to HEAD` 06-10
23:35 PT); writer was healthy. Conflict: .gitignore comment says trades/ is
*intentionally* tracked for cloud analysis. Options to present, not decide:
(a) untrack rolling files, keep daily files tracked; (b) commit-on-write;
(c) move cloud-sync to the outbox channel. Either way: backfill the 22 rows
from trades_2026-06-10.csv into the rolling file first. NEVER resolve this by
running git restore/reset on a live tree with unsynced ledgers.

## B13 — Split the slippage column: execution vs plan-deviation  [pending]  (small)
From B4 forensics: `slippage = fill − signal_entry` (app/journaling.py:178-191)
where signal_entry is the FVG proximal edge → 111.5 "slippage" on 0.5pt of real
slippage. Add `exec_slippage` (fill vs last bar close at order time — broker
already has `_last_bar_close`-equivalent via the engine's bar stream) and rename
the existing semantics to `plan_deviation` (keep the old column name for
compatibility, add the new one). Defining-behavior test. Bonus: surface both in
the StrategyDebug/fills UI per Rule 13. Related Monday recommendation already
in the forensics doc: `slippage_ticks_market: 2` for backtests; consider a
nonzero `max_entry_slippage_frac` live (would have skipped the 06-12 trade —
strategy question, needs a backtest before recommending).

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk1-r1  [done — 3 items appended: B14 ORB reentry, B15 long-only iFVG, B16 inversion quality gate]
Replenish strategy-hypothesis backlog. Last 2 completed were build items (B6, B7);
session #9 (9 % 3 == 0). Sources: WebSearch + data mining on MFE/MAE excursion CSVs.

Key data findings driving the 3 proposals:
- iFVG short side PF=0.960 (loss-making over 5y); long-only PF=1.136 (+9%)
- ORB 10:xx signals PF=1.276 > 9:xx PF=1.176; ORB monthly ceiling ~23 trades (1/trading-day)
- ORB reentry after stop is a genuine untested hypothesis; naive max_trades_per_day=2 fires
  second signal before first stop (wrong) — needs on_stop_loss callback for correct semantics
- Web research found no new mechanisms beyond iFVG/ORB/KZ; all practitioner ideas map to
  existing tested engines (Opening Rip = ORB; Liquidity Sweep = iFVG; VWAP = rejected)

## B14 — ORB reentry after stop  [pending]  (strategy research, combine+funded)
Hypothesis: a failed ORB breakout (first signal stopped) followed by a second breakout of
the same range is a higher-conviction "false breakout → true breakout" signal. The existing
max_trades_per_day=2 fires a second signal at the SECOND bar above the range (before the first
stop), which is wrong. The correct mechanism: re-arm the detector only when on_stop_loss fires.

Mechanism:
- Add `orb_reentry_after_stop: bool = False` to StrategyParams (default off)
- Replace `_NoopComposer` with `ORBComposer(detector, config)` that implements `on_stop_loss`:
  when `reentry_after_stop=True`, call `detector._rearm()` which resets `_fired` to 0
- `detector._rearm()` is gated: only re-arms if the STOP fired (not a target hit); this
  requires a `_last_side` field and the composer tracking what the last exit was
  OR: simpler — always re-arm on stop_loss (ExecutionEngine only calls on_stop_loss on stops)
- Stop and target unchanged. Max 1 reentry per day (re-arming once allows 1 more signal).

Fixed defaults: `orb_reentry_after_stop=False` (default off; enable for benchmark)
Defining-behavior tests (tests/test_orb_reentry.py):
1. Stop hit during a day → detector re-armed → next breakout bar fires signal 2
2. Target hit during a day → no re-arm → no signal 2
3. Two stops in one day → only 1 reentry (re-arm happens once; second stop hits when
   detector already at max → no second re-arm)

Success criteria (vs B3 baseline: ORB r1.0 funded $393/mo, sust 1.26x):
- Funded: XFA net $/month improves; sustainability ratio unchanged or better
- Combine (secondary): pass rate doesn't degrade below 12/61

Benchmark:
1. run_monthly_combine.py --set engine=orb --set orb_r_multiple=2.5 --set orb_reentry_after_stop=True
2. equity_export + funded_sim with same config, compare to B3 ORB r1.0 baseline

Note: `orb_max_trades_per_day` stays at 1; the reentry mechanism is orthogonal and handled
via the on_stop_loss callback. Do NOT use max_trades_per_day=2 — it fires the second signal
immediately on the second trending bar, not after a stop.

## B15 — Long-only iFVG funded benchmark  [pending]  (strategy research, funded)
Hypothesis: the iFVG short side is structurally loss-making on NQ 5min (5y PF=0.960 from
MFE/MAE data: 1251 short trades, WR 28.9%). Blocking shorts lifts long-only PF to 1.136
(+9%) which should reduce funded-phase bust rates and improve XFA payouts.

From excursion analysis (partial_r=0 benchmark data, 2021-2026 excl 2022):
- All trades: 2477, WR 31.5%, PF 1.043
- Long trades: 1226, WR 34.3%, PF 1.136
- Short trades: 1251, WR 28.9%, PF 0.960 (net negative over 5y)
- Long-only monthly count: median 25/mo, max 36 — INSUFFICIENT for Combine (needs 60+)
→ Route ONLY to funded objective (PF-heavy, not volume-heavy)

Mechanism:
- Add `ifvg_long_only: bool = False` to StrategyParams (default off)
- In SweepDisplacementComposer._emit() or the on_bar signal path, check:
  `if self.config.long_only and signal.side == "short": return None`
- The displacement and sweep logic still runs; shorts are blocked at signal emission, not
  at detection (so sweep states aren't wasted, just signals suppressed)

Fixed defaults: `ifvg_long_only=False` (default off; enable for benchmark)
Defining-behavior tests (tests/test_ifvg_long_only.py):
1. Long-only=True: short displacement event → on_bar returns None
2. Long-only=True: long displacement event → on_bar returns Signal as normal
3. Long-only=False: short displacement → Signal returned (unchanged behavior)

Success criteria (vs B3 baseline: iFVG Combine + ORB r1.0 Funded = $393/mo, sust 1.26x):
Primary: standalone long-only iFVG funded (equity_export + funded_sim) has better XFA net
$/month or bust rate than full iFVG at same risk level (r1.25)
Secondary: iFVG(Combine) + LongOnly-iFVG(Funded) two-phase pair — does it beat ORB Funded?

Note: two-phase iFVG/LongOnly pairing may be impractical (same instrument, different mode
in Combine vs Funded requires config switching). Focus on standalone funded comparison.

## B16 — iFVG inversion bar quality gate  [pending]  (strategy research, funded)
Hypothesis: the inversion bar quality predicts follow-through. A larger inversion body
(stronger conviction at the FVG level) should produce better winner rates than a "barely
inverted" bar with a tiny body. Testing a minimum body threshold filters out weak inversions
while preserving the strong ones.

Motivation: Lesson 1 says "the iFVG inversion IS the quality filter." Making the inversion
stricter should improve PF. B6 tested loosening the DISPLACEMENT threshold (upstream); this
tests tightening the SIGNAL CONFIRMATION (downstream). Different part of the pipeline.

Mechanism:
- Add `inversion_min_body_r: Decimal = Decimal("0")` to StrategyParams (default 0 = off)
- In SweepDisplacementComposer, when an awaiting sweep detects inversion (bar.close inside
  the FVG zone), additionally check:
  `bar_body = abs(bar.close - bar.open)`
  `if config.inversion_min_body_r > 0: require bar_body >= inversion_min_body_r * stop_dist`
  where stop_dist = abs(awaiting.signal_entry - awaiting.signal_stop)
- This requires passing stop_dist into the _Awaiting struct (or computing it inline)

Fixed defaults: `inversion_min_body_r=0` (off); test at `0.15` (15% of stop distance).
At 3.0pt stop distance, 0.15 requires the inversion bar body >= 0.45 pts (~1.8 ticks).
This blocks only very small "doji" inversions while accepting normal bars.

Defining-behavior tests (tests/test_inversion_quality.py):
1. Inversion bar body = 1.0pt, stop_dist=3.0, min_body_r=0.15 → body(1.0) >= 0.45 → signal fires
2. Inversion bar body = 0.2pt, stop_dist=3.0, min_body_r=0.15 → body(0.2) < 0.45 → None
3. min_body_r=0 → any inversion fires (existing behavior preserved)

Success criteria: funded PF improves vs iFVG control at inversion_min_body_r=0.15;
volume cut < 30% (don't filter more than 30% of signals — route to funded if volume drops
further than that). Combine objective: secondary; must not destroy the 13/61 pass rate.

Source: first-principles ("large inversion body = strong rejection at FVG level"); no direct
academic citation — this is a data-falsifiable hypothesis derived from our own trade anatomy.
