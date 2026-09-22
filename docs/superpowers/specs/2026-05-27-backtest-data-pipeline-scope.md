# Backtest Data Pipeline — Scope / Decomposition

**Date:** 2026-05-27
**Status:** Scoping (parent doc; sub-projects get their own spec → plan cycles)
**Purpose:** Make parameter optimization trustworthy. Today the backtester cannot reproduce live behavior, so any sweep result is noise. This document decomposes the fix into independent sub-projects and recommends a build order that de-risks data spend.

---

## The problem (two distinct gaps)

1. **Data depth.** TopstepX stores only ~43 trading days of 1-min bars. Walk-forward needs 60+ to run meaningful test windows against the $3,000 combine target (see `project_better_test_data` memory: the 2026-05-21 walk-forward returned 0% pass across 108 configs purely because windows were too short).

2. **Forming-bar fidelity (the real blocker).** Even with unlimited 1-min bars, the backtest diverges from live because most live entries fire on the **intra-minute forming-bar path** (`ExecutionEngine._poll_forming_bars` → `StrategyRunner.try_signal_from_forming`), which `replay_mode=True` disables and `PaperBroker` cannot model. Measured 2026-05-27: completed-1min-bar replay generates **~0.3 signals/day vs ~5/day live** (the live runner produced 14 completed-bar signals over 59k bars / 43 days). More 1-min depth does not close this gap.

Both must be solved for a sweep to mean anything. Gap 2 is more fundamental and is the engineering unlock.

---

## Sub-projects

### A — Daily archiving (cheap, independent, start now)
Append `scripts/fetch_bars.py` (1-min) **and** the live intrabar recorder's 5s samples to master CSVs on a daily Windows Task Scheduler job. Zero cost; accumulates both 1-min depth and sub-minute history going forward.
- Existing assets: `fetch_bars.py` already paginates 1-min history; `TopstepXBroker` already writes `intrabar_<instr>.csv` (5s forming-bar snapshots) live — but it is a single rolling file that must be rotated/archived rather than overwritten.
- Effort: ~half a day. No design risk.
- Dependency: none.

### B — Forming-bar-aware replay (the unlock; own design cycle)
Teach the backtest to replay sub-minute samples as forming bars and drive `try_signal_from_forming`, and teach `PaperBroker` to model **intra-minute bracket fills** (stop/target touched within a minute, resolved in time order). Closes the fidelity gap and is also what makes the partials/BE backtest faithful.
- Touches: `app/sim/paper.py` (intrabar fill model), `app/backtest/runner.py` (forming-bar feed + dedup mirroring the engine's `_forming_signal_fired`).
- Effort: meatiest; real design risk. **Gets its own brainstorm → spec → plan.**
- Dependency: a sub-minute data format (can prototype against the existing `intrabar_MGC.csv` before any data spend).

### C — Historical sub-minute data source (cost decision)
For depth *now* instead of waiting on A to accumulate:
- **Databento** — ~$40-100/mo, years of MGC/MES/MNQ, best quality, immediate.
- **Alpaca** — free tier ~2yr 1-min; must verify micro-futures coverage; sub-minute availability unclear.
- **Rithmic** — TopstepX's underlying provider; full history but a separate data subscription.
- Effort: integration + reformat to the bars/intrabar schema. Gated on a cost decision.
- Dependency: decide only after B is proven.

---

## Recommended build order (de-risks spend)

1. **A now** — free, runs in the background accumulating depth + sub-minute history immediately.
2. **B prototype** — validate the forming-bar replay against the **existing one session** of 5s data (`intrabar_MGC.csv`): it must reproduce the ~4 live signals the 2026-05-26/27 log shows. This proves fidelity with **zero data spend**.
3. **C** — choose and integrate a historical source only after B works.
4. **Re-run the faithful sweep** (already wired in `scripts/backtest.py` / `walkforward.py`) with B + depth → the first trustworthy parameter optimization.

Key insight: step 2 validates the entire approach against ground truth before paying for data, and B is a prerequisite for trusting both the parameter sweep and the partials/BE backtest.

---

## Out of scope (for now)
- Parameter optimization itself — blocked until B + depth exist. Fitting params to the current ~4-signal sessions is overfitting (the 2026-05-26 vs 05-27 sessions already showed opposite regime conclusions).
- Live trading changes — this is backtest-infrastructure only.

## Next step
Brainstorm sub-project **B** (forming-bar-aware replay) as its own design cycle.
