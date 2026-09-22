# Strategy discovery loop — overview

A research system that discovers intraday futures strategies on NQ, ES and GC, validates them
against prop-account rules, and teaches the survivors to a human trader through replay drills.

**There is no live execution.** Nothing in this project places an order. The terminal output is
a trainer, not a bot.

## Pipeline

```
Databento bars (NQ/ES/GC, 2010-06-06 onward)
  → feature pass (Polars, no model)          nightly, deterministic
  → ranked anomaly table
  → Grok: regime labels                       batch backfill, classification only
  → Kimi K3: triage + variant enumeration
  → Astra: hypothesis with stated mechanism
  → Kimi K3: adversarial review (sees mechanism, NOT backtest)
  → Strategy IR (declarative JSON)
  → backtest, expanding-window walk-forward
  → gates 0–10
  → survivors BLENDED into an equal-weighted ensemble
  → human review (only point the holdout may be opened)
  → trainer: replay drills scored on rule adherence
```

The research ledger sits beside every stage and records everything tested, forever.

## Corpus and windows

| | |
| --- | --- |
| Data source | Databento `GLBX.MDP3` |
| Corpus | 2010-06-06 → present, minus holdout (~16 years) |
| Holdout | Most recent 18 months, sealed, filesystem-enforced |
| Walk-forward | Expanding window; fit strictly on the past |
| Disjoint robustness blocks | 2010–13, 2014–17, 2018–21, 2022–25 |
| Nested recency checks | 5-year and 10-year (not independent evidence — they nest) |

37 years of intraday history for these instruments does not exist: NQ launched in 1999, ES in
1997, and Databento's CME intraday coverage begins June 2010. Pooling across three instruments
plus a hard trial cap plus blending is what substitutes for the missing history.

## Instruments

Research runs on the full-size continuous series (NQ, ES, GC) for liquidity and data quality.
Execution sizes to micros (MNQ, MES, MGC) — a $2,000 trailing drawdown cannot absorb full-size
contracts at 150 trades a year. The sizing layer makes this translation; the rule never knows.

## Trial budget

**30–50 hypotheses per year**, enforced in the ledger as a hard cap, not a budget alert.

At 16 years of pooled data, the Sharpe cutoff needed to avoid accepting a fluke rises steeply
with trial count, while genuinely good rules carry a true Sharpe near 0.3. Testing hundreds of
rules makes the gate unpassable by anything real. Cost is not the constraint — roughly $1 per
hypothesis end to end. The constraint is that the data supports dozens of trials, not hundreds.

## Build order

Phases must be built in order. Phases 1 and 2 are the defences; they exist before the generator
that could abuse them.

| Phase | Builds | Done when |
| --- | --- | --- |
| 0a | Teardown | Risk module extracted and green; execution path deleted; `pre-research-refactor` tagged |
| 0 | Data spine | Continuous-contract rolls reproduce a known series; folds cut and frozen |
| 1 | Statistics kit + ledger | Existing strategies replay through the ledger reproducing known results; holdout unreadable from `research/` |
| 2 | Strategy IR | An existing hand-coded strategy expressed purely in IR backtests identically |
| 3 | Anomaly pass + Grok labels | Nightly job produces a ranked, labelled table for 3 instruments in <60s |
| 4 | Gate pipeline + ensemble builder | Known-bad rejected at the right gate; survivors blend rather than rank |
| 5 | Hypothesis loop | A full unattended cycle writes candidates and rejections to the ledger |
| 6 | Trainer | Drill a validated ensemble; fidelity score trends across sessions |
| 7 | Ledger chat agent | Ask what has been tested on GC, get an accurate answer citing rows |

## Specs

- `strategy-ir.schema.json` — the strategy IR, including the recognizability split
- `gates.md` — all gates, thresholds and rejection semantics
- `ledger.sql` — the append-only research ledger DDL
- `accounts/topstep-50k.json` — prop account rules for the combine simulator
- `cost-model.md` — round-turn costs including the human-latency penalty
- `folds.json` — frozen walk-forward folds, robustness blocks and holdout window (`research/data/folds.py` reads it; cut once by `scripts/freeze_research_folds.py`, never regenerated at runtime)
