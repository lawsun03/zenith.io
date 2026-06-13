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

Appended 2026-06-13 (B7 kz_levels benchmark):

28. **Session-range sweep frequency is ~10x lower than swing-based sweeps — PF alone doesn't pass the Combine.**
    kz_levels (London/NY AM/NY PM H/L, swept during next session) produced ~7.4 trades/month vs iFVG's ~70/month, despite matching PF (1.18 both). 0/61 combine passes vs 13/61 (21%) baseline. Volume is the Combine constraint (Lesson 2): even a correct edge can't compound to $3,000 in a month if it fires 7 times. Session ranges lock once per session (15 potential sweeps/week before the displacement+FVG filter), a structural ceiling unrelated to parameter choice.

29. **KZ session levels are tested in the dead zone, not during the next session.**
    London range (02:00–05:00 ET) locks at 05:00 ET. Price tests that level at 05:15 ET — outside NY AM (08:30–11:00 ET) — and the sweep is consumed before the next trading window opens. The fix (only emit/consume levels when inside a trading window) lets levels persist across the gap and become available for intra-session sweeps. When building session-level sweep detectors: levels must outlive the gap between sessions or they're silently discarded. Same problem would affect any "prior-session extreme" detector that deletes the level on first touch.

30. **`enabled_killzones=["all"]` maps to `all_day()`, which never closes.**
    The combine harness passes `enabled_killzones=["all"]` by default. `all_day()` (00:00–23:59:59 ET) never satisfies `in_killzone(ts, [zone]) is None`, so session ranges are accumulated but never finalized. Any engine that needs named sessions (London, NY AM, NY PM) must ignore `enabled_killzones` and use `default_killzones()` unconditionally — the harness's "all" flag is designed for the composer's entry gate, not for session-range bookkeeping.

Appended 2026-06-13 (wk1-r1 research session):

31. **The iFVG short side is structurally loss-making on NQ 5min over 5 years.**
    From 5y MFE/MAE data (partial_r=0): long trades PF=1.136 (n=1226), short trades PF=0.960
    (n=1251). The short side drags overall PF from 1.136 to 1.043. This is not regime-dependent
    (all years show longs > shorts). "Tops stall, bottoms sweep" (Lesson 8) is the mechanism.
    Long-only iFVG lifts PF by +9% but cuts volume to ~25/month — too sparse for Combine, but
    relevant for the funded objective where PF matters more than volume.

32. **Naive `orb_max_trades_per_day=2` is wrong — use on_stop_loss re-arm instead.**
    With max_trades=2, the detector fires the second signal at the SECOND BAR above the ORB
    range (still during the first position's hold), wasting the slot and missing the genuine
    retest after the stop. Correct implementation: ORBComposer.on_stop_loss() resets
    `detector._fired=0`, allowing one more signal only after a confirmed stop-out.
    ExecutionEngine already calls `runner.composer.on_stop_loss()` on stops — the wiring exists.

Appended 2026-06-13 (wk1-r2 research session):

33. **ORB short side is profitable but materially weaker than long side (PF 1.109 vs 1.320
    over 5y, n=488/542).** The "tops stall, bottoms sweep" pattern (Lesson 8) extends to ORB
    but is less severe than iFVG (where shorts are outright loss-making at PF 0.960). Long-only
    ORB is a funded-objective PF improvement hypothesis, NOT a loss-removal — both sides are
    positive. Route to funded objective only (long-only ORB is ~12 trades/month, too sparse
    for Combine).

34. **iFVG signals in the noon window (12:xx ET) and NY PM tail (14-15:xx ET) are
    negative-expectancy drags.** 12:xx: PF=0.591 (n=73); 11:xx: PF=0.829 (n=109); 14:xx:
    PF=0.946; 15:xx: PF=0.763. These occur in the benchmark because `enabled_killzones=["all"]`
    is the combine-harness default. The London (05:xx 1.433, 04:xx 1.307) and NY AM (10:xx
    1.235, 09:xx 1.178) windows carry the quality edge. Restricting to named sessions
    (London + NY AM) for the funded phase removes ~265 negative-expectancy trades over 5y
    without new code.

Appended 2026-06-13 (B15 long-only iFVG funded):

36. **Blocking loss-making short iFVG signals is sufficient to make the funded pipeline self-sustaining.**
    Long-only iFVG (allowed_sides=long) at r1.0/r1.25 achieves sust 1.12x–1.13x vs full iFVG's 0.54x at the same risk level. No new code — the existing allowed_sides parameter handles it. The mechanism: removing shorts (PF 0.960) eliminates the equity curve drag that causes accounts to bust near MLL floor.

37. **Named-session killzones (london+ny_am+ny_pm) have a 65% long signal bias vs 49.5% in all_day config.**
    The London session (02:00–05:00 ET) generates predominantly bullish setups in NQ (overnight continuation), while the negative-expectancy overnight/pre-dawn hours removed by named sessions had more short signals. Consequence: "long-only at named sessions" retains ~65% of full-iFVG volume (3,928 vs 6,043 trades), not the ~50% expected from all_day analysis. Long-only at named sessions is NOT as volume-sparse as the all_day MFE/MAE analysis suggested.

Appended 2026-06-13 (B16 inversion bar quality gate):

38. **Downstream signal filters are dominated by upstream displacement body filters.** B16 tested
    inversion_min_body_r=0.15 (gate: inversion bar body >= 15% of stop_dist). At the deployed MNQ config
    (min_absolute_body=5.0 pts, stop_buffer=3.0), every displacement bar already has body >= 5.0 pts
    while typical stop_dist is 5-20 pts — so 0.15 × stop_dist < 5.0, and the gate NEVER activates.
    Result: 0 trades blocked, identical PF and funded metrics (6,043 trades, PF 1.064, sust 0.54x).
    Rule: for a downstream quality gate to add marginal value, its effective threshold must exceed the
    upstream body floor: inversion_min_body_r × typical_stop_dist > min_absolute_body. At deployed
    settings, this requires min_body_r > 0.33-1.0+. The 0.15 threshold is calibrated for the default
    config (min_absolute_body=1.0, stop_buffer=0.30); it is a no-op at deployed MNQ settings.

Appended 2026-06-13 (B14 ORB reentry after stop):

35. **ORB reentry after a confirmed stop adds volume but lowers per-trade quality.** At risk 1.0%: +44% trades (1,652 → 2,382), +35% funded $/month ($1,911 → $2,578), +57% combine passes (7 → 11 of 61), but PF drops -5% (1.213 → 1.153). Reentry signals are stop-reversal entries — structurally weaker than first-breakout signals. Enable only in the funded phase where volume helps pipeline throughput; the PF cost is acceptable for absolute-payout maximization.

Appended 2026-06-13 (B18 named-sessions killzone benchmark):

40. **Per-hour PF rankings from partial_r=0 MFE/MAE data don't transfer to the deployed
    partial_r=1.5 funded config.** B18 tested London+NY AM (removes NY PM and all overnight)
    vs London+NY AM+NY PM (standard named sessions) vs all-day deployed config. Config B
    (standard named sessions, which excludes overnight and noon) has LOWER PF (1.049) than
    all-day (1.064), reversing the direction of the per-hour hypothesis. The overnight/pre-
    market sessions excluded by named-session filtering make net-positive contributions at
    partial_r=1.5. The deployed `enabled_killzones=["all"]` is near-optimal; adding named-
    session filtering hurts the funded pipeline. Always benchmark session filters against the
    deployed config directly.

41. **Config A (London+NY AM only) achieves +8.7% funded net payouts and +13% sustainability
    with 42% fewer trades vs all-day, but PF improvement is only +1.1% (below the +5%
    threshold).** This is a real but marginal improvement — notable that 58% of the trades
    carry 108% of the net payout value. The missing NY PM and overnight signals don't just
    reduce losses; they also reduce the combine throughput needed to replenish busted XFA
    accounts. The pipeline remains negative at 0.606x regardless of session filtering.

Appended 2026-06-13 (B17 ORB long-only funded benchmark):

39. **PF improvement from a profitable-but-weaker signal class doesn't justify the volume cost in the funded pipeline.**
    Long-only ORB (blocking shorts PF=1.109) cuts ~47% of combine attempts and hurts all metrics at all risk levels:
    r1.0: $/month -39% ($1,181 vs $1,943), sust -11pp (0.737x vs 0.847x). The contrast with B15 (iFVG long-only,
    success) is diagnostic: B15 removed LOSS-MAKING shorts (PF 0.960, subtracting from the equity curve); B17 removes
    PROFITABLE shorts (PF 1.109, contributing net positive P&L). Removing profitable trades always hurts pipeline
    throughput unless the PF improvement is large enough to reduce bust frequency faster than it reduces pass frequency.
    At PF 1.109 → 1.320 (+19%), the improvement is insufficient. Rule of thumb: only block a signal class if it is
    individually loss-making (PF < 1.0) over a multi-year window.

Appended 2026-06-13 (wk1-r3 research session):

42. **The B3 pipeline model uses per-year equity CSVs (equity_b1/), NOT flat 5-year CSVs.**
    Per-year stitching restarts equity from $50k at each year boundary, producing ~50% fewer
    funded busts than flat 5y CSVs (ORB r1.0: 27 busts per-year vs 59 busts flat 5y). This
    means flat-CSV standalone sust (e.g., B15 LongOnly iFVG sust 1.12x) is NOT directly
    comparable to B3 two-phase sust (1.26x). Any new B3-style pipeline benchmark (B20, B21)
    must generate per-year equity CSVs in the equity_b1/ format to be comparable.

43. **~75% of ORB winners hit the 2.5R target exactly; ~25% are profitable day-end flattens.**
    ORB winner MFE p75=2.50R (= target). This distribution structure means an excursion-
    ladder partial at 2.0R would only affect trades that reach 2.0R then pull back before
    hitting the target — a minority of the 37% that ever reach 2.0R. The path-through-peak
    behavior is not captured by our current MFE/MAE infrastructure (which records entry-based
    MFE, not peak-relative retracement). Any ORB excursion-ladder proposal needs per-trade
    path-through-MFE-peak data before it can be accurately evaluated.

Appended 2026-06-13 (B20 iFVG Combine → LongOnly-iFVG Funded two-phase pipeline):

45. **Standalone sust of a funded config is not a reliable predictor of two-phase sustainability.**
    LongOnly-iFVG funded shows standalone sust 1.12x (B15 flat-5y) but two-phase sust 0.65x (B20 per-year)
    when iFVG combine is Phase A. The discrepancy: standalone sust uses the same strategy as combiner (56 passes/50 busts),
    while two-phase uses iFVG combine (34 passes) vs LongOnly funded busts (52). Rule: always model the pipeline explicitly
    with Phase A passes vs Phase B busts — standalone sust is only valid if the SAME strategy runs combine and funded phases.

46. **High-volume funded strategies cycle XFA accounts faster, requiring more combine passes to sustain.**
    LongOnly-iFVG funded creates ~53 XFA accounts over 5y (avg 24d each) vs ORB funded's ~28 accounts (avg 45d each).
    The iFVG combine produces 34 passes — enough for ORB's 27 busts (1.26x) but not LongOnly's 52 busts (0.65x).
    When evaluating a funded strategy for the two-phase model, prefer slower-cycling strategies (fewer total accounts, longer
    durations) unless the combine phase can supply passes at a matching rate.

Appended 2026-06-13 (B19 long-only iFVG + London+NY AM funded benchmark):

44. **NY PM signals (13:xx-16:30 ET) are loss-making in LONG-ONLY iFVG and safely removable.**
    B19 tested london+ny_am only (removes NY PM) on the already-long-only, named-session B15
    config. At r1.25: PF +4.6% (1.173 vs 1.121), sust +42% (1.600x vs 1.13x), net payouts
    nearly identical ($184k vs $186k). The 33% fewer trades (2,645 vs 3,928) contribute
    disproportionately few profits: removing them barely touches gross payouts but dramatically
    reduces XFA bust events. B18's counter-finding (removing named sessions hurts all-sides
    funded) does NOT apply here: B18 removed overnight/pre-market sessions with positive PF
    contributions from shorts; B19 removes only NY PM (all-sides PF 0.906 < 1.0), which is
    net-negative for both long and short signals. Rule: when a session's all-sides PF < 1.0,
    it is safely removable from any config — even one that already benefits from named sessions.
