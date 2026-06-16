# B99 — ORB target-R sweep (1.0 / 1.5 / 2.0 / 2.5R)

**Date:** 2026-06-15 · **Session:** wk7-b99 · **Engine:** ORB (9:30 + 15min OR), MNQ 5min
**Data:** `bars/bars_MNQ_dbv_2021_2026.csv`, 2022 holdout EXCLUDED (1030 trades, 2021/23/24/25/26)
**Baseline:** shipped `orb_r_multiple=2.5`

## Motivation

Lawrence reviewed a live wide-OR ORB long (OR 133.75pt, entry 45pt above OR high → stop
178.75pt, 2.5R target = 447pt) and asked whether NQ even moves that far intraday. A
non-authoritative 1-min intuition sim said 2.5R hits ~12% of the time. This re-runs the
question on the OFFICIAL backtester.

## PRIMARY — per-R trade economics (risk 0.75%, partial_r=0)

| R   |  n   | win% |  PF  | meanR | exp$/tr | tr/mo | target% | stop% | EOD% |  net$  |
|-----|------|------|------|-------|---------|-------|---------|-------|------|--------|
| 1.0 | 1030 | 54.0 | 1.21 | 0.083 |  31.7   | 21.0  |  44.7   | 36.6  | 18.7 | 32,699 |
|**1.5**|**1030**|**48.9**|**1.24**|**0.104**|**47.9**|**21.0**|**29.2**|**40.4**|**30.4**|**49,355**|
| 2.0 | 1030 | 46.0 | 1.20 | 0.090 |  43.4   | 21.0  |  17.5   | 42.5  | 40.0 | 44,670 |
| 2.5 | 1030 | 45.0 | 1.19 | 0.089 |  43.8   | 21.0  |  11.2   | 43.2  | 45.6 | 45,097 |

**Lawrence's intuition is confirmed:** at the shipped r2.5, the target is hit on only
**11.2%** of trades (≈ the ~12% intuition figure); 43% stop out, 46% close as day-end
flattens. Raising reachability lifts target-hit% monotonically (r1.0: 44.7%).

**r1.5 is the expectancy winner:** highest PF (1.24), highest mean-R (0.104), highest
$/trade ($47.9, +9% vs r2.5's $43.8), highest net. The fat tail of r2.5 does **not** win —
beyond r1.5 the extra target distance is reached too rarely to pay for the lower win rate.
r1.0 over-shoots the other way: 54% win but the +1R wins are too small, so expectancy is
the worst of the four.

(mean-R normalised by the average stop-loss $ on this run; expectancy in $ is the metric.)

## Overfit guard — per-year PF / win% / net$

| year |      r1.0       |      r1.5       |      r2.0       |      r2.5       |
|------|-----------------|-----------------|-----------------|-----------------|
| 2021 | 1.36 / 57 / 6.1k| 1.60 / 55 /11.0k| 1.54 / 53 /10.1k| 1.53 / 53 /10.0k|
| 2023 | 1.00 / 51 /-0.0k| 1.04 / 45 / 2.1k| 1.17 / 44 / 9.0k| 1.17 / 43 / 9.1k|
| 2024 | 1.23 / 54 / 8.6k| 1.28 / 49 /14.1k| 1.20 / 44 /12.6k| 1.20 / 42 /13.7k|
| 2025 | 1.36 / 55 /15.3k| 1.26 / 49 /15.4k| 1.14 / 46 / 9.4k| 1.15 / 45 /10.3k|
| 2026 | 1.12 / 53 / 2.7k| 1.22 / 50 / 6.6k| 1.13 / 46 / 3.7k| 1.07 / 46 / 2.0k|

r1.5 is **positive in every year** (PF 1.04–1.60) and never the weakest cell. r1.0 has a
dead 2023 (PF 1.00, −$30). r2.5 is weakest in the most recent year (2026 PF 1.07). r1.5 is
the most year-stable choice — not a single-year artifact.

## Funded objective — XFA net payouts (funded_sim on the r0.75 equity curve)

| R   | h0 net | h200 net | h400 net |
|-----|--------|----------|----------|
| 1.0 | 33,548 | 31,800   | 30,936   |
| 1.5 | 55,388 | 56,269   | 50,974   |
| 2.0 | 51,793 | 53,140   | 51,073   |
| 2.5 | 56,112 | 55,775   | 54,612   |

On funded payouts r1.5 and r2.5 are essentially **tied** (~$56k at h200). r2.5's fat tail
makes it marginally more robust to the pessimistic haircut (h400: $54.6k vs r1.5 $51.0k);
r1.5 is marginally ahead at h200. r1.0 is clearly worst (low expectancy → low payout).

## Combine objective — pass rate & run PF (risk 1.25%, risk-limits ON)

| R   | passes /61 | run PF | worst-month maxDD |
|-----|------------|--------|-------------------|
| 1.0 |   11 (18%) |  1.13  |  1,993            |
| 1.5 |    8 (13%) |  1.05  |  2,358            |
| 2.0 |   11 (18%) |  1.06  |  4,001            |
| 2.5 |   10 (16%) |  1.06  |  4,098            |

**No R meets the 13/61 success criterion.** The combine objective does not favour any
target R — the risk-limited harness flips the ranking (r1.0 best PF here, r1.5 worst),
consistent with Lesson 2 (volume, not PF, is the Combine constraint; all four fire the
same 21 trades/mo). Notably the lower-R variants cut worst-month drawdown roughly in
half (r1.0/1.5 ≈ $2.0–2.4k vs r2.0/2.5 ≈ $4.0k) — the smoother curve is real.

## SECONDARY items — flagged, not run

Per the B99 spec ("flag + skip if it needs a real build, do NOT force"):
- **Boundary stop-entry** (vs current breakout-close confirm): needs a new entry path in
  the ORB engine + composer. Real build. **Skipped** — queue as its own item if pursued.
- **OR-width filter** (skip/cap when OR > Nth pct or k·ATR): needs a detector-side gate
  and a width distribution study. Real build. **Skipped.** The live wide-OR trade that
  motivated this is exactly the case it would target; worth a dedicated cheap Phase-1 cut.

## Verdict — CANDIDATE (r1.5, funded objective; default-off recommendation)

**The fat tail does not win.** r1.5 beats r2.5 on trade expectancy (+9%) and on year-
stability, ties it on funded payouts, and roughly halves worst-month drawdown — a smoother
equity curve for the same money. It does **not** beat r2.5 on the Combine (none do).

Recommendation for Lawrence (Monday decision): for the **funded** phase, `orb_r_multiple=1.5`
is a dollar-neutral, lower-variance, higher-win-rate alternative to the shipped 2.5. For the
**Combine** phase there is no reason to change off 2.5 (no R clears the bar). This is
analysis-only — nothing enabled, `bot_config.json`/`.env` untouched.

**Artifacts:** `research/equity_b99/{orb,trades}_r{1p0,1p5,2p0,2p5}.csv`,
`_funded_*.log`, `_combine_*.log`; `scripts/_b99_analyze.py`.
