# MGC / MES param tuning at 5min (and MGC 15min) — 2024 train (2026-06-10)

Companion to `2026-06-10_mnq5min_walkforward.md`. Same method: sweep
stop_buffer × min_absolute_body on 2024 only, grids scaled by each
instrument's median 5min bar range (MNQ 10.75, MES 2.00, MGC 1.40 pts —
MNQ's winning stop_buffer 3.0 ≈ 0.28× median range).

## Results (2024 train, stats net / PF)

| instrument | grid best | baseline | verdict |
|---|---|---|---|
| MNQ 5min | −$534, PF 0.99 (sb 3.0, mab 5.0) | −$2,499, PF 0.95 | tunable → passed frozen test (PF 1.13) |
| MGC 5min | −$5,032, PF 0.88 (sb 0.45, mab 1.0) | −$6,458, PF 0.84 | not salvageable |
| MES 5min | −$11,003, PF 0.73 (sb 0.30, mab 1.6) | −$11,820, PF 0.72 | not salvageable |
| MGC 15min | −$685, PF 0.96 (sb 0.30, mab 2.0) | −$721, PF 0.96 | already at optimum — no tuning gain |

No frozen 2025–26 tests were spent on MGC/MES: every train cell is negative
and the gradient is flat or adverse, so there is no candidate to validate.
MGC 15min's three best cells sit within $35 of each other at baseline
stop_buffer — its mild full-period edge (PF 1.03) is not parameter-starved,
and larger stop buffers actively hurt.

## Conclusion

The MNQ 5min result does not generalize: the same reversal rules have no
tunable edge on MGC or MES at 5min in 2024. Deploy decision follows:

- **Trade MNQ only at 5min** with `strategy_overrides: {"MNQ": {"stop_buffer":
  "3.0", "min_absolute_body": "5.0"}}` and `timeframes: ["5min"]`.
- MGC/MES stay off (or paper-only). MGC 15min would need per-instrument
  timeframes to deploy alongside and its edge is marginal (+$0.6k/2.5y).

Artifacts: `backtest_results/wf_train_sweep_{MGC,MES,MGC15}/`,
split bars `bars/bars_{MGC,MES}_{train_2024,test_2025_2026}.csv` (gitignored).
