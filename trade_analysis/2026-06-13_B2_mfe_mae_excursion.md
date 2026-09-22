# B2: MFE/MAE Excursion Ladder — 2026-06-13

**Session:** wk1-b2 | **Objective:** Combine | **Verdict:** infrastructure shipped; be_trail_r=1.0 rejected

---

## What Was Built

MFE/MAE (Maximum Favorable/Adverse Excursion) tracking added to PaperBroker. Every closed
bracket now records:
- `mfe_pts`: peak favorable price move from entry (in price points)
- `mae_pts`: worst adverse price move from entry (in price points)
- `initial_stop_dist`: stop distance at fill time (used for R-unit conversion)

These appear in `BacktestResult.trades` as `mfe_pts`, `mae_pts`, `r_mfe`, `r_mae`.

The `be_trail_r` field (`StrategyParams`, default 0 = off) moves the stop to break-even
the moment MFE crosses `be_trail_r × initial_stop_dist`. Fully wired through bot_config.py
and runner.py. 11 defining-behavior tests in `tests/test_mfe_mae.py`.

**Analysis scripts:** `scripts/analyze_mfe_mae.py` runs per-year backtests and writes
trade-level CSV files to `research/mfe_mae_*.csv`.

---

## Distribution Data (partial_r=0 for clean winner/loser classification)

### iFVG (frontier: r3.5, risk 1.25%, no partials)

| Metric | Winners | Losers |
|--------|---------|--------|
| MFE p25 | 1.43R | 0.21R |
| MFE p50 | 3.0R | 0.56R |
| MFE p75 | 3.69R | 1.03R |
| MFE p90 | — | 1.80R |
| MAE p50 | 0.39R | — |
| MAE p90 | 0.82R | — |

Note: winner MFE p75=3.69R > target (3.5R), meaning winners often run past the target bar
before the stop-check executes — consistent with OHLC bar resolution.

### ORB r2.5 (risk 1.25%, no partials)

| Metric | Winners | Losers |
|--------|---------|--------|
| MFE p75 | 2.50R | 0.78R |
| MFE p90 | — | 1.29R |
| MAE p90 | 0.82R | — |

Winner MFE p75=2.50R = target exactly (by construction of a OHLC bar hitting the target).

---

## be_trail_r=1.0 Benchmark

Candidate: move stop to break-even when MFE crosses 1.0R. Rationale from distributions:
- iFVG: 25% of losers (loser MFE p75=1.03R) cross 1.0R favorable before stopping. If we
  scratch those, we save ~25% of losses. Only ~5% of winners would scratch (winner MAE p90=0.82R).
- But this reasoning was flawed — see below.

### Results

| | iFVG baseline | iFVG + be_trail_r=1.0 | ORB baseline | ORB + be_trail_r=1.0 |
|--|--|--|--|--|
| Passes (61 mo) | ~35% (test) | 7/61 (11%) | 12/61 (20%) | 8/61 (13%) |
| Overall PF | 1.31 | **0.97** | 1.10 | **1.057** |
| MLL failures | 0 | 1 | 0 | 0 |

**Both strategies REJECTED** — both metrics worse on both.

---

## Why be_trail_r=1.0 Failed

The flaw in the pre-benchmark reasoning: **MAE from initial entry ≠ retracement from MFE peak.**

Winner MAE p90=0.82R means 90% of winners had their worst adverse dip within 0.82R of
initial entry. This looks "safe" for a 1.0R be_trail. But what matters for the be_trail
mechanism is whether a winner, AFTER going 1.0R favorable, subsequently dips back to the
entry price.

Example path (ORB, entry=20000, stop=19900, target=20250):
1. Price goes 20000 → 20100 (MFE=1.0R → be_trail fires, stop moves to 20000/BE)
2. Price consolidates 20100 → 20000 (pullback to entry — MAE from initial entry = 0)
3. Price would have continued to 20250 (target)
4. With be_trail: stopped at 20000 → scratch. Without: winner.

This "two-thrust" pattern is common in both iFVG and ORB structures. The strategy's
displacement mechanism selects for moves that have already shown one breakout thrust;
many then require a consolidation before the second leg. The be_trail at 1.0R cuts
exactly the consolidation leg.

The distributions confirm: loser-MFE and winner-MAE overlap at 1.0R with no clean
separation. For ORB, loser MFE p75=0.78R < 1.0R (most losers don't reach the threshold),
so the be_trail mostly converts winners to scratches without saving many full-loss losers.

---

## Lessons

1. Any be_trail threshold must clear the winner-MAE distribution measured from the MFE
   peak (not from initial entry). This data is NOT available in the current CSVs —
   it would require tracking the retracement from peak explicitly.
2. For ORB r2.5 (target=2.5R), a be_trail at 1.0R is particularly damaging because
   the strategy needs a 2.5R favorable excursion to win, and many winners have an
   intermediate dip on the way.
3. The MFE/MAE data in research/*.csv is available for future exit-mode designs.
   Key future question: what R-level be_trail would preserve pipeline positivity?
   This requires computing post-MFE-peak retracement distributions, not just MAE from entry.

---

## Files Modified

| File | Change |
|------|--------|
| `app/sim/paper.py` | MFE/MAE tracking, be_trail_r mechanism, `excursions_by_order_id()` |
| `app/backtest/runner.py` | Merge excursions into trade dicts, wire be_trail_r from config |
| `app/bot_config.py` | `be_trail_r: Decimal = Decimal("0")` in StrategyParams |
| `tests/test_mfe_mae.py` | 11 defining-behavior tests (new file) |
| `scripts/analyze_mfe_mae.py` | Per-year distribution analysis script (new file) |
| `backtests/ab_be_trail_r10_ifvg.json` | iFVG + be_trail benchmark result |
| `backtests/ab_be_trail_r10_orb25.json` | ORB + be_trail benchmark result |
| `backtests/ab_orb25_baseline.json` | ORB r2.5 baseline (new reference) |
| `research/mfe_mae_*.csv` | Per-trade excursion data (4 files) |
