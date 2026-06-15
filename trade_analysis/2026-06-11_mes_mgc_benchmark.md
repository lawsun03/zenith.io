# MES + MGC engine benchmarks — the edge lives in MNQ only (2026-06-11)

Same program as the MNQ campaign, per instrument independently: iFVG
control (instrument's own config — base StrategyParams, the MNQ overrides
are MNQ-tuned), full 16-config ORB sweep on 2024 (train), best cell
validated on 2025-26 (test). Risk 1.25%, 5min, full live ruleset. All
runs in UI registry (`ctl_mes_*`, `ctl_mgc_*`, `orb_mes_*`, `orb_mgc_*`).

## Summary

| instrument | iFVG test | iFVG 2024 | iFVG PF (t/24) | best ORB cell (2024) | ORB on test | verdict |
|---|---|---|---|---|---|---|
| **MNQ** (ref) | 6/17 (35%) | 4/12 (33%) | 1.31 / 1.37 | 9:30+15 r2.5, **PF 1.44**, 4/12 | 4/17 (24%), PF 1.21 | both engines viable |
| MES | 2/17 (12%) | 0/12 | 0.93 / 0.64 | 9:30+15 r3.0, PF 1.08, 1/12 | 1/17 (6%), PF 1.07 | **no viable engine** |
| MGC | 1/18 (6%) | 0/12 (+1 MLL) | 0.69 / 0.67 | 8:30+15 r1.5, PF 1.05, 2/12 | 4/18 (22%), **PF 0.99** | **no viable engine** |

## Detail

**MES iFVG:** PF < 1 both periods (0.93 / 0.64); 2024 is outright
negative on both sides. **MES ORB:** the 9:30+15 row that carries MNQ is
PF 0.96–1.08 here — marginal at best; 9:30+30 and 8:30+30 are dead
(0.67–0.83); 8:30+15 shows 2–3 passes on PF<1 (luck) plus the only MLL
fail. Test validation of the best cell: 1/17, PF 1.07. Parked
observation: MES ORB longs are PF 1.14–1.26 across the 9:30+15 row while
shorts are <1 — same shape as the MNQ T1 temptation; not chased (the
ablation campaign showed side-filters cost more volume than they save).

**MGC iFVG:** PF 0.67–0.69, the worst of the three; one MLL fail on 2024.
**MGC ORB:** all 16 cells PF 0.69–1.05; best is the 8:30 anchor
(shorts-carried, PF 1.25 shorts) — plausibly the metals' data-open vs the
equity cash open, but it never clears PF 1.05 on train and lands at PF
0.99 on test. The test run's 4/18 "passes" at PF 0.99 are a textbook
case of the lockout asymmetry manufacturing pass months without edge —
why this campaign always scores pass rate AND PF together.

**Skipped by stop rule:** combined-engine runs for MES/MGC (combining
two no-edge engines; the MNQ experiment already showed one-account
combination only subtracts), and broader ORB validation (no winning row
to validate).

## Read

1. **Both engines' edges are MNQ-specific.** Consistent with the 2.5-year
   multi-instrument scan (MNQ 5min was the only PF>1 iFVG instrument) —
   and now ORB shows the same concentration. Index-micro NQ volatility
   around the cash open is where both setups pay.
2. **The multi-account portfolio idea does NOT extend to MES/MGC today.**
   The two-account recommendation stays: iFVG-MNQ + ORB-MNQ. A third
   account would need a third edge, not a third instrument running these
   engines.
3. iFVG-MES/MGC ran untuned base params; a dedicated tuning campaign
   could in principle move them, but the MNQ experience (35% plateau,
   parameter-proof and ablation-proof) plus the prior multi-instrument
   scan argue against spending the effort.

## Caveats

- MGC test file spans 18 calendar months (vs 17 MES/MNQ).
- Commission modeled at the harness default ($0.74/side) for all
  instruments; MES/MGC actuals differ by cents — immaterial at PF≈1.
- Same small-n as all campaigns.
