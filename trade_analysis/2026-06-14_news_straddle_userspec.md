# News straddle — Lawrence's exact spec (15-min range, ±60t, 3-4R) — raw profitability

**Date:** 2026-06-14 · **Scope:** raw profitability only (NOT combine/funded yet).
**Spec:** 15-min pre-news range; buy_stop=range_high+60t, sell_stop=range_low−60t (OCO);
stop = broken range boundary (R=60 ticks); TP at 3R and 4R; 2022 excluded; no lookahead.
**Data:** `bars/bars_MNQ_dbv_2021_2026.csv` (1-min, MNQ). Events: `data/news_events.csv`
(47 CPI, 45 PPI, 32 FOMC). Script: `scripts/news_straddle_user_spec.py`. Per-event detail:
`research/news_straddle_userspec_events.csv`.

## Critical modeling note (the crux)
Result is sensitive to how the **entry (breakout) bar** is handled. Counting that bar's
raw LOW as a post-entry stop is WRONG (the low printed during the trigger move, before fill)
and gives a false −0.94R catastrophe (~3% win). The fair assumption — judge the entry bar by
its CLOSE, manage subsequent bars by high/low — gives the numbers below, and is identical to
skipping the entry bar (the entry minute almost never closes back beyond the stop). Residual
risk the 1-min data CANNOT resolve: an intra-minute "spike→stop→recover" on the entry bar
would lower the edge. So magnitudes below are an upper-fair estimate; tick/second data around
releases is the decisive confirmation, especially for CPI.

## Results (TP=3R, slip=2t — breakeven WR=25%)
| event | n | win% | PF | totR | by-year totR |
|---|---|---|---|---|---|
| **CPI** | 47 | **62** | **4.91** | **+69.0** | 2021 +13.9 / 2023 +11.7 / 2024 +15.7 / 2025 +16.8 / 2026 +10.9 — **positive every year** |
| PPI | 45 | 31 | 1.44 | +12.7 | 2021 +3.9 / 2023 +0.7 / 2024 +7.7 / 2025 +1.7 / 2026 −1.2 — thin, 2024-driven |
| FOMC | 32 | 25 | 1.05 | +1.0 | 2021 −1.2 / 2023 −0.3 / 2024 +4.8 / 2025 +0.8 / 2026 −3.1 — no edge |

Robust to slippage (CPI at slip=4t: 63% win, +1.49R/trade). At 4R the CPI edge grows
(+1.9R/trade, PF 5.45) since CPI moves are fat-tailed and directional.

## Verdict per event type (independent)
- **CPI — real, year-consistent edge.** Positive all 5 years, 50–83% win vs 25% breakeven,
  PF ~5. This is the finding. The high 3–4R target is what unlocks it: CPI prints produce
  sustained directional follow-through that a 1:1 target (loop's B83) gave back.
- **PPI — marginal / inconsistent.** Overall thin-positive (PF 1.44) but mostly 2024; 2026
  negative. Not reliable standalone.
- **FOMC — no edge.** Win rate = breakeven (25%), PF 1.05, 2026 0/3. Confirms the "FOMC
  fakeout": the 14:00 statement spikes then mean-reverts. Drop it.

## Why this differs from B83 (which REJECTED the straddle)
B83 used tp_r=1.0 (1:1 RR, opposite-leg stop) → needed >50% win, got 43% → negative. This
spec uses a TIGHT stop (R=60t) + 3–4R target → needs only 25% → CPI's 62% wins big. Same
events, opposite verdict, entirely due to the reward target + stop geometry. Lesson: the
straddle's viability is a function of RR, not just "does the break happen."

## Caveats / next steps before trusting size
1. Entry-bar intra-minute path (above) — confirm CPI with tick/second data.
2. Event-date confidence: CPI 2023 confirmed, others BLS-pattern + anchors (vol-expansion
   check validated 123/124). CPI edge holds even dropping 2026.
3. This is RAW profitability only — not yet combine/funded-scaled (sparse: 47 CPI/5yr).
4. MNQ only; not tested on other instruments.

## 1-SECOND CONFIRMATION (B85, 2026-06-14) — CONFIRMED, entry-bar concern resolved
Fetched NQ.v.0 ohlcv-1s for the 47 CPI windows ([-30m,+90m], $1.06; ledger $8.74/$20;
`scripts/fetch_cpi_1s.py` -> `bars/bars_NQ_1s_cpi_windows.csv`) and replayed the straddle
managing from the second AFTER the trigger (`scripts/news_straddle_cpi_1s.py`).

| resolution | 60/3R | 60/4R |
|---|---|---|
| 1-min (fair est.) | 63% win, PF 5.0, +1.51R, 5/5 | 59%, PF 5.57, +1.92R, 5/5 |
| **1-second (accurate)** | **67% win, PF 5.99, +1.68R, 5/5** | **61%, PF 5.88, +1.97R, 5/5** |

The intra-minute "spike→tag stop→recover" path does NOT eat the edge — 1s is slightly BETTER
than the conservative 1-min handling; only 7% of entries are same-second whipsaws.

**Slippage stress (1s, 60/3R), per-fill adverse ticks:** 2t PF 5.99 / 4t 5.86 / 6t 5.64 /
8t 5.53 / 10t 5.43 — all 67% win, 5/5 years. The edge is slippage-immune because wins are
+3R (45 pts); a few ticks barely dent them while the stop is fixed.

### Verdict: CPI breakout straddle CONFIRMED (the session's one real find)
Robust across resolution (1m→1s), target (3R/4R), offset (40/60/80 from the earlier sweep),
slippage (2–10t), and year (5/5). Remaining before live: (1) realistic STOP-ORDER fill on
the print second (we modeled adverse slip up to 10t and it held, but live fills should be
spot-checked); (2) it's sparse (~9/yr) → a supplement/overlay, not a Combine-volume engine;
(3) build as a default-off `news_straddle` engine (CPI-only) + funded/combine framing.

