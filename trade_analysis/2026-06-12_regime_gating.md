# Regime-gated engine routing — four probes, verdict: can't be timed daily (2026-06-12)

Lawrence's hypothesis: route ONE account between engines by regime — ORB
for small ranges, iFVG for large — so only one engine spends the shared
loss budget (the mechanism that killed the simultaneous combined engine).
Built as `engine="regime_switch"` (`app/strategy/regime_switch.py`):
parameter-free daily gate, previous ET-day range %-of-close vs trailing
60-day median, decided at the day roll; both sub-runners stay bar-warm;
exits honored from either side. Plus the chop_breakout `vwap_cross` gate
finally benchmarked. UI: `rs_test/rs_2024/rs_5y`, `rs_inv_*`, `chop_vwx_*`.

## Results

| variant | test 17mo | 2024 | 5y /61 | PF (t/24) |
|---|---|---|---|---|
| iFVG solo (baseline) | **6 (35%)** | 4 | **13** | 1.31 / 1.37 |
| ORB solo | 4 | 4 | 12 | 1.21 / 1.44 |
| switch: ORB-on-small (proposed) | 6 | 4 | 12 | 1.28 / 0.98 |
| switch: ORB-on-large (inverted) | 4 | 4 | — | 1.25 / **1.45** |
| naive union (= two accounts) | 9 | 7 | — | — |
| chop_breakout w/ vwap_cross gate | **0** (3 trades) | **0** (0 trades) | — | 0.38 / – |

Zero MLL fails in every variant.

## Reads

1. **Proposed polarity = an iFVG shadow with leakage.** Identical pass
   months to iFVG solo in both periods; ORB received just enough of
   iFVG's good days to bleed PF (2024-03: +$4.2k vs iFVG's +$14k; 2024
   PF 0.98). It converted ZERO of ORB's drought months — in every one,
   the gate kept iFVG on the field.
2. **The polarity of the real correlation is INVERTED, and it's
   ORB-sided only:** ORB-on-large-days reproduces ORB's pass months
   with better economics (2024 PF 1.45, the best one-account figure;
   2024-04 +$8.0k, 2024-07 +$9.1k) and even minted novel hybrid months
   (2025-01 PASS that NEITHER solo passed; 2026-02 +$12.6k, PF 4.34).
   ORB's edge does live on range-expansion days. But iFVG's months are
   NOT small-range months — they're structure-rich months — so the
   inverted gate forfeits most of iFVG's passes (4/17 vs 6/17).
3. **iFVG droughts ≠ low daily range.** 2025-04 is the proof: enormous
   tariff-era daily ranges, yet iFVG starved (4 trades) while ORB made
   +$7k. Range size and sweep→displacement→iFVG structure-richness are
   different axes; a daily range gate cannot see the second one.
4. **VWAP-cross chop identification (Lawrence's question): no.** The
   ≥6-crosses-per-20-bars-held-12 definition is ~28× stricter than the
   compression percentile (March 2024 diagnostic: 0.7% of bars in-chop,
   7 entries averaging ~5 bars, vs 18.5% / 47 entries) — the engine
   generated 3 trades in 29 months, zero passes. It identifies chop so
   rarely that nothing downstream can happen.

## Verdict

Day-level regime gating cannot harvest the engines' month-level
complementarity in one account: every routing variant lands at or below
the better solo engine. The complementarity is real but only separable
ex-post (monthly) — **the two-account plan (iFVG-MNQ + ORB-MNQ, 16/29
union) remains the only mechanism that captures it.** Parked nuggets:
the inverted gate's 2024 PF 1.45 and its two novel hybrid months suggest
"ORB qualified by prior-day range expansion" could improve ORB's OWN
account someday (fewer, better days) — a future ORB refinement, not an
account-merging path.
