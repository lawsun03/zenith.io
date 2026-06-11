# Monthly Combine Simulation — can one month pass the 50K Combine? (2026-06-11)

Goal: pass the $50K Combine (+$3,000 target) within a month, equity never
below $48,000 / trailing −$2,000. Method: `scripts/run_monthly_combine.py`
splits `bars/bars_MNQ_test_2025_2026.csv` into 17 calendar months
(2025-01 … 2026-05); each month runs as a fresh $50k account with risk
limits ON (`fifty_k_combine`: trailing MLL $2k → locks at $50k, DLL
$1k/day, DPL $1.5k/day, $500 soft buffer). Strategy = post-stale-FVG-fix
(d8dddbc) MNQ 5min with deployed overrides (stop_buffer 3.0,
min_absolute_body 5.0). PASS = equity touches $53,000 before any
trailing-floor breach.

Prerequisite fix shipped with this analysis: the backtest replay loop never
called `roll_trading_day`, so the first DLL hit dead-locked the rest of the
run. Now rolls at 5pm CT (`_trading_day_ct`); DLL/DPL lockouts clear daily,
MLL lockouts persist — matching Topstep.

## Results (17 months each, same params all months)

| variant | passed | pass % | worst min-equity | note |
|---|---|---|---|---|
| 0.25% risk (deployed) | 2 | 12% | 48,581 | |
| 0.25% + grade ≥ C | 0 | 0% | 48,781 | filter removes winners too |
| 0.5% | 3 | 18% | 48,484 | |
| 0.5% + grade ≥ C | 1 | 6% | 48,458 | |
| **0.75%** | **5** | **29%** | **48,413** | recommended |
| 1.0% | 5 | 29% | 48,220 | same passes, less margin |
| 1.0% no partials | 5 | 29% | 48,220 | partials ~neutral |
| 1.5% | 4 | 24% | 48,008 | $8 from MLL death |
| 2.0% | 4 | 24% | 48,144 | lockouts kill months early |
| 0.75% london+ny_am+ny_pm | 1 | 6% | 48,503 | 24h "all" is the edge |
| 0.75% ny_am+ny_pm | 0 | 0% | 48,284 | |

**Zero MLL failures in all 187 month-runs.** The DLL + soft-buffer + daily
rollover stack caps a losing month at ≈ −$1.0k to −$1.7k realized; the
floor constraint is comfortably satisfiable at ≤1.0% risk.

## Read

1. **Floor is not the binding constraint — the +$3k target is.** The
   strategy's edge (PF ~1.15 risk-scaled) averages well under $3k/month at
   safe sizing.
2. **Sizing is the only lever that worked.** Pass rate peaks at 29%
   (5/17 months) at 0.75–1.0% risk/trade and falls beyond — bigger size
   just reaches the daily lockouts in fewer trades. Grade floor ≥C and
   killzone restriction both reduced pass rates sharply.
3. **Passing months pass fast.** All 0.75% passes hit target by the 11th
   (02-06, 04-04, 07-03, 09-09, 05-11); at 1.0% all by the 5th trading
   days. The lockout asymmetry compounds a good first week. Corollary: a
   month that is down ~$1k by mid-month is very unlikely to pass — stopping
   and waiting for the next Combine reset preserves capital.
4. Expected time to pass at 29%/month ≈ 3–4 Combine attempts. Failed
   attempts never breach the account, so retries cost only the subscription
   reset + realized DD.

## Caveats

- Pass/breach is evaluated on the fill-granularity equity curve (the risk
  engine itself sees mark-to-market and locked out first in every case, but
  intratrade unrealized dips between fills are not separately audited).
- These 17 months are the same 2025–26 data used for the walk-forward
  frozen test — the sizing choice (0.75%) is now lightly fit to it. The
  param set itself (3.0/5.0) was tuned only on 2024.
- 29% is a per-month estimate from 17 samples — wide error bars
  (binomial 95% ≈ 13–53%).

## Recommendation (not applied)

`risk_per_trade_pct: 0.75` (from 0.25), keep partials 1.5R, keep
killzones "all", keep grader floor off. Apply only with explicit sign-off —
it triples per-trade risk on the live bot.
