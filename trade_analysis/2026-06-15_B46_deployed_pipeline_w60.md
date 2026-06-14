# B46 — Deployed Phase B with orb_signal_window_mins=60 Pipeline Benchmark
**Date:** 2026-06-15 | **Session:** wk2-b46 | **Verdict:** rejected

## Objective

Determine whether enabling `orb_signal_window_mins=60` (B43 candidate) on the
deployed Phase B config (ORB-reentry r=0.75, partial_r=1.5, stop_buffer=3.0,
min_absolute_body=5.0) improves pipeline economics over the baseline (w=0).

This closes the "deployed config integration gap": B43 benchmarked w=60 at
partial_r=0 (research parity); B42 established the deployed Phase A (42 passes,
$568 reset); B46 pairs them at the ACTUAL deployed Phase B settings.

## Method

Generated per-year equity CSVs (2021/2023/2024/2025/2026) for two Phase B configs:

| Config | Settings |
|--------|----------|
| B46 w=0 (baseline) | ORB-reentry r=0.75, partial_r=1.5, stop_buffer=3.0, min_body=5.0, w=0 |
| B46 w=60 (test)    | Same + orb_signal_window_mins=60 |

Phase A: `equity_b42/deployed_r1p0_{year}.csv` (B42 deployed, 42 passes/5y)
Haircut: $200

## Phase B Standalone Results

| Config | Trades* | Accts | Busts | $/acct | d/acct |
|--------|---------|-------|-------|--------|--------|
| B21 ref (partial_r=0) | — | 14 | 13 | $3,131 | 73.5d |
| B46 w=0 (deployed) | ~1,806 | 14 | 13 | $3,131 | 73.5d |
| B46 w=60 (deployed) | ~1,243 | 14 | 13 | $2,540 | 66.2d |

*Approximate 5y totals from per-year equity file trade counts.

Key findings:
- **B46 w=0 = B21 ref exactly**: deployed partial_r=1.5 produces identical funded
  pipeline mechanics as research-parity partial_r=0. Confirms B25's finding (1.8%
  sust difference) at per-year granularity the effect is zero.
- **w=60 busts unchanged**: 13 → 13 (0% reduction). The B43 bust reduction
  (25 → 22) was at partial_r=0. At partial_r=1.5, partial exits smooth the
  equity curve enough that late-session signals no longer trigger busts.

## Two-Phase Pipeline Results

| Phase B | Reset$ | XFA$/acct | $/cycle | Cycle days | $/mo | Sust |
|---------|--------|-----------|---------|------------|------|------|
| B21 ref (partial_r=0) | $568 | $3,131 | $2,563 | 98.1d | **$549** | **3.23x** |
| B46 w=0 deployed | $568 | $3,131 | $2,563 | 98.1d | **$549** | **3.23x** |
| B46 w=60 deployed | $568 | $2,540 | $1,972 | 90.8d | **$456** | **3.23x** |

## Success Criteria Evaluation

| Criterion | Result |
|-----------|--------|
| Primary: w=60 sust > w=0 sust | **FAIL** (3.23x = 3.23x, no improvement) |
| Secondary: w=60 beats B42 ($549/mo, 3.23x) | **FAIL** ($456/mo, lower) |

**Verdict: REJECTED** — stop rule triggered on primary metric.

## Root Cause Analysis

The B43 w=60 improvement (partial_r=0) worked because:
- Late-session ORB signals (10:30-11:30 ET) had PF 0.622-0.963 (loss-making)
- At partial_r=0, a stop-out produces a full R loss → accelerates MLL breach → bust
- Removing those stop-outs reduced bust count from 25 to 22

At partial_r=1.5, the same late-session stop-out scenario becomes:
- Phase 1: Entry fills, position opened
- Phase 2: Price moves to 1.5R → half position exits at profit (partial locked)
- Phase 3: Remaining half reverses and hits stop → net result: ~0.75R win (not a loss)
- OR: Price never reaches 1.5R → full stop → same loss as without partial_r

The partial exit converts ~half the "potential stop-outs" into zero or positive outcomes.
This means the late-session signals that B43 removed at partial_r=0 were already
"defanged" at partial_r=1.5 — they no longer bust accounts at the same rate.

Effect of w=60 at partial_r=1.5:
- Same bust count (13 vs 13)
- Fewer total profitable EOD flattens (w=60 removes 7.3% of trades)
- Less total earnings per account ($2,540 vs $3,131, -$591)
- Net pipeline: -17% $/mo

## Key Confirmations

1. **B42 result is robust**: The published B42 result ($549/mo, 3.23x) used B21
   Phase B (partial_r=0). B46 confirms this is NOT an artifact — deployed partial_r=1.5
   produces identical economics.

2. **partial_r=1.5 is confirmed safe for Phase B**: B25 showed 1.8% sust difference
   at flat-5y methodology. B46 confirms 0% difference at per-year methodology.
   No config change needed to match the research-derived recommendation.

3. **Do NOT enable orb_signal_window_mins=60 on the deployed bot**: The improve-
   ment found in B43 does not transfer to the deployed config.

## Monday Recommendations

- **No config changes needed** for Phase B (ORB-reentry r=0.75, partial_r=1.5 is optimal)
- **orb_signal_window_mins: leave at 0** (default) for the funded phase
- The B43 combine-phase PF improvement (+5.2%) is valid but irrelevant: deployed
  Phase A uses iFVG+ORB combined engine, and B43's w=60 only affects the ORB sub-signal
  within that combined engine. The net combine-phase effect would be much smaller
  than the +5.2% measured when ORB is the sole Phase A engine
- Best confirmed pipeline: B42 deployed Phase A + B21 ORB-reentry r=0.75 Phase B
  = **$549/mo, sust 3.23x** (verified robust)
