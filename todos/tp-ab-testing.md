title: TP A/B test — single fixed-R vs 3-tier partials vs runner-weighted
priority: high
status: in-progress
category: backtest
created: 2026-06-08
---
TJR findings: 3-tier+BE cuts winners into fractions while losers stay full size. Mathematically weaker than it feels. A/B test on MGC/MES/MNQ via Databento multi-instrument × variant matrix. Score = total realized R, not hit rate. Variants: single fixed-R target, current 3-tier+BE, runner-weighted (take 1/3 at 1R, let 2/3 run to 3R+).

Databento backtest matrix is partially built. Need to finalize variant scoring and run full date range.
