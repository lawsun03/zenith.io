# Project invariants — strategy discovery loop

These rules are not negotiable and not subject to your judgement during implementation.
If a task seems to require breaking one, stop and say so rather than working around it.

## Architecture

1. **Strategies are data, never code.** A strategy is a JSON document conforming to
   `docs/research-loop/strategy-ir.schema.json`. The engine interprets it. No model-generated
   Python is ever executed in the research loop. If a strategy cannot be expressed in the IR,
   the IR is too weak — say so; do not add an escape hatch that evaluates arbitrary expressions.

2. **Rules are pooled across instruments, never fitted per-instrument.** One generic rule is
   fitted across NQ, ES and GC together. Per-instrument parameter sets are over-fitting
   (Carver calls it the narrative fallacy). The IR has no per-instrument parameter block.

3. **Blend, do not select.** Gates are a filter that removes garbage. What gets promoted is an
   equal-weighted ensemble of *everything that survives*, not the top-ranked candidate.
   Evidence: 90 variations on gold futures scored 0.07 Sharpe when selecting the annual best,
   0.20 picking at random, 0.33 blending all of them. Any code path that sorts candidates by
   performance and takes the top N is a bug.

4. **Account size never enters rule design.** Trading rules and stops depend only on market
   volatility (ATR, realised sigma). The account balance, the daily loss limit and the trailing
   drawdown enter at position sizing and at the combine simulator — nowhere else. Three layers,
   strictly separated: rule → volatility target → position sizing.

5. **Regime labels are classification only.** The `regime_label` field exists to include or
   exclude sessions and to group them for validation. It must never appear in the IR predicate
   vocabulary. A label derived from knowing how a day turned out is lookahead bias.

6. **The holdout is off-limits.** The most recent 18 months live at a path the research process
   cannot read, enforced by filesystem permissions. No code in `research/` may reference it.
   It is opened only by an explicit human action, and every touch is logged.

## Statistics

7. **The ledger is append-only.** Nothing deletes from `hypotheses`. The row count is the trial
   count that the deflated-Sharpe and cutoff calculations depend on. A sweep of 90 variants
   counts as 90 trials, not 1.

8. **Fitting uses expanding or rolling windows only.** Never fit and test on the same period.
   The expanding-window scheme is the default: fit only on data strictly before the test period.

9. **Both macro-release variants must pass.** Every candidate is backtested with release
   sessions included and excluded. The candidate's score is the **worse** of the two. Never
   report or select on whichever number flatters.

10. **A high Sharpe is a red flag, not a result.** Observed Sharpe above ~1.5 triggers
    investigation (negative skew, lookahead, an over-generous cost model), not promotion.
    Realistic after-cost target for this system is 0.8–1.4.

## Engineering

11. **`Decimal` for all money math.** Never float. This has already caused one 10× tick-value bug.

12. **Pure functions for predicates.** Every IR predicate evaluates as a pure function of a bar
    window. No hidden state, no I/O.

13. **Property-based tests (Hypothesis) for every invariant that can be stated as one** —
    trailing drawdown monotonicity, ledger append-only behaviour, IR hash stability under
    key reordering.

14. **Tests for statistical functions are written by hand, not generated.** Deflated Sharpe,
    CSCV/PBO and the cutoff table are verified against known inputs with values computed
    independently. A generated test agrees with the implementation's bugs.

## Repository boundaries

**This repository is research and backtesting only. The live execution path has been deleted.**

The live bot is stopped and is not coming back here. The following were removed in the teardown
(phase 0a) and must never be reintroduced, added as a dependency, or recreated under another
name:

- ProjectX / TopstepX API clients, and the `project-x-py` dependency
- Tradovate or any other broker client
- The execution engine, order router, and fill reconciler
- Paper and live broker abstractions
- Order, fill and position-lifecycle types used for live trading
- The outbox sync to DigitalOcean, and the Windows Task Scheduler deployment
- Any credential handling for a broker account

If a task appears to need one of these, it is a misunderstanding of the project — say so.
A backtest produces a simulated trade sequence; it never places an order.

**Deliberately kept:**

- `risk/` — the trailing MLL, daily loss limit and buffer logic, extracted into a standalone
  package during teardown. It is a pure scoring library now, with no broker coupling. The
  combine simulator (gate 8) depends on it. Its property-based drawdown tests are preserved.
- The backtest harness, Polars pipeline and walk-forward machinery
- The strategy detectors (killzone, liquidity tracker, displacement) — these become IR
  predicate implementations in phase 2
- The FastAPI / React dashboard, which becomes the research and trainer UI

The pre-teardown state is recoverable from git history at the tag `pre-research-refactor`.
Recover from that tag rather than rewriting execution code from memory.

- Scratch, caches and downloaded data never go in a user-facing directory.
