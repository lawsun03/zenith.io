# B20 — iFVG Combine → Long-only iFVG Funded (Two-Phase Pipeline)

**Date:** 2026-06-13  
**Session:** wk1-b20  
**Verdict:** REJECTED — no B20 config meets both success criteria ($/mo >= $393 AND sust >= 1.26x)  
**B3 baseline remains recommended:** iFVG r1.25 Combine + ORB r1.0 Funded = $393/mo, sust 1.26x

---

## Hypothesis

B3's best two-phase pair is iFVG r1.25 Combine + ORB r1.0 Funded ($393/mo, sust 1.26x). The B19
standalone funded benchmark showed LongOnly-iFVG (london+ny_am, r1.25) has higher PF (1.173) and
better standalone sust (1.60x vs ORB r1.0's standalone 0.85x). Hypothesis: coupling LongOnly-iFVG
funded with the fast iFVG combine would beat B3 on both criteria.

---

## Methodology

- **Phase A (Combine):** Reused equity_b1/control_r1p25_{year}.csv from B3 (same iFVG r1.25 data)
- **Phase B (Funded):** Generated per-year equity CSVs in equity_b20/ using equity_export at
  r0.75/r1.0/r1.25 with `--set allowed_sides=long --killzones london,ny_am` (B19 config)
- Years: 2021/2023/2024/2025/2026 (2022 frozen holdout, excluded)
- Haircut: $200/payout | Reset fee: $150/attempt | TRADING_DAYS_PER_MONTH: 21
- Per-year stitching: each year restarts equity at $50k (same methodology as B3)

## Per-Year Equity Export Results (Phase B)

| Year | r0.75 trades | r0.75 net | r0.75 PF | r1.0 trades | r1.0 net | r1.0 PF | r1.25 trades | r1.25 net | r1.25 PF |
|------|-------------|-----------|----------|-------------|----------|---------|-------------|-----------|---------|
| 2021 | 314 | +$13,099 | 1.337 | 317 | +$19,767 | 1.348 | 319 | +$23,799 | 1.320 |
| 2023 | 497 | -$1,901 | 0.971 | 503 | -$3,015 | 0.966 | 507 | -$4,483 | 0.961 |
| 2024 | 542 | +$20,989 | 1.276 | 551 | +$29,686 | 1.260 | 552 | +$38,801 | 1.251 |
| 2025 | 473 | +$15,976 | 1.235 | 493 | +$19,969 | 1.205 | 495 | +$22,581 | 1.172 |
| 2026 | 210 | +$6,511 | 1.246 | 225 | +$10,023 | 1.283 | 232 | +$11,461 | 1.233 |

Key: 2023 is the sole losing year at all risk levels (regime-poverty, consistent with known pattern).
All other years profitable with PF 1.17–1.35.

---

## Phase Stats Summary (per-year stitched curves, h200)

| Config | Combine passes | Combine attempts | Days/funded | XFA accounts | XFA busts | Net 5y | Sust (standalone) |
|--------|---------------|-----------------|-------------|-------------|-----------|--------|-------------------|
| iFVG r1.25 (Phase A) | 34 | 162 | 28.6d | 74 | 73 | $100,256 | 0.47x |
| LongOnly r0.75 (Phase B) | 24 | 63 | 42.9d | 33 | 32 | $60,853 | 0.75x |
| LongOnly r1.0 (Phase B) | 32 | 95 | 32.2d | 53 | 52 | $93,900 | 0.62x |
| LongOnly r1.25 (Phase B) | 39 | 125 | 26.4d | 49 | 49 | $113,246 | 0.80x |
| ORB r0.75 (B3 ref) | 15 | 29 | 68.6d | 14 | 14 | $35,653 | 1.07x |
| ORB r1.0 (B3 ref) | 23 | 56 | 44.7d | 28 | 27 | $54,200 | 0.85x |

Note: ORB per-year accounts (28) is about half LongOnly per-year accounts (53) despite lower
annual trade count. ORB XFA accounts last ~45 trading days each vs ~24d for LongOnly r1.0.

---

## Two-Phase Pipeline Results

| Phase A -> Phase B | Reset$/acct | XFA$/acct | Net/cycle | Cycle days | Net/mo | Sust |
|-------------------|------------|----------|-----------|------------|--------|------|
| iFVG → LongOnly r1.25 | $715 | $2,311 | $1,596 | 49.6d | **$676** | 0.69x |
| iFVG → LongOnly r1.0 | $715 | $1,772 | $1,057 | 48.0d | **$463** | 0.65x |
| iFVG → LongOnly r0.75 | $715 | $1,844 | $1,129 | 59.8d | **$397** | 1.06x |
| iFVG → ORB r1.0 *(B3 ref)* | $715 | $1,936 | $1,221 | 65.3d | *$393* | *1.26x* |
| iFVG → ORB r0.75 *(B3 ref)* | $715 | $2,547 | $1,832 | 102.1d | *$377* | *2.43x* |

Sustainability formula: iFVG combine passes (34) / Phase B xfa busts.

---

## Why B20 Failed: Account Cycle Speed

The fundamental problem is that LongOnly-iFVG funded creates XFA accounts that terminate
much faster than ORB funded accounts:

| Funded config | XFA accounts (5y) | XFA busts | Avg duration | Sust (vs iFVG combine 34 passes) |
|--------------|-------------------|-----------|-------------|----------------------------------|
| LongOnly r0.75 | 33 | 32 | ~38d | 34/32 = **1.06x** (barely sustainable) |
| LongOnly r1.0 | 53 | 52 | ~24d | 34/52 = **0.65x** (pipeline negative) |
| LongOnly r1.25 | 49 | 49 | ~26d | 34/49 = **0.69x** (pipeline negative) |
| ORB r1.0 | 28 | 27 | ~45d | 34/27 = **1.26x** (B3 benchmark) |

LongOnly-iFVG funded at r1.0 creates 53 accounts over 5 years vs ORB r1.0's 28. The iFVG combine
only produces 34 passes (funded accounts) — enough to replace ORB's 27 busts with a 1.26x buffer,
but unable to replace LongOnly's 52 busts.

**Why shorter account durations?** LongOnly-iFVG generates ~500+ trades/year (2089 trades over 5y
non-holdout periods) while ORB r1.0 generates ~330 trades/year. The higher signal frequency means:
- More daily P&L events → equity curve moves faster
- Accounts hit both the payout cap AND the bust threshold more quickly
- Net effect: 2x more total accounts over the same 5-year window

---

## Key Finding: Standalone Sust vs Two-Phase Sust

B15 (standalone LongOnly iFVG) showed sust 1.12x at r1.0 flat-5y. B20 shows 0.65x two-phase.

The discrepancy has two sources:
1. **Different Phase A:** B15 assumes LongOnly-iFVG does its own combine (56 passes, 50 busts over flat-5y).
   B20 uses iFVG combine (34 passes). B15 was evaluating "if LongOnly-iFVG did everything," not "if
   a separate combiner fed accounts into LongOnly-iFVG funded."
2. **Per-year vs flat-5y methodology:** Per-year gives ~1.04x more busts for LongOnly r1.0 (52 vs 50)
   but the combine change is the dominant factor (34 iFVG passes vs 56 LongOnly passes).

The two-phase model correctly exposes that LongOnly-iFVG, while self-sustaining as a standalone,
CANNOT be sustained by the iFVG combine at any risk level above r0.75.

---

## Success Criteria Assessment

| Config | $/mo >= $393 | sust >= 1.26x | Result |
|--------|-------------|---------------|--------|
| LongOnly r0.75 | $397 ✓ (marginally) | 1.06x ✗ | FAIL |
| LongOnly r1.0 | $463 ✓ | 0.65x ✗ | FAIL |
| LongOnly r1.25 | $676 ✓ | 0.69x ✗ | FAIL |

No B20 config meets both criteria simultaneously.

---

## Recommendation

**B3 remains the recommended two-phase pipeline: iFVG r1.25 Combine + ORB r1.0 Funded ($393/mo, sust 1.26x).**

The LongOnly r1.25 config's $676/mo is tempting (72% better than B3's $393/mo) but at sust 0.69x
(pipeline negative) it collapses over the long run — each funded cohort requires 1.45x more
combine passes than the iFVG combine can supply.

The next investigation (B21) tests ORB-reentry as the funded phase, which may improve $/mo while
keeping the ORB structural advantage of longer account durations.
