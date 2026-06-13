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

Appended 2026-06-13 (B1 funded-objective scoring):

16. **All full-sizing bench variants are pipeline-negative on the funded objective.**
    At r1.25, every config needs more funded accounts than the Combine-pass density
    can supply (e.g., trail_1r: 46 XFA busts vs 21 Combine passes over 5 years).
    "Net payouts" of $75–100k assume an infinite account supply; model the pipeline
    explicitly before quoting funded-objective numbers.
17. **ORB's Combine pass rate is 2–3× better than iFVG variants** (39–57% vs
    16–21%) across all sizing levels. ORB's cleaner breakout structure produces
    better risk/reward on the Combine timescale. This is the structural funded-
    objective advantage of ORB over the iFVG chain.
18. **Only ORB at reduced sizing produces a self-sustaining funded pipeline.**
    ORB r0.5: 8 Combine passes vs 6 XFA busts (h200) — genuinely positive.
    ORB r0.75: 15 vs 14 — borderline. Above r1.0: pipeline deficit grows.
    Recommended Phase-B config: ORB r0.75 (sustainable, 2022-confirmed, PF 1.15+
    across all years).
19. **Trail-1R concentrates gains in single outlier days and produces the worst
    pipeline sustainability.** 2024's $49k net looks great in isolation but the
    stitched pipeline (21 passes vs 46 busts at r1.25) is the worst ratio of any
    variant. Route trail-1R ideas to the research-only bin unless pipeline
    sustainability is explicitly modeled.

Appended 2026-06-13 (B3 two-phase pipeline policy):

23. **Combine speed is more valuable than combine pass rate for pipeline economics.** iFVG generates funded accounts in 28.6 trading days (avg 6d/attempt, 4.76 attempts) vs ORB r0.75's 68.6 days (35.5d/attempt, 1.93 attempts) -- slower cycle but higher per-attempt pass rate (52% vs 21%). Because cost and time compound, the FASTER combine wins the pipeline clock even at higher total attempt count. Two-phase (iFVG Combine + ORB r1.0 Funded): $393/mo vs single-phase ORB r0.75: $333/mo (+18%).
24. **Sustainability ratio (combine passes / XFA busts over same 5y window) is the key pipeline filter.** The highest single-phase $/month configs (ORB r1.25: $596, ORB r1.0: $405) are pipeline-negative (sust 0.89x and 0.85x) and collapse long-run. Only filter on sust >= 1.0 before comparing $/month. The iFVG combine + ORB r1.0 funded pair achieves sust 1.26x while still beating all sustainable single-phase options.
25. **b1_results.json ORB r0.75 entry is corrupted (only 2022 holdout data).** The 5y equity CSVs (orb_r0p75_202{1,3,4,5,6}.csv) are valid; re-stitching them gives the correct numbers matching the B1 journal (15 passes / 14 busts / $35.7k net at h200). The corruption was likely caused by writing only the 2022 confirmatory-run result to b1_results.json without the full 5y scoring. Regenerate b1_results.json for ORB r0.75 before quoting B1 numbers from the JSON file.

Appended 2026-06-13 (B2 MFE/MAE excursion ladder):

20. **be_trail_r=1.0 kills "two-thrust" winners and hurts both objectives on both
    strategies.** iFVG: 7/61 passes (11%), PF 0.97 vs ~35% baseline, PF 1.31.
    ORB: 8/61 (13%), PF 1.057 vs 12/61 (20%), PF 1.10 baseline. The mechanism
    moves the stop to entry when MFE reaches 1.0R — any winner that consolidates
    back to entry before its final push to target becomes a scratch. Don't propose
    be_trail_r without first confirming that loser-MFE and winner-MAE distributions
    are cleanly separated at the candidate threshold.
21. **Adverse excursion from entry is not the same as retracement from MFE peak.**
    MAE p90=0.82R for winners means 90% of winners had their worst dip within 0.82R
    of entry — it does NOT mean those winners survived a 0.82R retracement from their
    peak before recovery. A winner can have MFE=2.0R, then pull back 1.0R to entry
    (MAE=0 from initial entry perspective), and be stopped at BE. The path through
    the MFE point is what matters for trail mechanisms, not the raw MAE number.
22. **MFE/MAE infrastructure now available.** BacktestResult.trades includes
    mfe_pts, mae_pts, r_mfe, r_mae for every closed trade. Analysis CSVs are in
    research/mfe_mae_*.csv. The be_trail_r parameter is wired and default-off —
    future exit-mode candidates can use it without new code.

Appended 2026-06-13 (B5 ORB prior-day-range qualifier):

26. **Prior-day range does not predict ORB trade quality.** The 60-day median
    gate cuts ~50% of ORB days with no improvement in win rate or PF (combine:
    12/61, PF 1.10 both ways; funded PF +2.4% only). The "2024 PF 1.45 regime
    gate" was a regime_switch artifact (gating whole-day strategy routing, not
    individual trade quality). Day-level range gates join day-level regime gates
    (Lesson 4) as mechanisms that fail to time intraday ORB quality.

Appended 2026-06-13 (B6 ATR-normalized displacement thresholds):

27. **The 2022-23 iFVG drought is structure-poverty, not threshold sensitivity.**
    B6 tested body_pct=0.000238 — at NQ 12k that relaxes the floor from 5.0 to
    2.86 pts (a 43% reduction) — yet 2022 monthly trade counts are IDENTICAL to
    baseline (2-18 trades). The months with 2-5 trades don't become 30-trade
    months when the threshold loosens; the sweep+inversion setups simply don't
    occur in 2022. Lesson 3 ("fixed-point thresholds are the drought mechanism")
    is wrong about causation; the regime-fragility of fixed thresholds is real
    but it is not what drives the drought. Update prior: threshold-loosening is
    not a drought fix — only regime-level structural changes can wake up those
    months. Additional finding: extra marginal displacements (bodies just above
    the new, looser floor) consume composer sweep states and degrade high-quality
    months (2021-11: 65->50 trades, PF 1.53->1.07). Features with pct>0 are
    available default-off but should not be enabled without a clear positive
    signal from the benchmark.
