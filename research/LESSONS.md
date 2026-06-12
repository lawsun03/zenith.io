# Lessons — append-only. Research sessions MUST read before proposing.

Seeded 2026-06-12 from the week's ~25 experiments (details in trade_analysis/):

1. **The iFVG inversion IS the quality filter.** Removing it (T5: PF 0.55) or
   diluting it with OB fallback zones (6 passes -> 1) both fail. Don't propose
   confirmation-loosening variants of the iFVG chain.
2. **Volume is the Combine constraint; PF is the funded constraint.** Pass
   months are the 60-90-trade months; filters that improve PF but cut volume
   lose Combines (frozen-ATR: PF 1.43, passes down; stop-cap: same; ssl15:
   18%). Route PF-heavy ideas to the FUNDED objective instead.
3. **Fixed-point thresholds are regime-fragile** (body 5.0 pts is 0.024% at NQ
   21k, 0.043% at 12k) — the 2022-23 drought mechanism. Normalize new
   thresholds by ATR or %-of-price.
4. **Day-level regime gates cannot time the engines** (both polarities tested):
   iFVG droughts are structure-poverty, not low daily range (2025-04: huge
   ranges, 4 iFVG trades, ORB +$7k). Month-level complementarity is only
   separable ex-post; two accounts harvest it, one account cannot.
5. **One shared loss budget couples engines exactly where their value is being
   uncorrelated** — simultaneous-combined, lower sizing, and buffer-off all
   lost to the better solo. Don't propose one-account engine blends.
6. **External claims have failed to transfer 3-for-3** (Revelio: long-only,
   sweep+BOS-simpler, bias filter). Treat web-sourced strategies as cheap
   falsification targets with explicit priors, not recipes.
7. **The displacement event arrives one bar late (b3 close).** Any detector
   keying off displacement must evaluate against state snapshotted at b2's
   OPEN (chop_breakout shipped two suppression bugs from this; both pinned by
   regression tests). Watch this class in any new detector.
8. **Tops stall, bottoms sweep.** Both engines' edges are long-biased
   structure; short-side fade proposals (VWAP bands: all 12 cells PF <= 1.0)
   have a strong negative prior on NQ 5min.
9. **Wide swing-anchored stops are load-bearing for the Combine objective**
   (ssl15 -> 18%; max_stop_atr caps -> passes down). The "unrealistic" r3.5
   target is mostly cosmetic: real exits are BE-scratches and flatten-harvests
   (live specimen 2026-06-12: +43pt flatten harvest).
10. **Process kills alpha too:** the 7:22 restart ate the day's ORB breakout
    (one-shot/day + warmup staleness). Restarts only while flat + market
    closed.
11. **Tooling lessons:** PowerShell -replace mangles Unicode in repo files;
    PowerShell here-string commit messages must not contain double quotes;
    journal signal history is session-scoped (parity must run same-day);
    bars CSVs are vendor-labeled at bar CLOSE time; v-rolled (NQ.v.0)
    continuous is the house data convention (c.0 has thin expiry Fridays).

Appended 2026-06-13 (B4 weekly forensics):

12. **The trades.csv `slippage` column is plan-deviation, not execution
    slippage.** `signal_entry` is the FVG proximal edge (composer.py:398) and
    market entries fill at confirmation-bar close, so the column reads 100+ pts
    in vertical moves while real fill-vs-market slippage is ~2 ticks (n=10 week
    of 06-08: mean 0.78 pts). Don't gate or grade anything on that column until
    B13 splits it.
13. **Roll-week fetch trap:** `fetch_bars.py` resolves the symbol to the
    CURRENT front contract for the whole lookback — on 06-12 the same command
    returned M26 at 18Z and U26 (+292 pts) at 23Z. Parity bars must be archived
    same-week; post-roll they are unrecoverable from the free API.
14. **Tracked runtime ledgers get wiped by git tree-restores** (22 trades.csv
    rows lost 06-10..12 to a `reset --hard`-class restore; daily untracked
    files survived). Resolve the tracked-trades/ policy (B12) before trusting
    the rolling ledger for analysis.
15. **Multi-instrument excursion rows 06-07..06-11 are unusable** — the live
    ExcursionTracker had no instrument filter, so MGC/MES/MNQ bars cross-
    contaminated mfe/mae/outcome (B11 fixes; MNQ-only rows 06-12+ are clean).
