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
