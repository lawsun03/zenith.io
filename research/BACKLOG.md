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

## B10 — funded_sim --save-id registry output  [done — shipped: funded_sim now accepts --save-id/--save-label/--instrument/--timeframe; writes backtests/<id>.json with funded_pipeline.combine + funded_pipeline.xfa; 5 defining-behavior tests; 609 total tests green]  (infra)
Give funded_sim (or equity_export) a `--save-id` that writes a UI-registry
JSON (shape like run_monthly_combine._save_ui_result, with the funded metrics
in a `funded_pipeline` block) so payout-frontier results render in the
dashboard alongside backtests.

## B11 — Excursion tracker instrument filter  [done — shipped: ExcursionWindow.instrument field + on_bar filter + _root_instrument normalisation; 4 defining-behavior tests; 613 total tests green; pre-06-12 multi-instrument rows stay unusable]  (data integrity, small)
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

## B12 — Tracked runtime-ledger policy (git-wipe hazard)  [done — backfill complete (20 rows); policy options doc written; decision deferred to Lawrence: Option A (untrack rolling files via .gitignore) recommended; doc trade_analysis/2026-06-13_B12_runtime_ledger_policy.md]
From B4 forensics: 22 rows of trades/trades.csv (06-10T01:32Z→06-12T14:59Z)
were destroyed by a git tree-restore (reflog: `reset: moving to HEAD` 06-10
23:35 PT); writer was healthy. Conflict: .gitignore comment says trades/ is
*intentionally* tracked for cloud analysis. Options to present, not decide:
(a) untrack rolling files, keep daily files tracked; (b) commit-on-write;
(c) move cloud-sync to the outbox channel. Either way: backfill the 22 rows
from trades_2026-06-10.csv into the rolling file first. NEVER resolve this by
running git restore/reset on a live tree with unsynced ledgers.

## B13 — Split the slippage column: execution vs plan-deviation  [done — shipped: exec_slippage column added (fill vs order_bar_close); slippage column unchanged for backward compat; 10 defining-behavior tests; 623 total tests green]  (small)
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

## B14 — ORB reentry after stop  [done — candidate: 11/61 combine (18%) PF 1.13 vs 7/61 (11%) PF 1.16 baseline; funded $2,578/mo sust 0.99x vs $1,911/mo sust 0.85x; +44% volume, -5% PF; both standalone configs pipeline-negative at risk 1.0%]  (strategy research, combine+funded)
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

## B15 — Long-only iFVG funded benchmark  [done — candidate: long-only sust 1.13x (r1.25) / 1.12x (r1.0) vs full iFVG 0.54x; +41% net, -37% XFA busts; NO NEW CODE needed (allowed_sides=long already existed); doc trade_analysis/2026-06-13_B15_longonly_ifvg_funded.md]  (strategy research, funded)
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

## B16 — iFVG inversion bar quality gate  [done — rejected: inversion_min_body_r=0.15 is a no-op at deployed MNQ config (min_absolute_body=5.0 already exceeds 0.15×stop_dist); 0/0 trade count change; PF/funded metrics identical to baseline; infrastructure default-off; 3 tests, 632 total green]  (strategy research, funded)
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

## RESEARCH — Session wk1-r2  [done — 2 items appended: B17 ORB long-only, B18 named-sessions killzone benchmark]
Session #12 (12 % 3 == 0); last 2 completed were build items (B8, B9). Sources: WebSearch +
per-side/per-hour data mining on 5y MFE/MAE CSVs.

Key data findings driving the 2 proposals:
- ORB long side PF=1.320 (n=542) vs short side PF=1.109 (n=488) over 5y — both profitable,
  but 19% PF gap; "tops stall, bottoms sweep" extends to ORB though shorts are still positive
- iFVG per-hour: noon (12:xx ET) PF=0.591 (n=73), 11:xx ET PF=0.829 (n=109), NY PM 14-15:xx
  ET PF=0.763-0.946 — all negative or borderline; London (05:xx 1.433, 04:xx 1.307) and
  NY AM (10:xx 1.235, 09:xx 1.178) are the quality windows
- Academic paper (arxiv 2605.04004): 14 OHLCV signal families on MNQ 5min 2021-2025 — no
  signal family survives; gross edge 0.07-1.50 pts/trade pre-cost. Confirms Lesson 6.
- Volatility-volume-gap classifier (SSRN 6750442): T=1.46 mean net +7.80pts/127 trades but
  2024 net loss -26.75pts — regime fragility, consistent with Lessons 3-4.
- No new mechanism families found beyond iFVG/ORB/KZ; web search returned same ideas as wk1-r1.

## B17 — ORB long-only funded benchmark  [done — rejected: long-only $/mo -39% ($1,181 vs $1,943) and sust -11pp (0.737 vs 0.847) at r1.0; ORB shorts are profitable (PF 1.109), removing them hurts pipeline at all risk levels; feature orb_long_only ships default-off]  (strategy research, funded)
Hypothesis: ORB short side (PF=1.109, n=488) is materially weaker than long side (PF=1.320,
n=542) over 5 years. Blocking shorts should improve funded-phase PF and reduce bust rates.
Unlike B15 (iFVG shorts PF=0.960, loss-making), ORB shorts are still profitable — so this
is a PF-improvement hypothesis, not loss-removal. Volume impact: halves signal count from
~23 to ~12/month; already too sparse for Combine, so route to funded objective only.

Mechanism:
- Add `orb_long_only: bool = False` to StrategyParams (default off)
- In ORBDetector.on_bar, when `self.config.orb_long_only`:
  skip bearish breakout (bar.close < or_low) → return None
  long breakout unchanged
- The OR range still builds on both sides; only signal emission is gated

Fixed defaults: `orb_long_only=False` (off; enable for benchmark)
Defining-behavior tests (tests/test_orb_long_only.py):
1. orb_long_only=True: bar breaks below or_low → on_bar returns None
2. orb_long_only=True: bar breaks above or_high → on_bar returns Signal (long) as normal
3. orb_long_only=False: bearish breakout → Signal returned (existing behavior preserved)

Success criteria (vs B3 ORB r1.0 funded baseline: $393/mo, sust 1.26x):
- Funded: XFA net $/month or sustainability ratio improves vs ORB r1.0 funded (same risk 1.0)
- Combine (secondary): not the primary route; pass rate is likely ~6-8/61 (expected); report it
- Route: equity_export + funded_sim only; no Combine benchmark needed

Benchmark:
1. equity_export + funded_sim --set engine=orb --set orb_r_multiple=2.5 --set orb_long_only=True
   at risk 0.75, 1.0, 1.25 (same sizing sweep as B1)
2. Compare to B3 ORB r1.0 funded ($393/mo, sust 1.26x) — same risk level

Source: 5y MFE/MAE data mining (this session): orb_long n=542 PF=1.320, orb_short n=488 PF=1.109.

## B18 — Named-sessions killzone config benchmark (iFVG, funded)  [done — rejected: Config A (London+NY AM) PF +1.1% misses +5% threshold; Config B (standard named sessions) is strictly worse than all-day on all metrics; deployed all-day config is near-optimal for funded phase; doc trade_analysis/2026-06-13_B18_named_sessions_killzone_benchmark.md]  (benchmark, no new code)
Hypothesis: the iFVG funded-phase equity curve is dragged down by signals in negative-
expectancy windows: noon (12:xx ET PF=0.591, n=73), late NY AM / NY PM (11:xx 0.829,
14:xx 0.946, 15:xx 0.763). These windows occur when the combine harness uses all_day
(enabled_killzones=["all"]); restricting to named sessions (London + NY AM) would remove
them and improve funded PF without touching strategy code.

Data (5y MFE/MAE, all_day config):
- London only (02-05:xx ET): n=611, blended PF ~1.185 (04:xx 1.307, 05:xx 1.433)
- NY AM only (08-10:xx ET): n=665, blended PF ~1.020 (09:xx 1.178, 10:xx 1.235, 08:xx 0.927)
- NY PM (13-15:xx ET): n=216, blended PF ~0.906 (negative net ~-$5,200 over 5y)
- Noon gap (11-12:xx ET): n=182, blended PF ~0.748 (negative net ~-$16,000 over 5y)
- Pre-market / overnight (00-07:xx ET excl. London): n=603, blended PF ~0.854 (negative)

Mechanism: no new code. The `enabled_killzones` StrategyParams field already accepts named
sessions. Test with:
  `--set enabled_killzones=["London","NY AM"]`  (best two windows; removes all bad ones)
  `--set enabled_killzones=["London","NY AM","NY PM"]`  (adds NY PM despite weak stats)
These override the all_day default in equity_export.py runs only.

Fixed defaults: no change (this is a benchmark-only item; production killzone config unchanged)

Success criteria (vs iFVG r1.25 funded baseline from B1/B3 — $167/mo solo, pipeline-negative):
- Primary: does PF improve materially (>= +5%) on funded equity run?
- Secondary: does the pipeline sustainability ratio improve vs all_day config?
- Volume cut must not exceed 50% (if London+NY AM removes > half the signals, it's too sparse)

Note: this item requires NO defining-behavior tests (no code change). A single benchmark run
per config (equity_export + funded_sim) is sufficient. Record volume change alongside PF.

Source: 5y iFVG MFE/MAE per-hour data mining (this session). Negative-expectancy windows
identified empirically; hypothesis is that they are genuinely structurally weak (consistent
with Lesson 8: short-session NQ edges are time-of-day dependent), not random noise.

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk1-b18  [done — 3 items appended: B19 long-only+london_ny_am benchmark, B20 iFVG→LongOnly-iFVG two-phase pipeline, B21 iFVG→ORB-reentry two-phase pipeline]

B18 exhausts all existing pending backlog items. The next session must replenish with new
testable strategy hypotheses. Priority themes based on open threads:

1. **Long-only iFVG + London+NY AM combined** (B15 + B18 intersection): B15 achieved
   sust 1.12x by removing loss-making shorts; B18 found London+NY AM adds +8.7% net payouts.
   Hypothesis: `allowed_sides=long` + `enabled_killzones=["london","ny_am"]` stacks both
   improvements; the long-only filter removes structural losses while London+NY AM removes
   marginal-quality signals.
2. **ORB reentry + funded phase** (B14 candidate thread): B14 showed ORB reentry achieves
   +44% volume, +35% funded $/mo but still pipeline-negative solo. Combined with iFVG Combine
   (the B3 recommended pipeline), ORB-with-reentry as the funded phase might beat ORB-without.
3. **ATR-normalized stop_buffer for post-2022 config** (Lesson 3 update): fixed_point stop_buffer
   3.0 pts is ~2x relatively stricter at NQ 12k vs 21k. A price-scaled stop_buffer (e.g.,
   0.014% of price) would match today's 3.0 pts at 21k but auto-adjust. Tests: would this
   change 2021-2023 signal frequency without degrading 2024-2026?
4. **Per-R excursion ladder for ORB** (B2 follow-on): B2 shipped MFE/MAE infrastructure but
   only tested be_trail_r=1.0 (which failed). The ORB winner distribution peaks at higher MFE
   values than iFVG; a partial exit at 1.5R or a trail at 2.0R+ might improve funded PF.
   Compute ORB-specific MFE/MAE distributions from research/mfe_mae_orb*.csv before proposing.

Run this as a research/ideation session (WebSearch + data mining on excursion CSVs + prior
lessons), following the RESEARCH session protocol. Output 1-3 new backlog items in mini-spec
format. Session count at B18+1 session = wk1-b18+1; check 3-session rule for ideation.

Key data findings driving the 3 proposals (2026-06-13):
- B3 pipeline model uses PER-YEAR equity_b1 CSVs (stitched); flat 5y CSVs give ~2x more
  busts than per-year. B20/B21 must generate per-year equity CSVs to be comparable to B3.
- ORB MFE/MAE (partial_r=0, 5y, n=1030): winner p25=1.11R, p50=1.64R, p75=2.50R (= target),
  p90=2.66R; ~75% of winners hit the target exactly. Loser MFE p75=0.78R, p90=1.30R.
  MFE >= 2.0R: winners 37%, losers only 2% → excellent separation. be_trail_r candidate
  would need to engage AFTER 2.0R to avoid killing two-thrust winners (same failure mode as
  be_trail_r=1.0 in B2). Not proposed — distribution evidence is insufficient to quantify
  path-through-peak behavior without additional instrumentation.
- iFVG short removal (B15) is mechanism-different from session filtering (B18): B18 showed
  removing named sessions HURTS full iFVG funded; but B15 uses london+ny_am+ny_pm already.
  B19 tests whether removing NY PM specifically from LONG-ONLY iFVG further improves sust.
  Lower prior than B20/B21 due to B18's counter-finding, but no-code so cheap to test.
- Web search: no new mechanism families. ORB variants and liquidity sweep are the only
  tested-positive mechanisms (4-for-4 failure of external claims, Lesson 6 confirmed).
  One SSRN paper on Ladder exits (5095349) noted as weak prior; not incorporated.

## B19 — Long-only iFVG + London+NY AM only (funded benchmark, no new code)  [done — candidate: r1.0 PF +3.6% sust 1.184x; r1.25 PF +4.6% sust 1.600x vs B15 baseline; 33% fewer trades, nearly identical net payouts at r1.25; doc trade_analysis/2026-06-13_B19_longonly_london_ny_am_funded.md]
Hypothesis: B15 (long-only iFVG) uses london+ny_am+ny_pm. NY PM signals (13:xx-15:xx ET)
have PF 0.906 in the full all-sides config (B18 data). For LONG-ONLY iFVG, NY PM long signals
may drag down PF (the B18 session removal was hurt by removing positive overnight long signals;
NY PM longs are a different population). Restricting further to london+ny_am only removes the
NY PM window and may lift PF above B15 baseline (1.127).

Mechanism: no new code. Use existing `allowed_sides=long` + `enabled_killzones=["london","ny_am"]`.

Benchmark (funded objective only — combine volume will be ~35-45/month, too sparse):
1. equity_export at r0.75/r1.0/r1.25: `--set allowed_sides=long --set enabled_killzones=london,ny_am`
2. funded_sim --haircut 0/200/400
3. Compare to B15 baseline (long-only, london+ny_am+ny_pm): PF 1.127, sust 1.12x at r1.0

Fixed defaults: `allowed_sides=long`, `enabled_killzones=["london","ny_am"]` (test only; prod unchanged)
Defining-behavior tests: none needed (no code changes).

Success criteria (vs B15 r1.0 baseline: PF 1.127, sust 1.12x):
- Primary: PF improves >= +2% (lower threshold than B18's +5% since B15 already has higher PF)
  AND sust >= 1.12x (doesn't regress pipeline sustainability)
- The test is cheap; rejection is informative (confirms B18's finding extends to long-only iFVG)

Warning: B18 showed session filtering can HURT funded metrics at partial_r=1.5. Low prior
(~35% that this passes success criteria). Run after B20/B21 if time permits.

Source: B15 (long-only) + B18 (session filter) data mining. NY PM PF 0.906 from B18 data.

## B20 — iFVG Combine → Long-only iFVG Funded (two-phase pipeline)  [done — rejected: no config meets $/mo >= $393 AND sust >= 1.26x (best: LongOnly r0.75 = $397/mo, sust 1.06x; LongOnly r1.0 = $463/mo, sust 0.65x; LongOnly r1.25 = $676/mo, sust 0.69x); root cause: LongOnly-iFVG funded cycles 2x more XFA accounts (53 vs 28) than ORB funded, iFVG combine (34 passes) cannot sustain 52 busts; doc trade_analysis/2026-06-13_B20_ifvg_combine_longonly_funded.md]
Hypothesis: B3's best two-phase is iFVG Combine + ORB r1.0 Funded = $393/mo, sust 1.26x.
Long-only iFVG funded has higher PF (1.127 vs ORB ~1.21) and standalone sust 1.12x vs ORB 0.85x.
With iFVG combine feeding the accounts (faster than ORB combine: 28.6d vs 68.6d), the
iFVG → LongOnly-iFVG pipeline may beat B3's best pair.

Key methodology note: B3 uses PER-YEAR equity CSVs (equity_b1/) stitched across 2021/2023/2024/
2025/2026. B20 must use SAME methodology to be directly comparable. Per-year CSVs give ~50%
fewer simulated busts than flat 5y CSVs (ORB r1.0: 27 busts per-year vs 59 busts flat).

Steps:
1. Generate per-year long-only iFVG equity CSVs in equity_b20/:
   For each year in bars/yearly/:
   `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --risk-pct 1.25
    --set allowed_sides=long --out research/equity_b20/longonly_r1p25_{year}.csv`
   Also at r0.75 and r1.0.
2. Extend scripts/run_b3_pipeline.py (or write scripts/run_b20_pipeline.py) to include
   "LongOnly-iFVG r0.75/r1.0/r1.25" as Phase B options using equity_b20/.
3. Run all two-phase combinations: iFVG Combine (Phase A) → LongOnly-iFVG (Phase B)
4. Report $/mo, sust, cycle_days vs B3 benchmark ($393/mo, 1.26x).

Fixed defaults: all B3 script defaults; only change is funded-phase equity source.
Defining-behavior tests: none needed (no new code; this is a benchmark).

Success criteria (vs B3 iFVG Combine + ORB r1.0: $393/mo, sust 1.26x):
- Primary: $/mo >= $393 AND sust >= 1.26x (beat B3 best on BOTH metrics)
- Secondary: any risk level that beats B3 on BOTH metrics is a candidate

Estimated outcome: iFVG combine passes=34, long-only iFVG funded busts (per-year methodology,
estimated) ≈ 23 (applying 0.46x flat-to-per-year correction). sust ≈ 34/23 = 1.48x.
Estimated $/mo depends on net_per_account from per-year equity. Medium-high prior (~55%
this passes criteria) — the long-only removal of loss-making shorts is a strong mechanism.

Source: B1/B3 pipeline model + B15 equity data + per-year methodology correction analysis.

## B21 — iFVG Combine → ORB-reentry Funded (two-phase pipeline)  [done — candidate: iFVG r1.25 Combine + ORB-reentry r0.75 Funded = $497/mo, sust 2.62x (vs B3 $393/mo, 1.26x — beats B3 on both criteria); r1.0/r1.25 reentry pipeline-negative; new recommended two-phase pair; benchmark baseline uses partial_r=0, killzones=["london","ny_am","ny_pm"], swing_stop_lookback=0 (BotConfig defaults at the time)]
Hypothesis: B14 ORB-reentry standalone sust 0.99x at r1.0 (borderline pipeline-negative).
B3 showed that iFVG combine speed lifts ORB r1.0 standalone sust 0.85x → 1.26x two-phase.
The same mechanism may lift ORB-reentry from 0.99x → ~1.46x two-phase. Additionally,
ORB-reentry adds +44% volume (+35% funded $/mo standalone), which should compound into
higher $/mo in the two-phase model.

Key methodology note: same as B20 — must use per-year equity CSVs for comparison with B3.

Steps:
1. Generate per-year ORB-reentry equity CSVs in equity_b21/:
   For each year in bars/yearly/:
   `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --risk-pct 1.0
    --set engine=orb --set orb_r_multiple=2.5 --set orb_reentry_after_stop=True
    --out research/equity_b21/orb_reentry_r1p0_{year}.csv`
   Also at r0.75 and r1.25.
2. Extend scripts/run_b3_pipeline.py (or write scripts/run_b21_pipeline.py) to include
   "ORB-reentry r0.75/r1.0/r1.25" as Phase B options using equity_b21/.
3. Run two-phase: iFVG Combine (Phase A) → ORB-reentry (Phase B) for each risk level.
4. Report $/mo, sust vs B3 benchmark ($393/mo, sust 1.26x).

Fixed defaults: `orb_reentry_after_stop=True`, all other params at B3 ORB defaults.
Defining-behavior tests: none needed (no new code; this is a benchmark).

Success criteria (vs B3 iFVG Combine + ORB r1.0: $393/mo, sust 1.26x):
- Primary: $/mo >= $393 AND sust >= 1.26x (beat B3 best on BOTH metrics)
- Any risk level that passes both criteria is a candidate

Estimated outcome: iFVG combine passes=34, ORB-reentry funded busts (per-year methodology,
estimated) ≈ 33 (applying 0.46x correction to flat 72 busts). sust ≈ 34/33 = 1.03x.
Estimated sust is marginal — below the 1.26x threshold. Lower prior (~35% passes criteria)
but still worth testing since $/mo may be substantially higher.

Source: B3 pipeline model + B14 ORB-reentry equity data + per-year methodology analysis.

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk1-r4  [done — 2 items appended: B22 ORB range_minutes sensitivity, B23 iFVG daily signal cap]

B21 exhausted the backlog. This session replenishes with new testable hypotheses.
Sources: WebSearch + data mining on mfe_mae_ifvg_clean.csv (per-signal-rank analysis).

Key data findings driving proposals:
- **Signal-rank analysis** (scripts/analyze_signal_rank.py, n=2477 over 5y excl. 2022):
  - Rank-1 (first iFVG signal of day): n=1016, PF=1.129 — BEST single-rank PF
  - Rank-2: n=726, PF=0.970 — LOSS-MAKING (drags aggregate PF from 1.129 to 1.062 when added)
  - Rank-3: n=419, PF=1.081; Rank-4: n=192, PF=0.813; Rank-5: n=88, PF=0.962
  - Cumulative PF: cap=1 → 1.129; cap=2 → 1.062; cap=3 → 1.066; full → 1.043
  - Rank-2+ SHORTS are the loss-making driver: PF=0.841 (n=731); rank-2+ LONGS PF=1.139
  - Hybrid (rank-1-all + rank-2+-long): PF=1.133, n=1746, 29.1/month — but 2023 PF=0.983 (red flag)
- **Config parity gap discovered**: current bot_config.json has partial_profit_r=1.5, 
  enabled_killzones=["all"], swing_stop_lookback=30, target_clarity_mode="off" — these differ
  from the research baseline used in B1-B21 (BotConfig defaults: partial_r=0, killzones=named sessions,
  swing_stop_lookback=0). Future benchmarks must pass `--partial-r 0` to match prior results.
- **ORB range_minutes=15 is untested** — already in StrategyParams as orb_range_minutes (default 15),
  no code needed to test 10 or 30.
- **Web search**: SSRN 6709401 (Apr 2026) tests 14 OHLCV signal families on MNQ 5min 2021-2025 —
  no family survives institutional standards. Lesson 6 confirmed 4-for-4. No new mechanisms found.

## B22 — ORB opening range window sensitivity (10 vs 15 vs 30 min)  [done — rejected: 30min strictly worse (6/61 PF 0.96 vs baseline 10/61 PF 1.06 — stop rule); 10min marginal improvement (+3pp PF, same 10/61 passes, +9% funded $/mo) but does not meet 13/61 success criterion; 15min default is near-optimal; long/short PF reversal at 10min is the notable finding (longs 1.15 vs 1.03 at 15min)]
Hypothesis: the 15-minute opening range window is arbitrary. A shorter window (10 min) locks
the OR faster and generates more breakout opportunities; a longer window (30 min) filters out
the first-bar noise and produces higher-quality breakouts at lower frequency. Either direction
could improve the combine pass rate or funded PF.

Mechanism: no new code. `orb_range_minutes` is already in StrategyParams and wired in both
runner.py and main.py. Test via `--set engine=orb --set orb_range_minutes=10` and
`--set engine=orb --set orb_range_minutes=30`.

Fixed defaults to test: 10 and 30 (alongside existing 15 baseline).
Defining-behavior tests: none needed (no code change).

IMPORTANT — baseline parity: pass `--partial-r 0` to all equity_export runs so results are
comparable to B1-B21 benchmarks (which used BotConfig defaults of partial_r=0 at the time).
Also pass `--set swing_stop_lookback=0` to match research baseline (deployed bot uses 30).

Benchmark:
1. Combine objective: `run_monthly_combine.py --set engine=orb --set orb_r_multiple=2.5 --set orb_range_minutes=10` and `...=30`.
   Compare to baseline (12/61 passes, PF 1.10 at orb_range_minutes=15).
2. Funded objective: `equity_export --partial-r 0 --set engine=orb --set orb_r_multiple=2.5
   --set orb_range_minutes=10` (and 30) + `funded_sim --haircut 200`.
   Compare to B1 ORB r1.0 baseline funded metrics.

Success criteria:
- Combine: any range_minutes value achieves >= 13/61 passes AND PF >= 1.10 (matches or beats B5/B7)
- Funded: net $/month or sust ratio improves vs ORB r1.0 standalone baseline from B1

Prior: ~30% (15-min is a standard market open interval; parameter plateau is real; but 10-min
could add meaningful volume if NQ's early-session volatility resolves faster).

Source: ORB timing mechanics + common practitioner variations (range_minutes is the one
unexplored ORB parameter after B5/B14/B17).

## B23 — iFVG daily signal cap (funded objective, cap=1)  [done — rejected: cap=1 r1.25 PF=1.137 sust=1.170x vs B19 r1.25 PF=1.173 sust=1.600x; both metrics worse at all risk levels; B19 long-only+london+ny_am remains the best funded standalone filter; ifvg_daily_signal_cap ships default-off, 4 defining-behavior tests, 640 total green]
Hypothesis: the first iFVG signal of the day (rank-1, both sides) has PF=1.129, which is +8.3%
above the full all-ranks aggregate (1.043). Rank-2 signals are loss-making (PF=0.970) due to
rank-2+ shorts (PF=0.841). Capping at 1 signal per day raises PF to 1.129 — better than B15's
long-only (PF=1.121) with balanced long/short exposure.

Volume concern: cap=1 yields ~17/month (too sparse for combine). Route to funded objective only.
Expected vs B15 (long-only r1.25, sust=1.13x, PF=1.121): cap=1 has slightly higher PF but
~42% less volume. Net effect on funded pipeline uncertain — fewer busts from higher PF, but fewer
total account earnings from lower volume. Test at r1.0/r1.25/r1.5 to probe the risk lever.

Mechanism:
- Add `ifvg_daily_signal_cap: int = 0` to StrategyParams (default 0 = disabled; > 0 = max
  signals emitted per calendar ET-day from the iFVG/SweepDisplacement engine)
- Add daily signal counter to SweepDisplacementComposer: reset on bar-day-change; increment on
  every emitted signal; return None (suppress) when count >= cap and cap > 0
- Day boundary: same ET-date comparison already used in ORBDetector

Fixed defaults: `ifvg_daily_signal_cap=0` (off). Test at cap=1 (primary), cap=2 (secondary).

Defining-behavior tests (tests/test_ifvg_signal_cap.py):
1. cap=1: first signal of day fires; second identical-setup signal returns None
2. cap=2: first and second signals fire; third returns None
3. cap=0 (default): unlimited signals (existing behavior preserved)
4. Day reset: cap=1, first signal fires on day 1, same setup fires again on day 2

Success criteria (vs B19 r1.25, best funded standalone: PF=1.173, sust=1.600x):
- Primary: sust >= 1.60x AND PF >= 1.17 (doesn't regress vs B19)
- If B19 beats cap=1 on both metrics, B23 is rejected (B15/B19 chain is already the right
  funded filter and cap=1's lower volume hurts it)

Benchmark (funded objective, explicit research baseline):
1. `equity_export --partial-r 0 --set ifvg_daily_signal_cap=1 --risk-pct 1.25`
   Compare to B19 r1.25 baseline (PF 1.173, sust 1.600x).
2. Also test cap=1 with `allowed_sides=long` (first-signal long-only): does removing rank-1 shorts
   improve B23 further, or does rank-1 short quality (PF=1.127) justify keeping them?

Prior: ~25% (B19 already captures most of the PF improvement through long-only filter;
cap=1 trades volume for PF improvement, but 2023 regime risk for first-signal shorts is real;
B15 long-only at same PF with more volume likely beats cap=1 on pipeline metrics).

Source: scripts/analyze_signal_rank.py data mining (this session). Rank-1 PF=1.129 vs rank-2
PF=0.970; year-by-year analysis shows 2023 cap=1 PF is regime-dependent (not computed directly,
but rank-1 2023 shorts likely dragged by the same mechanism as full iFVG 2023 weakness).

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk1-r5 (post-B23 backlog replenishment)  [done — 3 items appended: B24 entry-mode sensitivity, B25 partial-profit ORB-reentry, B26 swing-stop combine sensitivity]

B23 exhausted the backlog. This session replenishes with new testable hypotheses.
Primary source: deployed bot_config.json analysis vs BotConfig research defaults.

Key finding driving ALL 3 proposals: `ifvg_entry_mode="close"` (deployed) vs `"ifvg_edge"`
(research baseline used in B1-B23) is the largest untested config difference. Mechanics:
- "ifvg_edge": waits for price to retrace to FVG proximal edge — limit-like, may miss trades
  but enters at best structural price; armed_tracker + Rule F apply here
- "close": returns signal immediately at inversion bar close — 100% fill rate, entry is deeper
  inside the FVG zone than the proximal edge, giving larger stop distance → harder target
- Rule F (ifvg_rule_f_enabled) only applies in the armed-zone path ("ifvg_edge"/"retrace_ce");
  it is a structural no-op for "close" mode regardless of the flag value

The deployed bot also differs on: partial_profit_r=1.5 (vs 0), swing_stop_lookback=30 (vs 0),
target_clarity_mode="off" (vs "reject"). These 3 are tested by B24, B25, B26 respectively.

Note: target_clarity_mode and ifvg_rule_f_enabled are not proposed as separate items because:
- rule_f is irrelevant in "close" mode (structural no-op, confirmed engine.py:308-310)
- target_clarity_mode="off" vs "reject" allows setups without a clear structural target;
  this only affects the grader, not signal emission. Low prior (grader already mostly permissive
  at deployed settings with grader_min_grade="F"); B24 will capture this incidentally.

## B24 — iFVG entry mode sensitivity (close vs ifvg_edge) — funded + combine  [pending]
Hypothesis: the deployed bot's `ifvg_entry_mode="close"` (enter at inversion bar close) differs
mechanically from the research baseline's "ifvg_edge" (wait for retrace to FVG proximal edge).
All iFVG benchmarks B1-B23 used "ifvg_edge" — the deployed bot's iFVG trade economics are
fundamentally different and have never been benchmarked.

Direction of expected effect:
- "close" entry: inversion bar close is deeper inside FVG zone than the proximal edge →
  larger stop distance (entry further from sweep extreme) → target further away → lower WR
- "close" fill rate: 100% (no missed entries). "ifvg_edge" may miss trades that run
  without retracing to the proximal edge
- Net effect on PF/funded/combine: UNKNOWN — fill-rate gain vs WR degradation

Mechanism: no new code. `ifvg_entry_mode` is already a StrategyParams field. Test both values
with all other parameters held at research baseline (partial_r=0, swing_stop_lookback=0,
target_clarity_mode="reject") to isolate entry mode effect.

Benchmark configs to test:
1. iFVG combine at "close" vs "ifvg_edge" (run_monthly_combine.py):
   `--set ifvg_entry_mode=close` vs no flag (default "ifvg_edge")
   Pass --partial-r 0 --set swing_stop_lookback=0 --set target_clarity_mode=reject for parity
2. B19 funded (LongOnly, london+ny_am, r1.25) at "close" vs "ifvg_edge":
   equity_export + funded_sim --haircut 200 at each mode
   Compare PF, sust, $/mo

Important: ORB benchmarks are unaffected (ORB always uses bar-close entry naturally).

Success criteria:
- "close" mode PF within 3% of "ifvg_edge" → deployed config is acceptable
- "close" mode PF worse by >5% → deployment risk (recommend switching to "ifvg_edge")
- If "close" actually IMPROVES PF (higher fill rate offsets WR drag): candidate for upgrade

Stop rule: if "close" loses on BOTH combine passes AND funded PF vs "ifvg_edge", reject.

Defining-behavior tests: none needed (no code changes).

Prior: ~45% that "close" is materially worse (larger stop distance → harder target on NQ 5min
5-bar candles which often close 5-20 pts beyond FVG edge on strong displacement bars). ~35%
roughly equal. ~20% "close" is better (fill-rate gain dominates).

Source: app/execution/engine.py:293-349 (entry mode dispatch), deployed bot_config.json audit.
This is the highest-priority parity-gap item for the Monday deployment decision.

## B25 — partial_profit_r=1.5 effect on ORB-reentry funded (B21 Phase B)  [pending]
Hypothesis: B21's recommended Phase B (ORB-reentry r0.75) was benchmarked at partial_r=0
(research baseline). The deployed bot uses partial_r=1.5 (book half at 1.5R, stop to BE).
For ORB r_multiple=2.5, partial exit at 1.5R changes winner economics:
- Full winner: earns 2.5R on full position (baseline)
- Partial winner: earns 1.5R × 0.5 + 2.5R × 0.5 = 2.0R on the position (20% reduction)
- Partial-then-BE: earns 1.5R × 0.5 + 0 = 0.75R (vs 0 or loss without partial)

The key question: does partial_r=1.5 reduce funded busts enough to offset the 20% gross
payout reduction per winning trade?

Mechanism: no new code. partial_profit_r is already wired to equity_export via --partial-r.

Benchmark (funded objective only — ORB combine benchmark already done in B22):
1. equity_export --set engine=orb --set orb_r_multiple=2.5 --set orb_reentry_after_stop=True
   --risk-pct 0.75 --partial-r 0 vs --partial-r 1.5 (both at swing_stop_lookback=0)
2. funded_sim --haircut 200 on both
3. Compare busts, sust, $/mo to B21 baseline (partial_r=0: $497/mo, sust 2.62x, 13 busts)

Success criteria (vs B21 per-year baseline — note these are flat 5y funded_sim numbers, not
the per-year pipeline model; success means the deployed partial_r doesn't hurt):
- partial_r=1.5 achieves sust >= 80% of partial_r=0 baseline → deployed config is acceptable
- partial_r=1.5 achieves sust >= partial_r=0 → unexpected positive result, upgrade deployed config
- partial_r=1.5 sust < 70% of baseline → deployment risk (partial exits materially hurt ORB funded)

Note: ORB-reentry adds a second signal per day (after a stop), which may interact with partial_r:
if the first trade exits partial then runs to BE, the account avoids a full loss but also captures
only 0.75R. A second reentry signal would then execute with the same dynamics. The partial_r
effect compounds differently for reentry vs single-entry.

Defining-behavior tests: none needed.

Prior: ~45% that partial_r=1.5 hurts ORB-reentry funded (ORB's ~75% target-hitters get reduced
payout; the ~25% day-end-flatten population benefits from partial locks). Net likely negative
since the dominant outcome is target-hits, and those are penalized. ~30% neutral (effects cancel).
~25% positive (bust reduction from partial locks dominates).

Source: B21 methodology + deployed bot_config.json + ORB winner MFE analysis (B3/wk1-r3:
~75% of winners hit 2.5R target; partial at 1.5R cuts gross payout for ~75% of winners).

## B26 — swing_stop_lookback sensitivity for iFVG combine (0 vs 30)  [pending]
Hypothesis: the deployed bot uses swing_stop_lookback=30 (stop anchored to the lowest point of
last 30 bars, not the immediate sweep extreme). The research baseline used 0 (stop at sweep
extreme). B1-B23 iFVG combine benchmarks all used lookback=0. The deployed iFVG combine
(Phase A of the B21 recommendation) uses lookback=30 — its combine pass rate is unknown.

Direction of expected effect:
- lookback=30: wider stop (lower anchor than immediate sweep extreme) → same r_multiple target
  but larger absolute target distance → harder to hit → lower WR per trade
- BUT: wider stop → fewer false stop-outs on "normal" retracements → more winning months
- Net effect on combine pass rate: UNKNOWN (same tradeoff as B24's fill-rate vs WR issue)

Mechanism: no new code. swing_stop_lookback is already a StrategyParams field.

Benchmark (combine objective only):
1. run_monthly_combine.py at swing_stop_lookback=0/15/30 for iFVG:
   Pass --partial-r 0 --set ifvg_entry_mode=ifvg_edge to match research baseline
   (isolating just the swing_stop parameter)
2. Report combine passes/61, PF, and monthly trade volume at each value
3. Compare to iFVG research baseline (13/61, PF 1.31 from wk1-r1 Lesson 33 data)

Also run at "close" entry mode (to check interaction with B24) if time permits.

Success criteria:
- Any lookback value achieves >= 15/61 combine passes vs 13/61 → uplift to Phase A supply
- If lookback=30 gives fewer passes than 0 → deployed config is degrading Phase A throughput

Defining-behavior tests: none needed.

Prior: ~30% that lookback=30 helps combine pass rate. Most months that fail to pass $3k/month
do so due to insufficient trade volume (Lesson 2), not poor stop placement. Wider stops reduce
average trade R (same target, wider stop = harder), which may HURT monthly win rates more than
false-stop-out protection helps.

Source: Lesson 50 (config parity gap), deployed bot_config.json. The combine-pass-rate effect
of swing_stop_lookback has not been measured in any prior session.
