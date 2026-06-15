# B94 — Event-calendar router: stack base + CPI straddle + gold-FOMC straddle

**Date:** 2026-06-15 · **Session:** wk7-b94 (Opus) · **Objective:** funded pipeline
**Script:** `scripts/run_b94_pipeline.py` · **Tests:** `tests/test_b94_router.py` (5, green)
**Verdict:** CPI straddle CONFIRMED additive (ship); **gold-FOMC straddle REJECTED from the router** (not robustly pipeline-additive).

## Question
Lawrence asked to stack the three confirmed pieces and toggle each by day type:
base engine (daily) + CPI straddle (NQ, on CPI days) + gold-FOMC straddle (MGC, on
FOMC days). Does the full stack raise funded $/mo and/or sustainability vs base-only
*without raising busts*? Attribute per piece.

## Method
Two-phase funded pipeline (same model as B42/B90/B93, `pipeline_economics`):
Phase A = deployed iFVG+ORB combine (`equity_b42/deployed_r1p0`), Phase B = ORB-reentry
r0.75 funded (`equity_b21/orb_reentry_r0p75`). 2022 frozen (excluded everywhere).

**Day-type routing = per-date P&L injection.** CPI and FOMC dates are disjoint (only 1
CPI+FOMC same day in 5y), so "toggle by day type" reduces to: add each straddle's
per-event P&L only on its own event dates. Base runs every day.

**Multi-instrument equity combination (the spec's hard part) is done in R-space.** Every
straddle outcome is normalised to an R-multiple, then sized to the SAME fixed-fractional
dollar risk **R_FIXED = $375 (0.75% of $50k)**. So an MGC gold straddle and an NQ straddle
both risk $375/event — the combined funded account's dollar equity is the base curve plus
each event-day's `R × $375`, regardless of which instrument produced the R. This is the
correct way to merge MNQ + MGC P&L into one account stream.

**Oracles** (both 1s-resolved, managed from the second after trigger, 2022 excluded):
- CPI (NQ): fixed 60-tick offset, tp_r=3 → **46/47 filled, 67% win, PF 5.99, 5/5 yrs+, +1.68R/event** (B85/B93).
- gold-FOMC (MGC): 0.5×ATR offset, tp_r=3 → **31/32 filled, 45% win, PF 1.99, 4/5 yrs+, +0.66R/event** (B91, reproduced exactly).

**Self-checks (calibration):** BASE @h200/gap0 = $549/mo 3.23x (= B42 exact); +CPI @h200/gap0
= $853/mo 2.80x +2 busts (= B93 OV-3R exact). The harness is trustworthy.

## Results (haircut $200)

### gap=0 (optimistic, parity with B42/B93 references)
| Variant | $/mo | sust | B-busts | Δ$/mo | Δbusts |
|---|---|---|---|---|---|
| BASE | 549 | 3.23x | 13 | — | — |
| **+CPI (additive)** | **853** | 2.80x | 15 | **+304** | +2 |
| +FOMC (additive) | 603 | 2.21x | 19 | +55 | **+6** |
| FULL STACK (Phase B) | 905 | 2.33x | 18 | +356 | +5 |
| CPI switch (Phase A, cf B90a) | 557 | 3.23x | 13 | +8 | 0 |
| STACK (both phases) | 938 | 2.56x | 18 | +389 | +5 |

### gap=24 (realistic, post-B86)
| Variant | $/mo | sust | B-busts | Δ$/mo | Δbusts |
|---|---|---|---|---|---|
| BASE | 387 | 3.50x | 12 | — | — |
| **+CPI (additive)** | **715** | 3.50x | 12 | **+327** | **0** |
| +FOMC (additive) | 506 | 3.23x | 13 | +119 | +1 |
| FULL STACK (Phase B) | 749 | 3.23x | 13 | +362 | +1 |
| STACK (both phases) | 771 | 3.54x | 13 | +384 | +1 |

### Haircut sensitivity for the gold-FOMC piece (+FOMC additive, Δ$/mo vs BASE)
| | h0 | h200 | h400 |
|---|---|---|---|
| gap=0 | +60 | +55 | +31 |
| gap=24 | **−40** | +119 | **−25** |

## Per-piece attribution / verdict

1. **CPI straddle — CONFIRMED additive; the value driver.** +$304/mo (h200/gap0, +55%);
   at the realistic gap=24 it is **bust-neutral** (Δbusts=0 in 4 of 6 sensitivity cells,
   −5 at h400/gap0) while adding +$327/mo. It improves $/mo in *every* cell of the
   sensitivity grid. This re-confirms B93. **Ship it (additive).**

2. **Gold-FOMC straddle — REJECTED from the router.** It adds only marginal $/mo
   (+$31…+$119) and **raises busts in every cell** (+1 to +6; sust 3.23x→2.21x at h200/gap0).
   Worse, its sign is **not robust**: at h0/gap24 and h400/gap24 it is *negative* $/mo
   (−$40, −$25). A router piece must help across the haircut/gap grid; gold-FOMC does not.
   Fails the spec's primary bar ("raises $/mo and/or sust *without raising busts*").

3. **Why gold-FOMC fails the pipeline despite being a real edge.** It is a confirmed
   standalone edge (PF 1.99, B91), but PF 1.99 ≪ CPI's 5.99, win rate 45% vs 67%, and it
   *lost* in 2026 (−3.21R). With ~31 event-days/5y it injects roughly as many event-days as
   CPI but at ~⅓ the expectancy, so its losers tip near-MLL funded accounts into busts
   faster than its thin expectancy replenishes them. **A low-PF event overlay can be net-
   positive standalone yet pipeline-negative**, because the pipeline's binding constraint is
   bust frequency, not raw expectancy.

4. **Where you apply the straddle matters enormously (additive ≫ mode-switch).** Adding the
   CPI straddle to the high-throughput funded phase (+$304/mo) dominates *replacing* the
   combine phase's CPI days (B90a-style switch: +$8/mo here). Confirms B84/B93: suppressing
   the base on event days starves volume; additive just adds a high-PF trade. The "both
   phases" stack (straddle in whichever phase the account is in) is the most complete model
   and posts the highest $/mo ($938), but inherits gold-FOMC's bust increase — so it does
   not change the verdict.

5. **CPI+FOMC same-day is a 1-in-5-years event** — the stacking interaction is negligible.

## Recommendation (Lawrence, Monday)
The "three-strategy router" collapses to **base + CPI straddle (additive)**. The recommended
router is exactly B93's already-confirmed candidate: base engine daily + CPI straddle on CPI
days (NQ, tp_r=3) → **$853/mo h200/gap0, $715/mo gap24, bust-neutral**.

**Drop the gold-FOMC straddle from the funded router.** Because the eval rejects it, **no new
live engine or multi-instrument router code is justified** (Rule 2 — simplicity): the only
active piece (CPI straddle) already ships as B92's `news_straddle` engine (live, default-off).
The base engine already runs daily. Nothing new needs to be enabled to realise the
recommended stack. Gold-FOMC may still be worth trading in a *non-pipeline-constrained*
context (a dedicated account, or once surplus combine throughput exists), but not as an
addition to this MLL-constrained funded pipeline.

## Notes / honesty
- Sizing: R_FIXED=$375 (0.75%) matches B93 so +CPI reproduces B93 exactly. B90a used $500/R,
  so the "CPI switch (Phase A)" row here is the within-B94 analog, not a numeric match to
  B90a's published $565/mo.
- No 2022 confirmatory run: the net recommendation (base + CPI) is unchanged from B93's
  already-confirmed candidate, and the *new* element (gold-FOMC) is being rejected — rejected
  pieces don't get a holdout run.
- Edge-localization not run on gold-FOMC: at 31 events/5y, every sub-slice (side/year) is
  below the n≥30 robust-bucket guardrail; the rejection is on pipeline-bust grounds, not an
  aggregate near-miss, so the diagnostic is not the right tool here.
