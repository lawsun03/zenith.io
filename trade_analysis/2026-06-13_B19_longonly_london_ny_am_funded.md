# B19 — Long-only iFVG + London+NY AM Only (Funded Benchmark)

**Date:** 2026-06-13  
**Session:** wk1-b19  
**Objective:** Funded pipeline  
**Verdict:** Candidate — both primary criteria met at r1.0 and r1.25

---

## Hypothesis

B15 (long-only iFVG, london+ny_am+ny_pm, r1.0) achieved sust 1.12x — the first self-sustaining standalone funded config. B18 showed that NY PM signals (13:xx-16:30 ET) have all-sides PF 0.906 (net-negative over 5y). Removing NY PM from the long-only iFVG config should improve funded PF and pipeline sustainability by eliminating a session whose signals consistently underperform.

**Key distinction from B18:** B18 removed named sessions from ALL-SIDES iFVG and found the overnight/pre-market sessions provided positive PF contributions. B19 removes only NY PM (the specific bad session) from an already long-only config. The overnight sessions B18 was protecting are already absent from the long-only named-session view.

---

## Method

- Config: `allowed_sides=long`, `enabled_killzones=london,ny_am` (removes NY PM vs B15)
- Bars: `bars/bars_MNQ_dbv_2021_2026.csv` (5y full, including 2022)
- Risk levels: r0.75, r1.0, r1.25
- `funded_sim --haircut 200` for all runs
- No code changes — existing `allowed_sides` and `enabled_killzones` parameters handle this
- Comparison baseline: B15 (london+ny_am+ny_pm, long-only) at matching risk levels

---

## Results

### Equity Export (raw backtest stats)

| Config | Trades | PF |
|--------|--------|----|
| B19 r0.75 (london+ny_am, long-only) | ~2,640 | N/A (from prior session file) |
| B19 r1.0 (london+ny_am, long-only) | 2,643 | 1.168 |
| B19 r1.25 (london+ny_am, long-only) | 2,645 | 1.173 |

### Funded Sim (haircut $200)

| Config | Combine Passes | XFA Busts | Net (5y) | Sust | $/mo |
|--------|----------------|-----------|----------|------|------|
| B19 r0.75 | 50 | 58 | $124,024 | 0.862x | $2,067 |
| B19 r1.0 | 58 | 49 | $165,210 | **1.184x** | $2,754 |
| B19 r1.25 | 64 | 40 | $184,104 | **1.600x** | $3,068 |

### Comparison to B15 Baseline (london+ny_am+ny_pm, long-only)

| Config | Trades | PF | Passes | Busts | Net (5y) | Sust |
|--------|--------|----|--------|-------|----------|------|
| B15 r0.75 | 3,931 | 1.139 | 42 | 64 | $135,472 | 0.66x |
| B15 r1.0 | 3,946 | 1.127 | 56 | 50 | $177,542 | 1.12x |
| B15 r1.25 | 3,928 | 1.121 | 60 | 53 | $186,214 | 1.13x |

### Delta vs B15 (same risk level)

| Metric | r0.75 | r1.0 | r1.25 |
|--------|-------|------|-------|
| Trade count | -33% | -33% | -33% |
| PF change | N/A | +3.6% | +4.6% |
| Combine passes | +19% | +4% | +7% |
| XFA busts | -9% | -2% | -25% |
| Net payouts | -8% | -7% | -1% |
| Sust change | +30pp | +5.7pp | **+42pp** |

---

## Success Criteria

Primary (vs B15 r1.0: PF 1.127, sust 1.12x):
- PF improves >= +2%: r1.0: +3.6% ✓, r1.25: +4.6% ✓
- Sust >= 1.12x: r1.0: 1.184x ✓, r1.25: 1.600x ✓

**Both criteria met at r1.0 and r1.25. Stop rule not triggered.**

---

## Key Findings

**1. NY PM removal improves quality without sacrificing quantity**

At r1.25, removing ~1,283 NY PM trades (33% of B15 volume) produces virtually identical net payouts ($184k vs $186k — a mere -1% difference). This confirms that NY PM signals contribute disproportionately few profits relative to their count, while their variance contributes to XFA bust events.

**2. R1.25 is the standout result**

Sust 1.600x at r1.25 is dramatically better than B15's 1.13x. This means for every 10 funded accounts that bust, approximately 16 new accounts are created via combine passes — a healthy 60% surplus that compounds over time. B15 at 1.13x was barely self-sustaining; B19 at 1.60x has real headroom.

**3. R1.0 also passes and is more conservative**

Sust 1.184x at r1.0 gives a smaller surplus but is still clearly pipeline-positive. Use r1.0 if the deploy environment requires tighter risk management.

**4. The B18 counterintuitive finding is specific to overnight sessions**

B18 concluded that removing named sessions from all-sides iFVG hurts the funded pipeline because overnight/pre-market sessions have positive PF contributions. B19 removes only NY PM (all-sides PF 0.906), which is net-negative. These are different sessions with different characteristics:
- Overnight/pre-market (00:00-08:30 ET): shorts-heavy, complex; removing these hurts funded
- NY PM (13:00-16:30 ET): profit-taking / chop environment; removing these helps funded

**5. Volume sustainability at ~44 trades/month**

2,645 trades over 5y ≈ 44 trades/month. This is insufficient for the Combine objective (needs 60+), but adequate for the funded phase where PF quality matters more than volume.

---

## Implications for B20 (Two-Phase Pipeline)

B20 tests iFVG Combine → LongOnly-iFVG Funded. Based on B19:
- **Phase B config to use:** B19 r1.25 (london+ny_am, long-only, risk 1.25%)
- **B20 must generate per-year equity CSVs** (not flat 5y) for valid comparison to B3 ($393/mo, sust 1.26x)
- **Estimated B20 sust:** if per-year busts scale at 0.46x flat (same as ORB r1.0 scaling), then B19 r1.25's 40 flat XFA busts → ~18 per-year busts vs iFVG combine's ~34 passes → estimated sust ~1.89x, easily beating B3's 1.26x

B20 is the next priority item in the backlog.

---

## Recommendation

**Funded phase configuration (pending B20 two-phase validation):**
- Engine: iFVG (allowed_sides=long)
- Killzones: london, ny_am (remove NY PM)
- Risk: 1.25% per trade
- Sustainability: 1.600x (standalone), estimated ~1.89x in two-phase

Do not deploy until B20 confirms the two-phase pipeline numbers. The standalone metrics are favorable but the full decision should wait for pipeline-integrated analysis.
