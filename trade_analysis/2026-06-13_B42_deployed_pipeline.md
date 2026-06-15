# B42 — Deployed Config Full End-to-End Pipeline Simulation

**Date:** 2026-06-13  
**Session:** wk2-b42  
**Objective:** Funded pipeline — what does the ACTUAL deployed bot earn?

---

## Setup

The deployed bot config (bot_config.json as of 2026-06-14):
- `engine=combined` (iFVG + ORB both active)
- `ifvg_entry_mode=close` (entry at inversion bar close)
- `partial_profit_r=1.5` (book 50% at 1.5R, stop to BE)
- `swing_stop_lookback=30` (wider swing stop)
- `killzones=["all"]` (all-day signals)
- `risk_pct=1.0%`
- MNQ overrides: `min_absolute_body=5.0`, `stop_buffer=3.0`, `r_multiple=3.5` (iFVG), `orb_r_multiple=2.5` (ORB)
- `target_clarity_mode=off`

Prior benchmarks (B21, B31) used research-baseline settings (ifvg_edge, partial_r=0, lookback=0, named sessions, r_multiple=2.5). B42 tests the actual deployed config for the first time.

**Key difference from B31:** B31 used ifvg_entry_mode=ifvg_edge, partial_r=0, lookback=0 — only the body/stop/engine/killzone settings were deployed. B42 adds close mode, 1.5R partials, and 30-bar swing stop.

**Key difference from all prior benchmarks:** `r_multiple=3.5` for iFVG (MNQ override) vs `r_multiple=2.5` in B21/B31. This makes iFVG target further away — higher per-trade value but lower win rate.

---

## Phase A Results (deployed config as Phase A)

| Config | Passes/Attempts | d/attempt | d/funded | Reset$/funded | Trades 2022 |
|--------|-----------------|-----------|----------|---------------|-------------|
| B42 deployed r=1.0% | **42/159** | 6.5d | 24.6d | $568 | 1097 |
| B42 deployed r=2.0% | 49/235 | 4.4d | 21.1d | $719 | — |
| B31 deployed-edge r=2.0% (ref) | 37/168 | 6.1d | 27.8d | $681 | — |
| B21 iFVG-edge r=1.25% (ref) | 34/162 | 6.0d | 28.6d | $715 | — |

**Notable:** Deployed r=1.0% generates 42 Phase A passes over 5y — the highest of any tested config. The all-day killzones + combined engine + close mode generates ~80-100 trades/month, creating more monthly P&L variance → more months crossing the $3k threshold. Even at lower per-trade risk (1.0% vs 2.0%), the higher trade frequency keeps cycle duration short (24.6d/funded vs 27.8d for B31).

---

## Phase B Results (ORB-reentry r=0.75, unchanged from B21)

| Config | Accounts | Busts | Net 5y | $/acct | Avg days |
|--------|----------|-------|--------|--------|----------|
| ORB-reentry r0.75 (B21) | 14 | 13 | $43,834 | $3,131 | 73.5d |

---

## Two-Phase Pipeline Matrix (5y, excl 2022)

| Phase A → Phase B | Reset$ | XFA$ | Net/cyc | Cycle d | Net/mo | Sust |
|-------------------|--------|------|---------|---------|--------|------|
| **B42 deployed r=1.0%** | **$568** | **$3,131** | **$2,563** | **98.1d** | **$549** | **3.23x** |
| B42 deployed r=2.0% | $719 | $3,131 | $2,412 | 94.6d | $535 | 3.77x |
| B31 deployed-edge r=2.0% (ref) | $681 | $3,131 | $2,450 | 101.3d | $508 | 2.85x |
| B21 iFVG-edge r=1.25% (ref) | $715 | $3,131 | $2,416 | 102.1d | $497 | 2.62x |

**B42 r=1.0% beats B31 on BOTH primary criteria**: $549/mo (vs $508) and sust 3.23x (vs 2.85x). This is the new best two-phase result.

**B42 r=2.0% also beats B31**: $535/mo, sust 3.77x. The higher sust comes from more passes (49 vs 42) despite same 13 busts, but lower $/mo than r=1.0% because the higher reset cost ($719 vs $568) more than offsets the faster cycling.

**Why r=1.0% beats r=2.0%**: At r=2.0%, each Phase A attempt is more volatile → more MLL busts during attempts → 235 total attempts over 5y (vs 159 at r=1.0%) with only 49 passes (vs 42). The bust rate grows faster than the pass rate, driving up the reset cost per funded account from $568 to $719.

---

## Standalone Deployed Config (same strategy for both phases)

| Config | Net/mo | Sust | Accounts | Busts |
|--------|--------|------|----------|-------|
| Deployed r=1.0% (standalone) | $844/mo | 0.79x | 54 | 53 |
| Deployed r=2.0% (standalone) | $1,211/mo | 1.09x | 46 | 45 |

The deployed iFVG (r_multiple=3.5, close mode, all-day) as a FUNDED strategy is pipeline-negative at r=1.0% (sust=0.79x) and borderline at r=2.0% (sust=1.09x). The high standalone $/mo ($844-$1211) is misleading — the pipeline is not self-sustaining because each funded account bust requires a new combine pass, and the deployed strategy doesn't generate enough passes to cover busts over 5y.

**Conclusion: The Phase B switch to ORB-reentry r=0.75 is ESSENTIAL.** ORB-reentry's 3.23x sust is what makes the pipeline viable; the deployed iFVG alone cannot sustain it.

---

## 2022 Holdout Confirmatory

Since B42 r=1.0% is a candidate (beats B31 on both 5y metrics), the protocol requires exactly one 2022 holdout run.

| Year | Phase A Net | Phase A PF | Phase B Net | Phase B PF |
|------|-------------|------------|-------------|------------|
| 2022 (holdout) | -$11,824 | 0.934 | +$4,096 | 1.072 |

**Phase A in 2022: LOSS-MAKING** (PF=0.934, net=-$11.8k)  
**Phase B in 2022: Positive** (PF=1.072, net=+$4.1k)

### 6-Year Pipeline (incl 2022)

| Config | 6y passes | 6y busts | Net/mo | Sust |
|--------|-----------|----------|--------|------|
| B42 r=1.0% deployed Phase A | 46 | — | — | — |
| ORB-reentry r0.75 Phase B | — | 21 | — | — |
| **6y Two-Phase** | **46** | **21** | **$424** | **2.19x** |

The 6y pipeline drops to $424/mo sust=2.19x — below B21's 5y baseline ($497/mo, 2.62x).

### Root Cause Analysis

The deployed Phase A has 1097 trades in 2022 (vs <200 for pure iFVG at named sessions). This high signal frequency comes from:
1. `engine=combined`: ORB fires regularly in 2022 (ORB held up in 2022 per B1)
2. `killzones=all`: No session filtering; signals fire all day including overnight
3. `ifvg_entry_mode=close`: Immediate fill at inversion bar close → no missed entries

The iFVG component in 2022 is in a structural drought (Lesson 27: threshold-loosening doesn't fix drought — the setups simply don't occur in 2022). But the deployed config still triggers many iFVG setups via close mode + all-day, which fail at the r_multiple=3.5 target during the 2022 bear market. Combined with ORB's volatility in 2022, the net Phase A equity is loss-making.

**Phase B (ORB-reentry)** adds 8 more busts from the 2022 CSV (PF=1.072 in 2022 — barely positive; the ORB edge is thin in 2022 bear market compared to 2023-2026 bull market).

---

## Key Findings

1. **The deployed config is the strongest 5y Phase A driver tested** (42 passes over 5y — best of any config). The all-day + combined + close mode + high trade volume creates more monthly P&L variance, enabling more $3k threshold crossings.

2. **Deployed r=1.0% is the Phase A risk optimum** (beats r=2.0% on $/mo). At r=2.0%, more volatility causes more MLL busts without proportionally more passes, inflating the reset cost.

3. **2022 drought is a structural risk for the deployed config** — the high-frequency all-day combined engine amplifies losses during iFVG drought years. This is the inverse of what makes it strong in 2023-2026: more trades means more losses in a drought regime.

4. **The Phase B switch to ORB-reentry r=0.75 remains essential** regardless of Phase A config. The deployed iFVG standalone is pipeline-negative.

5. **B42 r=1.0% is a candidate on 5y metrics but the 2022 holdout reveals a meaningful 2022 regime risk.** The 5y result ($549/mo, 3.23x) is valid for the 2021-2026 excluding 2022 regime. Whether 2022-like conditions recur determines actual realized performance.

---

## Monday Recommendations for Lawrence

1. **Current deployed config as Phase A is performing well** — 42 passes/5y is the best result. No Phase A config changes needed from a pure pipeline optimization standpoint.

2. **The 2022 drought risk is real but hard to hedge** at the Phase A level. The B26 finding (swing_stop_lookback=0 + target_clarity=reject improves Phase A from 6/61→11/61) was measured in ifvg_edge mode — it may not apply to the deployed close mode + combined engine. A definitive deployed-config combine sensitivity test would require another session.

3. **Keep r=1.0% for Phase A** — r=2.0% gives fewer $/mo despite higher sust due to higher reset costs.

4. **The two-phase model ($549/mo, 3.23x) is the target**: Deployed combined Phase A → ORB-reentry r=0.75 Phase B. If Lawrence switches to Phase B (funded account), the ORB config (orb_reentry_after_stop=True, risk_pct=0.75%) is the recommendation.

5. **2022 holdout note**: This is the FIRST 2022 holdout for any pipeline configuration. B21 and B31 are also unconfirmed in 2022 — they would likely fare better (fewer trades in 2022 = less drought amplification) but would also have lower 5y pass counts.
