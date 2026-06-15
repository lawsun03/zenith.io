# Structural Ablation Campaign — T1–T5 vs the 35% plateau (2026-06-11)

Source spec: `ABLATION_TEST_SPEC.md` (Downloads) — 5 structural ablations
(remove/replace, not tune) motivated by Revelio Trading's TJR backtests,
run against the frontier monthly-Combine config. Harness:
`scripts/run_monthly_combine.py`, post-flatten ruleset (flatten 15:05 CT,
consistency rule, DLL/DPL, $500 soft buffer, 5pm CT rollover), MNQ 5min,
deployed overrides (stop 3.0 / body 5.0 / r3.5), risk 1.25%, partials
1.5R (except T4). Control = that config unchanged. One change per run;
every run reported on both periods. All runs saved to the UI registry
(`ab_control_*`, `ab_t1_*` … `ab_t5_*`).

Harness upgrade shipped with this campaign: per-month PF column, run-level
PF / worst-month maxDD, and long/short PF split (`by_side` on
BacktestStats). Control reruns reproduced the round-5 anchors exactly
(6/17, 4/12) — the reporting change is read-only.

## Results

| run | test 17mo | 2024 12mo | trades/mo* | run PF (t / 24) | worst-mo maxDD | MLL fails | long PF (t / 24) | short PF (t / 24) | verdict |
|---|---|---|---|---|---|---|---|---|---|
| **Control** | **6/17 (35%)** | **4/12 (33%)** | 25 / 24 | 1.31 / 1.37 | 4793 / 4608 | 0 | 1.39 / 1.44 | 1.25 / 1.28 | baseline |
| T1 long-only | 5/17 (29%) | 2/12 (17%) | 12 / 14 | 1.26 / 1.22 | 3705 / 3403 | 0 | 1.26 / 1.22 | — | **drop** |
| T2 4h-bias ON | 4/17 (24%) | 3/12 (25%) | 17 / 16 | 1.26 / 1.20 | 4747 / 4667 | **1** | 1.47 / 1.24 | 1.10 / 1.14 | **drop** |
| T3 NY-only | 1/17 (6%) | 3/12 (25%) | 8 / 10 | 1.02 / 1.16 | 2293 / 2926 | 0 | 0.92 / 1.94 | 1.12 / 0.61 | **drop** |
| T4 trail-1R | 1/17 (6%) | 2/12 (17%) | 12 / 12 | 1.25 / 1.23 | 3621 / 5924 | 0 | 0.76 / 1.17 | 1.91 / 1.30 | **drop** |
| T5 drop-iFVG | **0/17 (0%)** | 1/12 (8%) | 7 / 16 | **0.55** / 1.30 | 1963 / 5140 | **1** | 0.69 / 0.90 | 0.44 / 2.00 | **drop** |

\* trades = exit-fill counts; T4 (no partials) under-counts ~×0.5 relative
to partial-enabled runs.

**Every ablation lost on pass rate in both periods; none rescued itself on
PF.** No stacking was run (stop rule: zero winners to stack). The 35%
plateau is not hiding a removable component — the frontier config survives
every structural simplification the spec proposed.

## Per-test reads

**T1 long-only (spec prior: STRONGEST) — the premise failed verification.**
The spec's "shorts PF ~0.72, −$10k/17mo" was reconstruction-level and
flagged for equity-truth verification. On the equity path at frontier
sizing, shorts are PROFITABLE in both periods (PF 1.25 test, 1.28 2024).
Long-only halves trade volume (the binding constraint in a $3k/month
race), drops 3 pass months on 2024, and its maxDD improvement (~$1.1k)
buys nothing — zero MLL failures either way. Revelio's long-only-on-
indices result does not transfer to this entry chain.

**T2 4h-bias ON (spec: ambiguous, NASDAQ exception possible) — resolved:
keep it OFF.** Bias-on blocks enough volume to lose 2 pass months on test
and introduced the campaign's only test-period MLL failure (2025-08).
Control already runs bias-off, so nothing changes; the ambiguity is now
data-backed, both standalone and (moot) for stacking.

**T3 NY-only (spec: moderate) — strongly negative, prior confirmed.**
1/17 on test. The round-1 finding holds under the live ruleset: the
24h "all" window is itself the edge — pass months are built on volume
that ny_am+ny_pm cannot supply (8 trades/mo vs 25).

**T4 trail-1R (spec: best-of-12 exits on directional assets) — fails on
Topstep's rules, not on edge.** Run PF 1.25 is respectable and short-side
PF 1.91 on test is the campaign's best cell, but pass rate collapses to
1/17: (a) the spec's own warned failure mode fired — 2025-07 netted
+$13.2k yet can NEVER pass because one +$9,886 runner day permanently
violates the 50% consistency cap; (b) without a TP, winners aren't banked
before DPL lockouts/flatten, so the lockout asymmetry that powers passing
months disappears. Slippage was ON (1 tick adverse, all exits are stops).
A trail exit might still have a place in a FUNDED account (no consistency
rule on payouts above the minimum days) — parked, not pursued.

**T5 drop-iFVG (spec: "got better every time we removed something") —
catastrophic, the iFVG requirement IS the edge filter.** 0/17, run PF 0.55
on test. Mechanism: in displacement-only mode the FIRST displacement bar
after a sweep fires the signal — entry chases an already-extended move at
its close, and the well-timed iFVG-retrace entries that control would have
taken later never occur (the sweep queue is consumed). Trade count did
not even rise in most months (7/mo vs 25 on test); 2024's PF 1.30 is one
freak 110-trade January carrying an otherwise losing year (plus an MLL
fail in October). Revelio's "simpler won everywhere" finding inverts here.

## Campaign verdict

The plateau stands at ~35% test / ~33% 2024 per attempt, and it is now
ablation-proof as well as parameter-proof (round 5). Five structural
removals, ~120 month-simulations: every one degraded pass rate; two
produced the campaign's only MLL failures. The components the spec
proposed deleting (shorts, all-hours sessions, the TP, the iFVG gate)
are each load-bearing.

Paths beyond 35% remain what they were, now with stronger evidence:
1. **Second, uncorrelated signal engine for drought months** (ORB build —
   next up). Droughts (3–5 trades/mo) were untouched by every ablation.
2. Excursion-driven exit ladder from MFE/MAE data (monitoring backlog).
3. Live slippage reduction (protects the simulated edge).

## Caveats

- Code modes added for the campaign (`allowed_sides`, `confirmation`,
  `trail_1r`) are research switches: tested, committed, default-off.
  None are wired into the UI/StrategyDebug (Rule 13 deferred — they are
  not deployed strategy features; wire them up only if one is ever adopted).
- T4's exit-fill counts aren't directly comparable to partial-enabled runs.
- Same small-n warnings as rounds 5–6 (17+12 months; binomial 95% on
  6/17 ≈ 17–59%).
