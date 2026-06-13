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
