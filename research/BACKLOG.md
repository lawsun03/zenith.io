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

## B24 — iFVG entry mode sensitivity (close vs ifvg_edge) — funded + combine  [done — candidate: "close" mode materially better on all metrics (combine 11/61 vs 7/61, funded sust 2.524x vs 0.815x); deployed config validated as superior baseline]
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

## B25 — partial_profit_r=1.5 effect on ORB-reentry funded (B21 Phase B)  [done — acceptable: sust 0.750x vs 0.764x baseline (ratio 98.2%, within 80% threshold); deployed partial_r=1.5 is acceptable; partial exits reduce both combine passes (-7%) and XFA busts (-5.5%) nearly equally; PF slightly improves (1.1617 vs 1.1526); no config change recommended]
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

## B26 — swing_stop_lookback sensitivity for iFVG combine (0 vs 30)  [done — rejected: lookback=30 loses on BOTH combine passes (4/61, PF 0.78) AND run PF vs lookback=0 baseline (7/61, PF 1.00) in ifvg_edge mode — stop rule; bonus finding: deployed Phase A (close+lookback=30+target_clarity=off) achieves 6/61 (10%) vs 11/61 (18%) at B24 baseline; target_clarity=off costs 4 passes (primary driver); swing_stop_lookback=0 + target_clarity=reject recommended for Phase A]
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

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk1-r6 (post-B26 backlog replenishment)  [done — 3 items appended: B27 close-mode Phase A pipeline, B28 close-mode LongOnly-iFVG Phase B, B29 ORB-reentry 10min Phase B]

B26 exhausted the backlog. This session replenishes with new testable hypotheses.
Primary source: Phase A equity analysis (scripts/research_phase_a_analysis.py) + B24 close-mode funded stats.

Key data findings driving the 3 proposals:
- **Phase A ifvg_edge per-year pass rates (equity_b1/control_r1p25)**: 2021: 5/19 (26%), 2023: 5/37
  (14%), 2024: 7/41 (17%), 2025: 12/47 (26%), 2026: 5/19 (26%) — TOTAL: 34/163 (21%).
  These were all generated with BotConfig default ifvg_entry_mode="ifvg_edge".
- **B24 showed close mode gives +57% more combine passes** (11/61 vs 7/61 per 61 months).
  Applying scale factor 1.571: projected close-mode Phase A passes over 5y = ~53 (vs 34).
  Projected B27 economics (close Phase A + B21 ORB-reentry r0.75 Phase B):
  - Attempts/funded: 3.08 (vs 4.76 ifvg_edge) → reset cost $461 (vs $715)
  - Days/funded: 18.5d (vs 28.6d) → cycle days 92.0d (vs 102.1d)
  - Net/month: ~$610/mo (vs $497/mo, +23%) — sustainability: ~4.08x (vs 2.62x, +56%)
- **B24 close-mode LongOnly-iFVG flat 5y funded** (london+ny_am, r1.25, haircut 200):
  53 combine passes, 21 XFA busts, 22 accounts, $180,534 net payouts.
  Per-account net = $180,534/22 = $8,206 (2.6x higher than ORB-reentry r0.75's $3,131).
  Reason: higher PF + longer account durations in LongOnly-close configuration.
  Projected two-phase sust (per-year correction, ~50% fewer busts): 53/~10.5 = ~5x.
- **B22 10-min ORB** (no reentry): +9% funded $/mo vs 15min at r1.0 with same combine passes.
  Whether this advantage holds for ORB-reentry at r0.75 in the two-phase model is untested.
- B27 must run before B28 and B29 (all three share the close-mode Phase A equity from equity_b27/).

## B27 — Close-mode Phase A: re-run B21 two-phase pipeline with ifvg_entry_mode=close  [done — rejected: close-mode (ifvg engine + named sessions) = 10 Phase A passes vs 34 for ifvg_edge; all pairs below B3 threshold on sust; B21 ($497/mo, sust 2.62x) remains best pipeline; see Lessons 62-63]
Hypothesis: The B21 pipeline ($497/mo, sust 2.62x) used equity_b1/control_r1p25 as Phase A,
generated with BotConfig default ifvg_entry_mode="ifvg_edge". B24 confirmed close mode gives
+57% more combine passes (11/61 vs 7/61). Regenerating Phase A equity with close mode should
produce ~53 passes over 5y (vs 34) — reducing reset cost from $715 to ~$461 and improving
sust from 2.62x to ~4.08x. This validates whether the deployed config (which uses close mode)
produces materially better pipeline economics than the B21 recommendation implies.

Mechanism: no new code. Generate per-year iFVG-close equity CSVs via equity_export.py:
  For each year in [2021, 2023, 2024, 2025, 2026]:
  `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
   --risk-pct 1.25 --partial-r 0 --set swing_stop_lookback=0 --set target_clarity_mode=reject
   --set ifvg_entry_mode=close --out research/equity_b27/close_r1p25_{year}.csv`
Also at risk 1.0 (secondary):
  `--risk-pct 1.0 --out research/equity_b27/close_r1p0_{year}.csv`
Write scripts/run_b27_pipeline.py (clone of run_b21_pipeline.py) substituting:
  Phase A: equity_b27/close_r1p25_{year}.csv
  Phase B: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21)

Fixed defaults: `ifvg_entry_mode=close`, `partial_r=0`, `swing_stop_lookback=0`,
`target_clarity_mode=reject` (research baseline with close mode — same as B24).
Defining-behavior tests: none needed (no code changes).

Success criteria (vs B21: $497/mo, sust 2.62x):
- Primary: net/month and sust both improve vs B21 → close-mode Phase A is the correct config
- If sust improves but $/mo decreases: still a win (sustainability is the harder constraint)
- If neither improves: close mode's combine improvement does not translate to pipeline economics
  (unexpected but informative — would mean the ifvg_edge Phase A had compensating per-account value)

Note: B28 and B29 depend on the equity_b27/ Phase A equity generated here.
Run B27 before B28 or B29.

Prior: ~85% that B27 beats B21 on both criteria. The phase A undercount is structural (+57%
more passes = +57% fewer reset costs), and the Phase B is unchanged. The projection is approximate
but the direction is clear and the magnitude is large.

Source: scripts/research_phase_a_analysis.py (this session); B24 combine results.

## B28 — Close-mode LongOnly-iFVG as funded Phase B (two-phase, vs B27 ORB-reentry)  [done — rejected: best pair iFVG-close r1.25 -> LongOnly-close r1.25 = $668/mo sust 0.26x; LongOnly-close has 39 per-year XFA busts vs 10 Phase A passes (needs <=8 for sust>=1.26x); per-account net ($3,054) matches ORB-reentry but 3x higher bust frequency; B21 ($497/mo, sust 2.62x) remains best]
**REVISED PREREQUISITES (post-B27):** B27 showed close-mode Phase A (ifvg engine + named sessions)
gives only 10 passes (vs projected 53). The original success criteria assumed ~53 Phase A passes;
with 10 passes, B28 needs LongOnly-close Phase B to generate ≤ 8 funded busts (per-year) for
sust ≥ 1.26x. This is uncertain but plausible if per-year correction yields <7 busts from the
flat 5y 21-bust baseline. Run B28 to discover the actual per-year bust count.

Hypothesis: B20 tested LongOnly-iFVG (ifvg_edge) as Phase B and found sust 0.65x (rejected:
52 funded busts vs 34 Phase A passes). B24 showed close mode dramatically changes the funded picture:
LongOnly-close flat 5y has 21 XFA busts (vs 52 for ifvg_edge B20). With close-mode Phase A (10
passes from B27, not 53 as projected), the two-phase sust = ~10/(10-11 per-year busts) ≈ ~1x —
borderline. Per-account net ($8,206) is 2.6x higher than ORB-reentry r0.75 ($3,131), which may
push $/mo above B3 even if sust is marginal.

Mechanism: no new code. Generate per-year close-mode LongOnly-iFVG equity CSVs:
  For each year in [2021, 2023, 2024, 2025, 2026]:
  `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
   --risk-pct 1.25 --partial-r 0 --set swing_stop_lookback=0 --set ifvg_entry_mode=close
   --set allowed_sides=long --killzones london,ny_am
   --out research/equity_b28/longonly_close_r1p25_{year}.csv`
Also at r1.0 (secondary).
Write scripts/run_b28_pipeline.py using:
  Phase A: equity_b27/close_r1p25_{year}.csv (from B27 — B27 must run first)
  Phase B: equity_b28/longonly_close_r1p25_{year}.csv

Fixed defaults: `ifvg_entry_mode=close`, `allowed_sides=long`, `killzones=london,ny_am`,
`partial_r=0`, `swing_stop_lookback=0`.
Defining-behavior tests: none needed (no code changes).

Success criteria (vs B27: projected ~$610/mo, ~4.08x sust):
- Primary: net/month >= B27 AND sust >= B27 → LongOnly-close Phase B beats ORB-reentry Phase B
- If $/mo >> B27 but sust < B27: note as a high-volume option (user risk tolerance decides)
- If sust < 2.62x (B21 baseline): B28 fails to beat B21 Phase B even with close mode

Caution: flat 5y sust 2.524x for B24-LongOnly-close does NOT translate directly to two-phase
sust. The per-year busts and per-account net in the two-phase model depend on the funded_sim
running over the stitched per-year equity — same correction (~50% fewer busts) used in B3/B21.

Prerequisite: B27 must complete first (B28 Phase A equity = equity_b27/).
Prior: ~55% that B28 beats B21 on $/mo but uncertain on sust. The per-account net is much higher
($8,206 vs $3,131) which drives $/mo strongly upward, but LongOnly-iFVG may cycle accounts
faster (higher volume), requiring more Phase A passes to sustain.

Source: B24 flat 5y funded_sim output (53 passes, 21 busts, 22 accounts, $180,534 net); B20
per-year methodology analysis (Lesson 42); scripts/research_phase_a_analysis.py projection.

## B29 — ORB-reentry with 10-minute opening range as Phase B in B27 two-phase pipeline  [done — rejected: 10-min generates 2.3x more XFA busts (30 vs 13) and -50% per-account net ($1,554 vs $3,131) vs 15-min; best pair $183/mo sust 0.33x — far below B3 threshold; flat-5y B22 finding (+9% $/mo) does not transfer to per-year reentry dynamics; B21 ($497/mo, sust 2.62x) remains best two-phase pipeline]
**REVISED PREREQUISITES (post-B27):** B27 showed close-mode Phase A gives only 10 passes (vs
projected 53). Phase A for B29 is the same equity_b27/ (10 passes). For B29 to beat B21 ($497/mo,
sust 2.62x), the 10-min ORB-reentry Phase B would need to generate fewer funded busts than
15-min r0.75 (which has 13 busts and gives sust=10/13=0.77x). Unless 10-min generates ≤ 4 busts
(very unlikely given B22's similar bust profile), B29 cannot beat B21 on sust. B29 may still be
useful as a $/mo comparison (if 10-min per-account net is higher). Lower priority given Phase A limitation.

Hypothesis: B22 showed ORB 10-min (no reentry) has +9% funded $/mo vs 15-min at r1.0 flat 5y,
with identical combine passes (10/61 each). B21 uses ORB-reentry at 15-min as Phase B. Switching
Phase B to 10-min ORB-reentry at r0.75 may improve $/mo in the two-phase model. The 10-min window
captures cleaner directional breakout structure (longs PF 1.15 vs 0.99 at 15-min per B22), which
should translate to better per-account funded earnings.

Mechanism: no new code. Generate per-year ORB-reentry 10-min equity CSVs:
  For each year in [2021, 2023, 2024, 2025, 2026]:
  `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
   --risk-pct 0.75 --partial-r 0 --set swing_stop_lookback=0 --set engine=orb
   --set orb_r_multiple=2.5 --set orb_reentry_after_stop=True --set orb_range_minutes=10
   --out research/equity_b29/orb_reentry_10min_r0p75_{year}.csv`
Write scripts/run_b29_pipeline.py using:
  Phase A: equity_b27/close_r1p25_{year}.csv (from B27 — B27 must run first)
  Phase B: equity_b29/orb_reentry_10min_r0p75_{year}.csv

Fixed defaults: `orb_range_minutes=10`, `orb_reentry_after_stop=True`, `partial_r=0`,
`swing_stop_lookback=0`.
Defining-behavior tests: none needed (no code changes).

Success criteria (vs B27 ORB-reentry 15min: projected ~$610/mo, ~4.08x sust):
- Primary: net/month improves vs B27 with sust still >= 2.62x (B21 baseline)
- If $/mo improves but sust drops: report as high-yield option with lower pipeline buffer
- The 10-min window effect on reentry specifically (second signal after stop) is untested;
  10-min longs outperform 15-min (B22 PF 1.15 vs 0.99), but reentry direction may differ

Prerequisite: B27 must complete first (Phase A equity).
Prior: ~40% that B29 improves B27's $/mo. The 10-min ORB improvement (+9% $/mo in B22)
was for non-reentry ORB at r1.0; at r0.75 and with reentry, the effect may differ. Combine
passes with 10-min may differ enough to change sust. Low code risk, cheap test.

Source: B22 ORB range_minutes benchmark (10-min +9% funded $/mo); B22 long/short PF reversal
at 10-min (longs 1.15 vs 0.99 at 15-min); wk1-b18 RESEARCH theme 4.

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk2-r1 (post-B29 backlog replenishment)  [done — 3 items appended: B30 DOW filter, B31 Phase A higher risk, B32 ORB Phase B r=0.5]

All B1-B29 items completed; all 6 prior research sessions done. This session replenishes with new
testable hypotheses. Primary source: 5y MFE/MAE day-of-week mining + web research.

**Key data findings:**
- **iFVG DOW (n=2477, excl 2022):** Tuesday PF=0.917 (n=539, net=-$15,792) is the only loss-making day.
  All other days: Mon 1.088, Wed 1.182 (best), Thu 1.032, Fri 1.008.
- **ORB DOW (n=1030, excl 2022):** Monday PF=0.898 (-$5,100) and Wednesday PF=0.942 (-$3,131) both
  loss-making. Friday PF=1.775 (+$29,446) is by far the strongest ORB day — 60% of total net from 20%
  of trades. Tuesday (1.351) and Thursday (1.282) also strong.
- **Mechanisms:** iFVG Tuesday = post-Monday consolidation (chop); ORB Monday = gap reversal false
  breakouts; ORB Wednesday = FOMC announcement days (choppy); ORB Friday = end-of-week position
  squaring (directional momentum). All are data-derived, not external claims.
- **B21 Phase A risk sensitivity:** never tested above r=1.25%. If higher risk generates more
  Phase A passes per year (higher expected monthly gain / fixed $3k target), B21 $/month improves.
- **B21 Phase B risk floor:** r=0.5 not tested. Completing the risk curve below r=0.75 clarifies
  whether r=0.75 is a true optimum or if lower risk improves pipeline sustainability further.
- **Web search:** No new mechanism families (Lesson 6 confirmed 5-for-5; SSRN/arxiv finds no robust
  signals on index futures). Topstep published 16.8% combine success rate vs our 21% baseline.

## B30 — Day-of-week (DOW) filter: skip_trading_days parameter  [done — rejected: iFVG skip Tuesday catastrophically worse (PF 0.996, sust 0.27x vs B24 1.167/2.524x — Tuesday longs in close-mode long-only london+ny_am ARE profitable; removing them destroys edge); ORB skip Mon+Wed mixed ($/mo +42% to $708/mo, sust -19% to 2.12x, 16 busts > 13 criterion — fails primary); skip_trading_days feature ships default-off; Lessons 67-68]
Hypothesis: iFVG Tuesday (PF=0.917) and ORB Monday+Wednesday (PF=0.898/0.942) are structurally
loss-making over 5 years. Suppressing signals on these days should improve funded-phase PF and
reduce bust frequency without touching the entry/exit mechanics.

**iFVG without Tuesday:** estimated PF 1.043 → ~1.080 (+3.5%), volume 2477 → ~1938 trades (78%).
At ~55/month, combine phase may lose some borderline-passing months (Lesson 2: 60+ trades needed).
Route to funded objective only.

**ORB without Mon+Wed:** estimated PF ~1.10 → ~1.147 (+4.3%), volume 1030 → ~617 trades (60%).
At ~14/month non-reentry (vs ~23/month full), funded phase per-account net drops but bust rate may drop
proportionally or more. Route to funded Phase B only (combine already tested; combining ORB with DOW
filter is too sparse at ~7 passes/61mo estimated).

Mechanism (code required, ~30 lines total):
- Add `skip_trading_days: list[str] = Field(default_factory=list)` to StrategyParams (e.g.,
  `["Tuesday"]` or `["Monday", "Wednesday"]`). Day names match Python's `datetime.strftime("%A")`.
- In SweepDisplacementComposer (iFVG signal path), check `bar.ts` ET day: if in skip_trading_days,
  return None before any other logic (don't suppress the detector state, only signal emission).
- In ORBDetector.on_bar, same check: if bar.ts ET weekday in skip_trading_days, return None.
- ET conversion: use `bar.ts.astimezone(ZoneInfo("America/New_York")).strftime("%A")`.
- The range/sweep state continues accumulating (Monday gap can still build the range); only signals
  are suppressed. This prevents discarding context that persists across the gap.

Fixed defaults: `skip_trading_days=[]` (no suppression, existing behavior preserved).

Defining-behavior tests (tests/test_dow_filter.py):
1. iFVG skip_trading_days=["Tuesday"]: bars on Tuesday → no signal from composer; Monday bar → signal
2. ORB skip_trading_days=["Monday"]: Monday breakout bar → on_bar returns None; Tuesday bar → Signal
3. skip_trading_days=[] (default): Tuesday/Monday bars fire normally (existing behavior unchanged)
4. Day check uses ET timezone (a bar at 23:45 UTC Monday = Tuesday ET → correctly suppressed if
   skip_trading_days=["Tuesday"])

Benchmark:
1. iFVG funded (B19 LongOnly + london+ny_am, r1.25 close mode): add `--set skip_trading_days=Tuesday`
   Compare to B19 r1.25 baseline: PF=1.173, sust=1.600x. Success: PF >= 1.20 AND sust >= 1.60x.
2. ORB-reentry Phase B (B21 config, r0.75): add `--set skip_trading_days=Monday,Wednesday`
   equity_export + per-year ORB Phase B equity → two-phase pipeline.
   Compare to B21 Phase B baseline: 13 busts, $3,131/account, sust 2.62x (with B21 Phase A).
   Success: busts < 13 AND $/month >= $497 with same Phase A (B21 equity_b1/control_r1p25).

Stop rule: both iFVG and ORB DOW-filtered variants must independently improve vs their respective
baselines on funded PF/sust. A filter that improves one but fails the other is tested separately.

Prior: ~55% for iFVG Tuesday skip (clear structural pattern, $15.8k net cost removed). ~45% for
ORB Mon+Wed skip (correct mechanisms but 40% volume drop is steep; might hurt per-account net enough
to offset the bust reduction).

Source: scripts/research_dow_analysis.py (this session) — 5y MFE/MAE per-day PF analysis.
Mechanisms: iFVG Tuesday = post-Monday consolidation chop; ORB Monday = gap reversal false breakouts;
ORB Wednesday = FOMC announcement days; all corroborated by institutional calendar literature.

## B31 — Phase A higher-risk sensitivity (r=2.0) to increase annual combine passes  [done — candidate: r=2.0 deployed settings → $508/mo, sust 2.85x — beats B21 on both criteria via faster combine cycling (27.8d vs 38.1d); r=1.5 shows no improvement vs r=1.25; config discrepancy documented]
Hypothesis: B21 Phase A runs iFVG r=1.25%. The $3k combine target is FIXED. At r=2.0%, each
winning trade earns 1.6× more — fewer winning trades needed to reach the $3k threshold. Expected
monthly gain rises from ~$3.5k (r=1.25) to ~$5.6k (r=2.0), making the $3k target easier to reach
on average. This should increase Phase A pass rate AND pass speed (fewer days to reach $3k from
a positive starting position). Faster passes → more Phase A supply per year → higher pipeline $/month.

Mechanism: no new code. Generate per-year Phase A equity at r=2.0:
  For each year in [2021, 2023, 2024, 2025, 2026]:
  `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
   --risk-pct 2.0 --partial-r 0 --set swing_stop_lookback=0 --set target_clarity_mode=reject
   --set ifvg_entry_mode=ifvg_edge --out research/equity_b31/ifvg_r2p0_{year}.csv`
Write scripts/run_b31_pipeline.py using:
  Phase A: equity_b31/ifvg_r2p0_{year}.csv
  Phase B: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21)

Counterarguments (assign prior weight):
- At r=2.0, MLL ($2k below starting balance) is breached with only 2-3 consecutive full stops
  ($50k × 0.02 × 2.5R WR-flip = $1k per loss at worst; 2-3 losses = $2k) → busts happen faster
- But faster busts = more attempts per year = possibly same total passes with faster cycling
- PF is unchanged by risk level (it's a dimensionless ratio) → pass RATE per attempt stays same
- The benefit is purely from faster cycling: if attempts complete 2x faster, 2x more passes/year

The crucial test: does the funded_sim show more combined attempts + passes over the 5y period at
r=2.0 vs r=1.25? If Phase A passes roughly double (to ~68), the pipeline can sustain more Phase B
accounts even if Phase B bust rate stays at 13 — sust stays 2.62x but $/month could double.

Also test r=1.5 (secondary) to map the curve: r=1.25 → r=1.5 → r=2.0 → identify the optimum.

Fixed defaults: same research baseline (ifvg_edge, partial_r=0, lookback=0, target_clarity=reject).
Defining-behavior tests: none needed (no code changes).

Success criteria (vs B21: $497/mo, sust 2.62x):
- Primary: $/month improves AND sust stays >= 2.62x (both criteria)
- If sust drops: report as high-yield option with risk commentary
- If Phase A passes < 34 (fewer than r=1.25): reject (higher risk is strictly worse at this risk level)

Prior: ~40% that r=2.0 generates more Phase A passes per year (faster cycling is the mechanism;
the question is whether MLL busts happen proportionally faster or slower than passes; if both scale
linearly with risk, cycling is faster at same efficiency, and $/month stays the same or improves
modestly from fewer combine attempt fees paid per pass).

Warning: do NOT use 2022 for any r=2.0 equity generation (frozen holdout per protocol).

Source: Phase A pipeline analysis (wk2-r1); B1-B21 Phase A stats. This is the only Phase A risk
level we haven't tested (B1 tested r=0.5/0.75/1.0/1.25 for Phase B ORB; Phase A was always r=1.25).

## RESEARCH — Session wk2-r2  [done — 3 items appended: B35 ORB-reentry r-sweep, B36 Phase A config-parity r=2.0, B37 combined-engine Phase A test]

Session 39 (39 % 3 == 0) + last 2 completed items B30/B31 are build items → protocol mandates research/ideation. B32 (pending) will be claimed in the next session.

**Key data findings:**
- **ORB exit structure (n=1,030, 5y excl 2022):** 55.0% SL, 33.6% profitable EOD flatten, 11.4% target hits (MFE>=2.5R). The r-multiple directly affects only 11.4% of trades. EOD management is the primary value driver.
- **Plain ORB r-sweep paired with B31 Phase A (r=2.0, 37 passes):** r=0.5: $300/mo sust=6.17x; r=0.75: $387/mo sust=2.64x; r=1.0: $408/mo sust=1.37x; r=1.25: $531/mo sust=1.03x. Primary criterion (sust>=2.62x) fails at r>=1.0. Secondary (sust>=1.26x) fails at r>=1.25.
- **ORB-reentry vs plain ORB at r=0.75:** reentry $508/mo sust=2.85x vs plain $387/mo sust=2.64x (+31% $/mo, similar sust). The reentry mechanism captures profitable reversal-day EOD flattens.
- **B31 winner confirmed:** iFVG r=2.0 deployed + ORB-reentry r=0.75 = $508/mo sust=2.85x. No plain ORB Phase B meets primary sust criterion.
- **No new mechanism families** -- Lesson 6 confirmed 6-for-6.

## B32 — ORB-reentry Phase B at r=0.5 (below-optimum risk floor)  [done — rejected: over-conservative; sust 3.08x (vs 2.85x at r=0.75) BUT $/mo only $246 (vs $508) — pipeline throughput-constrained at r=0.5 (only 13 accounts/5y, $1,935/acct); r=0.75 confirmed as Phase B risk optimum]
Hypothesis: B21's Phase B optimum is r=0.75 (sust 2.62x, $3,131/account, 13 busts). B1 showed
plain ORB r=0.5 standalone had 6 XFA busts (flat 5y) vs 8 Combine passes — "genuinely positive."
ORB-reentry at r=0.5 should have fewer busts than r=0.75 (smaller per-trade risk → smaller daily
swings → harder to breach MLL in a few bad days). The question: does the per-account net reduction
(~$2,087 estimated at r=0.5 vs $3,131 at r=0.75) make $/month lower, or does high sust compensate?

At r=0.5, per-year busts could be as low as 3-6 (vs 13 at r=0.75). With Phase A 34 passes:
sust = 34 / 4 = ~8.5x. But $/month depends on how many funded accounts cycle in 5y. At very high
sust (>> 1), the pipeline is supply-constrained on Phase B: very few funded accounts bust per year,
so very few new ones are opened per year. Net $/month might be only $100-200 (account earns ~$2k
over many months before anyone replaces it) — well below B21's $497/mo.

Value of running this: definitively closes the question of whether the risk sensitivity curve has
a lower optimum than r=0.75. If confirmed, r=0.75 is the true optimum. If r=0.5 gives more $/month
(unexpected but possible if the funded account survival rate lets it run for 6+ months earning multiple
payouts), that's a significant finding.

Mechanism: no new code. Generate per-year ORB-reentry r=0.5 equity:
  For each year in [2021, 2023, 2024, 2025, 2026]:
  `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
   --risk-pct 0.5 --partial-r 0 --set swing_stop_lookback=0 --set engine=orb
   --set orb_r_multiple=2.5 --set orb_reentry_after_stop=True
   --out research/equity_b32/orb_reentry_r0p5_{year}.csv`
Run B21 pipeline model with equity_b32/ as Phase B (Phase A = equity_b1/control_r1p25 unchanged).

Fixed defaults: orb_reentry_after_stop=True, orb_r_multiple=2.5, partial_r=0, swing_stop_lookback=0.
Defining-behavior tests: none needed (no code changes).

Success criteria (vs B21: $497/mo, sust 2.62x):
- Primary: any improvement in sust while $/month >= $300/mo (meaningful minimum)
- If sust >> 2.62x but $/month < $300: note as "over-conservative, not practically useful"
- If sust < 2.62x: reject (r=0.5 is strictly worse than r=0.75 across the board)

Prior: ~25% that r=0.5 beats B21 on $/month (pipeline math suggests the sequential model limits
throughput when sust >> 1; conservatively-sized accounts earn slowly, and very few busts means
very few account replacements, so $/month is determined by time-to-payout per account rather than
by volume of accounts). This is an important but expected-to-reject item that formally closes the
lower end of the Phase B risk sensitivity ladder.

Source: B1 Phase B risk ladder (r=0.5/0.75/1.0/1.25 plain ORB); B21 established r=0.75 reentry
as the optimum. This item extends the ladder to r=0.5 for reentry ORB.

## B33 — Anticipatory probe entry + scale-up on iFVG confirmation  [done — rejected at Phase 1; 56.4% probe stop rate, expected_R = -0.098R; pre-inversion entries bleed (see Lesson 72); Phase 2 engine NOT built]
Lawrence-requested directly. Rank this ABOVE B32 when claiming fresh.

Hypothesis: enter a SMALL-risk "probe" on a closed-bar S/R reaction (liquidity
sweep + reclaim, or break + hold) BEFORE the iFVG/FVG forms, then ADD size
(increase risk/reward) when the existing iFVG signal confirms the SAME
direction. The probe captures a better average entry on trades that go on to
confirm; the scale-up concentrates the larger risk only on confirmed setups.
Net thesis: better blended entry on winners + small bleed on unconfirmed
probes > the cost of trading the probe leg without the inversion quality filter.

Priors / lessons that bear on this (read FIRST):
- Lesson 1 — the iFVG INVERSION is the quality filter. A probe fires BEFORE the
  inversion, so it is a lower-quality entry by construction. The edge must come
  from (a) blended-entry improvement on confirmed trades and (b) probe-only legs
  being cheap. If unconfirmed probes bleed, this fails.
- Forming-bar lesson (memory: project_forming_bar_gate) — NO mid-bar entries.
  The probe MUST trigger on a CLOSED bar (sweep+reclaim close), never intrabar.
- B2 lesson — scaling EXITS killed two-thrust winners. This scales ENTRIES
  (a pyramid), which is different and untested; watch for the symmetric failure
  (probe stop hit on the pullback before the iFVG confirms → realized loss, then
  the confirmed trade wins without the probe / re-enters worse).
- sweep_bos already encodes "sweep + reclaim" detection and the iFVG path
  already tracks `awaiting_sweeps`; reuse them, do not reinvent the level logic.
  Use ONE level source (the swing highs/lows the iFVG sweep detection uses).

PHASE 1 — cheap data-mining falsification FIRST (no engine code; go/no-go gate):
Using 5y bars + iFVG signal replay + the existing excursion tooling (B2/B11),
over 2021/2023/2024/2025-26 (NOT 2022):
1. For every historical iFVG signal, look back up to `probe_lookback_bars`=6
   CLOSED bars: did a probe trigger (sweep of a tracked swing level with a
   closed-bar reclaim, same eventual direction as the iFVG) occur?
2. Measure:
   a. Confirmation rate: of all probe triggers, % followed by a same-direction
      iFVG within `probe_confirm_window_bars`=8 while the probe stop (swept
      extreme ± stop_buffer) is unhit.
   b. Probe-only economics: MFE/MAE (R-units, existing excursion infra) of probe
      triggers that NEVER confirm — net-negative, and how negative?
   c. Blended-entry gain: for confirmed cases, probe entry price vs iFVG entry
      price, expressed in R of the eventual stop distance.
3. GO/NO-GO: proceed to Phase 2 ONLY if the arithmetic plausibly nets positive:
   expected_R = conf_rate x blended_gain − (1 − conf_rate) x probe_only_loss.
   If clearly negative, REJECT here and document — build no engine.

PHASE 2 — engine (only if Phase 1 = GO):
Default-off MODE on the SweepDisplacement/iFVG composer (it needs the iFVG
confirmation signal, so it couples to that path rather than being a standalone
engine). StrategyParams (all default-off / 0):
- `probe_entry_enabled: bool = False`
- `probe_risk_frac: Decimal = Decimal("0.33")`  (probe size = frac x normal risk)
- `probe_confirm_window_bars: int = 8`
- `probe_lookback_bars: int = 6`
Probe trigger (closed bar): a tracked swing level is swept (wick beyond) and the
bar CLOSES back across the level (reclaim). Direction = reclaim direction. Entry
at that bar close; stop = swept extreme ± stop_buffer; probe target = fixed 1.0R,
else time-stop at confirm_window booking BE-or-better (deterministic).
Scale-up: if the iFVG signal fires same-direction within confirm_window while the
probe is open, ADD size so total = normal per-trade risk, set the bracket target
to the full r_multiple, added leg stop = the iFVG signal stop. TOTAL position
risk CAPPED at the normal per-trade risk (conservative; stays inside the tested
risk envelope + MLL). Note "over-size on confirm" as a follow-up only if the
conservative version shows promise. Closed-bar confirmation only; exits via
`runner.exit_request`.

Defining-behavior tests (tests/test_probe_entry.py):
1. probe_entry_enabled=False (default): no probe ever; iFVG output byte-identical
   to baseline.
2. Sweep+reclaim closed bar, enabled → probe Signal at frac size, stop beyond the
   swept extreme.
3. Probe open + same-direction iFVG within window → add leg brings total to
   normal size, target = full r_multiple.
4. Probe open + NO iFVG within window → probe exits at modest target / BE, no
   scale-up.
5. Sweep that closes WITHOUT reclaim → no probe (closed-bar reclaim required).
6. Total risk after scale-up <= normal per-trade risk (assert combined
   stop-distance x size).

Benchmark (BOTH objectives; parity flags `--partial-r 0 --set swing_stop_lookback=0`):
- Combine: run_monthly_combine probe-on vs probe-off (control).
- Funded: equity_export + funded_sim vs control AND vs B21/B31 best
  ($497-508/mo, sust 2.6-2.85x).

Success criteria: probe mode must improve PF AND the objective metric vs
probe-off. Stop rule: loses on BOTH PF and the objective vs probe-off → reject
(no tuning of frac/window beyond the one declared default — this is a mechanism
test, not a sweep).

Source: Lawrence direct request 2026-06-13 — anticipatory S/R-reaction entry at
small risk, scale up risk/reward when the FVG/iFVG actually appears. Connects
existing sweep detection (awaiting_sweeps / sweep_bos) to iFVG confirmation as a
two-stage pyramid entry.

## B34 — Breaker-block entry + OTE retracement zone  [done — rejected at Phase 1; OTE retrace depth is INVERSELY correlated with BOS forward performance (no-retrace WR 84.4%/PF 18.90 vs OTE WR 37.1%/PF 2.06); 56% of BOS events stopped during retrace window; Phase 2 engine NOT built; see Lessons 73-74]
Lawrence-requested (ICT "FVG and OTE entry" reference video, Gold chart). Rank
ABOVE B32, AFTER B33.

Hypothesis: an ICT breaker-block + OTE setup improves entry quality. Sequence:
liquidity sweep (sell-stops/buy-stops taken) -> break of structure the other way
-> the "breaker" (last opposing order block before the BOS move) is the entry
zone -> require price to retrace into the OTE window (0.62-0.79 Fibonacci of the
impulse leg) before entering -> target the opposing liquidity pool.

READ FIRST -- strong adverse priors (this is mostly recombined, partly already
rejected):
- Memory `project_fib_filter_finding`: a Fibonacci-retracement filter was proven
  to have NO edge over 2.5y of MGC -- every fib bucket was equally negative.
  OTE is a fib-retracement concept. Do not assume it works; the burden of proof
  is high. (Caveat justifying a re-test: that test was MGC/2.5y; this is MNQ/5y
  and the user trades MNQ.)
- The `sweep_bos` engine ALREADY implements sweep -> break-of-structure ->
  order-block fallback zone. A "breaker block" is essentially sweep_bos's OB leg
  with the violation/retest refinement. Build on sweep_bos; do NOT write a new
  engine from scratch. Diff against what sweep_bos already does before adding.
- LESSONS: external/practitioner claims have failed to transfer here 4-for-4.
  ICT-concept videos are exactly that class. Falsify cheaply, don't trust.

PHASE 1 -- cheap falsification FIRST (re-test the fib prior on MNQ; go/no-go):
No engine code. Reuse the iFVG/sweep_bos signal replay + excursion tooling over
MNQ 5min, 2021/2023/2024/2025-26 (NOT 2022):
1. For each impulse leg following a sweep+BOS, bucket the eventual entry/retrace
   depth into fib bands (<0.5, 0.5-0.62, 0.62-0.79 [=OTE], 0.79-1.0) and measure
   forward PF / win-rate / MFE per bucket.
2. GO/NO-GO: proceed to Phase 2 ONLY if the 0.62-0.79 (OTE) bucket shows
   MATERIAL positive separation from the others (not "all buckets equally
   negative" as on MGC). If no separation -> REJECT here, document that the MGC
   fib no-edge finding replicates on MNQ, build nothing.

PHASE 2 -- engine refinement (only if Phase 1 = GO):
Default-off MODE on `sweep_bos` (reuse its sweep + BOS + OB detection):
- StrategyParams (default-off / 0):
  - `breaker_ote_enabled: bool = False`
  - `ote_low: Decimal = Decimal("0.62")`, `ote_high: Decimal = Decimal("0.79")`
  - `breaker_require_violation: bool = True`  (OB must be violated->retested =
    a true breaker, vs a plain OB)
- Entry: after sweep+BOS, arm the breaker zone; enter only when a CLOSED bar
  retraces into BOTH the breaker zone AND the OTE fib window of the impulse leg,
  in the BOS direction. Stop beyond the breaker / swept extreme + stop_buffer.
  Target = opposing liquidity / full r_multiple (reuse sweep_bos target logic).
- Closed-bar confirmation only. Exits via runner.exit_request.

Defining-behavior tests (tests/test_breaker_ote.py):
1. breaker_ote_enabled=False (default): sweep_bos output byte-identical to baseline.
2. Sweep+BOS, retrace into breaker zone AND 0.62-0.79 -> Signal at the OTE bar close.
3. Retrace into breaker zone but only to 0.5 (shallower than OTE) -> no Signal.
4. breaker_require_violation=True: OB never violated/retested -> no breaker, no Signal.
5. Retrace deeper than 0.79 (blew past OTE) -> no Signal.

Benchmark (BOTH objectives; parity flags `--partial-r 0 --set swing_stop_lookback=0`):
- vs sweep_bos baseline (breaker_ote off) AND vs control / B21 best.

Success criteria: breaker+OTE must improve PF AND the objective vs sweep_bos
baseline. Stop rule: loses on BOTH vs baseline -> reject. NO fib-band tuning
beyond the one declared 0.62/0.79 default -- mechanism test, not a sweep.

Source: Lawrence ICT reference (FVG + OTE entry, breaker blocks). Mechanism =
sweep_bos + OB-violation refinement + OTE fib gate. Directly tests whether the
documented MGC fib no-edge result holds on MNQ.

## B35 — Daily-bias directional gate (ICT "Power of Three")  [done — rejected: gate-on 4/61 (7%) PF 0.84 vs gate-off 7/61 (11%) PF 1.00; stop rule triggered on BOTH PF and passes; short-bias day PF 0.66 (worse than baseline — Lesson 8 extends to DOW bias); volume collapsed 86% (5.6/mo); daily_bias_gate_enabled ships default-off; 5 tests, 649 total green]
Source: JadeCap "The EASIEST Way to Trade ICT in 2025" (youtu.be/ZqPEuatIYMc).
The one part of that video NOT already covered by iFVG/sweep_bos/B33/B34.

Hypothesis: a daily directional bias + "room to target" gate improves quality.
Rules from the video, made deterministic:
- Daily bias (ONE fixed definition): bias is LONG if the prior completed ET-day
  closed above its open, SHORT if it closed below. (No discretion; computed from
  the daily bar.)
- Direction gate: on a LONG-bias day, suppress all SHORT signals; on a
  SHORT-bias day, suppress all LONG signals.
- "Room to target" gate: target = prior ET-day HIGH (long) / LOW (short). Only
  allow longs while current price < prior-day-high; only allow shorts while
  current price > prior-day-low. Once price has reached the prior-day extreme
  (target hit), suppress further same-direction entries for the rest of the day
  ("do not trade if the market has already hit the target").

Priors: B5 (prior-day RANGE qualifier) was REJECTED — but that was a volatility
gate, not a DIRECTION gate; different mechanism. Lesson: "day-level gates can't
time engines" is a caution, but this gates DIRECTION + target-room, not entry
timing. Medium-low prior (~30%). External ICT claims are 4-for-4 failures here.

Mechanism: default-off `daily_bias_gate_enabled: bool = False` in StrategyParams.
Needs the prior ET-day OHLC (open/high/low/close). The backtest already feeds
bars; compute the prior completed daily bar from the 5min stream (track
rolling per-ET-day OHLC, freeze at ET-day rollover) — no HTF feed dependency.
Gate is applied in the iFVG signal path (suppress at emission, keep detector
state). Closed-bar only.

Defining-behavior tests (tests/test_daily_bias_gate.py):
1. Gate off (default): signals unchanged vs baseline.
2. Prior day closed up (long bias): a short signal is suppressed; a long fires.
3. Prior day closed down (short bias): a long is suppressed; a short fires.
4. Long bias, price already >= prior-day-high: long suppressed (target hit).
5. ET-day rollover: bias/target recompute from the newly completed daily bar.

Benchmark (BOTH objectives; parity flags `--partial-r 0 --set swing_stop_lookback=0`):
- run_monthly_combine + equity_export/funded_sim, gate-on vs control.
Success: improves PF AND the objective vs gate-off. Stop rule: loses on BOTH ->
reject. No tuning of the bias definition beyond the one declared rule.

## B36 — FVG-midpoint stop placement  [done — rejected 2026-06-14]
Source: same video — "stop loss at 50% of the Fair Value Gap" (vs our current
stop = beyond the swept extreme / swing + stop_buffer).

Hypothesis: a stop at the FVG midpoint (50% between proximal and distal edges)
is tighter than the swing/swept-extreme stop, raising R per winner. On the
FUNDED objective (R-sensitive, payout-driven) a tighter stop could lift $/trade
IF the win rate doesn't collapse. The risk: NQ 5min displacement bars are large,
so a mid-FVG stop sits close to entry and may get wicked out far more often.

Mechanism: default-off `stop_mode: str = "swing"` (current behavior) with new
value `"fvg_mid"` in StrategyParams. When `fvg_mid`: stop = midpoint of the FVG
zone that produced the signal (the engine already has the FVG proximal/distal
prices). All other logic (target = r_multiple from entry, sizing) unchanged —
note that a tighter stop with the SAME r_multiple means a nearer target too;
ALSO benchmark a variant that keeps the absolute target distance (so the tighter
stop genuinely raises the R multiple). Report both.

Defining-behavior tests (tests/test_fvg_mid_stop.py):
1. stop_mode="swing" (default): stop placement byte-identical to baseline.
2. stop_mode="fvg_mid": stop = (fvg_proximal + fvg_distal)/2 for a known FVG.
3. fvg_mid stop is tighter than the swing stop for a wide-swing setup (assert
   distance ordering).
4. Sizing respects the new (smaller) stop distance (risk-pct held constant).

Benchmark (BOTH objectives; parity flags `--partial-r 0 --set swing_stop_lookback=0`):
- equity_export/funded_sim + run_monthly_combine, fvg_mid vs swing baseline.
Success: improves PF AND the objective vs swing stop. Stop rule: loses on BOTH
-> reject. Prior ~35% (tighter stop helps funded R but NQ wicks may dominate).

(Other JadeCap video elements map to existing/queued work and are NOT separately
queued: FVG=core iFVG; MSS=sweep_bos BOS; Turtle Soup=B33 sweep+reclaim probe;
Breaker Block + Premium/Discount/OTE=B34; 9:30-11:30 EST=killzone filter / B18;
no-overnight=EOD flatten. "Trade smaller if price already expanded" overlaps the
B35 room-to-target gate.)

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk2-r2 appendix: pipeline research items

Items B38-B40 are pure-pipeline research (no new strategy code). They extend the
funded-pipeline optimization thread started by B21/B31.
(Named B38-B40 to avoid collision with strategy B35/B36 done items above.)

## B38 — ORB-reentry Phase B r-multiple sensitivity (r=1.0 and r=1.25)  [done — rejected: r=1.0 stop rule (both metrics below B31: $431/mo sust 0.90x); r=1.25 sust collapses to 0.77x despite $545/mo; ORB-reentry sust advantage over plain ORB at r=0.75 reverses at r=1.0 (reentry 0.90x < plain ORB 1.37x); r=0.75 confirmed as Phase B optimum from both below (B32) and above (B38)]
Hypothesis: ORB-reentry at r=0.75 gives $508/mo sust=2.85x (B31 winner). This session
showed plain ORB's sust collapses at r>=1.0 (r=1.0: sust=1.37x; r=1.25: sust=1.03x).
But the ORB-reentry mechanism adds profitable reversal entries without proportionally
increasing bust frequency -- at r=0.75, reentry sust (2.85x) is better than plain ORB
sust (2.64x) at the same r. The question: does the reentry mechanism's sust advantage
persist at r=1.0 and r=1.25, or does reentry accelerate busts at higher r just as
plain ORB does? If reentry at r=1.0 gives sust >= 2.62x AND $/mo > $508, it beats B31.

Mechanism: no new code. Generate per-year ORB-reentry equity CSVs at r=1.0 and r=1.25:
  For each year in [2021, 2023, 2024, 2025, 2026]:
  `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
   --risk-pct 1.0 --partial-r 0 --set swing_stop_lookback=0 --set engine=orb
   --set orb_r_multiple=2.5 --set orb_reentry_after_stop=True
   --out research/equity_b35/orb_reentry_r1p0_{year}.csv`
  Repeat at risk-pct 1.25. Write scripts/run_b35_pipeline.py using:
    Phase A: equity_b31/ifvg_r2p0_{year}.csv (B31 winner Phase A)
    Phase B: equity_b35/orb_reentry_r1p0_{year}.csv and r1p25

Also run Phase A from equity_b1/control_r1p25 (B21 Phase A) for full matrix:
  B21 Phase A x ORB-reentry r=1.0, r=1.25 (2 more combos)

Fixed defaults: orb_reentry_after_stop=True, orb_r_multiple=2.5, partial_r=0, lookback=0.
Defining-behavior tests: none needed (no code changes).

Success criteria (vs B31 winner: $508/mo, sust 2.85x):
- Primary: $/mo > $508 AND sust >= 2.62x (beats B31 on $/mo, stays in B21 sust range)
- If sust drops below 2.62x at any tested r: apply stop rule (reject that r-level)

Prior: ~35% that r=1.0 reentry maintains sust >= 2.62x. The plain ORB data shows
sust collapses to 1.37x at r=1.0; if reentry adds ~0.2-0.3 sust vs plain at r=1.0
(proportionally similar to the 0.21-point gain at r=0.75), estimated reentry sust = ~1.57x
-- still below criterion. However, the reentry mechanism's bust reduction is nonlinear
and untested at higher r. Worth one test run.

Stop rule: if any r level loses on BOTH $/mo and sust vs B31 winner, that level is
rejected immediately (no further tuning).

Source: wk2-r2 plain ORB r-sweep data. ORB-reentry at r=0.75 = $508/mo sust=2.85x (B31);
plain ORB at r=1.0 = $408/mo sust=1.37x; plain ORB at r=1.25 = $531/mo sust=1.03x.
The sust advantage of reentry over plain (0.21x at r=0.75) is the only empirical basis
for projecting r=1.0 reentry -- the actual result could be substantially different.

## B39 — B21 research-baseline Phase A config-parity test at r=2.0  [done — rejected: r=2.0 at research baseline gives 12/82 Phase A passes (vs B21's 34/162), reset $1,025/funded, pipeline $394/mo sust=0.92x — stop rule on both metrics; B31's r=2.0 advantage is config-specific (deployed combined+all-day generates 25 more passes at same risk); do NOT recommend r=2.0 as universal Phase A upgrade; Lesson 79]
Hypothesis: B31's candidate ($508/mo, sust=2.85x) used deployed Phase A settings
(engine=combined, all-day killzones, MNQ body=5.0/stop=3.0 overrides). B21's benchmark
($497/mo, sust=2.62x) used the research baseline (engine=ifvg, named sessions,
ifvg_edge, no MNQ body/stop overrides). These are different configs -- we can't cleanly
isolate the r=2.0 contribution from the config differences.

Clean test: rerun B21 research-baseline Phase A at r=2.0 (same as B21 but risk=2.0%):
  `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
   --risk-pct 2.0 --partial-r 0 --set swing_stop_lookback=0 --set target_clarity_mode=reject
   --set ifvg_entry_mode=ifvg_edge --out research/equity_b36/ifvg_edge_r2p0_{year}.csv`
  Note: NO MNQ overrides (body=1.0, stop=0.30, named sessions) -- exact B21 baseline.

Pair with B21 Phase B (equity_b21/orb_reentry_r0p75). Compare to:
  - B21 (ifvg_edge r=1.25 + reentry r=0.75): $497/mo, sust=2.62x
  - B31 (deployed r=2.0 + reentry r=0.75): $508/mo, sust=2.85x

This answers: is r=2.0's +$11/mo improvement real and config-agnostic, or an artifact
of the deployed Phase A config being different from B21?

Fixed defaults: ifvg_entry_mode=ifvg_edge, partial_r=0, swing_stop_lookback=0,
target_clarity_mode=reject, NO MNQ overrides (standard body/stop thresholds).
Defining-behavior tests: none needed.

Success criteria (vs B21: $497/mo, sust 2.62x):
- Primary: $/mo > $497 AND sust >= 2.62x at r=2.0 (research baseline confirms r=2.0 advantage)
- If r=2.0 research baseline is WORSE than B21: r=2.0 advantage is config-specific, not
  from the risk level itself -- do not recommend r=2.0 as a general upgrade

Prior: ~55% that r=2.0 shows improvement even in research-baseline config. The mechanism
(faster combine cycling from higher volatility) is independent of body/stop overrides.
The risk level is what drives the cycle-duration reduction, not the config.

Source: B31 config discrepancy (JOURNAL wk2-b31); Lesson 69 (cycle-speed mechanism).
Needed to confirm B31's candidate before Lawrence deploys higher risk live.

## B40 — Combined-engine vs iFVG-only Phase A in the combine (engine sensitivity)  [done — candidate: engine=combined 10/61 (16%) PF 1.02 vs engine=ifvg 5/61 (8%) PF 0.85 at B21 research baseline; 2x more passes, +20% PF; ORB signals fill the volume gap in named-session configs; deployed engine=combined Phase A is structurally correct]
Hypothesis: B21 used engine=ifvg (iFVG-only signals) for Phase A combine. The deployed
bot uses engine=combined (iFVG + ORB signals both contribute to the combine phase).
B31 also used engine=combined for Phase A. We have never isolated the engine= parameter's
effect on combine pass rate.

Test: run_monthly_combine.py with engine=ifvg vs engine=combined, holding all other
parameters at B21 research baseline (named sessions, ifvg_edge, lookback=0, partial_r=0,
target_clarity=reject). Compare combine passes/61 and run PF.

If engine=combined gives materially more Phase A passes than engine=ifvg at the same
research baseline, this validates the deployed combined-engine Phase A. If not, the
B21 ifvg-only Phase A is optimal and the deployed combined engine adds noise.

Mechanism: no new code. StrategyParams already supports engine=combined.

Fixed defaults: both modes use B21 research baseline (ifvg_edge, named sessions,
lookback=0, partial_r=0, target_clarity=reject). Only engine= differs.

Defining-behavior tests: none needed (no code change).

Success criteria (vs B21 Phase A baseline: 34/162 passes over 5y, ~7/61 monthly):
- engine=combined achieves materially more combine passes than engine=ifvg at same config:
  >= 10/61 (43% more passes vs ifvg baseline 7/61) = signal that combined Phase A is better
- If engine=combined <= engine=ifvg on passes AND run PF: stop rule triggered (combined
  Phase A rejected; recommend ifvg-only for the combine phase)

Prior: ~40% that combined gives more Phase A passes. ORB signals during the combine phase
may help reach the $3k monthly target in ORB-favorable months (B21 pure-iFVG misses those).
Risk: ORB months that BUST the combine (drawdown months) get double-counted. The combine
objective favors strategies with positive monthly PF; ORB+iFVG combined may have more
variance and more bust-months.

Source: B31 Phase A config discrepancy analysis; Lesson 50 (config parity gap).
Note: this is a combine-only test. The two-phase pipeline implication is indirect --
if combined Phase A gives more passes, the pipeline supply improves, which B39 can
quantify once the pass count is known.

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk2-r3  [done — 3 items appended: B41 combined-engine Phase A pipeline, B42 deployed config full pipeline, B43 ORB late-session signal cutoff]

Backlog fully exhausted (B1-B40 all done) — mandatory research session. Sources: 5y MFE/MAE
data mining (scripts/_research_mining.py) + WebSearch + B40 projection analysis.

**Key data findings:**
- **ORB timing analysis (n=1030, excl 2022):** Signals split cleanly at 10:30 ET:
  9:45-10:30 ET (n=926): PF ~1.21 blended (all profitable, peak PF 1.528 at 10:15-30 ET).
  10:30-11:00 ET (n=57): PF=0.963 (loss-making). 11:00-11:30 ET (n=27): PF=0.622 (clearly negative).
  11:30+ ET (n=19): PF=3.213 (too small to trust). The 10:30-11:30 window costs ~$2,900 over 5y.
  Note: 10:05-30 ET window (delayed breakouts) is HIGHER quality than 9:45-55 ET (PF 1.43-1.53 vs 1.17).
- **iFVG MAE distribution (n=2477, excl 2022):** Trades with MAE <0.25R: WR=93.3%, PF=335 (n=267).
  MAE 0.25-0.5R: WR=83.1%, PF=40. MAE 0.5-0.75R: WR=72.3%, PF=17. MAE 0.75-1R: WR=43.9%, PF=2.6.
  MAE 1R+: WR=0.7%, PF=0.026 (n=1438 — these are virtually all stop-outs). This validates the existing
  swept-extreme stop: trades that go favorably have low MAE; losers go all the way to stop.
- **B40 projection:** Combined-engine Phase A at research baseline projects ~68 passes over 5y vs 34
  for ifvg-only. With B21 Phase B (13 busts): sust ~5.2x, $/month ~$560-600. Never tested as full pipeline.
- **Deployed config pipeline:** The deployed bot (combined+close+partial_r=1.5+lookback=30+all-day+
  r=1.0%+MNQ overrides) has never been simulated end-to-end through the funded pipeline model.
  B31 used deployed Phase A but at r=2.0%/partial_r=0; B25 tested partial_r=1.5 for Phase B but not
  Phase A; B26 showed lookback=30 hurts Phase A (-4 passes). The net effect of the full deployed
  config is unknown.
- **Web search:** Order flow imbalance (OFI) papers found (arxiv 2505.17388, 2508.06788). OFI is a
  novel mechanism (not in our rejection list) but requires tick-level order book data not available
  in our OHLCV bars. Not testable without expensive new data. No other new mechanism families found.

## B41 — Combined-engine research-baseline Phase A two-phase pipeline  [done — rejected: combined Phase A gives 10/41 passes over 5y (vs 34/162 for ifvg-only B21) because higher trade frequency damps equity variance → longer attempts (12.7d vs 6.0d) → fewer total pipeline cycles; net $421/mo sust 0.77x — stop rule on both metrics vs B21 ($497, 2.62x); B40 combine-harness 2x advantage does not transfer to funded pipeline throughput; Lesson 83]
Hypothesis: B40 showed engine=combined at B21 research baseline (named sessions, ifvg_edge, no MNQ
overrides) gives 2x more Phase A combine passes than ifvg-only (10/61 vs 5/61, PF 1.02 vs 0.85).
The B21 pipeline ($497/mo, sust 2.62x) used ifvg-only Phase A (5/61 per period, ~34 passes/5y).
If combined Phase A gives ~10/61 per period, that's ~68 passes over 5y — doubling sust and
improving $/month substantially.

Mechanism: no new code. Generate per-year equity CSVs at combined engine + research baseline:
  For each year in [2021, 2023, 2024, 2025, 2026]:
  `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
   --risk-pct 1.25 --partial-r 0 --set swing_stop_lookback=0 --set target_clarity_mode=reject
   --set ifvg_entry_mode=ifvg_edge --set engine=combined
   --set min_absolute_body=1.0 --set stop_buffer=0.30
   --killzones london,ny_am,ny_pm
   --out research/equity_b41/combined_r1p25_{year}.csv`
Write scripts/run_b41_pipeline.py (clone of run_b21_pipeline.py) using:
  Phase A: equity_b41/combined_r1p25_{year}.csv
  Phase B: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21)
Compare to B21 (ifvg_only r1.25 Phase A: $497/mo, sust 2.62x) and B31 (deployed r2.0 Phase A: $508/mo, sust 2.85x).

Fixed defaults: engine=combined, ifvg_entry_mode=ifvg_edge, partial_r=0, swing_stop_lookback=0,
target_clarity_mode=reject, min_absolute_body=1.0, stop_buffer=0.30, killzones=named (same B21 baseline,
only engine= changed).
Defining-behavior tests: none needed (no code changes).

Success criteria (vs B31 winner: $508/mo, sust 2.85x):
- Primary: $/month >= $508 AND sust >= 2.85x (beats B31 on BOTH metrics)
- Secondary: if $/month >= $497 AND sust >= 2.62x (beats B21), counts as improvement
- If combined Phase A per-year passes < 34 (same or fewer than ifvg-only B21): reject
  and flag the B40 combine-harness result (10/61) as not representative of per-year dynamics

Pipeline projection (from B40 combine result extrapolated to per-year):
  Phase A per-period passes: ~10/61 months → ~2.0 passes per 12-month window
  5y total projected passes: ~10 × (60/61) × (5/1) ≈ 49 (conservative) to 68 (linear)
  With B21 Phase B (13 busts, $3,131/acct, 73.5d): sust = 49-68 / 13 = 3.8x-5.2x
  Cycle days: (5y×252d / 49-68) + 73.5d ≈ 92-99d; $/month = ~$540-600

Important: B40 used a 61-month run_monthly_combine (2021-2026 incl 2022); per-year uses 2021,2023-2026
(excl 2022). The actual per-year pass count may differ from the monthly-combine projection.

Prior: ~75% that combined Phase A beats B21 on both metrics; ~50% it beats B31. The 2x pass rate
from B40 is empirical, not just a projection. The B40 combine test used the same 61-month window
as B21's baseline, providing a direct apples-to-apples comparison (ifvg 5/61 vs combined 10/61).

Source: B40 combine result + B21 pipeline model + wk2-r3 research projection.

## B42 — Deployed config full end-to-end pipeline simulation  [done — candidate: $549/mo 5y sust=3.23x (beats B31); 6y holdout incl 2022 degrades to $424/mo 2.19x — Phase A loss-making in 2022 (PF=0.934); doc: trade_analysis/2026-06-13_B42_deployed_pipeline.md; lessons 84-85 added]
Hypothesis: The deployed bot config (as of 2026-06-14) uses: engine=combined, ifvg_entry_mode=close,
partial_profit_r=1.5, swing_stop_lookback=30, killzones=["all"], risk_pct=1.0%, min_absolute_body=5.0,
stop_buffer=3.0, r_multiple=2.5 (MNQ overrides). This combination has NEVER been run through the
funded pipeline model. B31 approximated it (deployed Phase A) but used r=2.0% and partial_r=0.
B25 tested partial_r=1.5 only on Phase B (not Phase A combine). B26 showed lookback=30 hurts
Phase A (-4 passes) but that was at ifvg_edge mode; close mode may interact differently (B27
showed close mode worse under named sessions but better under all-day). The net deployed pipeline
economics are genuinely unknown.

Mechanism: no new code. Generate per-year equity CSVs with the exact deployed config:
  For each year in [2021, 2023, 2024, 2025, 2026]:
  `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
   --risk-pct 1.0 --partial-r 1.5 --set swing_stop_lookback=30 --set engine=combined
   --set ifvg_entry_mode=close --set min_absolute_body=5.0 --set stop_buffer=3.0
   --killzones all
   --out research/equity_b42/deployed_r1p0_{year}.csv`
Also at r=2.0% (to match B31 Phase A risk level for comparison):
   `--risk-pct 2.0 --out research/equity_b42/deployed_r2p0_{year}.csv`
Run funded_sim on stitched per-year CSVs (not B21-style two-phase — the deployed config is used
for BOTH phases simultaneously; this tells Lawrence the "single-strategy deployed" funded pipeline).
Also run as Phase A in a two-phase model paired with equity_b21/orb_reentry_r0p75 (Phase B).

Note on methodology: "deployed config full simulation" means the funded phase ALSO uses the deployed
config for intraday position management (partial exits at 1.5R, wider swing stops). The Phase A
(combine phase) is fully replicated. This is genuinely new — all prior Phase A equity runs used
research-baseline parameters for parity within the research series.

Fixed defaults: exact deployed bot_config.json settings (no research-baseline overrides).
Defining-behavior tests: none needed.

Success criteria (vs B31 winner: $508/mo, sust 2.85x — the best comparable benchmark):
- If deployed config achieves sust >= 2.85x AND $/month >= $508: the deployed config is better
  than ANY prior benchmark and is already deployed correctly.
- If deployed config is worse on either metric: identify which parameter (partial_r, lookback, risk)
  is the primary driver and flag for Lawrence's Monday config review.
- The comparison is valid against B31 only if we also run the deployed config at r=2.0% to isolate
  the risk-level contribution.

Caution: B26 found lookback=30 costs -4 Phase A passes (in ifvg_edge mode). Close mode may interact
differently (B27: close mode loses under named sessions, but deployed uses all-day + combined engine
which B24 showed benefits from close mode). Net effect of (close+all-day+combined) vs (ifvg_edge+
named+combined) is an open question — B24's close mode advantage was measured with combined+all-day.

Source: deployed bot_config.json audit (from B40 session); B25/B26 partial sensitivity tests;
B31 Phase A methodology.

## B43 — ORB late-session signal cutoff (orb_signal_window_mins parameter)  [done — candidate: orb_signal_window_mins=60 removes 8% of ORB signals (10:30-11:30 ET, PF 0.622-0.963), combines passes stable 10/61, combine PF 1.15→1.21 (+5.2%), Phase B busts 25→22 (-12%), two-phase sust 1.68x→1.91x (+14%); recommended value=60; Lesson 86]
Hypothesis: ORB signals after 10:30 ET (60 minutes post-open) are loss-making over 5 years
(10:30-11:00 ET: PF=0.963, n=57; 11:00-11:30 ET: PF=0.622, n=27). The early-session window
(9:45-10:30 ET, n=926) carries virtually all the ORB edge. After 10:30 ET, breakout momentum
exhausts into the "lunch doldrums" period. Suppressing new ORB signals after 60-90 minutes removes
10% of trades while eliminating a clearly negative segment.

Counterargument: the 11:30+ ET bucket (n=19, PF=3.213) suggests late-session breakouts may have
high quality, but n=19 over 5 years is too sparse to trust. The 10:30-11:30 ET loss ($2,896 over
5y) is real but small in magnitude. Volume impact: removing 84/1030 trades (8%) barely affects
combine volume (already ~23/month ORB; removing 8% → ~21/month).

Mechanism (requires code):
- Add `orb_signal_window_mins: int = 0` to StrategyParams (default 0 = no cutoff).
- In ORBDetector.on_bar: if `config.orb_signal_window_mins > 0`:
    compute `mins_since_open = (bar.ts.astimezone(ET).hour - 9)*60 + bar.ts.astimezone(ET).minute - 30`
    if `mins_since_open >= config.orb_signal_window_mins`: return None (suppress signal emission)
    Existing open positions (from earlier signals) are unaffected — only new signal generation stops.
- The OR range continues building regardless of the window cutoff (the range accumulation is useful
  for detecting new breakouts early in the window).
- ET conversion: `bar.ts.astimezone(ZoneInfo("America/New_York"))`.

Fixed defaults: `orb_signal_window_mins=0` (no cutoff, existing behavior preserved).
Test at: 60 (10:30 ET cutoff) and 90 (11:00 ET cutoff).

Defining-behavior tests (tests/test_orb_signal_window.py):
1. orb_signal_window_mins=0 (default): signals fire any time (existing behavior unchanged)
2. orb_signal_window_mins=60: a breakout bar at exactly 60min post-open → no signal;
   a bar at 59min → signal fires normally
3. orb_signal_window_mins=60: signal fired at 55min; position open at 65min → position
   remains open (cutoff affects NEW signals only, not existing positions)
4. Window check uses ET timezone (23:30 UTC = 19:30 ET, no signal even if late evening)

Benchmark (BOTH objectives; parity flags --partial-r 0 --set swing_stop_lookback=0):
1. Combine: run_monthly_combine.py --set engine=orb --set orb_r_multiple=2.5
   --set orb_signal_window_mins=60 (and =90). Compare to ORB baseline (10/61, PF 1.06).
   Success: passes improve AND PF improves (stop rule if both degrade).
2. Funded: equity_export --set engine=orb --set orb_r_multiple=2.5 --set orb_reentry_after_stop=True
   --set orb_signal_window_mins=60 --risk-pct 0.75 + funded_sim.
   Compare to B31 Phase B baseline (13 busts, $3,131/acct). Success: bust rate decreases or PF improves.
3. Also run: standard two-phase pipeline with orb_signal_window_mins=60 Phase B (per-year equity).

Success criteria:
- Primary: combine passes improve AND funded PF improves (at least one, without degrading the other)
- Stop rule: if BOTH metrics degrade at BOTH tested window values (60 and 90 min), reject
- Volume check: remove no more than 20% of ORB trades (currently removing 8% — safe)

Prior: ~35% that 60-min cutoff meaningfully improves the combine or funded objectives. The data shows
a real edge degradation after 10:30 ET, but n=57 (loss-making) and n=27 (clearly negative) are small
samples. The combine pass rate is primarily volume-limited (Lesson 2); removing 8% of trades may
slightly hurt combine months on the margin. The funded PF improvement is more likely to materialize.

Source: scripts/_research_mining.py (wk2-r3) — 5y ORB MFE/MAE timing analysis.
ORB 10:30+ ET structural weakness: less momentum continuation in the lunch doldrums vs early-session
urgency. The 10:05-10:30 ET window (PF 1.43-1.53) is STRONGER than the 9:45-55 ET window (PF 1.17),
suggesting delayed breakouts (after first-bar noise settles) are higher quality — the cutoff preserves
this higher-quality delayed window while removing only the post-10:30 deterioration.

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk2-r4  [done — 3 items appended: B44 iFVG mid-session block, B45 ORB range-width filter (Phase 1), B46 B42+B43 deployed-config pipeline benchmark]

B1-B43 exhausted. Mandatory research/ideation session to replenish (session count % 3 and last 2 items B42/B43 both build items). Sources: scripts/_research_wk2r4_mining.py (5y MFE/MAE per-hour/hold-time analysis) + WebSearch.

**Key data findings from wk2-r4 mining (5y excl 2022):**
- **iFVG per-hour (all-sides):** 11:xx PF=0.932 (n=83), 12:xx PF=0.752 (n=64), 13:xx PF=0.744 (n=85) — all
  three hours consistently loss-making, net=-$12,268 over 5y. Per-year: PF<1 in 4 of 5 years (2021:0.951,
  2023:0.511, 2024:0.631, 2025:0.941, 2026:1.623-sparse). 14:xx recovers to PF=1.108 (positive). The dead
  zone is specifically 11:00-14:00 ET. Lunch LONG-only signals are also negative (PF=0.971, net=-$555).
- **This block is NOT the same as B18's session filter.** B18 tested named sessions (removed overnight/
  pre-market sessions that have NET-POSITIVE PF at partial_r=1.5). B44 blocks ONLY 11:00-14:00 ET while
  keeping all overnight/pre-market sessions intact — the mechanism that hurt B18 (removing positive overnight
  sessions) does not apply here.
- **ORB hold-time structure (5y excl 2022):** All ORB value is in the 4h+ cohort:
  0-30m: PF=0.144 (n=110), 30-60m: PF=0.378 (n=99), 1-2h: PF=0.399 (n=104), 2-4h: PF=0.868 (n=131),
  4h+ (EOD): PF=4.129 (n=573, WR=68.6%). Net: early buckets total -$100k, 4h+ bucket +$153k.
  ORB is ENTIRELY an EOD-flatten strategy at the structural level. This confirms wk2-r2's 33.6% profitable-
  EOD-flatten finding and extends it: even the 11.4% target hits are mostly held all day (targets require
  large moves, typically 4h+ at 5min timeframe).
- **ORB long side 4h+ structure:** long PF=5.458 (n=317 of 542), short PF=2.998 (n=256 of 488).
  Long-side EOD flattens are nearly 2x better PF than short-side EOD flattens — consistent with Lesson 8.
- **B43+B42 deployed gap:** B43 tested orb_signal_window_mins=60 at partial_r=0 (parity), not deployed
  partial_r=1.5. B42 is the deployed Phase A (42 passes). Their combination (B42 Phase A + B43 w=60 Phase B
  at partial_r=1.5) has never been simulated as a full pipeline. B46 closes this gap.
- **WebSearch:** No new mechanism families found. Lesson 6 confirmed 7-for-7 (arxiv 2605.04004 explicitly
  tests 14 OHLCV signal families on MNQ 5min 2021-2025 — all fail institutional standards; our iFVG+ORB
  success is not explained by standard OHLCV signal families, suggesting the iFVG chain's structural
  confirmation requirement is what provides the edge). No new proposals from external sources.

## B44 — iFVG mid-session signal block (11:00-14:00 ET)  [done — rejected: block hurts close-mode LO (combine passes 45→39, long net $25k→$5.8k); per-hour data was ifvg_edge not close-mode; Lesson 89]
Hypothesis: iFVG signals emitted in the 11:00-14:00 ET window are consistently loss-making across
all years (5y excl 2022: PF=0.859, n=232, net=-$12,268). The mechanism is the "lunch doldrums":
low liquidity, mean-reverting price action, FVG inversions that trigger but fail to follow through
because institutional order flow is absent. This is structurally different from B18 (named-sessions
filter that removed overnight/pre-market positive-PF hours) — this block ONLY removes the 11-13 ET
hours while preserving all other windows including overnight/pre-market.

Per-hour data (5y excl 2022, all-sides, all-day killzones, research baseline config):
- 11:xx: n=83, PF=0.932, net=-$1,671  (borderline but 4 of 5 years < 1)
- 12:xx: n=64, PF=0.752, net=-$4,115  (clearly negative; 4/5 years < 1)
- 13:xx: n=85, PF=0.744, net=-$6,482  (clearly negative; 4/5 years < 1)
- 14:xx: n=78, PF=1.108, net=+$2,052  (POSITIVE — keep these signals)
Volume impact: n=232/2477 = 9.4% reduction (3% per blocked hour average).
Note: lunch LONG-only signals also negative (PF=0.971, n=76, net=-$555).

Per-year consistency for 11:xx-13:xx block:
- 2021: lunch (n=30) PF=0.951  vs non-lunch PF=1.191
- 2023: lunch (n=26) PF=0.511  vs non-lunch PF=0.883
- 2024: lunch (n=33) PF=0.631  vs non-lunch PF=1.033
- 2025: lunch (n=42) PF=0.941  vs non-lunch PF=1.089
- 2026: lunch (n=16) PF=1.623  (sparse, 6-month year — treat as noise)
4 of 5 years (with sufficient data) show PF<1 in the lunch window.

Mechanism (code required, ~25 lines):
- Add `ifvg_block_hours: list[int] = Field(default_factory=list)` to StrategyParams.
  Example: `[11, 12, 13]` blocks 11:00:00-13:59:59 ET (11:xx, 12:xx, 13:xx).
- In SweepDisplacementComposer.on_displacement(), after DOW filter and before FVG check:
  `if config.block_hours: hour = bar.ts.astimezone(ET).hour; if hour in block_hours: return None`
- The block applies to SIGNAL EMISSION only; sweep state continues accumulating.
- ET conversion: `bar.ts.astimezone(ZoneInfo("America/New_York")).hour`.

Fixed defaults: `ifvg_block_hours=[]` (no blocking — existing behavior preserved).
Primary test: `[11, 12, 13]` (block 11:00-14:00 ET).
Secondary test: `[11, 12, 13, 15]` (also block 15:xx, which shows PF=0.821, n=53, net=-$2,305).

Defining-behavior tests (tests/test_ifvg_block_hours.py):
1. block_hours=[] (default): signals fire at any hour (existing behavior unchanged)
2. block_hours=[12]: a displacement bar at 12:15 ET → on_displacement returns None
3. block_hours=[12]: a displacement bar at 11:59 ET → signal fires (11:xx not blocked)
4. block_hours=[12]: sweep state updated even during blocked hour (state not lost)
5. block_hours=[12]: bar at 13:00 ET → signal fires (14:xx not blocked)

Benchmark (funded objective — combine secondary):
1. `equity_export --partial-r 0 --set swing_stop_lookback=0 --set ifvg_block_hours=11,12,13`
   Compare to research baseline (no block). Primary comparison: PF, funded net payouts, sust.
2. `equity_export` with deployed settings (partial_r=1.5, close mode, all-day) + block_hours=[11,12,13]
   Compare to B42 Phase A (to check whether blocking helps the deploy-config Phase A combine).
3. funded_sim --haircut 200; report sust vs B19/B24 baselines.

Success criteria (vs B24 baseline: LongOnly close-mode funded, PF=1.167, sust=2.524x):
- Primary: funded PF improves >= +2% AND sust >= 2.524x (doesn't regress vs B24 best)
- Also report combine pass rate change (expect negligible at deployed ~80/month frequency)
- Stop rule: both PF AND sust degrade vs ANY reasonable baseline → reject

Prior: ~40% (data is structurally consistent across years; the targeted-hour approach avoids
B18's failure mode; but the all-day deployed config's overhead volume may absorb the signal
quality gap). Lower prior for secondary 15:xx block (only 53 trades over 5y; sample too small
to be confident).

Source: scripts/_research_wk2r4_mining.py (wk2-r4 session). Per-hour PF analysis, all-sides
and long-only. The 11:00-14:00 ET dead zone is consistent with the "CME electronic hours"
institutional lunch break (major liquidity providers step back 11:00-13:00 ET daily). The same
pattern was observed in B18's per-hour data but was not isolated as a testable mechanism
because B18 tested whole-session block/keep decisions.

## B45 — ORB opening-range width quality filter (Phase 1 data mining + Phase 2 code if GO)  [done — rejected at Phase 1: wide/narrow PF ratio=1.044 (need >=1.4); OR width is non-monotonic predictor (Q3 mid-range PF=1.439 beats both Q1 narrow 1.214 and Q5 wide 1.154); hold-time pattern real but doesn't translate to PF; Phase 2 NOT built; Lesson 90]
Hypothesis: a narrow opening range (first 15 minutes of trading) indicates low pre-market
conviction and produces false breakout ORB signals. A wide range indicates a decisive overnight
move being digested, producing stronger breakout signals when price finally resolves direction.
Proposed filter: require OR width >= X × (14-bar ATR at 9:45 ET) for ORB signals to fire.

This is NOT the same as B5 (prior-day range qualifier), which was REJECTED. B5 used the
PRIOR DAY'S range as a predictor — it measured nothing about the current session's opening.
B45 uses the CURRENT DAY'S own OR width, which is a direct measure of how much the overnight
session compressed or expanded the range before the regular session open.

Mechanism for the structural separation (hypothesis):
- Narrow OR (< 0.5× ATR): price chopped in a tight range pre-open → breakout bar likely
  wicks through the range and reverses (false momentum) → quick stop-out → short-hold loser
- Wide OR (> 1.5× ATR): price had a strong pre-open move and consolidated → when the range
  breaks, the directional conviction is high → sustained move → long-hold winner or target hit
- This matches the ORB hold-time data: early stops (0-2h, all losing) vs late holds (4h+, PF=4.1)

PHASE 1 — cheap data-mining falsification FIRST (no engine code; go/no-go gate):
Write `scripts/analyze_b45_orb_range.py`:
1. For each trading day in the 5y bars (2021/2023/2024/2025/2026, excl 2022):
   a. Find the 9:30-9:45 ET bars (up to 3 bars of 5min = OR formation bars)
   b. OR high = max(high of 9:30, 9:35, 9:40 bars); OR low = min(low of same bars)
   c. OR width = OR high - OR low (in points)
   d. ATR(14) computed from bars ending at 9:30 ET (prior to opening)
   e. OR/ATR ratio = OR width / ATR(14)
2. Match each ORB trade in mfe_mae_orb_clean.csv to its day's OR/ATR ratio.
3. Bucket trades by OR/ATR ratio quintile; compute WR and PF per bucket.
GO/NO-GO: proceed to Phase 2 ONLY if wide-range bucket (top 40%) PF >= 1.4× narrow-range
bucket (bottom 40%) AND each bucket has n >= 40.

PHASE 2 — engine (only if Phase 1 = GO):
- Add `orb_min_range_atr_factor: float = 0` to StrategyParams (default 0 = no filter).
  When > 0: after OR locks at 9:45 ET, compute ATR(14) from prior bars and check
  `(or_high - or_low) >= orb_min_range_atr_factor * atr`; suppress signals if too narrow.
- ORBDetector already tracks or_high/or_low; add `_or_atr: float = 0` field, computed once
  when the range locks.
- ATR(14) requires a 14-bar rolling computation — add an `_atr_buffer: deque[float]` of
  14 TR values, updated each on_bar call before range formation.
- Fixed default: `orb_min_range_atr_factor=0` (off; enable for benchmark only).

Defining-behavior tests (tests/test_orb_range_width.py), ONLY if Phase 1 = GO:
1. factor=0 (default): signals fire regardless of OR width (existing behavior unchanged)
2. factor=0.7, OR width < 0.7×ATR → breakout bar returns None (range too narrow)
3. factor=0.7, OR width >= 0.7×ATR → signal fires normally
4. ATR tracked correctly (14 TR values, handles gap-open days where true range is large)

Benchmark (ONLY if Phase 1 = GO, funded + combine objectives; parity flags):
1. `run_monthly_combine.py --set engine=orb --set orb_r_multiple=2.5
   --set orb_min_range_atr_factor=0.7` (fixed default from Phase 1 data)
2. `equity_export --set engine=orb --set orb_reentry_after_stop=True
   --set orb_min_range_atr_factor=0.7 --risk-pct 0.75 --partial-r 0`
   + funded_sim vs B43 baseline (w=60 Phase B, 22 busts, $1,725/acct)

Success criteria (only used if Phase 1 = GO):
- Combine: passes improve AND PF improves vs w=60 baseline (10/61, PF 1.21)
- Funded: sust ratio improves vs ORB-reentry r=0.75 baseline from B21

Stop rule (Phase 1 NO-GO): if wide OR/ATR does NOT materially outperform narrow OR/ATR
(PF ratio < 1.4x), do NOT implement the filter. B5's lesson was that day-level range
predictors fail; document that OR width joins that rejection set and close the item.

Prior for Phase 2: ~30% (B5 precedent is adverse; but current-day OR is fundamentally
different from prior-day range — it's a direct coil measurement, not a volatility proxy
for the day ahead). Phase 1 is the appropriate gate before committing code resources.

Source: wk2-r4 hold-time analysis showing ORB value is entirely in 4h+ cohort (EOD
flattens, WR=68.6%, PF=4.129). Early stop-outs (0-2h, PF=0.14-0.40) dominate the loss
side. If narrow OR ranges predict early stop-outs, the filter directly removes the dominant
loss mechanism. Related: B34 Lesson 73 showed no-retrace BOS signals (immediate momentum)
have the best performance — this structural principle generalizes: tight coils → false
breakouts → early reversals → stops.

## B46 — B42+B43 deployed-config full pipeline benchmark (orb_signal_window_mins=60 at partial_r=1.5)  [done — rejected: w=60 does not improve sust (3.23x vs 3.23x); $/mo drops $549->$456 (-17%); bust count identical (13 vs 13); partial_r=1.5 converts the bust-preventing effect of w=60 into zero additional bust reduction; do NOT enable orb_signal_window_mins=60 on deployed bot; Lesson 93; B46 w=0 validates partial_r=1.5 produces same pipeline as partial_r=0 (identical to B42 result)]

## RESEARCH — Session wk2-r5  [done — 3 items appended: B47 confluence gate, B48 rank hybrid, B49 breakout extension; 2026-06-15T02:00Z]
Hypothesis: B43 showed orb_signal_window_mins=60 reduces ORB-reentry Phase B busts from
25 to 22 (-12%) and improves two-phase sust from 1.68x to 1.91x (+14%). BUT B43 was
benchmarked at partial_r=0 (research parity baseline), while the deployed bot uses
partial_r=1.5. B42 was the deployed-config Phase A (42 passes, $568 reset, $549/mo
5y-sust 3.23x) benchmarked with B21 Phase B (partial_r=1.5, ORB-reentry r=0.75). The
B42+B43 combination — deployed Phase A paired with deployed Phase B INCLUDING the
orb_signal_window_mins=60 improvement — has never been explicitly computed.

This benchmark answers the deployment question: does enabling orb_signal_window_mins=60
on the live bot improve the pipeline economics, and by how much?

Also: B43 Phase B used parity (no MNQ overrides, no partial_r). The deployed Phase B
uses partial_r=1.5 + MNQ overrides (stop_buffer=3.0, min_absolute_body=5.0). B25 showed
partial_r=1.5 reduces sust by 1.8% for ORB-reentry at r=0.75 (from 0.764 to 0.750).
Whether this holds when combined with w=60 needs verification.

Mechanism: no new code. B43 already shipped orb_signal_window_mins=60 (658 tests green).
Generate per-year Phase B equity CSVs with deployed settings + w=60:
  For each year in [2021, 2023, 2024, 2025, 2026]:
  `equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
   --risk-pct 0.75 --partial-r 1.5 --set swing_stop_lookback=0 --set engine=orb
   --set orb_r_multiple=2.5 --set orb_reentry_after_stop=True
   --set orb_signal_window_mins=60 --set stop_buffer=3.0 --set min_absolute_body=5.0
   --out research/equity_b46/orb_reentry_w60_r0p75_{year}.csv`
Write scripts/run_b46_pipeline.py. Phase A: equity_b42/deployed_r1p0_{year}.csv (already exists).
Phase B: equity_b46/ (new). Also include reference: equity_b21/orb_reentry_r0p75 (B21 Phase B
at partial_r=1.5 — need to verify if the existing CSVs used partial_r=1.5 or 0).

Note: the equity_b21 CSVs were generated with research baseline settings (partial_r=0).
The deployed Phase B is ORB-reentry r=0.75 WITH partial_r=1.5 (deployed). This means
we should ALSO generate the "deployed Phase B without w=60" baseline (same as B46 but
--set orb_signal_window_mins=0) to isolate the w=60 effect cleanly.

Benchmark plan:
1. Generate equity_b46/orb_reentry_w60_r0p75_{year}.csv (deployed + w=60)
2. Generate equity_b46/orb_reentry_w0_r0p75_{year}.csv (deployed + w=0, baseline)
3. Run two-phase pipeline: B42 Phase A × equity_b46/ Phase B
4. Report $/mo, sust for both w=0 and w=60 configurations
5. Compare to B42's published result ($549/mo, 3.23x) which used B21 Phase B (partial_r=0 CSVs)

Fixed defaults: partial_r=1.5, stop_buffer=3.0, min_absolute_body=5.0, orb_r_multiple=2.5,
orb_reentry_after_stop=True. Only w=60 vs w=0 varies.
Defining-behavior tests: none needed (no code changes).

Success criteria:
- Primary: w=60 Phase B sust > w=0 Phase B sust (window cutoff improves pipeline sustainability)
- Secondary: the B42+B43-deployed pipeline beats B42+B21 ($549/mo, 3.23x) → deploy both changes
- If w=60 shows no benefit at partial_r=1.5: note that partial_r moderates the bust-reduction
  effect and the recommendation is to NOT enable w=60 in the funded phase (deploy in combine only)
- In any case: document the correct deployed Phase A + Phase B combination for Monday config review

Prior: ~70% that w=60 improves deployed Phase B sust. B43 showed -12% busts at partial_r=0.
B25 showed partial_r=1.5 moderates sust by only 1.8%. Net expected effect: ~10% bust reduction
at partial_r=1.5, which translates to higher sust and likely slightly lower $/acct (partial exits
reduce per-trade payout). The B42 Phase A (42 passes) gives good pipeline supply, so even a
modest sust improvement is valuable.

Source: B43 (orb_signal_window_mins=60 candidate, partial_r=0 benchmark); B42 (deployed Phase A
baseline, partial_r=1.5); B25 (partial_r=1.5 effect on ORB-reentry Phase B). This is the
"deployed-config integration test" that closes the gap between research-baseline benchmarks
and the actual live configuration.

## B47 — iFVG×ORB directional confluence gate  [done — rejected (stop rule: sust 0.50x vs baseline 0.79x; pass rate 3/17 vs 4/17; both worse; Lessons 94+95)]

Phase 1 data mining COMPLETE (wk2-r5) — GO status confirmed. Proceed directly to Phase 2 engine.

Mechanism: London iFVG and NY ORB represent institutional order flow from two separate sessions.
When both agree on direction for the same day, signals from both engines show dramatically higher
quality: ORB same-direction PF=1.689 (n=360, 35% of ORB trades) vs opp-direction PF=0.957
(n=227, 22%, loss-making). iFVG orb_same_only PF=1.375 vs orb_opp_only PF=0.757 (loss-making
in 4/5 years; 2021 exception PF=1.030, borderline). Suppressing ORB opp-only signals improves
per-year ORB PF in 5/5 years (+1.5% to +12.9%). This is the strongest cross-engine quality
predictor found in this research program.

Two complementary gates (both implemented in one Phase 2 build):

GATE 1 — ORB suppressed when all prior same-day iFVG signals are in the OPPOSITE direction:
  At ORB signal time (09:30-09:45 ET), check today's iFVG signals (filed since 00:00 ET):
    - ifvg_same_only or ifvg_both or no_prior_ifvg → ORB fires normally
    - ifvg_opp_only (all prior same-day iFVG opposite to ORB direction) → suppress ORB

GATE 2 — post-ORB iFVG signals in the opposite direction of ORB are suppressed:
  After ORB fires, record orb_direction_today in shared session context.
  For any iFVG signals emitted AFTER ORB fires (typically intraday re-entries):
    - signal.side == orb_direction_today → allow (orb_same_only case, PF=1.375)
    - signal.side != orb_direction_today → suppress (orb_opp_only case, PF=0.757)
    - No ORB fired today → allow signal (no_orb case)

Both gates are causal: iFVG fires London session (03:00-07:30 ET) before ORB fires (09:30-09:45
ET); ORB fires before any post-ORB iFVG intraday re-entries. No lookahead required.

Fixed defaults: `ifvg_orb_confluence_gate: bool = False` (off by default; enabled only for
benchmark; enabling gates both Gate 1 and Gate 2 simultaneously).

Implementation plan:
1. Read `app/strategy/composer.py` and the combined engine runner to understand how engines
   share state. Do NOT add state to individual runner classes — use a shared context object.
2. Add `DailySessionContext` (or extend the existing session context if one exists):
   - `ifvg_signals_today: list[tuple[date, str]]` — reset daily (ET date boundary)
   - `orb_direction_today: str | None` — set to 'long'|'short' when ORB fires; reset daily
3. iFVG runner: on signal emission, append (et_date, signal.side) to context.ifvg_signals_today.
4. ORB runner: at signal-ready time, read context.ifvg_signals_today for today; apply Gate 1.
5. ORB runner: after emitting, set context.orb_direction_today = signal.side.
6. iFVG runner: before emitting a post-ORB signal, check context.orb_direction_today; apply Gate 2.
7. Both context fields reset to empty/None at each new ET date boundary.

Defining-behavior tests (tests/test_confluence_gate.py):
1. Gate disabled (flag=False): all signals fire regardless of confluence (backward compat)
2. No prior iFVG same day → ORB fires (no_prior case unchanged)
3. Prior iFVG same direction as ORB → ORB fires (same_only case)
4. Prior iFVG OPPOSITE direction only → ORB suppressed (opp_only case) ← key test
5. Prior iFVG both directions → ORB fires (both case; PF=1.485, positive expectancy)
6. ORB long fired → subsequent iFVG short suppressed (Gate 2 opp case) ← key test
7. ORB long fired → subsequent iFVG long allowed (Gate 2 same case)
8. No ORB fired today → iFVG signals allowed regardless of side (no_orb case)
9. Context resets at ET midnight: day-N iFVG signals do not gate day-N+1 ORB signals

Benchmark (funded + combine objectives; parity flags; run AFTER B46):
1. B46 baseline (B42 Phase A + B43-deployed Phase B, no confluence gate)
2. B47 Gate-1-only: suppress ORB opp-only; iFVG unchanged
3. B47 full: Gate 1 + Gate 2 (suppress both ORB opp-only AND post-ORB iFVG opp)
Report: passes/61, sust, $/mo for each variant; per-year PF consistency check.

Success criteria:
- Primary: B47 full sust > B46 baseline sust
- Secondary: combine passes >= B46 baseline passes (gating may reduce ORB volume 22%;
  net effect on passes depends on pipeline balance)
- Per-year consistency: PF improvement in 4+/5 non-holdout years for whichever gate is kept
- Stop rule: if BOTH B47 Gate-1-only AND B47 full reduce sust AND reduce combine passes vs
  B46 baseline, reject. If mixed (one metric better, one worse), report as MIXED; do not
  deploy without Lawrence review.

Prior: 80% (data mining is done; year-by-year consistency confirmed; GO/NO-GO already passed;
implementation complexity is the remaining risk — shared context across two runners).

Source: wk2-r5 data mining on mfe_mae_orb_clean.csv + mfe_mae_ifvg_clean.csv. Year-by-year
consistency check in JOURNAL wk2-r5. Lessons 91, 92.

## B48 — iFVG hybrid rank-aware signal filter  [done — rejected: hybrid 11/61 (18%) PF 1.09 vs LO baseline 12/61 (20%) PF 1.21 — stop rule triggered (both metrics worse); close-mode rank-1 iFVG shorts loss-making (492 exits PF 0.84 vs 220 ORB-only shorts PF 1.02); rank-1 short quality (PF=1.127 research baseline) does not transfer to close-mode deployed config; ifvg_max_short_rank ships default-off; 4 defining-behavior tests; 677 total green]

Mechanism: iFVG sweep-displacement signals have a within-day rank per side (rank-1 = first
signal of the day on a given side; rank-2+ = subsequent signals on the same side). From 5y
data (excl 2022): rank-1 signals (all sides) PF=1.129; rank-2+ longs PF=1.090; rank-2+ shorts
PF=0.858 (loss-making). Current deployed config (B15 LO) removes all shorts, including
profitable rank-1 shorts. Hybrid approach: keep rank-1 on both sides + rank-2+ longs only;
suppress only rank-2+ shorts. Expected: PF=1.133 at ~29 signals/month vs LO PF=1.136 at ~25
signals/month. Same PF, +16% volume — potentially more combine passes.

Deterministic rule:
  Track daily_side_count[et_date][signal.side] (increment on each emission).
  If signal.side == 'short' AND daily_side_count[et_date]['short'] >= 2:
      suppress signal (rank-2+ short, PF=0.858)
  All other signals allowed: rank-1 shorts (PF=positive), all longs at any rank.

Fixed defaults: `ifvg_max_short_rank: int = 0` (0 = no limit, existing behavior;
set to 1 in benchmark to suppress rank-2+ shorts). Does NOT break existing `allowed_sides` param.

Defining-behavior tests (tests/test_rank_filter.py):
1. max_short_rank=0 (default): rank-3 shorts fire normally (backward compat)
2. max_short_rank=1: rank-1 short fires; rank-2 short suppressed; rank-2 long fires
3. max_short_rank=1: rank counter resets at ET midnight (new day, rank=1 again)
4. Longs are not affected by max_short_rank at any rank level

Benchmark (combine objective; parity flags):
  B42 LO baseline (long-only, 42 passes/61 months, PF=~1.136)
  B48 hybrid: max_short_rank=1, rank-1 both sides + all longs
  Report: passes/61, combine PF for both; sust vs B46 if funded phase also updated.

Success criteria:
- Hybrid passes >= LO passes (more volume should produce >= passes if PF is equivalent)
- Hybrid PF >= 1.12 (within 1.5% of LO's 1.136, given research-to-deployed config noise)
- Stop rule: if hybrid passes < LO passes AND hybrid combine PF < LO PF, reject (rank-1 shorts
  add vol without adding quality; in deployed config they may behave differently from baseline)

Prior: 55% (rank-1 shorts are positive in research baseline, but close-mode LO deployed config
may reduce their incidence; the rank-2+ short suppression may not add meaningful volume over LO
in practice; mild uncertainty about behavioral transfer to deployed config).

Source: wk2-r5 rank analysis on mfe_mae_ifvg_clean.csv (rank column imputed from intraday
signal ordering by entry_ts within each ET date and side). JOURNAL wk2-r5.

## B49 — ORB breakout extension quality filter (Phase 1 data mining)  [done — rejected Phase 1: deep/shallow PF ratio 1.128 < 1.4; non-monotonic peaks Q3; Lesson 98]

Mechanism (hypothesis): When the ORB breakout bar (first bar closing past the OR boundary)
extends significantly past that boundary, it signals stronger momentum and predicts higher
probability of EOD-flatten wins. A shallow close (just clearing OR boundary) may indicate a
weak breakout that reverses early, producing the 0-2h stop-out losses that dominate the ORB
loss side (Lesson 88). This is DISTINCT from B45 (which measured OR WIDTH before the break):
B49 measures the breakout BAR's extension past the boundary at the moment of signal emission.
A tight OR can produce a decisive breakout bar; a wide OR can produce a shallow one — the
variables are not correlated.

Phase 1 data mining (no engine code; go/no-go gate):
Write `scripts/analyze_b49_breakout_extension.py`:
1. For each ORB trade in mfe_mae_orb_clean.csv, extract entry_ts, side, instrument.
2. Load 1-min bars for that session day; compute ATR(14) from bars ending before 09:30 ET.
3. Reconstruct 5-min bars; find the first bar after 09:30 ET where:
   - Long: close > OR_high
   - Short: close < OR_low
   This is the breakout bar.
4. Compute extension: ext = |close - or_boundary| / atr14 (in ATR units, always positive).
5. Bucket trades by ext quintile; compute WR and PF per bucket.
6. GO/NO-GO: top 40% ext PF >= 1.4× bottom 40% ext PF AND each bucket n >= 40.

Phase 2 (engine, ONLY if Phase 1 = GO):
- In ORBDetector, track each bar's close vs OR boundary after range locks.
- At first breakout bar close: compute ext = |close - or_boundary| / atr14.
- Add `orb_min_breakout_ext_atr: float = 0` (default 0 = no filter).
- Suppress signal if ext < orb_min_breakout_ext_atr.

Defining-behavior tests (ONLY if Phase 1 = GO):
1. ext=0 (default): all breakouts fire regardless of extension (backward compat)
2. ext=0.3: breakout bar 0.3x ATR past boundary → fires; 0.1x ATR → suppressed
3. ATR computed correctly from pre-ORB bars (not from OR bars themselves)

Benchmark (ONLY if Phase 1 = GO, funded + combine objectives, vs B46 baseline):
Same script pattern as B45; threshold set by Phase 1 optimal bucket.

Stop rule (Phase 1 NO-GO): if top-40% extension PF < 1.4× bottom-40% extension PF, do not
implement. Document that ORB breakout extension magnitude joins OR width (B45) and prior-day
range (B5) in the set of day-level ORB quality predictors that fail on NQ 5min data. Close
the "ORB breakout bar quality" research line; no further variants warranted.

Prior: 40% (B45 precedent is adverse for OR-quality predictors in general; the breakout bar
extension is a more direct momentum indicator than OR width and is mechanistically more likely
to predict EOD-flatten survival; the 40% prior acknowledges B45 while staying open to this
different variable).

Source: Lesson 88 (ORB value concentrated in EOD-flatten cohort; early 0-2h stop-outs are
the dominant loss mechanism); B45 (Phase 1 falsification precedent for OR quality filters);
wk2-r5 review. Next after B46 and B47.

## B50 — Account-state dynamic risk sizing (varying risk, not constant)  [done — rejected: all three variants (combine_ramp, funded_survival, full B50) worse on BOTH $/mo AND sust vs B42 baseline ($549/mo, 3.23x); combine_ramp $498/mo 2.46x; funded_survival $451/mo 2.47x; full B50 $399/mo 1.88x; Lesson 97; 7 tests added; 684 total green; 2026-06-14T04:00Z]
Lawrence asked directly: "backtest with varying risk instead of keeping risk a
constant variable." Every benchmark to date holds risk-pct constant within a run
(B1 swept LEVELS, but each run was one fixed %). This item varies risk-pct
WITHIN the attempt as a deterministic function of account state.

READ FIRST -- the metric trap: scaling a fixed-edge process by a state function
does NOT change per-trade expectancy or PF (it is a dimensionless rescale of
each bet). It changes the PATH distribution -- Combine pass rate, XFA bust rate,
drawdown depth, payout cadence. So this item is judged ONLY on the Combine and
funded objectives, NEVER on PF. Do not report "PF unchanged" as a finding; that
is expected by construction. The whole point is path/survival under the trailing
MLL.

Mechanism: a risk policy applied with knowledge of the running per-attempt
equity (distance to the +$3k target; distance to the trailing MLL). Default-off
flag `risk_policy: str = "constant"` (current behavior). Implementation decision
the session must resolve: the policy needs RUNNING-STATE knowledge, so it must
be applied inside the per-attempt simulation (the monthly-combine harness and
funded_sim already track equity vs target and trailing MLL as they walk an
attempt) -- re-size each trade there, rather than post-transforming a
constant-risk equity curve from equity_export. If full state-coupling is too
invasive for one session, the cheaper fallback is a PATH-ONLY policy (function of
recent realized P&L / consecutive-loss count) which needs only the trade stream;
implement that first, note the limitation.

Two declared deterministic policies (NO tuning beyond these):
- `risk_policy="combine_ramp"` (build-then-protect): risk 1.5% while cumulative
  attempt gain < +$1,500; risk 0.75% once gain >= +$1,500 (protect the buffer to
  +$3,000); risk 0.5% (survival) whenever equity is within $750 of the trailing
  MLL, overriding the above.
- `risk_policy="funded_survival"`: risk 0.75% base; risk 0.4% whenever within
  $750 of the trailing MLL; back to 0.75% once clear. (Survival-first for XFA.)

Defining-behavior tests (tests/test_risk_policy.py):
1. risk_policy="constant" (default): sizing byte-identical to current behavior.
2. combine_ramp: trade while gain < $1,500 sizes at 1.5%; after gain >= $1,500
   sizes at 0.75%.
3. combine_ramp: equity within $750 of trailing MLL → 0.5% regardless of gain.
4. funded_survival: near-MLL trade sizes at 0.4%, normal trade at 0.75%.
5. Risk policy reads running equity, not start equity (state-dependence proven).

Benchmark (objective metrics ONLY):
- Combine: run_monthly_combine with risk_policy=combine_ramp vs the deployed
  constant-r baseline. Metric: pass rate + MLL-fail count (NOT PF).
- Funded: funded_sim with risk_policy=funded_survival vs constant-r baseline.
  Metric: XFA busts, $/month, sustainability ratio.
Success: combine_ramp raises pass rate without raising MLL fails; OR
funded_survival lowers bust rate enough to raise $/month or sustainability vs
constant risk at the SAME average risk level. Stop rule: if a policy is worse on
its objective than constant risk, reject it (no re-tuning of the ladders).

Source: Lawrence direct request 2026-06-14. Distinct from B1 (which swept static
risk levels). This is the one sanctioned dynamic-sizing search; the objective
(survival under a trailing MLL) is exactly where path-dependent sizing can pay.

## B51 — Setup-grade-scaled position sizing  [done — rejected: Phase 1 NO-GO; grade does not predict outcome monotonically (top-2 A+B PF 1.286 / bottom-2 D+F PF 1.080, ratio 1.190 < 1.3); C grade (PF 0.996) is WORST bucket, worse than D (1.060) and F (1.124); non-monotonic pattern kills the sizing premise; 4th quality predictor rejected (joins B5/B45/B49); 684 total green; Lesson 99]
A second "varying risk" axis: size by the existing SetupGrader grade instead of
a flat size. UNLIKE B50, this CAN change expectancy (it concentrates risk on
trades the grader rates higher) -- so PF IS a valid metric here, IF grade
actually predicts outcome.

PHASE 1 -- cheap falsification FIRST (no sizing code; go/no-go):
From existing backtest trade lists that carry the grade (or re-run the control
config capturing grade per trade), compute win-rate / PF / mean-R per grade
bucket (A/B/C/D/F) over 2021/2023/2024/2025-26 (NOT 2022).
GO/NO-GO: proceed ONLY if higher grades MATERIALLY outperform (monotonic-ish:
A/B PF clearly > D/F PF, e.g., top-2 grades PF >= 1.3x bottom-2). If grade does
not separate outcomes, REJECT here -- document that SetupGrader grade is not a
sizing signal (joins the fib/OTE no-edge findings). Build nothing.

PHASE 2 -- sizing (only if Phase 1 = GO):
- `grade_sizing_enabled: bool = False` (default off).
- size multiplier by grade (fixed ladder): A=1.5x, B=1.25x, C=1.0x, D=0.5x,
  F=0 (skip). Applied to the per-trade risk-pct; total risk still capped at the
  configured max per-trade risk so the A-grade up-size respects MLL.
Defining-behavior tests (tests/test_grade_sizing.py):
1. grade_sizing_enabled=False (default): sizing unchanged.
2. A-grade trade sizes at 1.5x base; C-grade at 1.0x; F-grade skipped.
3. Up-sized A-grade risk respects the per-trade risk cap (no MLL breach by sizing).

Benchmark (BOTH objectives, parity flags `--partial-r 0 --set swing_stop_lookback=0`):
- vs flat-size control. Success: improves the objective AND (since expectancy can
  move) PF vs flat sizing. Stop rule: loses on both → reject.

Source: Lawrence direct request 2026-06-14 (varying risk). Uses the existing
SetupGrader; Phase 1 first because we have no evidence grade predicts outcome
and several quality-score hypotheses have already failed (fib/OTE, OR width).

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk3-r1  [done — 2026-06-14T05:00Z]

B1-B51 all exhausted. Mandatory research/ideation session. Data-mined 3 topics; yielded 3
new backlog items (B52/B53/B54):
1. iFVG N+1 bar confirmation (analyze_b53_next_bar.py): confirmed PF=1.619 vs unconfirmed
   PF=0.661, ratio 2.45x — strongest signal-level discriminator found. Phase 1 NO-GO for
   confirm-filter (only 46.8% confirm, < 60% threshold) but REFRAMED as adversity early exit.
2. ORB pre-RTH direction alignment (analyze_b54_prertth.py): aligned PF=1.300 vs opposing
   PF=1.134, ratio=1.146 < 1.25 threshold, non-monotonic (2021 inverted) — Phase 1 NO-GO.
3. Phase A config optimization (lookback=0 + target_clarity=reject, run_monthly_combine.py):
   9/61 (15%) passes vs B26 deployed 6/61 (10%) — +50% improvement, config change recommended.

## B52 — Phase A deployed config parity fix  [done — session wk3-r1]

Deployed Phase A (B42) uses swing_stop_lookback=30 and target_clarity_mode="off". B26
established these degrade monthly Phase A pass rate vs research defaults (lookback=0,
target_clarity=reject). B52 benchmarks the corrected config in the deployed context.

Config tested: engine=combined, ifvg_entry_mode=close, killzones=all, risk=1.0%,
partial_r=1.5, swing_stop_lookback=0, target_clarity_mode=reject, min_absolute_body=5.0,
stop_buffer=3.0, orb_r_multiple=2.5.

Results (monthly harness, 61 months, 2021-2026 incl 2022 holdout):
- Passes: 9/61 (15%) vs B26 deployed 6/61 (10%) — +50%
- Run PF: 1.11
- Saved: backtests/b52_phase_a_optimized.json

VERDICT: config change RECOMMENDED. Lawrence to apply Monday:
  swing_stop_lookback: 30 → 0
  target_clarity_mode: "off" → "reject"
Full funded pipeline validation deferred (needs funded_sim run with corrected params).
Source: B26/Lessons 58-59; scripts/run_monthly_combine.py --save-id b52_phase_a_optimized.

## B53 — iFVG N+1 bar adversity early exit gate  [done — rejected: Phase 1 NO-GO; early exit WORSENS both criteria (not-confirmed net -$194k vs -$173k; aggregate PF 1.029 vs 1.043); cutting 315 not-confirmed winners (-$365k gross wins) exceeds loser savings (+$344k); N+1 adverse is an outcome predictor not an exit signal; 5/5 years aggregate PF degrades; Phase 2 NOT built; scripts/analyze_b53_early_exit.py committed; Lesson 102]

**Hypothesis**: After entering an iFVG signal, if the first bar after entry (N+1, 5 min later)
closes AGAINST the signal direction (below entry for longs; above entry for shorts), exit
immediately rather than holding to the full swept-extreme stop. The N+1 bar direction is a
real-time signal observable 5 min after entry that predicts outcome with 2.45x PF separation.

**Evidence from wk3-r1 data mining (analyze_b53_next_bar.py, 5y excl 2022, n=2477):**
- Confirmed (N+1 closes with signal direction): n=1159 (46.8%), PF=1.619, Net=+$209,964
- Not confirmed (N+1 closes against signal direction): n=1318 (53.2%), PF=0.661, Net=-$173,312
- PF ratio: 2.45x — strongest discriminator found in this research program
- Per-year: confirmed > unconfirmed PF in ALL 5 tested years (5/5 consistent)

Why the confirm-filter was Phase 1 NO-GO: only 46.8% confirm (< 60% volume threshold).
Reframe: adversity EXIT, not entry filter. Enter all signals normally; exit at N+1 close
if adverse. Converts full-stop losses to small early-exit losses for 53.2% of trades.

**Phase 1** (data mining, no code): compute early-exit P&L for each not-confirmed trade.
For longs: early_exit_pnl = (nb_close - entry_price) × position_size. Sum vs current
held-to-stop P&L. GO/NO-GO: early exit reduces not-confirmed net losses by ≥ 30% AND
improves aggregate PF ≥ 10% vs baseline aggregate PF.

**Phase 2** (build if Phase 1 GO): add `ifvg_early_exit_on_nbar_adverse: bool = False` to
StrategyParams. Live broker: after fill, arm 5-min N+1 bar check; if bar close adverse vs
entry price, close position immediately. Requires live broker timer integration.

Objective: funded (PF improvement). Script: scripts/analyze_b53_next_bar.py.
Source: wk3-r1 research session.

## B54 — ORB pre-RTH direction alignment gate  [done — Phase 1 NO-GO, session wk3-r1]

**Hypothesis**: ORB signals aligned with 08:30-09:30 ET pre-RTH direction have better quality.
Economic data releases at 08:30 ET establish intraday momentum that persists through the ORB.

**Phase 1 data mining (analyze_b54_prertth.py, 5y excl 2022, n=1026):**
- Aligned (ORB with pre-RTH direction): n=486, PF=1.300
- Opposing (ORB vs pre-RTH direction): n=540, PF=1.134
- Ratio: 1.146 (GO threshold: ≥ 1.25) — FAILS
- Per-year: 2021 INVERTED (opposing PF=1.696 > aligned PF=1.330) — non-monotonic

VERDICT: Phase 1 NO-GO. Ratio below threshold AND 2021 inversion makes hypothesis
structurally unstable. Phase 2 NOT built. Script: scripts/analyze_b54_prertth.py.

---
(Research sessions append new items below this line.)

## RESEARCH — Session wk3-r2  [done — 3 items appended: B55 Phase-A-optimized pipeline, B56 ORB×iFVG alignment gate Phase 2, B57 iFVG r_multiple sensitivity]

Backlog fully exhausted (B1-B54). Protocol mandates research/ideation session. Sources: WebSearch (no new mechanisms — 7-for-7 external failures, Lesson 6 confirmed) + data mining on mfe_mae_ifvg_clean.csv, mfe_mae_orb_clean.csv, and bars volume data.

**Key data findings driving the 3 proposals:**

1. **ORB×iFVG cross-engine directional alignment** — Phase 1 GO (ratio 1.48x, 5/5 years):
   - A+D (any prior same-day same-direction iFVG before ORB): n=595, PF=1.427, net=+$53,893
   - B+C (no same-direction iFVG before ORB): n=435, PF=0.963, net=-$3,940
   - Per-year: A+D > B+C in all 5 tested years (min gap: 2023, 1.204 vs 1.084)
   - Group breakdown: A (all same-dir): PF=1.707; B (no prior iFVG): PF=0.990; C (all oppose): PF=0.939; D (mixed): PF=1.224
   - Mechanism: London iFVG establishes institutional directional conviction; ORB aligned with that conviction has structural support. ORB without any prior directional signal fires into ambiguity.

2. **iFVG inversion bar volume** — Phase 1 NO-GO (non-monotonic, high/low ratio 0.823 < 1.4):
   - Q1 (low, <=64): PF=1.023; Q2 (moderate, 65-155): PF=1.268 (BEST); Q3 (high-mid, 155-782): PF=0.885; Q4 (highest, >782): PF=0.995
   - High-vol underperforms low-vol but pattern peaks at Q2, NOT monotonic → no clean threshold gate

3. **Phase A config optimization pipeline impact** — B52 proved +50% Phase A combine pass rate (9/61 vs 6/61) from swing_stop_lookback=0 + target_clarity_mode=reject, but full funded pipeline $/mo + sust impact is UNQUANTIFIED. This is the most actionable open item for Lawrence's Monday config decision.

4. **iFVG r_multiple** — never varied in funded benchmarks. Deployed r_mult=3.5 was assumed; ORB uses orb_r_multiple=2.0/2.5 separately. A lower iFVG target (2.5R vs 3.5R) increases WR but reduces per-trade R — net effect on funded pipeline is unknown.

## B55 — Phase A config-optimized full funded-pipeline benchmark  [done — REJECTED 2026-06-14]

**Result:** B55 worse than B42 on BOTH metrics: $508/mo sust=2.00x vs $549/mo sust=3.23x. B52's combine-harness improvement (9/61 vs 6/61) does NOT transfer to pipeline economics. target_clarity=reject cuts signal count -> 69 combine attempts vs 159 -> 26 absolute passes vs 42, despite 38% vs 26% per-attempt rate. 2022 holdout: B55 PF=1.164 (positive vs B42's 0.934), but 5y throughput penalty dominates. Monday action: keep deployed settings (lookback=30, clarity=off). See Lesson 106, scripts/run_b55_pipeline.py, research/equity_b55/.

**Hypothesis (original):** B52 showed swing_stop_lookback=0 + target_clarity_mode=reject improves Phase A combine pass rate 50% (9/61 vs 6/61, PF 1.11 vs 1.11). These settings are config-only changes. If Phase A passes improve from ~42 to ~63 over 5y (same 1.5x scale as combine harness), the full pipeline $/mo and sust should improve materially vs B42 ($549/mo, sust=3.23x).

Mechanism: no new code. Generate per-year Phase A equity CSVs using the optimized Phase A settings:
```
for year in [2021, 2023, 2024, 2025, 2026]:
  equity_export --bars bars/yearly/bars_MNQ_dbv_{year}.csv --instrument MNQ --timeframe 5
    --risk-pct 1.0 --partial-r 1.5 --set engine=combined --set ifvg_entry_mode=close
    --set enabled_killzones=all --set swing_stop_lookback=0 --set target_clarity_mode=reject
    --out research/equity_b55/phase_a_opt_{year}.csv
```
Write scripts/run_b55_pipeline.py (or extend run_b42_pipeline.py) using:
  Phase A: equity_b55/phase_a_opt_{year}.csv (replaces equity_b42/ Phase A)
  Phase B: equity_b21/orb_reentry_r0p75_{year}.csv (unchanged from B21/B42)
Run funded_sim --haircut 200. Report $/mo, sust vs B42 baseline.

Also run 2022 holdout: equity_export with same settings on 2022 bars → check Phase A PF in 2022.

Fixed defaults: engine=combined, ifvg_entry_mode=close, killzones=all, risk=1.0%, partial_r=1.5, swing_stop_lookback=0, target_clarity_mode=reject.
Defining-behavior tests: none needed (no code changes).

Success criteria (vs B42: $549/mo, sust=3.23x):
- Primary: $/mo AND sust both improve → config change is directly deployable
- Secondary: sust improves even if $/mo marginally drops → priority is pipeline health
- If neither improves: B52's combine-harness result does not translate to pipeline (unexpected)

Priority: HIGHEST — directly informs Monday config decision for Lawrence. Run immediately.

Source: B52 combine harness result (9/61 passes); Lesson 101 (B52 confirmed +50% Phase A passes with zero code changes).

## B56 — ORB×iFVG same-day directional alignment gate (Phase 2 code + benchmark)  [done — rejected: sust=0.82x (unsustainable); volume starvation kills funded phase (51/52 accounts bust, avg 18.7d life vs 73.5d baseline); gate removes 42.2% of already-sparse ORB signals; Lesson 109]

**Phase 1 GO confirmed (wk3-r2):** ratio A+D/B+C = 1.48x > 1.4x threshold; 5/5 years consistent.

Hypothesis: Suppressing ORB signals when no prior same-direction iFVG has fired that day filters the 42.2% B+C group (PF=0.963) and retains the 57.8% A+D group (PF=1.427). For the funded phase, higher PF → lower bust frequency → improved pipeline sustainability despite lower volume.

Mechanism (code changes):
1. Add `orb_ifvg_alignment_required: bool = False` to StrategyParams (default off)
2. Add `app/strategy/day_signal_log.py` — `DaySignalLog` class:
   ```python
   class DaySignalLog:
       def on_bar(self, ts: datetime) -> None:  # reset on new day
       def log(self, ts: datetime, side: str) -> None
       def has_same_direction(self, ts: datetime, side: str) -> bool
   ```
3. In `SweepDisplacementComposer.on_bar()`: when `self.day_log` is set and a signal fires, call `self.day_log.log(bar.ts, signal.side)` before returning
4. In `ORBComposer.on_bar()` (or ORBDetector.on_bar): when `orb_ifvg_alignment_required=True`, check `self.day_log.has_same_direction(bar.ts, signal.side)` — if False, return None
5. Wire in backtest/runner.py `_build_runner`: create shared DaySignalLog instance; pass to both the iFVG runner's composer and the ORB runner's composer when engine=combined
6. Wire in main.py `_build_runner`: same

Defining-behavior tests (tests/test_orb_ifvg_alignment.py):
1. Day log resets on new trading day (signal logged day 1, query day 2 → False)
2. Gate passes: same-direction iFVG logged before ORB time → ORB signal fires
3. Gate blocks: only opposite-direction iFVG logged → ORB returns None
4. Gate blocks: empty log (no prior iFVG on that day) → ORB returns None
5. Gate passes with mixed log: some same, some opposite → ORB fires (A+D group included)
6. Gate disabled by default: orb_ifvg_alignment_required=False → ORB fires unconditionally
7. iFVG runner logs signal to shared log on signal emission

Fixed defaults: `orb_ifvg_alignment_required=False` (off; enable for benchmark).

Benchmark (after code):
1. Phase B: equity_export --set engine=orb --set orb_reentry_after_stop=True
   --set orb_r_multiple=2.5 --set orb_ifvg_alignment_required=True --risk-pct 0.75 --partial-r 1.5
   per-year equity_b56/ CSVs + funded_sim --haircut 200
2. Full pipeline: Phase A (equity_b55/ optimized) → Phase B (equity_b56/ filtered ORB-reentry)
3. Compare to B42 ($549/mo, sust=3.23x)

Volume note: from Phase 1 analysis, 42.2% of ORB days filtered → ~12 ORB trades/month before reentry; with reentry ~16-17/month. Lower volume reduces per-account earnings; higher PF reduces busts. Net pipeline impact: unknown until benchmarked.

Success criteria (vs B42: $549/mo, sust=3.23x):
- Primary: sust >= 3.23x (maintains pipeline sustainability at lower volume)
- Secondary: $/mo >= $450 (acceptable reduction)
- If sust > 3.23x AND $/mo >= $549: clear win; recommend as Phase B replacement

Source: wk3-r2 cross-engine confluence data mining (scripts/analyze_cross_engine_confluence.py). Lesson 103.

## B57 — iFVG r_multiple sensitivity benchmark (funded + combine)  [done — wk3-b57-rmult 2026-06-14; CANDIDATE: r=2.5 beats B42 baseline ($566/mo, 3.54x vs $549/mo, 3.23x); recommend remove MNQ r_multiple override 3.5→base 2.5]

**Hypothesis:** The deployed iFVG r_multiple=3.5 has never been varied in funded benchmarks (all B1-B54 used the deployed MNQ instrument override of 3.5). A lower target (2.5R or 3.0R) increases WR by bringing target closer to entry — potentially improving combine pass rate (more months reach $3k threshold) and funded bust rate (more frequent exits reduce holding-period risk exposure).

From MFE/MAE data (iFVG, partial_r=0, 5y excl 2022):
- Winner MFE p25=1.11R, p50=3.0R, p75=3.69R, p90=3.69R+ → ~50% of winners hit 3.5R target exactly
- Loser MFE p75=1.03R, p90=1.80R → at r=2.5R, trades with loser-MFE >= 2.5R (10%) become winners
- Lowering target to 2.5R: ~25% of current winners (those with MFE 2.5-3.5R) capture target earlier; losers with MFE >= 2.5R flip to winners

Mechanism: no new code. `r_multiple` is already in StrategyParams (bot_config.py:31). Test via `--set r_multiple=2.5` etc. NOTE: verify MNQ instrument override (r_mult=3.5 in MNQ overrides) doesn't silently take precedence over CLI `--set r_multiple`. If it does, disable or modify the MNQ override in the benchmark call (using `--instrument-override MNQ r_mult=2.5` or similar).

Fixed defaults to test: r_multiple=2.0, 2.5, 3.0 (vs deployed 3.5 baseline).
Defining-behavior tests: none needed (no code changes).

Benchmark (funded objective, deployed config):
1. Combine harness: run_monthly_combine.py --set r_multiple=2.0/2.5/3.0 --instrument MNQ
   Compare passes/61 and PF at each value vs deployed baseline (9/61, PF=1.11 from B52)
2. Funded: equity_export per-year at r_multiple=2.0/2.5/3.0 with deployed settings + funded_sim
   Compare $/mo, sust vs B42 baseline ($549/mo, 3.23x)

Important: run at FULL deployed config (combined+close+all-day+partial=1.5+r=1.0%) to avoid research-vs-deployed discrepancy (Lesson 50). Verify r_multiple override behavior before running.

Success criteria (vs B42/B55 baselines):
- Combine: r_multiple=2.5 or 3.0 achieves >= 11/61 passes (B24 level) → shorter target improves combine throughput
- Funded: any r_multiple achieves sust >= 3.23x AND $/mo >= $500 → actionable change

Prior: ~40% that r=2.5 improves both combine AND funded. Shorter targets often help combine pass rate (more achievable monthly goals) but reduce per-account funded earnings. Net pipeline effect uncertain.

Source: MFE/MAE winner MFE distribution (wk1-r3); Lesson 50 config parity gap. iFVG r_multiple is the only unexplored primary StrategyParams field for the funded objective.

## B55 — iFVG "Silver Bullet" window (10:00-11:00 AM ET only)  [done — wk3-b55 2026-06-14; REJECTED both objectives]
Source: Chermane Trades "This Trade Required PATIENCE / NQ ICT SMC" walkthrough
(youtu.be/rj7B8bdFaLs). The video's mechanics are standard ICT (sweep ->
displacement -> FVG/OB retracement in a killzone) = already covered by iFVG +
sweep_bos + killzone filtering. The one crisp, testable element of this ICT-NQ
style not yet isolated: the "Silver Bullet" hour, 10:00-11:00 AM ET.

Hypothesis: concentrating iFVG entries into the single 10:00-11:00 ET hour
improves quality. Supporting prior (OUR data, wk1-r2 per-hour): 10:xx ET is the
strongest NY-AM hour (PF 1.235). This is a CONCENTRATION test (keep only the
best hour), distinct from B18 (which REMOVED sessions and hurt) and B44 (which
blocked a mid-session band, close-mode mismatch). Volume will be severe (~1
hour/day, ~5-10 signals/mo) -> route to the FUNDED objective primarily; combine
is secondary (likely too sparse).

Mechanism: default-off `silver_bullet_only: bool = False` in StrategyParams.
When True, the iFVG signal path emits ONLY for closed bars whose ET time is
within [10:00, 11:00) ET; detector state still accumulates outside the window
(suppress at emission, not detection). ET conversion as elsewhere
(astimezone(ZoneInfo("America/New_York"))). Note: check whether enabled_killzones
can already express this window; if so, prefer a no-code benchmark and skip the
flag.

Defining-behavior tests (tests/test_silver_bullet.py):
1. silver_bullet_only=False (default): signals unchanged vs baseline.
2. =True: a valid iFVG at 10:30 ET fires; the same setup at 09:45 ET and at
   11:15 ET is suppressed.
3. Window boundary: 10:00:00 ET fires; 11:00:00 ET does not (half-open interval).
4. ET timezone correct across a UTC bar that maps into the window.

Benchmark (FUNDED primary, combine secondary; parity `--partial-r 0 --set swing_stop_lookback=0`):
- equity_export/funded_sim silver-bullet-on vs control AND vs B19 best.
Success: improves the funded objective AND PF vs control despite the volume cut.
Stop rule: loses on both -> reject (it joins B18/B44 as evidence that NQ iFVG
time-windowing does not add value). Prior ~30% (concentration into a known
high-PF hour is evidence-backed, but the volume cut is brutal and prior
time-window items have failed).

## B56 — SetupGrader audit + conditional refactor  [done — informational; grader miscalibrated (fvg_singular INVERTED, mom/pd vacuous, no component predicts outcome); grader_min_grade=F currently; zero P&L impact; B57 to replace grader from scratch]
Lawrence: "the grader might need to get refactored, I'm not sure it's working
well." B51 already produced strong evidence he is RIGHT: grade does not predict
outcome monotonically (A+B PF 1.286 vs D+F 1.080, ratio 1.190; and C PF 0.996 is
the WORST bucket -- worse than D 1.060 and F 1.124). A grader where C ranks below
F is miscalibrated. This item diagnoses WHY before changing anything (Rule 3 /
Rule 8: audit before refactor; don't refactor what you haven't measured).

PHASE 1 -- per-component audit (no behavior change):
SetupGrader scores a setup from several inputs/components (read
app/strategy/grader.py / SetupGrader to enumerate them). For EACH component,
over 5y (2021/2023/2024/2025-26, NOT 2022), measure its standalone correlation
with trade outcome (WR / PF / mean-R), bucketing trades by that component's
value. Output a table: component -> predictive (monotone with outcome)?,
inverted?, or noise. Reuse the trade lists + excursion data; write
scripts/audit_grader.py.

DECISION (from the audit, three outcomes):
- (a) Some components predict, others are noise/inverted -> Phase 2 REFACTOR:
  re-weight to keep only the predictive components. Grader is used live via
  grader_min_grade, so per loop guardrails this ships default-off + a written
  recommendation; Lawrence decides Monday.
- (b) NO component predicts outcome -> recommend REMOVING the grader gate rather
  than refactoring (consistent with fib/OTE, OR-width, breakout-ext, and B51
  grade all failing as NQ 5min quality predictors -- grade may simply not be a
  signal here). Document; do not build a refactor that can't help.
- (c) The grader is computing something other than what its grades imply (a bug,
  e.g. an inverted sign on a component) -> fix the bug, add a regression test,
  re-run the B51 monotonicity check to confirm grades now rank by outcome.

Phase 2 tests (only if a refactor/bugfix happens):
1. Grader produces expected grades for known synthetic setups (pin intended
   behavior first).
2. Post-refactor: re-run the per-grade PF check -> grades rank monotonically with
   outcome (the property B51 showed is currently violated), OR the item lands as
   "remove the gate" with evidence.

Guardrails: deterministic only (Rule 5 -- grader stays code, no LLM). NEVER
change live grader_min_grade behavior without Lawrence; default-off /
recommendation only. Success: either a grader whose grades actually predict
outcome (monotone A>B>C>D>F by PF), or a documented recommendation to drop the
gate. Source: Lawrence flag + B51 non-monotonic finding (Lesson 99).

## B57 — New lessons-based trade-quality grader (composite filter)  [done — Phase 1 NO-GO: composite WoE score (is_long, is_rank1, is_orb, hour buckets) fails both OOS criteria (decile ratio 1.08 < 1.30; top-half PF 1.187 < best single rank-1 PF 1.234); contextual features are not additively independent; no grader built; analysis script scripts/analyze_b57_composite_grader.py committed]
Lawrence: build a NEW grader that takes ALL lessons learned into account, ranks
a scenario more accurately than the current one, to filter trades. B56 audits
the OLD grader's components; B57 builds a fresh composite quality score from
EVERY empirically-validated predictor we have, validated OUT OF SAMPLE. Do B56
first — its per-component table is B57's starting feature inventory.

What we have LEARNED actually predicts iFVG/ORB outcome (candidate features):
- side: iFVG long PF 1.136 vs short 0.960 (B15); ORB long 1.320 vs short 1.109 (wk1-r2)
- signal rank of day: rank-1 PF 1.129 vs rank-2 0.970 / rank-2+ shorts 0.841 (B23)
- hour-of-day: 10:xx ET 1.235, 09:xx 1.178, London 04-05:xx 1.31-1.43; noon/NY-PM
  negative (B18, B55)
- engine/context: combined Phase A 2x passes (B40); ORB value concentrates in the
  EOD-flatten cohort (Lesson 88)
- N+1 next-bar confirmation: confirmed PF 1.619 vs unconfirmed 0.661, ratio 2.45x
  (B53) — THE strongest discriminator, BUT it is POST-entry (arrives one bar after
  fill) so it CANNOT be a pre-trade filter; B53 already showed using it as an exit
  fails. EXCLUDE from the entry grader; note it as the reason a pre-trade grader
  has a hard ceiling.
What we have learned does NOT predict (EXCLUDE — do not re-add as features):
  fib/OTE retrace depth (B34), OR width (B45), breakout extension (B49), prior-day
  range (B5), daily-bias direction (B35), inversion body size at deployed config
  (B16), and the current SetupGrader's own grade (B51). Quality-of-geometry has
  failed 6x; the predictors that survive are STRUCTURAL/CONTEXTUAL (side, rank,
  hour), not setup-shape.

THE central risk: a composite of "long + rank-1 + good-hour" is just B15/B23/B18
stacked, and stacking filters has cut volume without adding edge before (B19
marginal, B48 hybrid rejected). So B57 must prove the COMPOSITE beats the BEST
SINGLE existing filter OUT OF SAMPLE — not merely beats the broken current grader
(a low bar) and not merely separates IN sample (multi-feature scores overfit).

PHASE 1 — feature study + interpretable composite design (analysis only, go/no-go):
1. Assemble the per-trade feature matrix (side, rank, ET-hour bucket, engine,
   killzone, ATR-regime bucket, displacement size — all PRE-entry) + outcome
   (R / win) from 5y trade lists + excursion data. EXCLUDE 2022.
2. Fit a SIMPLE, INTERPRETABLE, DETERMINISTIC scorer — a weighted score table or
   a shallow (depth<=3) decision tree / few-term logistic regression. NO opaque
   ML (Rule 5: deterministic, inspectable). Train on 2021+2023+2024; VALIDATE on
   held-out 2025-26 (true OOS). Report per-decile PF on the OOS set.
3. GO/NO-GO: proceed to Phase 2 ONLY if, on the OOS years, the top score-decile
   PF materially exceeds the bottom decile (monotone-ish) AND the top-half-by-score
   subset beats the BEST single existing filter (long-only / rank-1 / 10-11ET) on
   the funded objective at comparable volume. If the composite only matches a
   single filter, REJECT — it is complexity without edge (document it).

PHASE 2 — engine + benchmark (only if Phase 1 = GO):
- Implement the scorer as a NEW default-off grader (e.g. `quality_grader="v2"`),
  emitting a numeric score + a pass/block at a fixed threshold chosen on TRAIN,
  applied on the OOS years only for the headline result. Deterministic; keep the
  old grader intact (this is additive, default-off).
- Defining-behavior tests (tests/test_quality_grader_v2.py): scorer is a pure
  function of the documented features; known feature vectors -> known scores;
  threshold gates as specified; off by default (signals unchanged).
- Benchmark BOTH objectives (parity `--partial-r 0 --set swing_stop_lookback=0`)
  vs (a) control, (b) the best single filter, (c) the old grader. Report volume.

Success: the v2 grader's score ranks trades monotonically by OOS PF AND the gated
subset beats the best single existing filter on the funded objective. Stop rule:
fails OOS monotonicity OR fails to beat the best single filter -> reject; document
that contextual filters are best applied singly, not composited (would be the
strongest statement yet of the quality-score ceiling on NQ 5min).

Guardrails: deterministic only; OOS validation mandatory (overfitting is the
expected failure); never change live grading without Lawrence (default-off +
recommendation). Source: Lawrence direct request 2026-06-14; builds on B51/B56
grader findings + the full validated-predictor set from B5/B15/B18/B23/B34/B40/
B45/B49/B53/B55.

## B58 — Confluence-weighted sizing (additive, not a gate) incl. Silver Bullet  [done — REJECTED: $430/mo sust 0.65x vs flat-size $797/mo 0.77x; down-sizing dominates up-sizing; document alongside B47]
Lawrence asked: can B55 (Silver Bullet 10-11 ET window) be used in confluence
with other strats? Landscape: HARD confluence gates already failed -- B47
(iFVGxORB must-agree) lost on both metrics by removing trades. SOFT scoring
confluence is B57 (the new grader scores hour+side+rank+engine together). The
remaining untested angle is ADDITIVE confluence: do not drop any trade -- SIZE UP
when multiple validated edges align. This sidesteps the volume-cut failure mode
that killed B47/B19/B48 (all subtractive).

Hypothesis: a trade where several independent validated edges stack is higher
conviction and deserves more size; sizing the high-confluence cohort up (and the
low-confluence cohort down) improves the funded objective without cutting volume.
Confluence count uses ONLY validated predictors: +1 long side (B15), +1 rank-1 of
day (B23), +1 inside 10-11 ET Silver Bullet (B55/B18), +1 combined-engine context
(B40). EXCLUDE the 6 failed predictors per B57. If B57 ships a numeric score,
reuse it as the confluence measure instead of a hand-rolled count.

Mechanism: reuse B50's dynamic-sizing hook (this is a sizing policy, not a new
engine). Default-off `risk_policy="confluence"`: size = base x multiplier(count),
fixed ladder (NO tuning): count>=3 -> 1.5x, count==2 -> 1.0x, count<=1 -> 0.5x.
Total risk still capped at the configured per-trade max (respects MLL). All
signals still TAKEN (additive -- no gate).

Depends on: B50 (sizing hook), B55 (confirms the window cohort), ideally B57
(score). If B50/B57 not done, implement the simple count inline.

Defining-behavior tests (tests/test_confluence_sizing.py):
1. risk_policy != "confluence" (default): sizing unchanged.
2. count>=3 trade sizes at 1.5x base; count==2 at 1.0x; count<=1 at 0.5x.
3. No trade is suppressed (additive -- assert trade COUNT equals baseline; only
   sizes differ).
4. 1.5x up-sized trade respects the per-trade risk cap (no MLL breach).

Benchmark (objective metrics; sizing rescales bets so judge on the objectives,
NOT PF -- see B50 metric-trap note): funded_sim + run_monthly_combine,
confluence-sizing vs flat-size control AND vs the best single filter.
Success: improves the funded objective (busts / $-per-month / sustainability) vs
flat sizing at the SAME average risk. Stop rule: worse than flat on its objective
-> reject (confluence carries conviction info but not tradable edge; document
alongside B47).

Source: Lawrence 2026-06-14 (B55 confluence question). The additive complement to
B57's scoring and B50's dynamic risk; the one confluence form (size, not gate)
not yet tested.

## B59 — Long-only sweep-reentry micro-engine (funded-only overlay)  [done — REJECTED: overlay does not improve funded pipeline at any tested risk level; sust worse at risk=0.50% (1.27x vs 2.14x) and risk=1.0% (0.64x vs 0.68x); marginal apparent win at risk=0.25% is 1-bust noise; engine ships default-off; doc research/equity_b59/; Lesson 113 added]
Lawrence-specified. A long-only reentry micro-engine that ACTIVATES only after
the day's main ORB long has stopped out, as a funded-phase overlay.

Mechanism (standalone detector + runner shim per the engine pattern; new engine
`sweep_reentry`, default-off):
- Arm only after an ORB long stop-out on the day (consume the ORB on_stop_loss
  signal, same wiring as B14 ORB-reentry). Long-only; never short.
- Trigger: a downside liquidity sweep that breaks the current session low OR the
  prior-day low by an ATR-normalized amount (sweep depth >= k x ATR; declare k
  fixed, e.g. 0.25 ATR), THEN a clean iFVG-style upward displacement + inversion
  (reuse the existing SweepDisplacement detection + inversion quality filter --
  do NOT reinvent; Lesson 1 the inversion is the quality filter).
- Entry respects the existing iFVG inversion quality filter and swept-extreme stop
  convention; WIDE swing-anchored stop; SMALL risk (test r0.25 and r0.5 only).
- Log trades SEPARATELY (own engine tag) so the overlay's contribution is isolable.

Priors: B14 (ORB-reentry) was candidate-only and pipeline-negative solo but lifted
in two-phase (B21). B33 (anticipatory probe pre-inversion) was rejected -- but THIS
keeps the full inversion filter (post-inversion entry), so it does not inherit B33's
failure. Sweeping the session/prior-day low + displacement is the iFVG mechanism
applied to a specific reentry context. Medium prior.

Defining-behavior tests (tests/test_sweep_reentry.py):
1. Engine off / no prior ORB stop today: no signal.
2. ORB long stopped, then sweep of session low by >= k ATR + upward displacement +
   inversion -> long signal at inversion close, stop beyond swept extreme.
3. Sweep too shallow (< k ATR) -> no signal. 4. Short setups -> never fire.
5. Only one reentry overlay arm per day.

Benchmark: Combine trade count + PF (report, expect sparse); FUNDED as an overlay
ON TOP of the B21/B27 best pipeline (ORB-reentry Phase B) -- does adding this
long-only overlay raise funded $/mo or cut busts without dropping sustainability
< 1.0? Success: funded pipeline improves with overlay vs without. Stop rule: no
funded improvement -> reject. Source: Lawrence 2026-06-14.

## B60 — ORB + iFVG expansion to ES.v.0 / MES.v.0  [done — diagnostic: iFVG fails on ES/MES (PF 0.896, sust 0.18x); ORB weak edge (PF 1.075, sust 0.55x); both below 1.0 threshold; edge is NQ/MNQ-specific at 1-contract sizing]
Lawrence-specified. Port the NQ/MNQ 5min ORB and iFVG engines to ES/MES with the
SAME structural rules (opening-range window, iFVG inversion chain, swept-extreme
stop geometry). Replace fixed-point thresholds (min_absolute_body 5.0, stop_buffer
3.0) with ATR- or %-of-price-normalized values calibrated per instrument (this is
B6's ATR-normalization, which was a no-op on NQ alone -- but ES has a different
price/tick regime so it matters here). Tune ONLY ATR and volume multipliers so
Combine trade frequency stays in the 60-90 trades/month band (declare the tuned
values; this is a calibration, not an edge sweep).

Data: pull ES.v.0 + MES.v.0 5min over the SAME 5y window as NQ via Databento.
DATABENTO BUDGET: estimate-only FIRST, log to research/databento_ledger.txt, abort
if it would exceed the $20 cap. Prefer the v-rolled contract (Lesson: c.0 has thin
expiry Fridays). Save to bars/bars_ES_dbv_2021_2026.csv etc.

Benchmark: per-year Combine passes, funded PF, pipeline sustainability (passes vs
XFA busts) for ES/MES vs NQ. Question answered: is the edge NQ-specific or general
across index futures? Success criterion is DIAGNOSTIC (not pass/fail) -- report
whether ES/MES show comparable per-year passes + sustainability >= 1.0. Defining
tests: engine runs on ES bars; ATR-normalized thresholds reproduce NQ behavior at
NQ price levels (parity check). Source: Lawrence 2026-06-14.

## B61 — Excursion-ladder exit research (BE / partial variants)  [done — REJECTED 2026-06-14]
Lawrence-specified. Using the existing 5y MFE/MAE infrastructure (B2) and the
`mfe_pts/mae_pts/r_mfe/r_mae` fields, mine excursion-ladder exit policies for
ORB-reentry r0.75 and close-mode iFVG. Keep the initial swept-extreme stop; vary
BE-move point and partial-profit point as PLUGGABLE exit modes:
- BE at 1.0R vs 1.5R; partial at 2.0R vs 2.5R (the declared grid -- this is the one
  sanctioned exit sweep, justified because B2 only tested be_trail_r=1.0 which
  failed by killing two-thrust winners; the lesson said any BE must engage AFTER
  ~2.0R, so this grid tests exactly that boundary).
- Implement as default-off exit modes via runner.exit_request (B2 infra exists).
For each ladder, re-score Combine PF, funded PF, two-phase pipeline metrics.
Success: an exit scheme that improves funded PF OR cuts XFA busts WITHOUT dropping
Combine volume below 60-90/mo or sustainability below 1.0. Stop rule: every ladder
worse than the fixed-target baseline on both objectives -> reject (confirms B2's
finding that NQ winners need to run, exits don't help). Defining tests: each exit
mode triggers BE/partial at the specified R; off-by-default unchanged.
Source: Lawrence 2026-06-14; extends B2.

## B62 — Orderflow-proxy confirmation + veto for ORB (cum-delta + RVOL)  [pending — Lawrence-requested 2026-06-14; rank ahead of routine queue; model:opus]
Lawrence-specified. Extend the NQ 5min ORB engine with bar-derived orderflow-style
filters (we have no tick/L2 data, so APPROXIMATE from OHLCV):
- Cumulative delta proxy: per bar, approximate up-tick vs down-tick volume (e.g.,
  classify the bar's volume by close position in range, or Bear/Bull volume split);
  maintain an intraday cumulative series. Declare ONE fixed proxy formula.
- RVOL: intraday relative volume vs the trailing-N-day same-time-of-day average.
Experiments (default-off flags):
  (a) CONFIRM: take ORB breakout only when 3-bar cumulative-delta AND RVOL confirm
      the breakout direction.
  (b) VETO/EXIT: exit early when a 3-bar cumulative-delta divergence forms against
      the open position.
Report trade count, Combine pass rate, funded PF, pipeline sustainability per
variant vs ORB baseline. Priors: orderflow proxies from bars are crude; academic
work found no OHLCV signal family survives on MNQ (Lesson 6). Treat as falsifiable.
PHASE 1 cheap check FIRST: does the cum-delta/RVOL proxy at breakout correlate with
ORB outcome at all (bucket by proxy value -> PF separation)? If no separation, do
not build (b). Success: a variant improves funded PF or pass rate without cutting
volume below 60-90/mo. Stop rule: both variants worse -> reject. Defining tests:
proxy computed deterministically; confirm-gate suppresses unconfirmed breakouts;
divergence-exit fires on the specified pattern. Source: Lawrence 2026-06-14.

## B63 — Pipeline-aware funded-only sizing/routing variants  [pending — Lawrence-requested 2026-06-14; rank ahead of routine queue]
Lawrence-specified. Funded-only configs for ORB and long-only close-mode iFVG that
target the funded objective, ASSUMING Phase A supplies Combine accounts separately.
Start from the current best two-phase pair (iFVG Phase A + ORB-reentry r0.75 Phase
B, B21). Experiments:
  (a) conditional intraday SIZE INCREASE after an early ORB winner (declare the
      rule: e.g., +0.5x risk on the next signal if the first trade of the day won
      by >= 1R; reuse B50 sizing hook).
  (b) long-only / session-filtered iFVG funded configs that raise PF but change
      account cycling speed (reuse B15/B19 configs).
For each: per-account net, funded bust count, cycle duration, resulting two-phase
$/mo and sustainability. Success: beats the B21/B27 pipeline benchmark on $/mo or
sustainability WITHOUT violating sustainability >= 1.0. Stop rule: none beat B21 ->
reject (B21 stands). Metric note: (a) is path-dependent sizing -> judge on funded
objective not PF (B50 trap). Defining tests: size-up rule fires only after the
qualifying early winner; respects per-trade risk cap. Source: Lawrence 2026-06-14.

## B64 — SMT divergence (NQ vs ES) reversal filter  [pending — Lawrence-requested 2026-06-14 (TradeZella review); DEPENDS ON B60 (needs ES data); rank after B60]
From TradeZella (Trader Kane "SMT Divergence + PO3"; Trader Mayne) -- the one
genuinely new mechanism on that page not already covered. SMT divergence: when NQ
makes a higher high but the correlated ES does NOT (or NQ lower low while ES holds),
it flags a likely reversal -- a cross-instrument liquidity-sweep tell.

Hypothesis: gating/confirming iFVG (or ORB) signals with concurrent NQ-vs-ES SMT
divergence improves quality. Requires the ES feed from B60 (do B60 first).
Mechanism: at an iFVG sweep, check whether ES swept its corresponding level on the
same bar; SMT-divergence = NQ swept but ES did NOT (non-confirmation). Default-off
`smt_filter_enabled: bool = False`; when on, require SMT divergence at the sweep.
PHASE 1 cheap check FIRST (no engine): over 5y, label each iFVG signal with
SMT-divergence-present? and compare forward PF (divergence vs no-divergence). GO
only if divergence cohort PF materially exceeds non-divergence (>= 1.25x). Else
reject (SMT joins the failed external-claim list).
Priors: external/ICT claims are 4-for-4+ failures here; SMT needs a second data
feed and adds a hard gate (B47 gates failed). But it is a genuinely different
signal (cross-instrument, not setup-geometry). Medium-low prior (~25%).
Benchmark (if GO): both objectives vs control. Defining tests: SMT label computed
from aligned NQ/ES bars; gate suppresses non-divergent signals; off-by-default
unchanged. Source: Lawrence 2026-06-14; TradeZella strategies page.

(TradeZella review note: the other ~35 strategies map to existing/rejected work --
ICT sweep/FVG/OTE/breaker/PO3/liquidity = iFVG/sweep_bos/B33/B34/B47; auction/
volume-profile/VWAP/value-area = rejected VWAP-MR + kz_levels; break&retest =
sweep_bos; order-flow/delta/Bookmap = B62 proxy. Round-number .20/.80 levels and
ADR-target models noted as LOW-prior candidates, not queued. Only SMT divergence
was novel + testable enough to queue.)

## B65 — Markov 2.0 regime FILTER over iFVG + ORB  [pending — PRIORITY: Lawrence-requested 2026-06-14; rank ahead of routine queue; model:opus]
Apply the Markov 2.0 regime method (see skill `markov-2-hedge-fund-method`) as a
daily-regime FILTER that gates the existing intraday engines. The method: label
each day by its trailing 20-day cumulative return (>= +5% BULL, <= -5% BEAR, else
SIDEWAYS), build a state-transition matrix, derive the daily regime signal
`P(bull tomorrow) - P(bear tomorrow)`. As a filter: allow LONG entries only when
signal > +thr, SHORT entries only when signal < -thr, flat in chop.

NON-NEGOTIABLE method fixes (from the skill):
- FIX 1 stride sampling: build the transition matrix from NON-overlapping 20-day
  windows (stride=20), NOT overlapping rolling windows (which fake diagonal
  persistence). Compute both, but the filter uses the stride-sampled matrix.
- FIX 2 label self-check: programmatically verify BULL/BEAR mapping against known
  NQ periods (e.g. 2021 run-up = BULL, 2022 selloff = BEAR, a flat stretch =
  SIDEWAYS) before using the labels.
- Walk-forward / point-in-time ONLY: recompute the matrix as you walk; the regime
  signal on day D uses data through D-1 only. No lookahead (this is the whole game
  — the method's own "proof not promises" rule).
- Deterministic Python (numpy/pandas), CLAUDE.md Rule 5 — never LLM-estimated.
Data: resample the local `bars/bars_MNQ_dbv_2021_2026.csv` to daily; NO new fetch.

PHASE 1 -- cheap falsification FIRST (no engine; go/no-go):
Label every historical iFVG/ORB trade with the prevailing point-in-time regime
signal bucket (bull/sideways/bear) and compare forward PF / win-rate per bucket
PER SIDE, over 2021/2023/2024/2025-26 (NOT 2022). GO only if the regime
materially separates outcomes in the expected direction (longs in bull-regime PF
>= 1.3x longs in bear-regime, and symmetric for shorts). If no separation, REJECT
-- document that the Markov daily regime does not predict NQ 5min trade quality
(joins B35 daily-bias gate + B5 prior-day-range in the failed daily-context set).

PHASE 2 -- filter engine (only if Phase 1 = GO):
- StrategyParams `markov_filter_enabled: bool = False` (+ `markov_signal_thr`,
  fixed default e.g. 0.0). Suppress at signal emission per the regime gate; keep
  detector state. Closed-bar only; the daily regime is fixed for the session.
- Defining tests (tests/test_markov_filter.py): off=unchanged; bull-regime day
  suppresses shorts, allows longs; bear-regime suppresses longs; chop suppresses
  both; stride matrix used (not overlapping); label self-check passes.
Benchmark BOTH objectives (parity `--partial-r 0 --set swing_stop_lookback=0`)
vs control. Success: improves PF AND the objective vs filter-off. Stop rule:
loses on both -> reject. Prior ~30% (daily-context gates have failed here, but the
probabilistic stride-corrected matrix is a more principled test than B35).
Source: Lawrence 2026-06-14; skill markov-2-hedge-fund-method.

## B66 — Markov 2.0 STANDALONE daily-directional engine (Topstep-compatible)  [pending — Lawrence-requested 2026-06-14; DEPENDS ON B65 signal infra; rank ahead of routine queue; model:opus]
Markov STANDALONE mode, expressed within Topstep's intraday-flatten constraint
(pure multi-day holds are impossible — accounts flatten EOD). Each session: the
day's regime signal sets direction + conviction; enter at session open in the
signal's direction, size scaled to `|signal|` with a fixed cap, flatten EOD.
Reuses B65's walk-forward stride-sampled regime signal (do B65 first).

Mechanism: new default-off engine `markov` (standalone detector + runner shim per
the engine pattern). One position/day, opened at the configured session start in
the regime direction when `|signal| >= thr`, sized to `|signal|` (cap at the
configured max risk), stop = fixed ATR-multiple, exit = EOD flatten or fixed R.
All the B65 fixes apply (stride, label self-check, walk-forward, deterministic).

Defining tests (tests/test_markov_engine.py): off-by-default (engine!=markov ->
no signal); bull-regime day -> one long at session open sized to |signal|;
bear-regime -> short; chop (|signal|<thr) -> no trade; size respects the cap.
Benchmark BOTH objectives vs control AND vs the B65 filter result. Success:
beats control on an objective per stop rules. Prior ~20% (a daily-directional
intraday bet is a coarse instrument vs the sweep/displacement engines; the value,
if any, is likely as the B65 filter, not standalone -- but the user asked to test
it on its own, so we do, honestly). Note in the writeup whether standalone adds
anything the filter does not. Source: Lawrence 2026-06-14; skill markov-2-hedge-fund-method.
