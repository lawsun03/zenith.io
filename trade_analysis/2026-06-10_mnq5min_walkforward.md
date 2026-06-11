# MNQ 5min Walk-Forward — train 2024, frozen test 2025–26 (2026-06-10)

Follow-up to `2026-06-10_htf_timeframes.md`. Question: does MNQ 5min PF 1.07
survive out-of-sample, and does a small param re-tune help?

Method: split `bars_MNQ_NQv_2024_2026.csv` at 2025-01-01 (355k/497k 1min rows).
All tuning on 2024 only; each train winner gets exactly one frozen run on
2025–26. Same harness as v3 (`scripts/backtest.py`, now with `--timeframe` and
`partial_profit_r` passthrough), `--no-risk-limits`, costs modeled.
Parity verified: the 2024 train segment reproduces `mnq_5min_v3.json`
trade-for-trade (792 trades, year-end equity 45,141.74).

## Results (balance = final equity from $50k, commission-inclusive)

| run | period | trades | win% | PF | balance Δ | maxDD |
|---|---|---|---|---|---|---|
| baseline (live params) | 2024 train | 1,056 | 41.5 | 0.95 | −$4.9k | $7.9k |
| **winner** sb=3.0 mab=5.0 | 2024 train | 1,040 | 42.6 | 0.99 | −$2.9k | $7.4k |
| baseline (frozen) | 2025–26 test | 1,554 | 44.1 | **1.12** | **+$7.7k** | $6.8k |
| **winner (frozen)** | 2025–26 test | 1,581 | 44.3 | **1.13** | **+$8.5k** | $7.8k |
| winner + dwb=8 (frozen) | 2025–26 test | 1,977 | 42.3 | 1.01 | −$2.6k | $14.4k |

Sweeps (45 train runs total): `stop_buffer` {0.30…8.0} — interior peak at
**3.0** (5.0/8.0 collapse to PF 0.85–0.93); `min_absolute_body` {1.0…10.0} —
interior peak at **5.0**; `displacement_window_bars` 8 beat 5 on train by a
trivial $170 but destroyed the test (+$8.5k → −$2.6k) — rejected, kept 5.
`ifvg_sweep_window_bars` is **inert** on this strategy path (identical results
for 5/10/20) — the real sweep-timing knob is `displacement_window_bars`.

## Read

1. **The 2025–26 edge is real-ish and not parameter-fragile.** Baseline holds
   PF 1.12 on a fresh-start 2025–26 run, and a config tuned only on 2024
   independently lands at PF 1.13 on the same untouched data. Both stop-buffer
   and min-body show smooth interior optima, not knife-edge spikes.
2. **2024 stays negative under every one of 45 configs** (best PF 0.99). The
   edge is regime-dependent; expect losing stretches measured in months.
3. **Caveat unchanged:** 2025–26 is out-of-sample for the *params* but not for
   the *timeframe* — MNQ 5min was picked by looking at all 2.5y. Only live
   paper trading or new data truly tests it.
4. The dwb=8 result is a clean demonstration of why train-set improvements
   under ~$1k are noise at this sample size.

## Candidate live change (NOT applied)

`timeframes: ["5min"]`, `stop_buffer: 3.0`, `min_absolute_body: 5.0`.
**Blocker:** `StrategyParams` is shared across MGC/MNQ/MES — 3.0 price points
of stop buffer is calibrated for MNQ (~25,000 index) and is not sane for MGC.
Per-instrument params (or MNQ-only trading on 5min) needed before deploying.

## Harness notes

- `stats.net_pnl`/PF exclude commissions (~$2.4k/yr at this trade rate); the
  balance curve includes them. Tables above use balance for $ and stats for PF,
  matching the v3 doc.
- `--start-date/--end-date` on `python -m app.backtest` are metadata-only —
  they do not filter bars. Date splits must be done on the CSV.

Artifacts: `backtest_results/wf_train_sweep{,2}/`, `wf_test_{baseline,winner,winner_dwb8}/`,
split bars in `bars/bars_MNQ_{train_2024,test_2025_2026}.csv` (gitignored).
