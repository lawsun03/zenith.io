# B18: Named-Sessions Killzone Config Benchmark (iFVG, Funded)

**Date:** 2026-06-13  
**Session:** wk1-b18  
**Objective:** Funded / pipeline  
**Method:** equity_export + funded_sim at h0/200/400; iFVG r1.25; no code changes

---

## Hypothesis

The deployed iFVG funded-phase equity curve is dragged down by negative-expectancy
signal windows (noon PF=0.591, 11:xx PF=0.829, NY PM 14-15:xx PF=0.763-0.946)
identified in the all-day (`enabled_killzones=["all"]`) per-hour MFE/MAE analysis.
The deployed bot_config.json uses `enabled_killzones=["all"]`; restricting to named
sessions should improve funded-phase PF and pipeline sustainability.

Two variants tested:
- **Config A:** London + NY AM only (`--killzones "london,ny_am"`)
- **Config B:** London + NY AM + NY PM (`--killzones "london,ny_am,ny_pm"`)

All compared to the all-day baseline (6,043 trades, PF 1.064) from the current deployed
`enabled_killzones=["all"]`.

---

## Equity Export Results (iFVG r1.25, 5y 2021-2026)

| Config | Trades | vs Baseline | PF | Net (5y) |
|--------|--------|------------|-----|---------|
| All-day (baseline) | 6,043 | — | 1.064 | $93,313 |
| Config A: London+NY AM | 3,533 | -42% | **1.076** | $86,259 |
| Config B: London+NY AM+NY PM | 3,832 | -37% | 1.049 | $50,052 |

Volume cut is within the -50% limit for both configs.

**Surprising result:** Config B (standard named sessions) has LOWER PF (1.049) than
all-day (1.064), despite excluding the worst-performing hours. This means the hours
excluded by named-session filtering (05-08:30 ET, 11-13 ET, 16-24 ET, 00-02 ET) make
a net-positive contribution to PF at the deployed partial_r=1.5 setting — opposite of
what the per-hour MFE/MAE data (partial_r=0) predicted.

---

## funded_sim Results (h200, primary haircut)

| Config | Comb Att | Passes | Busts | XFA Accounts | XFA Busts | Net Payouts | Sust |
|--------|---------|--------|-------|-------------|-----------|-------------|------|
| All-day | 196 | 45 | 150 | 85 | 84 | $132,380 | 0.536x |
| Config A: London+NY AM | 184 | 43 | 140 | 72 | 71 | $143,849 | 0.606x |
| Config B: London+NY AM+NY PM | 190 | 43 | 146 | 98 | 97 | $127,735 | 0.443x |

Raw $/mo (net payouts / 60 months):
- All-day: **$2,206/mo**
- Config A: **$2,397/mo** (+8.7%)
- Config B: **$2,129/mo** (-3.5%)

---

## Sensitivity (h0 / h200 / h400)

| Config | h0 passes/busts/net | h200 passes/busts/net | h400 passes/busts/net |
|--------|--------------------|-----------------------|-----------------------|
| All-day | 45/76/$132,304 | 45/84/$132,380 | 48/98/$132,596 |
| Config A | 42/64/$142,823 | 43/71/$143,849 | 46/82/$146,878 |
| Config B | 44/82/$127,231 | 43/97/$127,735 | 42/107/$129,059 |

Config A is haircut-robust: net payouts improve slightly at h400.
Config B degrades at higher haircuts (98→108 XFA busts, growing deficit).

---

## Success Criteria vs Primary Threshold

| Criterion | Threshold | Config A | Config B |
|-----------|-----------|---------|---------|
| PF improvement | >= +5% | +1.1% **FAILS** | -1.4% FAILS |
| Sust ratio improvement | vs baseline 0.536x | +13% PASSES | -17% FAILS |
| Volume cut | <= 50% | -42% PASSES | -37% PASSES |

**Stop rule check (BOTH metrics vs baseline):**
- Config A: PF +1.1% (better), net payouts +8.7% (better) → NOT stopped
- Config B: PF -1.4% (worse), net payouts -3.5% (worse) → STOPPED (rejected)

---

## Verdict

**Config B rejected** (stop rule: both PF and funded metrics worse than baseline).

**Config A: rejected (primary criterion not met).**
- PF improves only +1.1% (below the +5% threshold)
- Net payouts improve +8.7% — real but modest
- Sustainability improves 0.606x vs 0.536x (+13%) — meaningful but still pipeline-negative
- 42% volume reduction with nearly unchanged combine passes (43 vs 45) is notable:
  London+NY AM captures ~96% of the funded pipeline value with 42% fewer trades

**The deployed `enabled_killzones=["all"]` is already near-optimal for the funded phase.**
Named-session filtering based on the per-hour MFE/MAE analysis (Lesson 34) does not
transfer: the standard named-sessions config (Config B) is strictly worse than all-day,
and the best-possible named-sessions config (Config A, London+NY AM) shows only marginal
improvement that doesn't justify the volume reduction for Combine purposes.

---

## Key Finding: Why Per-Hour Data Did Not Predict the Result

The per-hour PF analysis in Lesson 34 was computed with `partial_r=0` (no partial profit
exits). At the deployed `partial_r=1.5`, partial exits modify the per-trade P&L profile
in a way that changes which hours look good vs bad. The overnight/pre-market hours (excluded
by named sessions) contribute differently when partial exits are active.

Additionally, the per-hour data excluded 2022 (holdout rule), while equity_export includes
2022. The 2022 signal distribution across sessions may differ significantly from 2021/2023+.

The lesson: per-hour PF rankings computed on one configuration (partial_r=0, excl 2022)
are NOT reliable predictors of session-filter performance on a different configuration
(partial_r=1.5, full 5y). Always benchmark session filters directly.

---

## Implications for Future Research

1. **London+NY AM as funded phase supplement**: If combined with the B15 long-only filter
   (allowed_sides=long, which achieved sust 1.12x), a "long-only London+NY AM" funded config
   might achieve still better sustainability. Worth testing as a future item.
2. **iFVG alone remains pipeline-negative regardless of session config**: the fundamental
   problem is that iFVG shorts drag PF below the level needed for sust > 1.0. B15's long-only
   fix (sust 1.12x) is the structural improvement; session filtering is a marginal add-on.
3. **Current recommendation unchanged**: iFVG Combine + ORB r1.0 Funded (B3, $393/mo, sust
   1.26x) remains the best identified two-phase pipeline.
