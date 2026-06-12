# 5-year OOS walk-forward on fresh Databento data (2026-06-12)

Frozen-config walk-forward: the two validated engines, params untouched
(iFVG frontier 1.25% r3.5 + deployed overrides; ORB 9:30+15min r2.5),
run over `bars/bars_MNQ_dbv_2021_2026.csv` — 5 years of freshly pulled,
quality-verified NQ.v.0 1-min data (2021-06 → 2026-06, 61 monthly
Combine attempts each). 2021-H2–2023 is genuine out-of-sample: never
seen by any tuning, spanning the melt-up top, the 2022 bear, and the
2023 recovery. UI: `wf5y_ifvg`, `wf5y_orb`.

## Consistency check: PERFECT reproduction

On the overlapping 2024–2026 months, both engines reproduce the prior
campaigns **month-for-month and dollar-for-dollar** (e.g. iFVG 2024-03:
70 trades +$14,022.21 — exact; ORB passes Feb/Apr/Jul/Oct-24,
Apr/Oct-25, Feb/Apr-26 — identical). Combined with the 100.00% OHLC
lineage match, the entire chain — data source, loader, aggregation,
engines, harness — is deterministic and reproducible end-to-end.

## Pass rate by year (monthly Combine attempts)

| year | iFVG frontier | ORB r2.5 | regime |
|---|---|---|---|
| 2021 H2 (7mo) | 2/7 (29%) | 0/7 | melt-up top |
| 2022 | **0/12** | 3/12 (25%) | bear, high vol |
| 2023 | 1/12 (8%) | 1/12 (8%) | grind recovery |
| 2024 | 4/12 (33%) | 4/12 (33%) | (train yr) |
| 2025 | 4/12 (33%) | 2/12 (17%) | (test) |
| 2026 (6mo) | 2/6 (33%) | 2/6 (33%) | (test) |
| **5y total** | **13/61 (21%)**, PF 1.18 | **12/61 (20%)**, PF 1.10 | |

**Zero MLL failures in all 122 month-attempts across every regime** —
the DLL + soft-buffer + rollover governor is genuinely regime-robust;
worst min-equity 48,131. Failed attempts cost subscriptions, never
accounts.

## Reads

1. **Both edges are regime-dependent, concentrated in 2024–2026.**
   2022–2023 is near-dead for both (iFVG 1/24, ORB 4/24). The
   two-account union math (~55%/mo) holds in regimes like the last 2.5
   years; in a 2022-style regime, expect months of subscription burn
   with no passes (but no busts).
2. **A mechanical contributor to iFVG's 2022–23 drought:** the deployed
   thresholds are FIXED-POINT (min_absolute_body 5.0, stop_buffer 3.0)
   while NQ traded ~11–16k in 2022–23 vs ~21k+ now — the same 5.0-pt
   body filter was ~2× stricter in relative terms, mechanically
   suppressing signals (most 2022–23 months show 2–8 trades). An ATR-
   or %-of-price-normalized threshold would equalize this; flagged as a
   structural follow-up, NOT tuned here (no-sweep discipline).
   Notably 2021-10/11 and 2023-01 — the OOS months where volatility
   made 5-pt bodies common — were signal-rich and PASSED.
3. ORB's weakness in 2021/2023 is different — normal trade counts but
   PF < 1 (the open-range breakout simply didn't follow through in
   those tapes). Its 2022 (3 passes) partially complements iFVG's 0.
4. **Interpretation for the funded plan:** per-attempt pass probability
   is conditional on regime: ~33%/mo per engine in trending-vol years,
   ~5%/mo in dead years. The plan survives this because attempts are
   capital-safe (0 busts in 122 months) — the cost of a dead regime is
   time + subscriptions, not the account.

## Caveats

- 2021–23 simulated on v-rolled CONTINUOUS NQ prices; micro-contract
  fills/commissions approximated as today. Roll gaps are inside the
  data (v-roll minimizes but doesn't eliminate).
- Same harness optimism caveats as all campaigns (fill-granularity
  equity, no latency/book depth).
- Vendor flags 3 degraded days in 5y (2021-12-05, 2022-01-02,
  2025-09-17) — immaterial at monthly granularity.
