# Combined iFVG+ORB engine — one account can't hold two engines (2026-06-11)

Build: `app/strategy/combined.py` `CombinedRunner` (both sub-runners see
every bar; one Signal out; iFVG wins same-bar collisions; engine-facing
attrs delegate to the iFVG primary → zero ExecutionEngine/server.py
changes). `StrategyParams.engine="combined"`; reversal semantics
unchanged and source-agnostic (an ORB signal opposite an open iFVG
position flattens-and-reverses). Tests: `tests/test_combined_runner.py`.
Benchmark configs: frontier iFVG (1.25% r3.5) + ORB winner (9:30+15min
r2.5). All runs in UI registry (`comb_*`).

## Results (test 17mo + 2024 12mo = 29 month-attempts)

| variant | test | 2024 | total | MLL busts | run PF (t/24) |
|---|---|---|---|---|---|
| iFVG solo (control) | 6/17 | 4/12 | **10/29 (34%)** | 0 | 1.31 / 1.37 |
| ORB solo r2.5 | 4/17 | 4/12 | 8/29 (28%) | 0 | 1.21 / 1.44 |
| naive union (separate accts) | 9/17 | 7/12 | **16/29 (55%)** | 0 | — |
| combined, 1.25%, buffer on | 4/17 | 3/12 | 7/29 (24%) | 0 | 1.32 / 1.38 |
| combined, 1.25%, buffer OFF | 6/17 | 4/12 | 10/29 (34%) | **7 (24%)** | 1.29 / 1.32 |
| combined, 0.75%, buffer on | 5/17 | 4/12 | 9/29 (31%) | 0 | 1.23 / 1.26 |

## Mechanism: the shared loss budget confiscates the complementarity

1. **Buffer on, 1.25%:** iFVG's first 2–4 losing trades in a drought month
   latch the $500 soft-buffer freeze (mark-to-market) before ORB's
   breakout fires. Every ORB drought conversion (2025-04/10, 2026-04,
   2024-02/04/07) shows 2–7 trades in combined mode — the month is dead
   before ORB trades. Interference runs both ways: iFVG's solo passes
   2024-05, 2025-09, 2025-12 degrade to no-pass (ORB losses eat DLL/DPL
   budget; cross-engine reversal churn).
2. **Buffer off:** recovery returns (passes back to 10/29) but two
   engines without the governor produce 7 real account busts in 29
   months — vs zero for iFVG solo with the buffer. Strictly worse.
3. **0.75%:** freeze latches later (gains 2024-02, an ORB conversion)
   but the slower grind loses other months. Net 9/29, still below
   control.

Three levers probed, all directions lose to control. This is not a
tuning problem: **one account = one loss budget = the engines are
coupled exactly where their value is being uncorrelated.**

## Recommendation: two engines, two accounts

The "naive union" row is not hypothetical — it is literally what two
parallel Combines deliver (Topstep allows multiple active Combines; the
bot already runs multiple instruments/processes — see
combined-strategy-bot plan for the SignalR per-login eviction
constraint).

- P(≥1 pass per month) ≈ 55% measured (16/29); engines anti-correlated
  by construction (clock-driven vs structure-driven).
- ≈80% over a 2-month horizon per the monthly hazard — the first
  configuration to reach Lawrence's 80–90% region.
- Cost: 2 × $49/month subscriptions; zero bust risk observed for either
  solo engine with the buffer on (~480 month-runs).
- Operationally: keep the current iFVG shadow Combine; add a second
  (shadow) Combine running `engine="orb"` solo with
  `orb_r_multiple=2.5`. ORB's live wiring needs the Rule-13 UI work
  (OR range / fired state in strategy_state + StrategyDebug) before a
  real run.

`engine="combined"` stays in the codebase (tested, default-off): it is
the right shape for a FUNDED account later, where one account is a hard
constraint and the freeze dynamics differ (payout cadence, no $3k race).

## Caveats

- Same small-n as all campaigns (29 months); union per-month rate has
  wide error bars (binomial 95% on 16/29 ≈ 36–74%).
- Separate accounts double subscription burn during losing streaks.
- The 0.75%/buffer-off probes were single-lever; a joint sweep could in
  principle find a better one-account compromise, but all three levers
  pointed the same direction — structural, not tunable (consistent with
  rounds 5–6 and the ablation campaign).
