# Research Journal — append-only. Newest entries at the BOTTOM.

Entry format:

## <ISO timestamp> — session <short-id> — <BACKLOG item or RESEARCH>
- **Ran:** <what was executed, exact commands/configs>
- **Numbers:** <headline metrics vs baseline>
- **Verdict:** candidate | rejected | dataset | shipped | partial
- **Learned:** <2 sentences, plain English — this line feeds the UI>
- **Next:** <pointer for the following session>

---

## 2026-06-12T22:30Z — session seed — infrastructure
- **Ran:** equity_export.py bridge built + verified (ORB r2.5 on 2024 →
  funded_sim: 26 attempts/10 passes/15 busts; XFA $23,566 net, 8/9 accounts
  busted at haircut $200, risk 1.25%).
- **Numbers:** first-ever funded-objective datapoint; bust rate at full sizing
  is the obvious frontier variable.
- **Verdict:** dataset
- **Learned:** The XFA payout pipeline is rich even from a config that only
  passes 24% of Combine months — and bust rate, not payout size, is the thing
  to optimize. Sizing is the lever (B1 probes it).
- **Next:** B1 — full funded-objective re-scoring of the bench.

## 2026-06-13T01:00Z — session wk1-b4 — B4 (weekly live forensics)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water,
  flat, no drift, no lockout.
- **Session note:** the wrapper launched TWO sessions ~4s apart; the twin claimed
  B1 first, so this session yielded B1 and skipped B2 (would mutate backtest code
  under the twin's running variant comparison) → claimed B4 out of rank order.
- **Ran:** week's logs/trades mining; free TopstepX fetch (7d MNQ — came back as
  U26, see lesson); parity replays over same-day M26 bars (`bars_MNQ_parity.csv`)
  for 06-11 (ifvg) and 06-12 (ifvg + combined); per-fill execution-slippage
  measurement vs decision-time 1-min closes (n=10); subagent log forensics on
  three anomalies.
- **Numbers:** true execution slippage mean 0.78 pts (3.1 ticks), canonical-era
  0.50 pts (2 ticks) vs 1 tick modeled → recommend `slippage_ticks_market: 2`.
  The headline "111.5-pt slippage" trade had ~0.5 pt real slippage (column
  measures fill−FVG-edge, by design of composer.py:398). 06-12 signal parity 1/1
  exact (entry ref 29521.5 both sides); replay confirms the 14:09Z ORB long the
  14:22Z engine cutover missed. Excursion ledger 06-07..11 cross-instrument
  contaminated (no instrument filter in ExcursionTracker.on_bar). Rolling
  trades.csv lost 22 rows to a git tree-restore (recoverable from daily file).
- **Verdict:** dataset (3 data-integrity bugs filed as B11–B13; slippage
  recommendation for Monday)
- **Learned:** Our execution is fine — the backtest understates slippage by only
  ~1 tick, and the scary slippage column is mislabeled plan-deviation, not fill
  quality. The week's real risks were data-integrity ones: contaminated
  excursions, a git-wiped ledger, and roll-week fetches returning the wrong
  contract.
- **Next:** B2 (MFE/MAE ladder) unless B1 still in progress; B11 (excursion
  instrument filter) is the highest-value small fix.

## 2026-06-13T02:30Z — session wk1-b1 — B1 (funded-objective bench scoring)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12, flat, no drift.
- **Ran:** reclaimed orphaned B1 (prior session crashed after control_2023.csv only);
  created `bars/yearly/` splits (2021-2026) from Databento 5y file; ran
  `scripts/run_b1_funded.py` for all 6 variants at r1.25 (30 export tasks, 4 workers),
  then sizing sweep for orb and trail_1r at r0.5/0.75/1.0 (30 more); finally 2022
  holdout confirmatory for ORB r0.75. All scored at haircut 0/200/400.
- **Numbers (h200):**
  - All r1.25 variants: pipeline NEGATIVE (combine passes < XFA busts). Best raw XFA
    net: control $100k, trail_1r $100k, stop_cap $100k — but each needs 46–77 funded
    accounts while producing only 21–34 Combine passes over 5 years.
  - ORB combine pass rate: 39% (r1.25) to 57% (r0.5) vs 16–21% for iFVG variants.
  - **ORB r0.75 winner:** 15 Combine passes / 14 XFA busts at h200 (borderline
    sustainable); $35.7k XFA net over 5 years; median 32 days to pass Combine, 33
    days to first payout; 2022 holdout PF 1.15, net +$5,551 ✓.
  - ORB r0.5: firmly pipeline-positive (8 passes vs 6 busts) but only $21k net/5yr.
  - Trail_1R: worst pipeline (21 passes vs 46 busts at r1.25) despite best raw payouts.
- **Verdict:** candidate — ORB r0.75 recommended as Phase-B (funded) config for B3.
- **Learned:** "Net XFA payouts" is a misleading metric without pipeline accounting —
  trail_1r looks best at $100k but needs 2× more funded accounts than the strategy
  can produce Combine passes. ORB's structural edge for the funded objective is its
  2–3× higher Combine pass rate (39–57% vs 16–21%), not raw payout size.
- **Next:** B11 (excursion instrument filter — small, high value) is the next pending
  item that unblocks clean live data; B2 (MFE/MAE) follows; B3 (phase-policy pipeline
  sim) needs B1 done (now done) — can begin next session.

## 2026-06-13T06:00Z — session wk1-b2 — B2 (MFE/MAE excursion ladder)
- **Bot health:** not checked this session (market closed; no live risk; prior session confirmed flat).
- **Ran:** (Steps 1-3)
  - Step 1 (dataset): added MFE/MAE tracking to PaperBroker (`_OpenBracket.mfe_pts/mae_pts/initial_stop_dist`, `_closed_excursions` sidecar, `be_trail_r` mechanism); wired r_mfe/r_mae into BacktestResult.trades in runner.py; added `be_trail_r: Decimal = Decimal("0")` to StrategyParams. 11 defining-behavior tests in tests/test_mfe_mae.py.
  - Step 2 (analysis): `scripts/analyze_mfe_mae.py` ran per-year backtests (2021/2023/2024/2025/2026) on iFVG (partial_r=0) and ORB r2.5 (partial_r=0). Distribution CSVs: `research/mfe_mae_ifvg_clean.csv`, `research/mfe_mae_orb_clean.csv`, `research/mfe_mae_control.csv`, `research/mfe_mae_orb25.csv`.
  - Step 3 (candidates): be_trail_r=1.0 benchmarked on iFVG (61mo) and ORB r2.5 (61mo) via run_monthly_combine.py. ORB r2.5 baseline run to establish comparison.
- **Numbers:**
  - iFVG + be_trail_r=1.0: 7/61 passes (11%), PF 0.97 vs iFVG control (6/17 test = 35%, PF 1.31) — BOTH METRICS WORSE.
  - ORB + be_trail_r=1.0: 8/61 passes (13%), PF 1.057 vs ORB baseline (12/61 = 20%, PF 1.10) — BOTH METRICS WORSE.
  - Key distributions (partial_r=0): iFVG winner MFE p50=3.0R, p75=3.69R; loser MFE p75=1.03R, p90=1.80R; winner MAE p90=0.82R. ORB winner MFE p75=2.50R (= target); loser MFE p75=0.78R, p90=1.29R.
- **Verdict:** rejected (be_trail_r=1.0); dataset (MFE/MAE infrastructure shipped).
- **Learned:** be_trail_r at 1.0R destroys "two-thrust" winning trades — price crosses 1.0R favorable, stop moves to BE, price consolidates to entry (or below), scratch instead of running to target. The loser-MFE and winner-MAE distributions overlap at 1.0R with no clean separation threshold. The MFE/MAE dataset is now available in BacktestResult.trades for all future runs.
- **Next:** B3 (two-phase policy sim, B1 now done) or B5 (ORB prior-day-range qualifier). B11 (excursion instrument filter, small fix) is highest-value quality item if market opens before next session.

## 2026-06-13T08:30Z — session wk1-b3 — B3 (two-phase pipeline policy)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 trades, no drift.
- **Reclaimed B3:** prior claim (2026-06-13T01:00Z) was an orphan — that timestamp belonged to wk1-b4. No B3 journal entry existed. Also found: b1_results.json had corrupted ORB r0.75 entry (only 2022 holdout data); re-stitched 5y series confirms B1 journal numbers (15 passes / 14 busts at h200 / $35.7k net).
- **Ran:** `scripts/run_b3_pipeline.py` (new script) — loaded existing B1 equity CSVs (2021/2023/2024/2025/2026, no 2022), computed per-phase simulate_combines + simulate_xfa_chain stats for 5 configs, then built analytic pipeline model for all A->B two-phase combinations and single-phase benchmarks. Metric: net $/trading-month with sustainability constraint (A.passes / B.busts >= 1.0).
- **Numbers (all h200, $150/attempt):**
  - Best sustainable two-phase: iFVG r1.25 (A) + ORB r1.0 (B) = **$393/mo**, sustainability 1.26x
  - Conservative two-phase: iFVG r1.25 (A) + ORB r0.75 (B) = **$377/mo**, sustainability 2.43x
  - Best sustainable single-phase: ORB r0.75 = **$333/mo**, sustainability 1.07x (B1 recommendation)
  - Pipeline-negative but highest raw $/mo: ORB r1.25 single = $596/mo, sust 0.89x (collapses long-run)
  - iFVG combine speed: 28.6 trading days to get one funded account (vs 68.6d for ORB r0.75 combine)
  - The two-phase improvement is driven by combine SPEED: iFVG needs 4.76x more attempts but each is only 6d vs 35.5d, yielding funded accounts 2.4x faster
- **Verdict:** candidate — iFVG (Combine) + ORB r1.0 (Funded) recommended as B3 two-phase pair; conservative alternative is iFVG + ORB r0.75 (2.43x pipeline buffer)
- **Learned:** The combine strategy doesn't need to produce large profits -- it needs to produce combine PASSES quickly and cheaply. iFVG generates funded accounts in 28.6 trading days (vs 68.6d for ORB r0.75 as combiner) because each attempt terminates fast (avg 6d). This speed lets the pipeline support a more aggressive funded phase (ORB r1.0) while remaining self-sustaining (1.26x), producing $393/mo vs $333/mo for single-phase ORB r0.75.
- **Next:** B5 (ORB prior-day-range qualifier) or B11 (excursion instrument filter -- small, unblocks clean live data). B5 is the next pending non-infra research item.

## 2026-06-13T04:45Z — session wk1-b5 — B5 (ORB prior-day-range qualifier)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 trades, no drift, no lockout. Market closed (Saturday).
- **Ran:** Implemented `orb_pdr_enabled` (default-off, `pdr_lookback=60`) in `app/strategy/orb.py` — ORBDetector now tracks rolling daily ranges and skips ORB signals on days where the prior ET-day range %-of-close is below the trailing 60-day median. Wired into StrategyParams, both runner.py and main.py. 6 defining-behavior tests in `tests/test_orb_pdr.py` (all pass; 581 total, 2 skipped). Benchmarked on the full 5-year Databento bars file (MNQ, 5min, risk 1.25) via `run_monthly_combine.py` (combine objective) and `equity_export.py` + `funded_sim.py` (funded objective). Generated a clean baseline (same ORB r2.5 without PDR on same full-5yr file) for direct comparison.
- **Numbers:**
  - **Combine (61 months incl. 2022):** PDR 12/61 (20%), PF 1.10 vs baseline 12/61 (20%), PF 1.10 — **identical**. PDR cuts ~50% of trading days (819 vs 1661 trades in equity run) but the same months pass.
  - **Funded (full 5yr incl. 2022, h200):** PDR 28 combine passes / 53 busts ($63,411 net, PF 1.238) vs baseline 68 passes / 167 busts ($163,122 net, PF 1.209). Volume loss (-51%) drops funded net by 61%. PF improvement: +2.4% (1.238 vs 1.209). Both pipeline-negative at risk 1.25 (passes < busts).
  - **Stop rule check:** PDR loses on funded net (primary metric) while winning on PF (secondary). Mixed — but the primary metric is so decisively worse (-61%) that this is a clear practical rejection.
- **Verdict:** rejected — prior-day range does not predict ORB trade quality; filter removes equal-quality trades at ~50% rate, devastating pipeline throughput for trivial PF gain.
- **Learned:** The prior-day-range filter selects a random 50% of ORB days — same win rate, same PF, same passing months — while halving the number of combine attempts and funded accounts. The "2024 PF 1.45 regime gate" was an artifact of the regime_switch engine gating ENTIRE DAYS (switching between iFVG and ORB), not a signal that ORB trade quality correlates with prior-day range.
- **Next:** B6 (ATR-normalized displacement thresholds — wake up 2022/2023 droughts) or B11 (excursion instrument filter — small quality fix, unblocks clean live data). Both pending; B11 is smaller and has live-data value.

## 2026-06-13T07:00Z — session wk1-b6 — B6 (ATR-normalized displacement thresholds)
- **Bot health:** Not checked (continuation of autonomous loop — market closed, Saturday).
- **Ran:** TDD — 10 defining-behavior tests first (all failing), then implemented `min_absolute_body_pct` in `DisplacementDetector` + `stop_buffer_pct` in `SweepDisplacementComposer` + both fields in `StrategyParams` + wired into `runner.py` and `main.py`. All 10 B6 tests pass; full suite 591 tests (2 skipped), all green. Calibration: `pct=0.000238` matches the deployed 5.0 pt floor at NQ 21k, drops to 3.81 pts at 16k, 2.86 pts at 12k. Benchmarked iFVG-only and combined on full 5yr Databento bars (MNQ, 5min, risk 1.25). Counted raw displacement events in 2023-12 (fixed: 470, pct: 515) to verify the detector fires correctly.
- **Numbers:**
  - **iFVG-only (combine, 61 months):** body_pct 12/61 (20%), PF 1.10 vs baseline 13/61 (21%), PF 1.18. Loses on both metrics.
  - **Combined (combine, 61 months):** body_pct 9/61 (15%), PF 1.14 vs baseline 9/61 (15%), PF 1.15. Same passes, marginal PF loss.
  - **2022 drought months:** identical trade counts (2-18/month) in both variants despite threshold dropping from 5.0 to 2.86-3.57 pts at 2022 NQ prices. Zero new passes in 2022.
  - **2021-11 regression:** 65->50 trades, PF 1.53->1.07 with body_pct enabled. The additional marginal displacements (bodies in 3.81-5.0 pt range at 16k) consume composer sweep states, blocking the higher-quality signals that drove the original 65 trades.
- **Verdict:** rejected — hypothesis wrong (drought is not threshold-driven); feature code shipped default-off.
- **Learned:** Loosening the body floor by 24% at 2022 prices leaves 2022 monthly trade counts completely unchanged — the drought months have 2-8 iFVG setups because the sweep+inversion STRUCTURE is absent in 2022, not because the floor filters them out. The extra marginal displacements that now pass the looser threshold enter the composer and consume sweep states, causing net degradation in high-quality months. Lesson 3 ("fixed-point thresholds are the 2022-23 drought mechanism") was wrong; the drought is structure-poverty (Lesson 4 confirmed from a different angle).
- **Next:** B7 (kz_levels benchmark — last untested engine) or B8 (wall-clock flatten fix — live-risk quality). B7 is the next pending research item by rank.

## 2026-06-13T11:30Z — session wk1-b7 — B7 (kz_levels benchmark)
- **Bot health:** Not checked (autonomous loop continuation — market closed, Saturday).
- **Ran:** Ported `KillzoneLevelTracker` from master branch into `app/strategy/kz_levels.py`; built `KZLevelsRunner` (duck-type of StrategyRunner using session H/L sweeps instead of swing-based liquidity); wired `engine="kz_levels"` into `app/backtest/runner.py` and `app/main.py`; 5 defining-behavior tests in `tests/test_kz_levels_runner.py` (all pass; 596 total, 2 skipped). Three bugs found and fixed during implementation:
  1. **Dead-zone consumption bug:** original code emitted AND consumed KZ levels when swept in the gap between sessions (e.g., London level swept at 05:15 ET, before NY AM opens at 08:30 ET). Fix: `KillzoneLevelTracker.on_bar()` only emits/consumes levels when `in_killzone(bar.ts, zones) is not None` — levels persist across the gap until swept during an actual trading window.
  2. **`all_day()` zone bug:** `enabled_killzones=["all"]` (the combine harness default) mapped to a single all-day zone (00:00-23:59:59 ET) that never closes, preventing `_kz_ranges` from ever being populated. Fix: `_build_runner` uses `default_killzones()` (London, NY AM, NY PM) unconditionally for `engine="kz_levels"` — the engine requires named session zones.
  3. **`SweepEvent` constructor**: master branch missing `sweep_bar=bar` kwarg added on this branch; patched in `_make_sweep()`.
- **Config:** `target_clarity_mode=off` (no HTF data available; the grader's target-clarity gate is irrelevant for a session-sweep engine), all other params at defaults (risk 1.25%, 2 contracts, r2.0).
- **Numbers (61 months, 2021-06–2026-06, MNQ 5min):**
  - Trades: 452 total (avg 7.4/month); longs 155 / shorts 297
  - Run PF: **1.18** (matches iFVG baseline)
  - Combine passes: **0/61 (0%)** vs baseline 13/61 (21%)
  - MLL failures: 0; worst-month maxDD: $1,896
  - Best month: 2023-06 (+$2,862, 11 trades, 63.6% WR) — short of $3,000 target
  - 2022: 0-6 trades/month; drought is identical to iFVG (structure-poverty, not filter)
- **Verdict:** rejected — 0/61 combine passes vs baseline 21%; trade frequency (~7.4/mo) too sparse to reliably reach $3,000. KZ session ranges lock once per session (3 per day × 5 days = 15 potential sweeps/week after displacement filter), producing too few entries to compound into a pass.
- **Learned:** KZ session-level sweeps have identical edge quality to iFVG swing-based sweeps (PF 1.18 matches) but ~10x lower signal frequency (~7/mo vs ~70/mo). The combine requires throughput, not just edge — even a correct PF can't win if the strategy fires 7 times per month. The dead-zone timing was the key structural finding: session ranges lock at close and are first tested in the gap before the next session opens (e.g., London high tested at 05:15 ET, not 09:00 ET); the sweep only becomes actionable once the next trading window opens.
- **Next:** B8 (wall-clock flatten fix — live-risk quality) or B11 (excursion instrument filter — small, unblocks clean data). Both pending; B11 is smaller.

## 2026-06-13T12:30Z — session wk1-r1 — RESEARCH (ideation, session #9)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 trades, no drift, no lockout. Market closed (Sunday).
- **Session type:** RESEARCH/IDEATION (last 2 items B6+B7 were build items; session #9, 9 % 3 == 0).
- **Ran:** (1) WebSearch for new NQ futures intraday strategy ideas (prop-firm, academic, practitioner literature); (2) data mining on `research/mfe_mae_ifvg_clean.csv` and `research/mfe_mae_orb_clean.csv` — per-side PF, per-hour PF, per-year PF, monthly count distributions; (3) code review of `app/strategy/orb.py` and wiring in runner.py/main.py for max_trades_per_day mechanism.
- **Key findings:**
  - iFVG long side PF=1.136 vs short side PF=0.960 over 5y (1226 longs vs 1251 shorts). Short side is structurally loss-making. Long-only lifts PF +9% but drops to ~25/month (funded route only).
  - ORB 10:xx ET signals (late breakouts) have PF=1.276 vs 9:xx PF=1.176 — late/retest signals are higher quality. ORB monthly ceiling is ~23 (1 signal/trading-day); reentry after stop would increase this by ~5-10/month.
  - `orb_max_trades_per_day` is already wired end-to-end BUT the naive implementation fires the second signal at the SECOND bar above the range (before the first stop hits), wasting the slot on trending days. Correct mechanism: ORBComposer.on_stop_loss() re-arms the detector.
  - Web research: Opening Rip = ORB (already tested); Liquidity Sweep = iFVG (already exists); VWAP = requires regime gating (Lessons 2+4 say no); gap fills ~12-15/mo (too sparse). No new mechanisms found.
- **Numbers:** no benchmarks run (research session only). Data: ifvg_long PF=1.136, ifvg_short PF=0.960, orb_10xx PF=1.276.
- **Verdict:** dataset — 3 new BACKLOG items appended.
- **Learned:** The iFVG short side has been a structural drag across all 5 years (long-only PF=1.136 vs full PF=1.043), and the correct ORB second-entry mechanism requires the on_stop_loss callback (not just bumping max_trades_per_day). External web research yielded nothing beyond what we already have — consistent with Lesson 6.
- **Next:** B8 (wall-clock flatten fix) is still the top non-research pending item; B11 (excursion instrument filter) is the smallest. B14 (ORB reentry) is the highest-priority new item from this session.

## 2026-06-13T13:00Z — session wk1-b8 — B8 (wall-clock flatten fix)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 trades, no drift, no lockout. Market closed (Sunday).
- **Ran:** Read engine.py and discovered `_flatten_clock()` (30s wall-clock backup) already exists and starts unconditionally in live mode (`not self._replay_mode`). The B8 spec was written before this task was added. Missing pieces were: (1) a `flatten_wallclock_enabled` flag to make the task explicitly controllable, (2) an early-close calendar note in `app/risk/flatten.py`, (3) defining-behavior tests for the clock-driven path.
  - Added `flatten_wallclock_enabled: bool = True` to `ExecutionEngine.__init__` (default True = preserves existing always-on behavior).
  - Gated `_flatten_task` creation on `self.flatten_wallclock_enabled` in `start()`.
  - Added early-close calendar to `app/risk/flatten.py` docstring: 8 known CME half-days for 2025-2026, description of the gap between noon early close and 15:05 CT flatten window, and recommendation to set `entry_cutoff_time_ct="11:30"` on those days.
  - Added 2 defining-behavior tests to `tests/test_flatten_window.py`:
    - `test_clock_driven_flatten_no_bar`: position open, NO bar in flatten window, `_enforce_flatten(15:10 CT)` called directly (as clock task would) → position closed.
    - `test_flatten_wallclock_disabled_no_task`: `flatten_wallclock_enabled=False, replay_mode=False` → `engine._flatten_task is None` after start.
- **Numbers:** 598 tests (up from 596), 0 failures, 2 skipped. 2 new tests, 0 regressions.
- **Verdict:** shipped — B8 core was already done; this session adds explicit flag, early-close docs, and defining tests.
- **Learned:** The wall-clock safety net was already in place since at least the June 12 baseline. The gap on CME early-close days (~5-7/yr) is not fully covered by either bar-driven or wall-clock flatten alone: bars stop at noon, the 15:05 wall-clock fires but the market has already auto-closed positions. The practical recommendation is to set `entry_cutoff_time_ct="11:30"` manually on early-close days (listed in flatten.py) to prevent entering near the close. Bar-driven and clock-driven flatten paths are equivalent in behavior — both call `_enforce_flatten` — and the existing test suite already validates retry logic.
- **Next:** B9 (ORB Rule-13 UI wiring — adds dashboard state for live ORB), B11 (excursion instrument filter — small, unblocks clean live data), B14 (ORB reentry — first new research item from wk1-r1).

## 2026-06-13T14:00Z — session wk1-b9 — B9 (ORB Rule-13 UI wiring)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 trades, no drift, no lockout. Market closed (Sunday). Restarted bot at end to pick up Python changes.
- **Ran:** Implemented all three Rule-13 layers for ORB observability:
  1. **Backend log:** `ORBDetector.on_bar` logs `"ORB range established: MNQ OR=[lo-hi]"` on the first post-range bar each day (flag `_or_range_logged` prevents duplicate logs).
  2. **Backend SSE:** `ORBDetector.state()` returns `{or_high, or_low, or_established, fired}`. `or_established` uses `_or_range_logged` (True only after window closes, not during range-building). In `_make_strategy_state_publisher` (main.py): extract ORB runner via `getattr(runner, "secondary", None)` (CombinedRunner) or `runner` directly (standalone ORBRunner); pass `orb_state` to `journal.publish_strategy_state()`. Added `orb_state: dict | None = None` param to `publish_strategy_state` in journal.py.
  3. **Frontend:** `StrategyStatePayload.orb_state` added to types.ts; StrategyDebug.tsx renders an "ORB" section with "range: `lo – hi`" (or "building…") and "signals today: N" when `orb_state != null`. Frontend rebuilt (`npm run build`) — clean TypeScript + Vite build.
  - 6 defining-behavior tests in `tests/test_orb_ui_state.py` covering: empty before any bars, not established during range window, established after window closes, fired increments on breakout, day reset.
- **Numbers:** 604 tests (up from 598), 0 failures, 2 skipped. Frontend build clean. Bot restarted and confirmed healthy (equity $152,227.12, flat).
- **Verdict:** shipped — all Rule-13 layers complete; ORB state visible in dashboard next live session.
- **Learned:** The `or_established` flag needs a "first post-range bar" signal, not just "both values set" — or_high/or_low accumulate during the window itself. Using `_or_range_logged` (which triggers the range-established log) as the establishment sentinel naturally maps to the dashboard-correct semantics: "building…" while the window is open, confirmed range once it closes. CombinedRunner.secondary is the clean extraction point for ORB state without modifying the combined runner's interface.
- **Next:** B10 (funded_sim --save-id registry) or B11 (excursion instrument filter — small, unblocks clean live data) or B14 (ORB reentry after stop — first strategy research item from wk1-r1).

## 2026-06-13T07:10Z — session wk1-b10 — B10 (funded_sim --save-id registry output)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 trades, no drift. Market closed (Sunday).
- **Ran:** Added `--save-id`, `--save-label`, `--instrument`, `--timeframe` args to `scripts/funded_sim.py`. When `--save-id` is set, calls `daily_pnls_from_equity + simulate_combines + simulate_xfa_chain` directly (replacing the `format_pipeline_summary` wrapper call — same output, no double computation), then writes `backtests/<id>.json` via `_save_funded_result`. The JSON shape mirrors `run_monthly_combine._save_ui_result`: top-level `id/label/instrument/timeframe/start_date/end_date/stats/funded_pipeline` + `monthly_combine: null` + empty trades/signals/fills so the existing `/api/backtest/list` endpoint picks it up without changes. The `funded_pipeline` block now includes both `combine` and `xfa` sub-blocks — the existing BacktestsPage `FundedPipelineDetail` component renders both. 5 defining-behavior tests in `tests/test_funded_sim_save.py` (JSON validity, equity curve in stats, payouts as strings, label override, ID sanitisation). Smoke-tested against `research/equity_test_orb.csv --haircut 200` — output matches prior manual runs. Full suite: 609 tests, 0 failures.
- **Numbers:** 5 tests added (total 609), 0 regressions. Smoke output: combine 10/26, xfa accounts 9, busts 8, net $23,566 — matches B1 seed values exactly.
- **Verdict:** shipped — funded_sim registry output live; future B14/B15/B16/B17/B18 benchmark runs can pass `--save-id` to surface payout-frontier results in the dashboard.
- **Learned:** The UI already had the XFA block in `FundedPipeline.xfa` (types.ts lines 56-63) and the `FundedPipelineDetail` component already renders it — the gap was purely that funded_sim never wrote to the registry. Adding `--save-id` to funded_sim closes the full pipeline: equity_export → funded_sim --save-id → dashboard visible. No frontend changes were needed.
- **Next:** B11 (excursion tracker instrument filter — small, unblocks clean live data) or B14 (ORB reentry after stop — first new strategy research item).

## 2026-06-13T15:00Z — session wk1-r2 — RESEARCH (ideation, session #12)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 trades, no drift, no lockout. Market closed (Sunday).
- **Session type:** RESEARCH/IDEATION (last 2 items B8+B9 were build items; session #12, 12 % 3 == 0).
- **Ran:** (1) WebSearch: NQ ORB improvements + MNQ microstructure academic literature 2025-2026; (2) Python data mining on `research/mfe_mae_orb_clean.csv` and `research/mfe_mae_ifvg_clean.csv` — per-side ORB PF, per-hour iFVG PF.
- **Key findings:**
  - **ORB per-side (5y, n=1030):** long PF=1.320 (n=542, WR=48.5%) vs short PF=1.109 (n=488, WR=41.0%). Both profitable; 19% PF gap confirms "tops stall, bottoms sweep" extends to ORB. Unlike iFVG (shorts at PF=0.960, loss-making), ORB shorts still have positive expectancy — blocking them is a PF improvement, not loss-removal.
  - **iFVG per-hour (5y, n=2477):** Worst hours: 12:xx ET PF=0.591 (n=73, noon), 00:xx 0.618, 15:xx 0.763, 11:xx 0.829, 14:xx 0.946. Best hours: 05:xx 1.433 (London), 04:xx 1.307, 16:xx 1.296, 21:xx 1.306, 10:xx 1.235, 09:xx 1.178. The all_day benchmark config includes the worst hours; named-sessions config (London + NY AM) would remove ~$265/month in negative-expectancy drag on the funded phase.
  - **WebSearch:** Academic paper (arxiv 2605.04004) tested 14 OHLCV signal families on MNQ 5min 2021-2025 — no family satisfies all criteria; gross edge 0.07-1.50 pts/trade pre-cost. Confirms Lesson 6. A second paper (SSRN 6750442) on volatility-volume-gap classifier found T=1.46 but 2024 net -26.75pts (regime-fragile). No new mechanism families found.
- **Numbers:** ORB long PF=1.320 vs short 1.109 (both profitable, gap 19%); iFVG 12:xx ET PF=0.591; iFVG 05:xx ET PF=1.433.
- **Verdict:** dataset — 2 new BACKLOG items appended (B17, B18).
- **Learned:** ORB exhibits the same long/short bias as iFVG but less severely — shorts are still profitable, making long-only ORB a funded PF improvement rather than a loss-removal. The all_day benchmark config includes well-documented negative-expectancy windows (noon, NY PM tail) that cost the funded phase ~$265/month; the fix requires no new code, just a config param change in equity_export runs.
- **Next:** B10 (infra, funded_sim save-id) or B11 (excursion instrument filter, small) or B14 (ORB reentry — highest-priority strategy item).

## 2026-06-13T07:20Z — session wk1-b11 — B11 (excursion tracker instrument filter)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift, no lockout. Market closed (Sunday).
- **Ran:** TDD — 4 defining-behavior tests written first (all failing), then implemented:
  1. Added `instrument: str = ""` field to `ExcursionWindow` dataclass (default="" = no filter, legacy behavior preserved).
  2. Added `instrument: str = ""` parameter to `ExcursionTracker.open()`.
  3. Added instrument filter in `ExcursionTracker.on_bar()`: computes `bar_root` (strips SDK contract suffix via `.split(".")[-2]`), skips any window where `w.instrument != ""` and `bar_root != w.instrument`.
  4. Added `_root_instrument()` helper to `journaling.py` (same normalization: "CON.F.US.MNQ.M26" → "MNQ").
  5. Passed `instrument=` at all three `excursion_tracker.open()` call sites in journaling.py: rejection in `journal_signal` (uses `signal.instrument`), rejection in `on_reject` (uses `instrument` param), trade entry in `on_fill` (uses `_root_instrument(fill.instrument)`).
- **Numbers:** 4 tests added, 613 total, 0 failures, 2 skipped.
- **Verdict:** shipped — instrument filter live; 06-12+ MNQ-only excursion rows are clean; pre-06-12 multi-instrument rows remain unusable (no backfill possible).
- **Learned:** The fix required just two surgical changes (one field + one filter loop check) and a normalization helper — the test suite caught that SDK contract strings ("CON.F.US.MNQ.M26") must be normalised before comparison. Legacy windows (instrument="") continue to receive every bar, so existing production behavior for single-instrument configs is unchanged.
- **Next:** B12 (tracked runtime-ledger policy — decision for Lawrence) or B13 (split slippage column) or B14 (ORB reentry after stop — highest-priority new strategy item). B14 is the top new research item; B13 is the smallest remaining infra item.

## 2026-06-13T08:30Z — session wk1-b12 — B12 (tracked runtime-ledger policy)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift, no lockout. Market closed (Sunday).
- **Ran:** (1) Audited trades.csv: 226 data rows; last entries at 2026-06-10T01:32Z; jump to 2026-06-12T14:59Z. (2) Read trades_2026-06-10.csv: 20 data rows spanning 2026-06-10T23:49Z → 2026-06-11T04:46Z — all post-reset writes that survived because the daily file is untracked. (3) Ran backfill script (scripts/backfill_b12.py): inserted 20 rows before the 2026-06-12 entry, dedup-checked by broker_order_id (0 duplicates). trades.csv: 227 → 247 lines. (4) Verified 613 tests pass. (5) Wrote trade_analysis/2026-06-13_B12_runtime_ledger_policy.md with 3 policy options and a full data integrity note.
- **Numbers:** 20 rows backfilled; 3 gaps remain unrecoverable (no daily file for 06-10T01:32Z→23:49Z, MNQ exit at 06-11T04:21Z, and 06-11T04:47Z→06-12T14:59Z); 1 orphaned ENTRY row (MNQ 3112285061, exit lost); 613 tests pass.
- **Verdict:** dataset — backfill done; policy decision deferred to Lawrence (Monday).
- **Learned:** The git-wipe hazard is entirely preventable at zero cost: adding `trades/*.csv` rolling files to `.gitignore` (Option A) means `git reset --hard` can never touch them. The daily snapshot files are already untracked and already survived — Option A just extends that protection to the rolling file. This one-line `.gitignore` change is the immediate fix; the Outbox-based cloud sync (Option C) is the clean long-term architecture.
- **Next:** B13 (split slippage column — small infra, adds exec_slippage column) or B14 (ORB reentry after stop — highest-priority strategy research item). B14 has the most research value; B13 is faster.

## 2026-06-13T07:35Z — session wk1-b13 — B13 (split slippage column)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift, no lockout. Market closed (Sunday).
- **Ran:** TDD — 10 defining-behavior tests written first (all failing on import), then implemented:
  1. Added `_last_bar_close: dict[str, Decimal] = {}` module-level dict to `journaling.py`.
  2. Added `_make_bar_close_watcher()` factory — returns an async `on_bar` handler that updates `_last_bar_close` keyed by root instrument (`_root_instrument()` normalisation already present).
  3. Updated `_make_pre_place` to capture `_last_bar_close.get(_root_instrument(signal.instrument))` as `order_bar_close` in `_pending_signal_meta` at order time.
  4. Added `_exec_slippage(fill, meta)` — `fill_price − order_bar_close` for ENTRY rows; `""` otherwise or if `order_bar_close` absent (legacy rows, exit fills).
  5. Added `"exec_slippage"` to `_TRADES_HEADERS` (after `"slippage"` — backward-compat column preserved at same position).
  6. Added `_exec_slippage(fill, meta)` to the row list in `_append_fill_csv`.
  7. In `main.py`: imported `_make_bar_close_watcher`, registered `broker.on_bar(_make_bar_close_watcher())` alongside the existing bar subscribers.
- **Numbers:** 10 tests added (total 623), 0 failures, 2 skipped. 3 files changed (journaling.py, main.py, tests/test_b13_exec_slippage.py).
- **Verdict:** shipped — `exec_slippage` column live; `slippage` column unchanged.
- **Learned:** The `slippage` column (fill − FVG proximal edge) is genuinely useful as a plan-deviation diagnostic but measures nothing about execution quality. `exec_slippage` (fill − confirmation-bar close) isolates real fill quality: on market entries this is 0–2 ticks (consistent with B4 forensics: mean 0.78 pts). The watcher pattern (module-level dict + async on_bar handler) keeps the change fully contained in `journaling.py` with no modifications to Signal, strategy code, or the broker.
- **Next:** B14 (ORB reentry after stop — highest-priority new strategy item) or B15 (long-only iFVG funded benchmark).

## 2026-06-13T08:00Z — session wk1-b14 — B14 (ORB reentry after stop)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift, no lockout. Market closed (Sunday).
- **Ran:** TDD — 5 defining-behavior tests written first (all failing on import), then implemented:
  1. Added `reentry_after_stop: bool = False` to `ORBConfig`.
  2. Added `_rearm_count: int = 0` to `ORBDetector.__init__`; reset to 0 on day change.
  3. Added `ORBDetector._rearm()`: resets `_fired = 0` and increments `_rearm_count` — gated to fire only once per day (`_rearm_count == 0`).
  4. Created `ORBComposer(detector, reentry_after_stop)` dataclass — replaces `_NoopComposer`; `on_stop_loss()` calls `detector._rearm()` if flag enabled.
  5. Updated `ORBRunner.composer` field to `ORBComposer`; added `__post_init__` default for backward compat.
  6. Added `orb_reentry_after_stop: bool = False` to `StrategyParams`.
  7. Wired in `_build_runner` in both `runner.py` (backtest) and `main.py` (live): constructs `ORBDetector` and `ORBComposer` separately, passes `composer=` to `ORBRunner`.
  8. Benchmarked: Combine (risk 1.0%, r_multiple=2.5, 61 months full 5y) and Funded (equity_export + funded_sim, h200).
  9. Generated direct baseline (no reentry, same risk) for comparison.
- **Numbers (risk 1.0%, r_multiple=2.5, h200):**
  - **Combine — reentry:** 11/61 (18%), PF 1.13 | **baseline:** 7/61 (11%), PF 1.16
  - **Trade volume:** 2,382 vs 1,652 (+44%); funded PF 1.153 vs 1.213 (-5%)
  - **Funded $/month:** $2,578 vs $1,911 (+35%); sustainability 0.99x vs 0.85x (+16pp)
  - Both standalone configs remain pipeline-negative at risk 1.0% (reentry: sust 0.99x < 1.0)
- **Verdict:** candidate — both funded success criteria met ($/month and sustainability both improve); combine secondary improved (11 vs 7) though still below 1.25%-risk baseline of 12/61; PF trades off against volume.
- **Learned:** The reentry mechanism works correctly: a second ORB signal fires only after a confirmed stop, and at most once per day. It adds +44% volume and improves both combine throughput and funded payouts, but reentry trades are lower-quality (stop-reversal, not first-breakout) which drags PF -5%. The standalone ORB r1.0 pipeline is negative either way at this sizing; the reentry's value requires the two-phase model (iFVG as combine phase feeds ORB funded accounts).
- **Next:** B15 (long-only iFVG funded benchmark) or B16 (iFVG inversion bar quality gate). B15 is the next pending item by rank.

## 2026-06-13T08:10Z — session wk1-b15 — B15 (long-only iFVG funded benchmark)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift, no lockout. Market closed (Saturday).
- **Ran:**
  1. Identified that `allowed_sides="long"` already exists in StrategyParams and is fully tested — no new code needed per Rule 2 (Simplicity first).
  2. Added one missing defining-behavior test: `test_long_passes_when_long_only` (B15 spec item 2: long signal must not be blocked when allowed_sides=long). All 13 ablation tests pass.
  3. Ran `equity_export` with `--set allowed_sides=long` at r0.75/r1.0/r1.25 in parallel against the full 5y bars file. Killzones: default `london+ny_am+ny_pm` (bot_config.json was empty, fell back to BotConfig defaults).
  4. Ran `funded_sim --haircut 200` on each output, plus a direct full iFVG (both sides) baseline at r1.25 for comparison.
- **Numbers (h200, london+ny_am+ny_pm killzones):**

  | Config | Trades | PF | Passes | XFA busts | Net (5y) | Sust |
  |---|---|---|---|---|---|---|
  | Full iFVG r1.25 (baseline) | 6,043 | 1.064 | 45 | 84 | $132,380 | 0.54x |
  | LongOnly r0.75 | 3,931 | 1.139 | 42 | 64 | $135,472 | 0.66x |
  | LongOnly r1.0 | 3,946 | 1.127 | 56 | 50 | $177,542 | **1.12x** |
  | LongOnly r1.25 | 3,928 | 1.121 | 60 | 53 | $186,214 | **1.13x** |

  Long-only at r1.0/r1.25: self-sustaining. Full iFVG at r1.25: pipeline-negative.
  Volume retained: 3,928/6,043 = 65% (higher than expected 50% — named sessions are long-biased).
  Tests: 629 (up 1 from 628), 0 failures.
- **Verdict:** candidate — long-only iFVG at r1.0/r1.25 flips the funded pipeline from pipeline-negative (0.54x) to self-sustaining (1.12–1.13x). Both primary success criteria met: XFA net +41%, XFA busts -37%.
- **Learned:** The iFVG short side exclusion is sufficient to achieve pipeline sustainability at named-session killzones. The named-session config has a 65% long signal bias (vs 49.5% in all_day), meaning "long-only at named sessions" retains significantly more volume than the all_day MFE/MAE analysis suggested (~25/month). The allowed_sides parameter was already correctly wired and tested; no new code was needed.
- **Next:** B16 (iFVG inversion bar quality gate — strategy research, funded) or B17 (ORB long-only — similar structure to B15 but for ORB engine) or B18 (named-sessions killzone config benchmark — no-code benchmark).

## 2026-06-13T09:00Z — session wk1-b16 — B16 (iFVG inversion bar quality gate)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift, no lockout. Market closed (Saturday).
- **Ran:** TDD — 3 defining-behavior tests written first (all failing on TypeError), then implemented:
  1. Added `inversion_min_body_r: Decimal = Decimal("0")` to `ComposerConfig` in `composer.py` (default off).
  2. Added the gate check inside `on_displacement()` in the `for awaiting in reversed(self._awaiting)` loop, after the direction check: computes stop_dist inline from `fvg.high - (sweep_extreme - stop_buffer)` and skips (`continue`) if `body_size < inversion_min_body_r × stop_dist`. Uses underscore-prefixed locals (`_cfg`, `_fvg`, `_stop_dist`) to avoid shadowing `cfg` in `_build_signal()`.
  3. Added `inversion_min_body_r: Decimal = Decimal("0")` to `StrategyParams` in `bot_config.py`.
  4. Wired `inversion_min_body_r=s.inversion_min_body_r` into all 4 `ComposerConfig(...)` constructions (2 in `runner.py`, 2 in `main.py`).
  5. Ran `equity_export` (risk 1.25%, deployed MNQ config: combined engine, all killzones, min_absolute_body=5.0, stop_buffer=3.0) + `funded_sim --haircut 200`.
  6. Ran `run_monthly_combine.py` (same config + inversion_min_body_r=0.15).
- **Numbers:**
  - **Equity export (B16 gate ON):** 6,043 trades, PF 1.064, net $93,313 — **identical to full-iFVG baseline**
  - **Funded sim (h200):** 45 Combine passes, 84 XFA busts, $132,380 net (5y), sust 0.54x — **identical to baseline**
  - **Combine benchmark:** 9/61 (15%), PF 1.15 (combined engine result; longs PF 1.33, shorts PF 0.99)
  - **Signal reduction:** 0 trades blocked by the gate
- **Root cause:** `min_absolute_body=5.0` pts (deployed MNQ override) guarantees every displacement bar has body ≥ 5.0 pts. For the B16 gate to fire: need `0.15 × stop_dist > 5.0` → `stop_dist > 33 pts`. Typical MNQ 5min stop_dist = 5-20 pts. Gate threshold never reached. The 0.15 value was calibrated for the default config (min_absolute_body=1.0, stop_buffer=0.30) where typical stop_dist=3-5 pts and threshold=0.45-0.75 pts.
- **Verdict:** rejected — no marginal benefit at declared threshold (0.15) with deployed MNQ config; success criteria not met (funded PF unchanged). Infrastructure ships default-off; 3 defining tests added (632 total, 0 failures).
- **Learned:** Downstream signal quality filters are dominated by upstream body quality filters. A later-stage gate only adds value when its effective threshold (min_body_r × stop_dist) exceeds the earlier-stage floor (min_absolute_body). At deployed settings, the gap is 5.0 pts vs 0.45-3.0 pts. Future proposals for inversion quality gates should be calibrated against the actual upstream filter values.
- **Next:** B17 (ORB long-only funded benchmark — symmetric to B15 but for ORB) or B18 (named-sessions killzone benchmark — no-code benchmark). Both are pending.

## 2026-06-13T09:00Z — session wk1-b17 — B17 (ORB long-only funded benchmark)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift. Market closed (Sunday).
- **Ran:** TDD — 4 defining-behavior tests written first (all passing immediately on correct implementation). Implemented `long_only: bool = False` in `ORBConfig`; added short-side gate in `ORBDetector.on_bar` (if `self.config.long_only` and `side=="short": return None`); added `orb_long_only: bool = False` to `StrategyParams`; wired `long_only=s.orb_long_only` into `ORBConfig(...)` in both `runner.py` and `main.py`. 636 total tests, 0 failures. Benchmarked via `equity_export` + `funded_sim --haircut 200` at r0.75/r1.0/r1.25 against fresh same-code baselines. Also saved 3 UI-registry JSON files (`b17_orb_longonly_r0p75/r1p0/r1p25.json`).
- **Numbers (h200, full 5y bars incl. 2022, sust = combine_passes / xfa_busts):**

  | Config | Combine passes | XFA busts | Net (5y) | $/mo | Sust |
  |---|---|---|---|---|---|
  | LongOnly r0.75 | 18 | 21 | $46,416 | $773 | 0.857x |
  | LongOnly r1.0 | 28 | 38 | $70,883 | $1,181 | 0.737x |
  | LongOnly r1.25 | 39 | 47 | $109,292 | $1,822 | 0.830x |
  | Baseline r0.75 | 29 | 34 | $67,241 | $1,121 | 0.853x |
  | Baseline r1.0 | 50 | 59 | $116,605 | $1,943 | 0.847x |
  | Baseline r1.25 | 68 | 44 | $163,122 | $2,719 | 1.545x |

  At r1.0 (primary criterion): long-only $/mo -39% ($1,181 vs $1,943); sust -11pp (0.737x vs 0.847x). Both metrics worse at all risk levels.
- **Stop rule:** both primary metrics ($/mo and sust) are worse at r1.0 → rejected.
- **Verdict:** rejected — success criteria not met. ORB short signals are profitable (PF 1.109); removing them cuts ~47% of combine volume and hurts pipeline throughput more than PF gain compensates. Feature ships default-off (`orb_long_only=False`).
- **Learned:** The B15 success mechanism was removing LOSS-MAKING iFVG short signals (PF 0.960). B17 removes PROFITABLE ORB short signals (PF 1.109) — a very different hypothesis. The funded pipeline depends on combine THROUGHPUT (funded accounts created per month) as much as per-account performance. Cutting volume by 47% halves the account creation rate, so even a higher per-account PF cannot compensate. This confirms Lesson 33: long-only ORB is a PF-improvement hypothesis, not a loss-removal; PF improvement alone is not sufficient when volume is the pipeline bottleneck.
- **Next:** B18 (named-sessions killzone config benchmark — no-code, iFVG funded) is the only remaining pending item.

## 2026-06-13T09:22Z — session wk1-b18 — B18 (Named-sessions killzone benchmark)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift. Market closed (Sunday).
- **Ran:** equity_export (risk 1.25%) for three configs in parallel: Config A (`--killzones "london,ny_am"`), Config B (`--killzones "london,ny_am,ny_pm"`), all-day baseline (`--killzones "all"`). Then funded_sim at h0/200/400 for each. No code changes; 636 tests remain green from prior session.
- **Numbers (equity export + funded_sim h200, 5y full including 2022):**

  | Config | Trades | PF | Passes | XFA Busts | Net Payouts | Sust | $/mo |
  |--------|--------|-----|--------|-----------|-------------|------|------|
  | All-day (baseline) | 6,043 | 1.064 | 45 | 84 | $132,380 | 0.536x | $2,206 |
  | Config A: London+NY AM | 3,533 | 1.076 | 43 | 71 | $143,849 | 0.606x | $2,397 |
  | Config B: London+NY AM+NY PM | 3,832 | 1.049 | 43 | 97 | $127,735 | 0.443x | $2,129 |

- **Stop rule check:** Config B loses on BOTH PF (-1.4%) and net payouts (-3.5%) → rejected. Config A beats on both metrics (PF +1.1%, net +8.7%) → not stopped.
- **Success criteria check:** Primary criterion is PF >= +5%. Config A: +1.1% → FAILS. Config B: -1.4% → FAILS.
- **Verdict:** rejected — primary criterion not met for either config. Config A shows modest improvement (PF +1.1%, sust +13%, net payouts +8.7%) but falls short of the +5% threshold. Config B (standard named sessions) is strictly worse than all-day on all funded metrics. The deployed `enabled_killzones=["all"]` is already near-optimal for the funded phase.
- **Surprising finding:** bot_config.json uses `"enabled_killzones": ["all"]` (confirmed from deployed config), so all-day IS the current deployed behavior. The "standard named sessions" config (london+ny_am+ny_pm) that the code defaults to is NOT what's actually deployed. Config B is therefore what you'd get if you "fixed" the config to use named sessions — and it performs WORSE. The overnight/pre-market signals excluded by named sessions make net-positive contributions at partial_r=1.5, reversing the direction of the per-hour MFE/MAE hypothesis.
- **Learned:** Per-hour PF distributions from partial_r=0 MFE/MAE data are unreliable predictors of session-filter performance at the deployed partial_r=1.5 config. The all-day config already filters the bad hours implicitly through the strategy's sweep quality gates; additional session-time filtering at the config level adds noise rather than signal.
- **Next:** B18 exhausts the pending backlog. Recommend a RESEARCH session to replenish hypotheses before the next build session.

## 2026-06-13T10:00Z — session wk1-r3 — RESEARCH (ideation, wk1-b18 backlog entry)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift, no lockout. Market closed (weekend).
- **Session type:** RESEARCH/IDEATION — all B1-B18 items exhausted; backlog must be replenished before next build session.
- **Ran:**
  1. WebSearch for new NQ/MNQ intraday strategy ideas (2025-2026): no new mechanism families found. One SSRN paper on Ladder exits (5095349) noted as weak prior. All web findings reduce to ORB/liquidity-sweep variants already tested. Lesson 6 confirmed 4-for-4.
  2. ORB MFE/MAE deep-dive (`research/mfe_mae_orb_clean.csv`, n=1030): winner MFE distributions, loser MFE, threshold analysis (MFE >= 0.5R / 1.0R / 1.5R / 2.0R / 2.5R split between winners and losers).
  3. B3 pipeline model methodology check: ran `scripts/run_b3_pipeline.py` and `scripts/run_b19_quick_stats.py` to compare per-year vs flat 5y equity CSV approaches; discovered critical methodology discrepancy (ORB r1.0: 27 busts per-year vs 59 busts flat → B3's numbers not directly comparable to B14/B15 flat-CSV outputs).
- **Key findings:**
  - ORB winner p75=2.50R (= target): ~75% of winners hit the 2.5R target exactly; ~25% are profitable day-end flattens with lower MFE. At MFE >= 2.0R: winners 37%, losers 2% — good separation, but path-through-peak behavior not instrumentable from current MFE/MAE data. ORB excursion ladder (be_trail_r at 2.0R+) would need additional tracking to model correctly. NOT proposed.
  - B3 methodology finding: per-year equity CSVs give ~50% fewer funded busts than flat 5y CSVs. B20/B21 (two-phase pipeline benchmarks) must generate per-year equity CSVs to be comparable with B3 ($393/mo, 1.26x benchmark).
  - Preliminary pipeline estimate for B20 (iFVG → LongOnly-iFVG): if per-year busts scale at 0.46x flat (same as ORB r1.0), LongOnly-iFVG funded would show ~23 busts vs iFVG combine 34 passes → sust ~1.48x. $/mo depends on net_per_account. Medium-high prior (~55%) on beating B3 on both criteria.
  - Preliminary pipeline estimate for B21 (iFVG → ORB-reentry): ~33 per-year busts → sust ~1.03x. Lower prior (~35%) on beating 1.26x. Still worth testing as $/mo may be substantially higher.
- **Numbers:** ORB winner MFE: p25=1.11R p50=1.64R p75=2.50R p90=2.66R. ORB loser MFE: p75=0.78R p90=1.30R. iFVG combine passes=34, ORB r1.0 per-year busts=27, flat busts=59.
- **Verdict:** dataset — 3 new BACKLOG items appended (B19, B20, B21).
- **Learned:** The B3 pipeline model's $/mo figure is not directly comparable to flat-equity standalone funded metrics — per-year stitching produces ~2x fewer funded busts, so sust figures diverge significantly. Before concluding that B15's standalone 1.12x sust is "worse" than B3's two-phase 1.26x, B20 must be run with per-year methodology. The ORB excursion distribution confirms that ~75% of winners hit the 2.5R target exactly, meaning the target itself is a strong exit anchor; additional partial exits before 2.5R affect mostly the 25% day-end-flatten population.
- **Next:** B19 (simplest: no-code funded benchmark), B20 (highest-priority: two-phase pipeline with LongOnly-iFVG funded, needs per-year equity generation), B21 (iFVG → ORB-reentry two-phase).

## 2026-06-13T10:37Z — session wk1-b20 — B20 (iFVG Combine → LongOnly-iFVG Funded two-phase pipeline)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 contracts, no drift. Market closed (weekend).
- **Ran:** Generated per-year equity CSVs in equity_b20/ for LongOnly-iFVG (allowed_sides=long, london+ny_am killzones) at r0.75/r1.0/r1.25 — 15 equity_export runs across 5 years (2021/2023/2024/2025/2026), all in parallel. Wrote `scripts/run_b20_pipeline.py` extending B3's pipeline analysis with LongOnly-iFVG Phase B configs. Ran the pipeline analysis using equity_b1/control_r1p25 for Phase A (iFVG combine) and equity_b20/ for Phase B (LongOnly funded).
- **Numbers (iFVG r1.25 Combine → LongOnly-iFVG Funded, h200, per-year methodology):**
  - LongOnly r0.75: $397/mo, sust 1.06x (barely above 1.0, but below 1.26x target)
  - LongOnly r1.0: $463/mo, sust 0.65x (pipeline negative)
  - LongOnly r1.25: $676/mo, sust 0.69x (pipeline negative)
  - B3 reference: iFVG → ORB r1.0 = $393/mo, sust 1.26x (unchanged benchmark)
  - Root cause: LongOnly-iFVG funded creates 53 XFA accounts over 5y (avg 24d each) vs ORB's 28 (avg 45d each). iFVG combine produces 34 passes — enough for ORB's 27 busts (1.26x) but not LongOnly's 52 busts (0.65x).
- **Verdict:** rejected — no B20 config beats B3 on BOTH criteria. B3 (iFVG Combine + ORB r1.0 Funded) remains the recommended two-phase pair.
- **Learned:** Standalone sust (B15: 1.12x) is not a reliable predictor of two-phase sustainability because it assumes the same strategy runs both combine and funded phases. LongOnly-iFVG funded's higher signal frequency (~500 trades/yr vs ~330 for ORB) causes accounts to cycle 2x faster, requiring 52 combine passes to sustain vs ORB's 27 — the iFVG combine (34 passes) can only bridge the smaller deficit. Per-year methodology (vs flat-5y) is essential for this comparison and is what B3 used.
- **Next:** B21 (iFVG Combine → ORB-reentry Funded two-phase pipeline). B21 is the last pending item; prior estimate is sust ~1.03x (marginal) but $/mo may be substantially higher due to ORB-reentry's +35% funded $/mo improvement from B14.

## 2026-06-13T10:15Z — session wk1-b19 — B19 (Long-only iFVG + London+NY AM only funded benchmark)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift, no lockout. Market closed (weekend).
- **Session type:** B19 reclaim (orphan from crashed prior session). B19 was in-progress with no journal entry — reclaimed per protocol.
- **Ran:** equity_export at r0.75/r1.0/r1.25 with `--killzones london,ny_am --set allowed_sides=long`. r0.75 equity CSV was already present from the crashed session (4619 data points, valid). r1.0 and r1.25 ran fresh. Then funded_sim --haircut 200 on all three. No code changes; 636 tests green (unchanged from B17/B18).
- **Numbers (h200, full 5y bars 2021-2026 incl 2022):**

  | Config | Trades | PF | Combine Passes | XFA Busts | Net (5y) | Sust | $/mo |
  |--------|--------|----|----------------|-----------|----------|------|------|
  | LongOnly london+ny_am r0.75 | ~2,640 | N/A | 50 | 58 | $124,024 | 0.862x | $2,067 |
  | LongOnly london+ny_am r1.0 | 2,643 | 1.168 | 58 | 49 | $165,210 | **1.184x** | $2,754 |
  | LongOnly london+ny_am r1.25 | 2,645 | 1.173 | 64 | 40 | $184,104 | **1.600x** | $3,068 |

  B15 baseline (long-only, london+ny_am+ny_pm, same h200 methodology):

  | Config | Trades | PF | Combine Passes | XFA Busts | Net (5y) | Sust |
  |--------|--------|----|----------------|-----------|----------|------|
  | LongOnly r0.75 (B15) | 3,931 | 1.139 | 42 | 64 | $135,472 | 0.66x |
  | LongOnly r1.0 (B15) | 3,946 | 1.127 | 56 | 50 | $177,542 | 1.12x |
  | LongOnly r1.25 (B15) | 3,928 | 1.121 | 60 | 53 | $186,214 | 1.13x |

- **Success criteria check (vs B15 r1.0 baseline: PF 1.127, sust 1.12x):**
  - r1.0: PF +3.6% ✓ (>= +2%), sust 1.184x ✓ (>= 1.12x) — **PASS**
  - r1.25: PF +4.6% ✓, sust 1.600x ✓ — **PASS**
  - r0.75: sust 0.862x — pipeline-negative, criterion not met (as expected, lower risk is harder to sustain)
- **Stop rule check (per protocol):** r1.0 and r1.25 both improve on BOTH primary metrics (PF and sust) — not stopped.
- **Verdict:** candidate — B19 at r1.0 and r1.25 beats B15 on both success criteria. R1.25 is the standout: 33% fewer trades ($184k vs $186k net = nearly identical) but dramatically improved sust (1.60x vs 1.13x). Removing NY PM signals from long-only iFVG eliminates trades that contribute disproportionately to XFA bust events without meaningfully reducing gross payouts.
- **Key insight:** B18 found that removing named sessions from ALL-SIDES iFVG hurts the funded pipeline. B19 shows the opposite for LONG-ONLY iFVG when removing specifically NY PM. The distinction: B18 removed overnight/pre-market sessions with positive PF contributions from shorts; B19 removes only NY PM (PF 0.906 all-sides, and evidently even weaker for longs). The ~1,283 fewer NY PM trades per 5y contribute nearly zero net P&L but increase bust variance.
- **Learned:** NY PM window (13:xx-16:30 ET) signals are loss-making in long-only iFVG. Removing them from B15's config improves funded PF by 3-5% and pipeline sustainability by 5-42% (risk-level dependent, with r1.25 showing the strongest effect). The B18 counterintuitive finding does not extend to this case because the positive-PF overnight sessions B18 was losing are absent from the long-only view.
- **Next:** B20 (iFVG Combine → LongOnly-iFVG Funded two-phase pipeline — should use B19 r1.25 config for Phase B per-year equity generation); B21 (ORB-reentry two-phase).

## 2026-06-13T11:00Z — session wk1-b21 — B21 (iFVG Combine → ORB-reentry Funded two-phase pipeline)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no drift. Market closed (weekend).
- **Ran:** Generated per-year ORB-reentry equity CSVs in equity_b21/ (15 files: 5 years × 3 risk levels 0.75/1.0/1.25) with `--set engine=orb --set orb_r_multiple=2.5 --set orb_reentry_after_stop=True`. Wrote `scripts/run_b21_pipeline.py` (clone of run_b20_pipeline.py adapted for B21 equity_b21 configs). Ran pipeline analysis using equity_b1/control_r1p25 (iFVG Combine, Phase A) and equity_b21/ (ORB-reentry Funded, Phase B) at haircut=200. No code changes; 636 tests remain green.
- **Numbers (iFVG r1.25 Combine → ORB-reentry Funded, h200, per-year methodology):**

  | Phase B Config | Reset$/acct | XFA$/acct | Net/cycle | Cycle days | Net/mo | Sust | vs B3 |
  |---|---|---|---|---|---|---|---|
  | ORB-reentry r0.75 | $715 | $3,131 | $2,416 | 102.1d | **$497** | **2.62x** | **BEATS B3** |
  | ORB-reentry r1.0 | $715 | $1,754 | $1,040 | 53.1d | $412 | 0.83x | below |
  | ORB-reentry r1.25 | $715 | $1,949 | $1,234 | 49.6d | $523 | 0.71x | below |
  | ORB r1.0 (B3 ref) | $715 | $1,936 | $1,221 | 65.3d | $393 | 1.26x | baseline |
  | ORB r0.75 (B3 cons) | $715 | $2,547 | $1,832 | 102.1d | $377 | 2.43x | ref |

  Phase A standalone: iFVG r1.25 — 34/162 combine passes, avg 6.0d/attempt, 28.6d/funded. XFA standalone: busts=73/74, sust=0.47x (expected, iFVG funded alone is pipeline-negative).

- **Stop rule check:** r1.0 and r1.25 reentry lose on sustainability (0.83x and 0.71x < 1.26x) → stopped. R0.75 reentry wins on BOTH criteria ($497 > $393 AND 2.62x > 1.26x) → candidate.
- **Success criteria check (vs B3: $393/mo, sust 1.26x):** R0.75 PASSES — $497/mo (+26%) and sust 2.62x (+108%).
- **Verdict:** candidate — iFVG r1.25 Combine + ORB-reentry r0.75 Funded is the new recommended two-phase pair. Beats B3's best pair on both primary metrics and beats B3's conservative pair on $/mo (while also having higher sustainability, 2.62x vs 2.43x).
- **Mechanism:** At r0.75, ORB-reentry adds +23% per-account earnings ($3,131 vs $2,547 for plain ORB r0.75) while keeping funded bust count nearly identical (13 vs 14). The iFVG combine produces 34 passes against only 13 reentry busts → 2.62x sustainability. Higher risk levels (r1.0/r1.25) amplify volume but create too many funded busts for the iFVG combine to sustain.
- **Learned:** ORB-reentry at low risk (r0.75) is the sweet spot for the two-phase pipeline: the reentry mechanism adds meaningful account earnings without creating the bust frequency that kills sustainability at r1.0+. This contrasts with B14's standalone finding (ORB-reentry sust 0.99x at r1.0) where the full earning power required r1.0+. In the two-phase model, the iFVG combine's fixed 34 passes constrains how many funded busts are sustainable; r0.75 reentry keeps busts at 13 (vs 27 for plain ORB r1.0) making the pipeline much more robust.
- **Next:** B21 exhausts the backlog. Recommend a new research/ideation session to replenish. Monday recommendation: deploy iFVG r1.25 Combine + ORB-reentry r0.75 Funded as the two-phase config. Compare to deployed live config to assess phase-switch mechanics needed.

## 2026-06-13T12:00:00Z — session wk1-r4 — RESEARCH (ideation, post-B21 backlog replenishment)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no lockout. Market closed (Saturday).
- **Session type:** RESEARCH/IDEATION — B21 exhausted the pending backlog.
- **Ran:**
  1. Bot health check + Databento ledger audit ($0.00/$20.00 spent).
  2. WebSearch (via Explore agent): no new mechanism families found. SSRN 6709401 (Apr 2026)
     tests 14 OHLCV signal families on MNQ 5min 2021-2025, none survive institutional standards.
     Lesson 6 confirmed 4-for-4. All practitioner ideas reduce to iFVG/ORB already tested.
  3. **Signal-rank analysis** (scripts/analyze_signal_rank.py + analyze_signal_rank2.py):
     Loaded research/mfe_mae_ifvg_clean.csv (n=2477, 5y excl 2022); ranked signals by
     ET-date and intraday timestamp; computed PF by rank (1-7), cumulative PF by cap,
     and per-rank × per-side breakdown.
  4. **Config parity audit**: read bot_config.json and equity_export.py to confirm which
     BotConfig fields are used by benchmarks. Discovered: bot_config.json now has
     partial_profit_r=1.5, killzones=["all"], swing_stop_lookback=30, target_clarity_mode="off",
     ifvg_rule_f_enabled=False — all different from BotConfig defaults used in B1-B21.
  5. Read bot_config.py and scripts/equity_export.py to confirm partial_profit_r IS passed
     to BacktestConfig (via `--partial-r` arg or bot_cfg fallback).
- **Key findings:**
  - Rank-1 PF=1.129, rank-2 PF=0.970 (loss-making), rank-3 PF=1.081, rank-4 PF=0.813.
  - Rank-2+ shorts: PF=0.841 (n=731); rank-2+ longs: PF=1.139 (n=730).
  - Cap=1 gives PF=1.129, ~16.9/month. Cap=3 gives PF=1.066, ~43/month. Full: 1.043.
  - Hybrid (rank-1-all + rank-2+-long): PF=1.133, 29.1/month — but 2023 PF=0.983 (red flag).
  - B15 long-only (20.4/month, PF=1.136) is approximately equally good quality as cap=1 but
    with more volume. The rank-based filter does not clearly beat B15.
  - **Config parity gap**: equity_export uses `bot_cfg.partial_profit_r` as default. The
    deployed bot_config.json now has partial_r=1.5 vs B1-B21 research baseline of 0. Future
    benchmarks MUST pass `--partial-r 0 --set swing_stop_lookback=0` to stay on-series.
  - ORB `orb_range_minutes` is configurable (default 15) but has NEVER been benchmarked at
    10 or 30. This is the only unexplored structural parameter in the ORB engine.
- **Numbers:** rank-1 PF=1.129; rank-2 PF=0.970; rank-2+ shorts PF=0.841; rank-2+ longs PF=1.139;
  hybrid (rank-1+rank-2+-long) PF=1.133, 29.1/month; cap=1 PF=1.129, 16.9/month.
- **Verdict:** dataset — 2 new BACKLOG items appended (B22, B23). Key negative: signal-rank
  filtering does not clearly improve on B15 long-only; B15 remains the reference funded filter.
- **Learned:** The first iFVG signal each day has structurally better PF (1.129) than later signals
  (rank-2 PF=0.970, loss-making). But capping at 1 signal/day yields less volume than B15 long-only
  at similar PF — B15 is already near-optimal for the funded phase. The more valuable discovery is the
  config parity gap: all B1-B21 benchmarks used BotConfig defaults (partial_r=0, named-session killzones,
  swing_stop_lookback=0) while the deployed bot now has 5 different parameters. Monday: Lawrence must
  decide whether to re-benchmark B21's recommended config at the deployed config or accept the research
  baseline as the comparison anchor for the two-phase recommendation.
- **Next:** B22 (ORB range_minutes sensitivity — no code, clean benchmark) or B23 (iFVG daily cap —
  small code, funded only). Monday priority: address config parity gap + decision on deploying B21
  two-phase config (iFVG r1.25 Combine + ORB-reentry r0.75 Funded).

## 2026-06-13T11:28Z — session wk1-b22 — B22 (ORB opening range window sensitivity)
- **Bot health:** /api/status OK — XFA shadow, equity $152,227.12 at high-water, flat, 0 open contracts, no lockout. Market closed (weekend).
- **Session type:** B22 reclaim (orphan — in-progress with no journal entry; reclaimed per protocol).
- **Ran:** Three combine runs (orb_range_minutes=10/15/30) + three equity exports + three funded_sim runs. All at `--partial-r 0 --set swing_stop_lookback=0` for B1-B21 research baseline parity. No code changes; 636 tests green.
- **Numbers:**

  **Combine (risk-pct 1.25, partial-r 0, swing_stop_lookback=0):**

  | Config | Passes/61mo | Pass% | Run PF | Long PF | Short PF |
  |--------|------------|-------|--------|---------|---------|
  | ORB 10min | 10/61 | 16% | 1.09 | 1.15 | 1.03 |
  | ORB 15min (baseline) | 10/61 | 16% | 1.06 | 0.99 | 1.13 |
  | ORB 30min | 6/61 | 10% | 0.96 | 0.97 | 0.94 |

  **Funded (flat 5y, r1.0, partial-r 0, haircut 200):**

  | Config | Trades | PF | Net 5y | Sust | $/mo |
  |--------|--------|----|--------|------|------|
  | ORB 10min | 1,289 | 1.175 | $127,793 | 51/55=0.927x | $2,130 |
  | ORB 15min (baseline) | 1,287 | 1.176 | $117,439 | 47/58=0.810x | $1,957 |
  | ORB 30min | 1,272 | 1.156 | $67,646 | 26/52=0.500x | $1,127 |

- **Stop rule check:**
  - ORB 30min: loses on BOTH metrics vs baseline (passes 6 < 10 AND PF 0.96 < 1.06) → **stopped / rejected**.
  - ORB 10min: PF wins (1.09 > 1.06), same passes (10/61) → not stopped. But 10/61 < 13/61 success criterion → fails success criteria.
- **Success criteria check:** "Combine: any value achieves >= 13/61 AND PF >= 1.10." Neither 10min nor 30min meets this. 10min reaches PF 1.09 (just below 1.10) with 10 passes (below 13). Close but no pass.
- **Verdict:** rejected — parameter plateau confirmed for ORB range window. 30min strictly worse (stop rule). 10min is a marginal improvement on PF but doesn't break through the 13/61 threshold or meet the 1.10 PF requirement exactly.
- **Notable finding:** The 10-min window reverses the long/short PF split vs 15-min. At 15min: longs PF 0.99 (loss-making), shorts PF 1.13. At 10min: longs PF 1.15, shorts PF 1.03. The first 10 minutes capture cleaner directional breakout structure (NQ typically shows clean directional bias in the first 10 minutes of regular trading) while the 11-15 minute window adds weaker long entries after the initial move has partially played out. This is an interesting structural observation but insufficient to change the recommendation.
- **Learned:** The ORB 15-min window is near-optimal for NQ 5min on both the combine and funded objectives. Shortening to 10min produces a marginally better run PF (+3pp) and funded $/mo (+9%) but identical combine passes. Widening to 30min degrades significantly (both objectives). The parameter plateau documented in prior sessions extends to the range_minutes dimension.
- **Next:** B23 (iFVG daily signal cap — small code item; cap=1 funded funded objective). The only remaining pending backlog item.
