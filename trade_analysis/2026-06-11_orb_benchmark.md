# ORB second engine — solo benchmark + complementarity (2026-06-11)

Build per `project_orb_second_engine` memory: `app/strategy/orb.py`
(ORBDetector + ORBRunner duck-typing the engine's runner surface),
`StrategyParams.engine="orb"` selects it in both `_build_runner` sites.
Closed-bar breakout of the opening range; stop = opposite OR edge; fixed-R
target; 1 trade/day. Harness: monthly Combine, risk 1.25%, partials 1.5R,
full live ruleset. Sweep on 2024 (train), validation on 2025-26 (test).
Lawrence chose to sweep both anchors. All runs in UI registry (`orb_*`).

## 2024 sweep (16 configs): pass rate / run PF

| anchor+range | r1.5 | r2.0 | r2.5 | r3.0 |
|---|---|---|---|---|
| **9:30+15min** | 2/12, 1.22 | 2/12, 1.26 | **4/12, 1.44** | 2/12, 1.32 |
| 9:30+30min | 1/12, 0.97 | 1/12, 1.02 | 1/12, 0.96 | 2/12, 1.06 |
| 8:30+15min | 1/12, 0.97 | 3/12, 1.15 | 3/12, 1.07 | 2/12, 0.96 |
| 8:30+30min | 3/12, 0.93 | 3/12, 0.99 | 3/12, 0.98 | 3/12, 1.00 |

Only the **9:30 cash-open + 15min range** row has consistent edge (PF
1.22–1.44 at every r). The 8:30 rows and the 30-min range are PF≈1 —
their passes are sequencing luck. Zero MLL failures in all 192 month-runs.

## Test-period validation (9:30+15min, top 3 r values)

| config | test 17mo | run PF | maxDD | long PF | short PF |
|---|---|---|---|---|---|
| r2.0 | 4/17 (24%) | 1.21 | 2232 | 1.33 | 1.07 |
| **r2.5** | 4/17 (24%) | 1.21 | 2219 | 1.33 | 1.08 |
| r3.0 | 4/17 (24%) | 1.22 | 2219 | 1.32 | 1.12 |

Identical pass months across all three (r-plateau, robust region):
**2025-04, 2025-10, 2026-02, 2026-04**. Candidate config: **r2.5**
(best 2024, tied test).

## THE KEY METRIC — month-complementarity vs iFVG control

| period | iFVG passes | ORB r2.5 passes | overlap | ORB passes in iFVG DROUGHTS |
|---|---|---|---|---|
| test 17mo | 02/07/09/12-25, 02/05-26 (6) | **04-25, 10-25**, 02-26, **04-26** (4) | 1 (2026-02) | **3 of 4** |
| 2024 | Mar, May, Oct, Dec (4) | **Feb, Apr, Jul**, Oct (4) | 1 (Oct) | **3 of 4** |

- iFVG droughts ORB converts to PASSES: 2025-04 (iFVG 4 trades, −$832 →
  ORB +$7,086), 2025-10 (3 tr, −$1.5k → pass), 2026-04 (3 tr, −$1.7k →
  pass), 2024-02 (3 tr, −$1.5k → pass), 2024-04 (2 tr, −$1.2k → pass),
  2024-07 (4 tr, −$774 → pass).
- Activity is anti-correlated too: ORB trades 25–31/mo in several iFVG
  dead months (2025-01/10, 2026-04), while ORB is quiet in iFVG's monster
  months (2025-02: ORB 4 trades). The clock-driven construction does what
  it was built to do.
- **Naive pass union (separate accounts): test 9/17 (53%), 2024 7/12
  (58%) → 16/29 (55%) vs iFVG-alone 10/29 (34%).** A combined one-account
  process will land below the naive union (shared DLL/DPL/MLL budget,
  margin interactions) but the direction and magnitude justify the build.

## Verdict: GO for the two-runner combined process

Next phase (not in this benchmark):
1. `engine.runners` is keyed by instrument — two runners on one
   instrument needs the keying change (known design item).
2. Risk interaction questions: shared 1.25%/trade? per-engine budgets?
   one position at a time vs concurrent?
3. Re-run monthly + 2-month windows + `funded_sim` on the combined
   process; target region 60%+ monthly.

## Caveats

- ORB solo trips the soft-buffer freeze in losing stretches (round-6
  mechanism, mark-to-market): quiet ORB months (3–6 trades) are mostly
  early freezes, not missing signals (detector fires ~1/day by
  construction; verified 20 signals in 2024-03 standalone).
- 9:30+15 r2.5 was picked on 2024 and confirmed on test, but the anchor/
  range grid was small (16 cells) — the row-level consistency (not the
  single cell) is the evidence.
- Same small-n caveats as all monthly campaigns (12+17 months).
- ORB modes are backtest-validated only; live wiring (Rule 13 UI
  observability: strategy_state SSE + StrategyDebug section for OR
  high/low/fired) is part of the combined build, not done here.
