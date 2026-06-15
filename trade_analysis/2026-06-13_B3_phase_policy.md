# B3: Two-Phase Pipeline Policy — Combine A + Funded B

**Date:** 2026-06-13  
**Session:** wk1-b3  
**Objective:** Funded / pipeline  
**Method:** Analytic pipeline model using existing B1 equity CSVs (5y, 2021/2023/2024/2025/2026)

---

## Background

B1 established ORB r0.75 as the Phase-B (funded) recommendation. B3 asks:
*Does using iFVG (which passes Combines frequently) for Phase A and ORB for Phase B
outperform running the same config for both phases?*

Topstep has two distinct phases with different success criteria:
- **Combine (Phase A):** need to hit the profit target without exceeding daily/max loss
- **Funded/XFA (Phase B):** maximize payouts before hitting the max-loss limit

The best combine-passer and the best funded-account-runner may be DIFFERENT configs.

---

## Key Corrections from B1

The b1_results.json ORB r0.75 entry was corrupted — it contained only 2022 holdout data.
The re-stitched 5-year series (2021/2023/2024/2025/2026) confirms the B1 journal numbers:

**ORB r0.75 (corrected, h200):**
- Combine: 15 passes / 29 attempts (52% pass rate, avg 35.5d/attempt)
- XFA: 14 accounts, 14 busts, net $35,653 total

This matches the B1 journal exactly; b1_results.json should be regenerated to fix the corruption.

---

## Pipeline Model

Each "cycle" for a single trader:
1. Run combine attempts using Phase A config until pass (cost: attempts * $150)
2. Run XFA funded account using Phase B config until bust or payout event
3. Return to step 1 on bust

**Metrics:**
- **Reset$/acct:** combine_attempts_per_funded * $150 (cost to get one funded account)
- **XFA$/acct:** total_xfa_net / total_accounts (average net payout per funded account)
- **Net/cycle:** XFA$/acct - Reset$/acct
- **Cycle days:** trading days in combine phase + trading days in funded phase
- **Net/mo:** Net/cycle * 21 / cycle_days (normalizes to calendar months)
- **Sustainability:** A.combine_passes / B.xfa_busts over the same 5-year window
  - >1.0 = self-sustaining (enough combines to replace busted funded accounts)
  - <1.0 = pipeline deficit (busts outpace combine regeneration — model collapses long-run)

All numbers at haircut $200 (assumed intraday adverse excursion below day close).
Reset fee: $150 per combine attempt (Topstep monthly subscription).

---

## Per-Phase Stats (5y, 2021/2023/2024/2025/2026, h200)

| Config     | Combine pass/att | Days/funded acct | Reset$/acct | XFA$/acct | XFA days/acct | Sust (solo) |
|------------|-----------------|-----------------|-------------|-----------|---------------|-------------|
| iFVG r1.25 | 34/162 (21%)    | 28.6d           | $715        | $1,355    | 13.1d         | 0.47x       |
| ORB r0.75  | 15/29 (52%)     | 68.6d           | $290        | $2,547    | 73.5d         | 1.07x       |
| ORB r0.5   | 8/14 (57%)      | 128.6d          | $262        | $3,532    | 171.5d        | 1.33x       |
| ORB r1.0   | 23/56 (41%)     | 44.7d           | $365        | $1,936    | 36.8d         | 0.85x       |
| ORB r1.25  | 32/82 (39%)     | 32.2d           | $384        | $2,108    | 28.6d         | 0.89x       |

**Key observations:**
- iFVG passes Combines 3x more OFTEN (34 passes vs 15 for ORB r0.75) over 5 years
- iFVG does so in SHORTER attempts (28.6d vs 68.6d per funded account reached)
- iFVG is terrible as a funded account: only $1,355 net/account vs $2,547 for ORB r0.75
- ORB r0.75 is the only ORB sizing with solo sustainability >1.0x (just barely: 1.07x)

---

## Two-Phase Pipeline Results

| Phase A -> Phase B          | Reset$/acct | XFA$/acct | Net/cycle | Cycle days | Net/mo | Sust   |
|-----------------------------|-------------|-----------|-----------|------------|--------|--------|
| iFVG r1.25 -> ORB r1.25     | $715        | $2,108    | $1,393    | 57.1d      | $512   | 0.94x  |
| ORB r0.75 -> ORB r1.25      | $290        | $2,108    | $1,818    | 97.2d      | $393   | 0.42x  |
| **iFVG r1.25 -> ORB r1.0**  | **$715**    | **$1,936**| **$1,221**| **65.3d**  | **$393**| **1.26x** |
| iFVG r1.25 -> ORB r0.75     | $715        | $2,547    | $1,832    | 102.1d     | $377   | 2.43x  |
| ORB r0.75 -> ORB r0.75      | $290        | $2,547    | $2,257    | 142.1d     | $333   | 1.07x  |
| ORB r0.75 -> ORB r1.0       | $290        | $1,936    | $1,646    | 105.3d     | $328   | 0.56x  |
| iFVG r1.25 -> ORB r0.5      | $715        | $3,532    | $2,817    | 200.1d     | $296   | 5.67x  |
| ORB r0.75 -> ORB r0.5       | $290        | $3,532    | $3,242    | 240.1d     | $284   | 2.50x  |

**Filtering on sustainability >= 1.0 (self-sustaining pipelines):**

| Phase A -> Phase B          | Net/mo | Sustainability |
|-----------------------------|--------|----------------|
| iFVG r1.25 -> ORB r1.0      | $393   | 1.26x          |
| iFVG r1.25 -> ORB r0.75     | $377   | 2.43x          |
| ORB r0.75 -> ORB r0.75      | $333   | 1.07x          |
| iFVG r1.25 -> ORB r0.5      | $296   | 5.67x          |
| ORB r0.75 -> ORB r0.5       | $284   | 2.50x          |

---

## Single-Phase Benchmarks

| Config     | Reset$/acct | XFA$/acct | Net/cycle | Cycle days | Net/mo | Sust   |
|------------|-------------|-----------|-----------|------------|--------|--------|
| ORB r1.25  | $384        | $2,108    | $1,723    | 60.7d      | $596   | 0.89x  |
| ORB r1.0   | $365        | $1,936    | $1,571    | 81.5d      | $405   | 0.85x  |
| ORB r0.75  | $290        | $2,547    | $2,257    | 142.1d     | $333   | 1.07x  |
| iFVG r1.25 | $715        | $1,355    | $640      | 41.7d      | $323   | 0.47x  |
| ORB r0.5   | $262        | $3,532    | $3,270    | 300.1d     | $229   | 1.33x  |

The highest single-phase $/month (ORB r1.25: $596, ORB r1.0: $405) are **pipeline negative**
— they consume funded accounts faster than combine passes can replenish them.

---

## Verdict and Recommendation

**Recommended two-phase pair: iFVG r1.25 (Combine) + ORB r1.0 (Funded)**

- $393/month — best $/month among all sustainable two-phase configurations
- 1.26x sustainability — generates 34 combine passes to absorb 27 ORB r1.0 busts
- 18% better than the best sustainable single-phase (ORB r0.75: $333/month)

**Why two-phase outperforms single-phase ORB r0.75:**
iFVG generates combine passes in 28.6 trading days per funded account (vs 68.6d for ORB r0.75).
Despite needing 4.76x more attempts per pass, each attempt only takes 6.0 days (vs 35.5d for ORB).
This speed advantage lets the pipeline support a slightly more aggressive funded phase (ORB r1.0)
while remaining sustainable — ORB r1.0 alone would be pipeline-negative (0.85x).

**Conservative alternative: iFVG r1.25 (Combine) + ORB r0.75 (Funded)**
- $377/month, 2.43x sustainability
- Recommended if Lawrence wants a wider buffer (can absorb 2+ consecutive funded busts
  without needing additional capital for combine fees)

**Why NOT iFVG → ORB r1.25 ($512/month):**
Pipeline is negative at 0.94x — 36 ORB r1.25 XFA busts vs only 34 iFVG combine passes.
Over 5+ years, this collapses. Do not deploy.

---

## Caveats

1. **Daily granularity**: intraday MLL touches between fills are invisible; busts are
   understated even at h200. Real bust rates are likely higher, which would favor
   more conservative funded sizing (ORB r0.75 vs r1.0).

2. **Regime risk**: Both 2022 (holdout — untested here) and 2023 were difficult years.
   iFVG's combine pass rate in hostile regimes may fall, tightening the sustainability ratio.

3. **Sequential assumption**: the model assumes the trader spends ALL time in either
   Combine or Funded — no gap, no休息. Real pipelines have setup delays, holidays, etc.

4. **Single-funded-slot assumption**: the model assumes one combine attempt and one funded
   account at a time. With capital for multiple simultaneous accounts, higher-risk configs
   become more viable through diversification.

5. **Config switching overhead**: switching from iFVG (Combine) to ORB r1.0 (Funded)
   requires a bot config change and restart. Per CLAUDE.md Rule, restarts must happen
   while flat and market closed. This is feasible but requires manual intervention.

---

## Implementation Note

The bot already supports `account_phase` config switching. The two-phase workflow:
1. During Combine: deploy bot_config.json with engine=ifvg (current live config is fine)
2. On pass notification: update engine=orb, orb_r_multiple=2.5, risk_pct=1.0
3. On XFA bust: revert to iFVG config for next combine

No new code required. This is a deployment policy recommendation, not a software change.
