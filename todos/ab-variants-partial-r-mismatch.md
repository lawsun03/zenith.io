---
key: ab-variants-partial-r-mismatch
title: A/B variant partial_profit_r doesn't match live MGC config (1.5R)
status: open
priority: medium
category: backtesting
---

The TJR preset variants use 1.0R for partial_profit_r (A, D, E) or 0 (B, C, F).
MGC live config uses 1.5R. This means the A/B test baselines don't reflect the
actual live setup — variant A in particular is labelled "baseline" but uses 1.0R
partials, not 1.5R.

Options:
1. Add a 7th "Live Config" control variant with partial_r=1.5 so there's a true like-for-like.
2. Override partial_profit_r in the relevant TJR variants to 1.5R before running.
3. Accept the mismatch and note it in the results interpretation (TJR variants are
   testing their own hypothesis, not our exact config).

Needs a decision before interpreting A/B results as a comparison against live behavior.
