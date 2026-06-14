# Supply/Demand zone retest — Phase 1 raw profitability — NO EDGE (standalone)

**Date:** 2026-06-14 · **Scope:** raw standalone profitability (Law 3 confluence OFF).
**Method:** momentum order-block zones on 15-min MNQ (base candle + 3 strong-body impulse
bars, net departure ≥ depart_k·ATR), fresh first-touch only, enter on retest at proximal,
stop beyond distal, TP 2R/3R, manage ≤3 days, 2022 excluded, no lookahead.
Script: `scripts/supply_demand_phase1.py`.

## Results (default body_k=0.5, depart_k=1.5)
| TP | side | n | win% | PF | R/trd | /yr | yrs+ |
|---|---|---|---|---|---|---|---|
| 3R | demand (long) | 298 | 29 | **1.13** | +0.09 | 60 | 3/5 |
| 3R | supply (short) | 366 | 26 | 0.98 | −0.02 | 73 | 1/5 |
| 3R | ALL | 664 | 27 | 1.04 | +0.03 | 133 | 3/5 |
| 2R | ALL | 664 | 33 | 0.93 | −0.05 | 133 | 1/5 |

## Law-1 momentum sweep (DEMAND, 3R) — the transcript's core claim is INVERTED on NQ
| body_k | depart_k | n | PF | R/trd | yrs+ |
|---|---|---|---|---|---|
| 0.5 | 1.5 | 298 | 1.13 | +0.09 | 3/5 |
| 0.8 | 1.5 | 96 | **1.17** | +0.12 | 4/5 |
| 0.5 | 2.5 | 158 | 0.82 | −0.14 | 1/5 |
| 0.8 | 2.5 | 74 | 0.80 | −0.16 | 2/5 |
| 1.0 | 2.5 | 44 | 0.65 | −0.30 | 1/5 |
| 1.0 | 3.5 | 27 | 0.83 | −0.14 | 1/5 |

Stronger departure (bigger "basketball bounce") → WORSE retest performance. The only
non-negative cells are the loosest departure (1.5×ATR). Best config (0.8/1.5) is still
marginal: PF 1.17, +0.12R, 19 trades/yr.

## Verdict
1. **Standalone S/D has no usable edge.** Long zones marginal (PF ~1.1), short zones
   negative (the familiar NQ long-bias), all-in ~breakeven. No momentum threshold sharpens
   it — the Law-1 "high-momentum zones are best" claim is reversed on NQ 15-min.
2. **This empirically confirms the transcript's own Law 3** ("never use S/D solo") — the raw
   method is breakeven by itself.
3. **S/D ≈ the deployed iFVG mechanism** (rapid displacement off a base + retest). The edge
   we already harvest comes from the EXTRA filters iFVG adds (liquidity sweep + FVG
   inversion + killzone). "S/D with confluence" converges to the strat we already run.
4. **Not a new strategy to build.** Optional remaining angle (low prior): use a fresh demand
   zone as a confluence FILTER on iFVG *longs* only — but B47 (confluence gate) was rejected
   and demand zones alone are too weak to expect it to add edge.

## INSTRUMENT UPDATE (2026-06-14) — the trader actually trades GOLD/OIL, so re-tested both

NQ was the wrong instrument. Re-ran the identical method on v-rolled MGC (gold) and MCL (oil),
1-min → 15-min, 2024–2026 (~2.5y; all the local history we have for these).

| instrument | side | n | win% | PF | R/trd | yrs+ |
|---|---|---|---|---|---|---|
| **GOLD (MGC)** | **demand (long)** | 185 | 37 | **1.62** | **+0.42** | **3/3** |
| GOLD (MGC) | supply (short) | 175 | 25 | 0.93 | −0.06 | 1/3 |
| GOLD (MGC) | ALL | 360 | 31 | 1.25 | +0.19 | 3/3 |
| OIL (MCL) | demand | 209 | 21 | 0.69 | −0.29 | 0/3 |
| OIL (MCL) | supply | 237 | 26 | 0.90 | −0.09 | 1/3 |
| NQ (MNQ) | demand | 298 | 29 | 1.13 | +0.09 | 3/5 |

**Gold demand-zone longs have a real edge; oil does not; NQ marginal.** Gold demand: PF 1.62,
+0.42R/trade, 3R target, positive all 3 years. Short side stays weak (gold bull market).

**Trend-confound control (critical):** gold was a strong bull 2024–26, so I compared to
RANDOM gold longs (same 1×ATR stop, 3R, 3-day hold, n=300): random = 28% win / PF 1.15 /
+0.11R. Demand zones = 37% / 1.62 / +0.42R. The zone adds ~+0.31R ABOVE the random-long
trend tailwind — i.e., the edge is the zone *timing*, not just "long gold went up."

### Revised verdict
- **On gold, supply/demand (demand/long) IS a genuine edge** — the instrument was the issue.
- Oil: no. NQ: marginal. Short side: weak everywhere (consistent with the long-bias finding).
- Caveats before sizing: only 2.5y, all gold-bull years (no bear/chop regime tested — need
  pre-2024 gold to confirm robustness); limit-fill at proximal is mildly optimistic.
- Next steps: (1) more gold history (Databento MGC pre-2024, budget-gated) incl. a bear/chop
  stretch; (2) momentum/freshness refinement on gold (NQ inverted Law 1 — re-check on gold);
  (3) combine/funded framing on MGC; (4) the bot doesn't trade gold today — this would be a
  new instrument deployment, not a tweak to the live MNQ config.

