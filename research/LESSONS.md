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

Appended 2026-06-13 (B21 iFVG Combine → ORB-reentry Funded two-phase pipeline):

47. **ORB-reentry at low risk (r0.75) is the funded-phase optimum in the two-phase pipeline.**
    iFVG r1.25 Combine + ORB-reentry r0.75 Funded = $497/mo, sust 2.62x — beats B3's best pair (ORB r1.0, $393/mo, 1.26x) by +26% $/mo and +108% sustainability. At r0.75, reentry adds +23% per-account earnings ($3,131 vs $2,547 for plain ORB r0.75) while keeping funded busts nearly identical (13 vs 14). The iFVG combine's 34 passes sustain only 13 busts comfortably. At r1.0/r1.25, reentry creates 41/48 busts — far more than the 34 iFVG passes can cover, making those configs pipeline-negative. Rule: in a constrained-pipeline model, match funded bust frequency to Phase A pass frequency before optimizing $/month.

48. **ORB-reentry adds 23% per-account earnings at r0.75 without proportionally increasing bust frequency.**
    The reentry mechanism (second signal after a confirmed stop) produces a second, lower-quality breakout entry that increases total account earnings while consuming account losses more slowly at conservative sizing. At r0.75, the per-account loss when the account busts is smaller, so the reentry-induced losers don't accelerate bust timing as much as at r1.0+. This sizing asymmetry means the optimal funded configuration differs from the optimal standalone configuration: standalone optimum is r1.0 (B14), two-phase optimum is r0.75.

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

Appended 2026-06-13 (wk1-r4 research session):

49. **First iFVG signal of each day (rank-1) has structurally higher PF (1.129) than the full
    aggregate (1.043).** Rank-2 signals are loss-making (PF=0.970); the primary drag is rank-2+
    shorts (PF=0.841 over 5y, n=731). Rank-2+ longs remain profitable (PF=1.139). A hybrid that
    takes all rank-1 signals plus rank-2+ longs only achieves PF=1.133 with 29.1/month volume —
    similar PF to B15 long-only (1.136) but 43% more volume. Mechanism: the second short signal
    of the day fires into an already-tested level; the structure that made the first iFVG worth
    trading is consumed by the first entry.

50. **Research benchmark config parity gap**: all B1-B21 funded benchmarks ran with BotConfig
    defaults (bot_config.json was empty at the time): partial_profit_r=0, enabled_killzones=
    ["london","ny_am","ny_pm"], swing_stop_lookback=0, target_clarity_mode="reject",
    ifvg_rule_f_enabled=True, ifvg_entry_mode="ifvg_edge". The deployed live bot_config.json
    (as of 2026-06-13) has: partial_profit_r=1.5, killzones=["all"], swing_stop_lookback=30,
    target_clarity_mode="off", rule_f_enabled=False. These 5 parameter differences mean the
    B1-B21 benchmark numbers (PF, $/month, sust) do NOT reflect live behavior. All future
    equity_export benchmarks must pass `--partial-r 0 --set swing_stop_lookback=0` (and other
    explicit overrides) to maintain within-series consistency with prior results — OR explicitly
    document that a new research baseline is being established.

Appended 2026-06-13 (B22 ORB range_minutes sensitivity):

51. **The ORB 15-min opening range window is near-optimal for NQ 5min.** B22 tested 10-min and
    30-min alternatives: 10-min achieves marginally better run PF (+3pp, 1.09 vs 1.06) and funded
    $/month (+9%) with identical combine passes (10/61); 30-min is significantly worse (6/61 passes,
    PF 0.96, -42% funded $/mo). Neither meets the 13/61 combine success criterion. The range_minutes
    parameter joins the parameter plateau — it is not a productivity lever. Additional structural note:
    the 10-min window reverses the long/short PF split (10-min longs PF 1.15 > shorts 1.03; 15-min
    shorts PF 1.13 > longs 0.99). The first 10 minutes of regular trading appear to capture cleaner
    directional breakout structure on NQ; the 11-15 minute extension adds weaker long-side entries.

Appended 2026-06-13 (B23 iFVG daily signal cap):

52. **Temporal rank filtering (cap=1 per day) is strictly dominated by structural side+session
    filtering (B19 long-only+london+ny_am) on the funded objective.** Cap=1 isolates rank-1 signals
    (PF=1.129) but still takes loss-making shorts when they happen to arrive first — the rank-1 short
    PF is positive (~1.12 blended) but below long-only aggregate (~1.173). B19 removes ALL shorts (PF=0.960)
    and ALL NY PM (PF=0.906), producing better quality at similar volume. At r1.25: cap=1 PF=1.137,
    sust=1.170x vs B19 PF=1.173, sust=1.600x. Rule: temporal rank filters are not a substitute for
    structural side/session filters when the negative-quality signals arrive in unpredictable rank order.

Appended 2026-06-13 (wk1-r5 research session):

53. **`ifvg_entry_mode="close"` in the deployed bot has never been benchmarked against the research
    baseline `"ifvg_edge"`.** All B1-B23 iFVG benchmarks used "ifvg_edge" (wait for retrace to FVG
    proximal edge — limit-like). The deployed bot uses "close" (fill at inversion bar close — 100%
    fill rate but entry is deeper inside the FVG zone, further from the proximal edge and structural
    support). Trade economics differ: "close" gives larger stop distance (entry further from sweep
    extreme) and a harder-to-reach target at same r_multiple. "ifvg_edge" may miss some trades
    (price runs without retracing) but enters at better structural price with smaller stop. Neither
    mode has been directly compared on combine or funded objectives. B24 resolves this.

54. **Rule F (`ifvg_rule_f_enabled`) is a structural no-op when `ifvg_entry_mode="close"`.**
    Rule F cancels an armed zone if TP1 is hit before entry fills — but in "close" mode, the signal
    is returned immediately without arming a tracker (engine.py:308-310), so Rule F never fires.
    The deployed bot's `ifvg_rule_f_enabled=False` setting is irrelevant to actual trade behavior
    since the deployed `entry_mode="close"` bypasses the entire armed-zone path. Don't propose
    rule_f sensitivity tests without first checking entry mode.

Appended 2026-06-13 (B24 iFVG entry_mode sensitivity):

55. **`ifvg_entry_mode="close"` is materially superior to `"ifvg_edge"` on combine AND funded
    objectives — the opposite of the prior hypothesis.** B24 tested both modes against identical
    configs (61-month combine + LongOnly london+ny_am funded r1.25, MNQ overrides r_mult=3.5).
    Combine: "close" 11/61 (18%) vs "ifvg_edge" 7/61 (11%); PF 1.18 vs 1.00. Funded: PF 1.1666
    vs 1.1229; sust 2.524x vs 0.815x; net payouts $181k vs $158k. The expected mechanism (deeper
    entry → larger stop → harder target → lower WR) is wrong. Mechanism: (1) confirmed entry at
    the inversion bar close is a higher-quality structural signal than a limit retrace to the
    proximal edge; (2) 100% fill rate captures trending setups that "ifvg_edge" misses; (3)
    "ifvg_edge" generates chop-retrace fills on oscillating markets that dilute PF. Practical
    implication: the deployed bot config is NOT just acceptable — it is the better config. All
    future iFVG benchmarks must use `ifvg_entry_mode="close"` as the new baseline. Benchmarks
    B1-B23 using "ifvg_edge" remain valid for within-series comparisons but are NOT representative
    of deployed bot performance.

Appended 2026-06-13 (B25 partial_profit_r sensitivity):

56. **Partial-profit exits (partial_r=1.5, stop to BE) have near-neutral effect on ORB-reentry
    funded sustainability at conservative sizing.** For ORB-reentry r0.75: sust 0.750x vs 0.764x
    (−1.8%, within 80% threshold). The mechanism: partial exits at 1.5R flatten the equity curve
    dramatically (combine busts −26.5%, XFA busts −5.5%) but also reduce winner payouts −10%
    (winning trades earn 2.0R instead of 2.5R). For ORB-reentry at r0.75, these effects nearly
    cancel. The combine-bust reduction is the surprising magnitude: equity dampening reduces
    account resets by >25% even though the same strategy runs both phases. Rule: partial exits
    are never a free lunch — they trade payout capacity for drawdown protection. For the deployed
    partial_r=1.5 with ORB-reentry r0.75, this tradeoff is acceptable but not beneficial.
    Deployed config can remain unchanged.

Appended 2026-06-13 (B26 swing_stop_lookback sensitivity):

57. **swing_stop_lookback=30 degrades iFVG combine pass rate by making the r=3.5 target harder
    to reach.** B26 tested 0/15/30 in ifvg_edge mode: lookback=30 loses on BOTH metrics vs
    lookback=0 (4/61 PF 0.78 vs 7/61 PF 1.00) — stop rule triggered. Mechanism: wider stop
    (anchored to 30-bar swing low, below the immediate sweep extreme) increases the absolute
    stop distance → same r_multiple target requires a larger absolute move → fewer monthly pass
    thresholds reached. This joins swing_stop_lookback in the parameter plateau (increasing the
    value only hurts).

58. **target_clarity_mode="off" (deployed) halves the Phase A iFVG combine pass rate vs "reject".**
    B26 bonus finding: deployed Phase A (close + lookback=30 + target_clarity=off) = 6/61 (10%)
    passes vs B24 baseline (close + lookback=0 + target_clarity=reject) = 11/61 (18%). The two
    deployed-vs-research differences contribute: target_clarity=off costs 4 passes (10→6, primary
    driver) and lookback=30 costs 1 pass (11→10, secondary driver). Rule: don't use
    target_clarity_mode="off" for the Phase A combine config; "reject" is the correct setting to
    focus combine months on high-structural-quality setups that are more likely to reach the $3k
    target. The B21 two-phase pipeline ($497/mo, sust 2.62x) assumed Phase A at the research
    baseline; deployed Phase A throughput is nearly half that assumption — both parameters should
    be fixed before deploying the B21 recommendation.

59. **The parity gap between research and deployed Phase A is actionable with zero code changes.**
    Setting swing_stop_lookback=0 and target_clarity_mode="reject" in bot_config.json restores
    Phase A from ~6/61 (10%) to ~11/61 (18%) combine passes — a ~83% improvement. This requires
    only config changes, no strategy logic changes. The bot supports these config values already
    (both are StrategyParams fields). Priority action for Lawrence on Monday: update Phase A config
    and re-run a B21-style pipeline projection with the corrected Phase A pass rate.

Appended 2026-06-13 (wk1-r6 research session):

60. **The B21 two-phase recommendation uses ifvg_edge Phase A, understating the deployed close-mode
    pipeline by ~57% combine passes.** Per-year equity_b1/control_r1p25 data: 34 passes / 163
    attempts over 5y (21% pass rate). B24 showed close mode gives 11/61 (18%) vs 7/61 (11%) —
    a 1.571× scale factor. Projected close-mode Phase A: ~53 passes over 5y. Applied to B21
    two-phase: reset cost drops $715→$461, cycle days 102.1→92.0, projected net/month $497→$610
    (+23%) and sust 2.62x→4.08x (+56%). B27 quantifies this exactly. Never quote B21 numbers
    as representative of the deployed config — they use ifvg_edge Phase A.

61. **Close-mode LongOnly-iFVG funded per-account net ($8,206) is 2.6× higher than ORB-reentry
    r0.75 ($3,131) but cycles ~2× faster** (22 accounts / 971 trading days = 44.1 days/account
    vs 73.5 days for ORB-reentry). The high per-account net comes from higher PF (1.167) and
    longer survival in the funded phase. Whether this translates to better two-phase pipeline
    economics than ORB-reentry depends on per-year funded bust frequency (flat 5y: 21 busts,
    per-year projected: ~10–11). B28 tests this directly. Rule: high per-account net does not
    automatically mean better pipeline — account cycling speed and bust frequency both matter.

Appended 2026-06-13 (B27 iFVG-close Phase A pipeline test):

62. **`ifvg_entry_mode="close"` and `"ifvg_edge"` interact differently with named session killzones,
    reversing the B24 ranking under ifvg-only engine.** B24 (combined engine, all-day killzones)
    showed close mode gives 11/61 combine passes vs 7/61 for ifvg_edge (+57%). B27 (ifvg engine,
    named sessions) shows close mode gives 10 passes vs 34 for ifvg_edge (29% — WORSE). Mechanism:
    `close` mode fires exactly at inversion bar close; if that bar falls outside the active killzone
    window, the signal is lost. `ifvg_edge` arms a tracker that persists across session boundaries —
    a tracker armed in London fills in NY AM or NY PM, accumulating cross-session fills that close
    mode cannot capture. The "100% fill rate" advantage of close mode only holds when killzones=all
    (every bar can trigger the signal). Under named sessions, ifvg_edge's cross-session persistence
    is the dominant performance driver, making it materially superior for the Phase A combine
    objective. Rule: when evaluating entry modes in restricted killzone configs, test both modes
    under the exact killzone configuration — the B24 all-day result does NOT transfer.

63. **Never apply a scale factor derived from one engine/killzone config to a different config's base.**
    The wk1-r6 B27 projection (~53 passes) used B24's close/edge ratio (11/7 = 1.571×, derived with
    combined engine + all-day killzones) applied to B21's ifvg_edge base (34 passes, derived with
    ifvg engine + named sessions). These two configs behave fundamentally differently under entry mode
    changes. The actual B27 result: close mode (ifvg engine + named sessions) = 10 passes, not 53.
    Scale factors from benchmark A are only valid when applied within the same config space as benchmark A.
    Cross-config extrapolation is invalid even when the parameter being varied (entry mode) appears
    independent of the other config axes (engine, killzones).

Appended 2026-06-13 (B28 close-mode LongOnly-iFVG Phase B):

64. **The per-year vs flat-5y bust correction direction is strategy-specific and depends on whether 2022 was active or quiet.**
    Lesson 42 established that per-year equity stitching gives ~50% FEWER busts than flat-5y for ORB r1.0
    (27 vs 59). B28 found the OPPOSITE for LongOnly-close iFVG: 39 per-year busts vs 21 flat-5y busts
    (+86% more). Mechanism: flat-5y includes 2022 (iFVG drought year — nearly flat equity, very few account
    resets); per-year methodology excludes 2022. For ORB (active in 2022), per-year removes bust-causing
    variance. For iFVG (drought in 2022), per-year removes a "quiet" year that suppressed bust frequency
    in the flat-5y count. Rule: always verify the per-year vs flat-5y direction before applying Lesson 42's
    correction to a new strategy. For iFVG-based funded configs, flat-5y bust counts understate per-year
    bust frequency (flat includes the quiet 2022 drought buffer).

Appended 2026-06-13 (B29 ORB-reentry 10min Phase B):

65. **Flat-5y standalone improvements do not transfer to per-year two-phase pipeline dynamics when account cycling speed differs.**
    B22 showed 10-min ORB (flat-5y, no reentry, r1.0) had +9% funded $/mo vs 15-min. B29 tested 10-min
    ORB-reentry (per-year, r0.75) as Phase B and found +130% more XFA busts (30 vs 13) and -50% per-account
    net ($1,554 vs $3,131) vs 15-min. Mechanism: 10-min ORB fires on more trading days; combined with reentry
    (a second signal after each stop), accounts experience more frequent P&L events — accelerating both gains
    and bust-causing drawdowns. The flat-5y metric averages away this cycling effect; per-year methodology
    exposes it. Rule: when a flat-5y benefit comes from higher trade frequency, verify per-year dynamics
    before committing to a pipeline recommendation — higher frequency always means faster account cycling,
    which amplifies bust rate in the two-phase model.

Appended 2026-06-13 (wk2-r1 research session):

66. **iFVG Tuesday and ORB Monday+Wednesday are structurally loss-making over 5 years; ORB Friday is the
    dominant single-day edge.** From 5y MFE/MAE data (excl 2022): iFVG Tuesday PF=0.917 (n=539,
    net=-$15,792) — the only loss-making iFVG day. ORB Monday PF=0.898 (-$5,100) and Wednesday
    PF=0.942 (-$3,131) are both loss-making; ORB Friday PF=1.775 (+$29,446) generates 60% of total
    ORB net from 20% of trades. These are 5-year structural patterns, not external claims. Proposed
    mechanisms: iFVG Tuesday = post-Monday-positioning consolidation (choppy); ORB Monday = weekend
    gap reversals creating false breakouts; ORB Wednesday = FOMC announcement days (choppy, indecisive);
    ORB Friday = end-of-week position squaring (clear directional momentum). B30 tests whether filtering
    these days improves funded-phase PF and sustainability. Importantly: these are DIFFERENT from
    session-hour filters (B18 rejected) — DOW patterns reflect institutional calendar cycles, not
    intraday microstructure.

Appended 2026-06-13 (B30 DOW filter):

67. **DOW PF from the full population does not transfer to a config subset that already filters the loss drivers.**
    Tuesday iFVG PF=0.917 (all-sides all-day) was caused by loss-making shorts (PF<1) and afternoon/overnight
    sessions (PF<1). The close-mode long-only london+ny_am config had already removed both loss drivers.
    Applying skip_trading_days=Tuesday to this already-filtered config removed profitable Tuesday LONGS in
    the London/NY AM windows, collapsing PF from 1.167 (B24 baseline) to 0.996 (loss-making) and sust from
    2.524x to 0.27x. Rule: before proposing a DOW filter on a config subset, compute DOW PF WITHIN that
    exact subset — not in the population-level data. Population-level DOW PF is only valid as a hypothesis
    for unfiltered configs.

68. **DOW filters that remove bad days can improve pipeline $/month by accelerating equity cycling, but this
    comes at the cost of sustainability.** ORB skip Mon+Wed removed loss-making Monday (PF=0.898) and Wednesday
    (PF=0.942) ORB signals. This made the ORB equity curve rise faster on Tue/Thu/Fri → combine phases completed
    in 16.1 days/attempt vs 26.4 days (−39%), enabling more pipeline cycles per year → $/month +42% ($708 vs
    $497). But faster cycling means more total funded account slots opened over 5y: 16 XFA busts (vs 13), sust
    2.12x (vs 2.62x). The DOW filter failed the primary criterion (busts <13 required). Mechanism is the same
    as the frequency lesson from B29: higher cycle speed amplifies both gains and bust frequency. Rule: DOW
    filters that remove bad days should be evaluated on sust (not just $/mo), as faster cycling always amplifies
    bust frequency in the two-phase pipeline model.

Appended 2026-06-13 (B31 Phase A risk sensitivity):

69. **Higher Phase A risk reduces combine cycle duration and improves pipeline $/month, but only at r=2.0 —
    the r=1.5 intermediate shows no benefit over r=1.25.** B31 tested iFVG Phase A at r=1.25%, r=1.5%, and r=2.0%
    (deployed settings). Results: r=1.25 → 27/99 passes, 38.1d/funded, $486/mo, sust 2.08x; r=1.5 → 29/119
    passes, 35.5d/funded, $485/mo, sust 2.23x; r=2.0 → 37/168 passes, 27.8d/funded, $508/mo, sust 2.85x.
    Mechanism: higher risk makes monthly P&L more volatile → more months cross the $3k combine threshold → combine
    cycle completes faster. But per-cycle net decreases (more reset fees per funded account). r=2.0 wins because
    cycle-duration reduction outpaces the per-cycle net decrease — daily throughput improves ($24.2/d vs $23.1/d).
    r=1.5 fails because it reduces cycle days only modestly (35.5d vs 38.1d) while also reducing per-cycle net —
    the two effects nearly cancel. Rule: in the two-phase combine model, risk is a cycle-speed lever, not a
    per-cycle-net lever. Only large risk increases that materially shift the combine duration distribution will
    improve $/month. Intermediate steps may show no benefit or even marginal regression.

Appended 2026-06-13 (wk2-r2 research session):

70. **ORB-reentry generates 31% more pipeline $/month than plain ORB at the same r-multiple (r=0.75).**
    Plain ORB r=0.75 paired with B31 Phase A: $387/mo sust=2.64x (14 busts). ORB-reentry r=0.75: $508/mo
    sust=2.85x (13 busts). The reentry mechanism adds profitable entries on reversal days — after the first
    ORB breakout stops out, a second entry fires in the opposite direction. This captures additional profitable
    EOD flattens on days the initial direction was wrong, adding account earnings without proportionally
    increasing bust frequency. The $/mo gain comes from the 33.6% EOD-flatten dominant exit structure (most
    ORB account value is in day-end position management), not from the 11.4% target-hit trades.
    Rule: ORB-reentry and plain ORB are NOT interchangeable Phase B options — the reentry mechanism is
    structurally additive, not just a risk-level variant.

71. **Plain ORB r-multiple sensitivity shows sust collapse above r=0.75; r=1.25 maximizes $/mo but barely
    sustains the pipeline (sust=1.03x).** Full curve (paired with B31 r=2.0 Phase A, 37 passes): r=0.5
    → $300/mo sust=6.17x; r=0.75 → $387/mo sust=2.64x; r=1.0 → $408/mo sust=1.37x; r=1.25 → $531/mo
    sust=1.03x (37 passes vs 36 busts — barely above minimum). Primary criterion (sust>=2.62x) fails at
    r>=1.0; secondary criterion (sust>=1.26x) fails at r>=1.25. Mechanism: higher r-multiple means more
    losers end deep in the MLL danger zone per funded account, accelerating bust timing faster than target
    hits increase payouts. At r=1.25 the pipeline essentially becomes unsustainable (one more bust per year
    would make it pipeline-negative). Rule: plain ORB r-multiple is not a reliable $/mo lever — sust collapses
    before $/mo improves enough to meet criteria. The ORB-reentry mechanism at r=0.75 achieves better $/mo
    ($508 vs $387) with better sust (2.85x vs 2.64x), strictly dominating plain ORB across all metrics.

Appended 2026-06-14 (B33 anticipatory probe entry — Phase 1 rejection):

72. **Entering at the sweep-reclaim bar close (before iFVG inversion) bleeds because 56% of triggers hit the
    probe stop before displacement+inversion fires.** 5y replay (44,365 armed sweep events vs 2,842 iFVG signals
    = 15.6x ratio): 56.4% stop-hit, 26.0% probe-target-hit (1.0R unconfirmed), 8.3% confirmed by iFVG within
    8 bars. Blended-entry gain on confirmed cases = only +0.025R (probe 0.33 frac × modest entry improvement).
    Net expected_R = -0.098R -- definitively negative. Root cause: the iFVG chain REQUIRES price to push past
    the sweep extreme (creating the displacement and FVG imbalance) before the inversion confirmation fires.
    The swept extreme is NOT a durable stop anchor at the pre-inversion stage -- it IS the level price must
    continue through to form the setup. Rule: Lesson 1 ("the iFVG INVERSION is the quality filter") extends
    to mean "anything entered before inversion is unfiltered." The swept-extreme stop is meaningful only AFTER
    the displacement leg confirms the reversal; entering at the sweep bar alone is a pre-structural-confirmation
    entry with a stop at exactly the wrong level.

Appended 2026-06-14 (B34 breaker-block + OTE Phase 1 falsification):

73. **OTE retrace depth is INVERSELY correlated with BOS forward performance on NQ 5min.**
    B34 Phase 1 (n=16,522 BOS signals, 5y excl 2022): no-retrace bucket WR=84.4%, PF=18.90;
    shallow (<0.38 fib) WR=62.3%, PF=5.79; OTE (0.62-0.79) WR=37.1%, PF=2.06; deep (0.79-1.0)
    WR=31.7%, PF=1.62. The hypothesis that "deeper retrace = better structural entry" is wrong for
    NQ 5min BOS signals -- shallow retraces indicate momentum continuation (strong reversal) while
    deep retraces indicate a weakening reversal (price struggling to maintain its BOS direction).
    Rule: on NQ 5min, the best BOS entries are those that DON'T retrace before continuing. Do not
    wait for OTE retracement on BOS signals. Phase 2 engine NOT built.

74. **56% of BOS signals are stopped during the retrace window -- same root cause as B33 probe failure.**
    After a sweep+BOS signal fires, 56% of cases see price return through the sweep extreme (the stop
    anchor) within 20 bars. This is the same mechanism as B33: the sweep extreme is structurally the
    level price was PUSHED THROUGH to create the setup -- it is not a durable support/resistance until
    the full chain (displacement + FVG + inversion for iFVG; or strong BOS continuation for sweep_bos)
    is confirmed. The BOS bar close is a weaker entry point than the iFVG inversion close because BOS
    requires one fewer structural confirmation step. Rule: the stop failure rate scales inversely with
    the number of confirmation steps -- more confirmations = fewer false stops = higher edge.
    This generalization applies to any new entry proposal: count the confirmation steps.

Appended 2026-06-14 (B35 daily-bias directional gate):

75. **Prior-day directional bias (close vs open) does not predict iFVG signal quality on NQ 5min.**
    B35 tested the ICT "Power of Three" concept: suppress signals that oppose the prior-day close vs
    open direction, and suppress longs once price has reached the prior-day high (room to target). Gate
    reduced volume by 86% (5.6 vs ~19 trades/month) and WORSENED both metrics: combine 4/61 (7%) PF 0.84
    vs baseline 7/61 (11%) PF 1.00. Short signals on short-bias days scored PF 0.66 — the "tops stall,
    bottoms sweep" mechanism (Lesson 8) is structural and independent of whether the prior day was bearish.
    Rule: prior-day directional bias extends Lesson 4 (day-level gates cannot time engines) to DIRECTION
    filters: the prior close vs open is not actionable information for NQ 5min iFVG signal quality.

Appended 2026-06-14 (B32 ORB-reentry r=0.5 risk floor):

76. **The ORB-reentry funded Phase B risk optimum is r=0.75 — confirmed from both below (B32) and above (B14/B21).**
    B32 tested r=0.5 as Phase B: sust improves to 3.08x (vs 2.85x at r=0.75) but $/mo collapses to $246
    (vs $508) — below the $300/mo useful threshold. The mechanism: at r=0.5, only 13 funded accounts open
    over 5y (nearly same as r=0.75's 14), but each earns $1,935 over 114 trading days vs $3,131 over 54d.
    Smaller position size means slower equity compounding — accounts survive longer but earn proportionally
    less. The throughput benefit of higher sust (fewer busts) is offset by each account needing 2x longer to
    reach payout. Rule: in a pipeline-constrained model, the funded risk level must balance (a) per-account
    net high enough for meaningful $/mo and (b) bust frequency low enough for sust >= Phase A pass rate.
    At r=0.5, (a) fails. At r>=1.0, (b) fails. r=0.75 satisfies both.

Appended 2026-06-14 (B36 FVG-midpoint stop):

77. **The FVG zone midpoint is inside the normal gap-fill retracement path — placing a stop there guarantees premature stop-outs.**
    B36 tested two modes: fvg_mid (stop = FVG midpoint, target scaled by new smaller r) and fvg_mid_abs
    (same stop, original absolute target). Both modes triggered stop rule on BOTH objectives: combine passes
    fell from 7/61 (PF 1.00 baseline) to 1-2/61 (PF 0.67-1.01), and 5y funded net fell to -$38k to -$43k
    (PF 0.79-0.83 vs baseline PF >1). Root cause: in the iFVG setup, price must retrace into the FVG zone
    (between proximal and distal edges) before the displacement completes — the midpoint sits squarely on
    this required path. A stop there is hit during normal iFVG development, not only on failures. Rule: for
    iFVG signals, stops must be placed OUTSIDE the FVG zone (distal edge or beyond); any stop inside the
    zone midpoint or nearer will be wicked out during valid setup development. The swept-extreme stop
    (current baseline) is geometrically correct because it sits past the liquidity sweep that triggers the
    signal — price must not revisit that level for the trade thesis to hold.

Appended 2026-06-14 (B38 ORB-reentry r-multiple sensitivity):

78. **ORB-reentry's sust advantage over plain ORB inverts above r=0.75.** At r=0.75, reentry
    sust (2.85x) exceeds plain ORB sust (2.64x) by 0.21. At r=1.0, reentry has 41 funded busts
    (sust 0.83-0.90x) vs plain ORB's 27 busts (sust 1.37x) — 52% MORE busts with reentry. The
    second-entry mechanism amplifies bust frequency faster than per-account earnings at aggressive
    sizing: each stop-reversal entry at r=1.0 carries full-sized risk, landing harder on the funded
    account and accelerating MLL approach. At r=0.75, second-entry losses are small enough not to
    materially increase bust timing. At r=1.25, sust collapses to 0.71-0.77x despite $523-545/mo
    (pipeline-negative). Rule: the ORB-reentry Phase B risk optimum is r=0.75, confirmed from below
    (B32: r=0.5, $/mo $246) and above (B38: r=1.0, stop rule; r=1.25, sust 0.77x). Do not propose
    ORB-reentry Phase B at r>=1.0 without a fundamentally different mechanism to reduce bust frequency
    at higher sizing (e.g., partial exits, session gating, DOW filtering — none of which has yet worked).

Appended 2026-06-13 (B39 research-baseline Phase A config-parity test):

79. **B31's r=2.0 advantage over B21 is entirely config-specific — higher risk at the research
    baseline hurts Phase A.** B39 tested ifvg_edge + named sessions + no MNQ overrides at r=2.0:
    only 12/82 Phase A passes (vs B21's 34/162 at r=1.25) with reset cost $1,025/funded.
    Pipeline result: $394/mo, sust=0.92x — WORSE than B21 ($497/mo, 2.62x) on both metrics,
    stop rule triggered. The deployed config generates 25 more passes at the same r=2.0 risk level:
    engine=combined + all-day killzones produce more monthly trades, shifting the monthly P&L
    distribution toward $3k threshold passes rather than MLL busts. Rule: r=2.0 Phase A is only
    beneficial in high-frequency configs (combined engine + all-day KZ). At research-baseline trade
    frequency (~25/month named sessions), higher risk amplifies bust frequency without proportionally
    increasing passes. Do not recommend r=2.0 as a universal Phase A upgrade.

Appended 2026-06-14 (B40 combined-engine vs iFVG-only Phase A):

80. **engine=combined gives 2x more Phase A combine passes than engine=ifvg at the B21 research
    baseline (named sessions, ifvg_edge).** B40: combined 10/61 (16%) PF 1.02 vs ifvg 5/61 (8%)
    PF 0.85. Mechanism: ORB signals added by the combined engine fire during NY AM (inside the named
    sessions window), adding ~4.8 trades/month of positive-expectancy entries that iFVG-only misses.
    At named sessions, iFVG-only generates only ~4.3 trades/month — too sparse to accumulate $3k
    monthly P&L reliably. The deployed engine=combined for Phase A is structurally correct and
    materially better than the B21 research baseline's iFVG-only config. Rule: in volume-constrained
    configs (named sessions), engine=combined substantially outperforms engine=ifvg for the combine
    phase. In high-frequency configs (all_day killzones), the difference is smaller because iFVG
    already generates sufficient volume without ORB's contribution.

Appended 2026-06-14 (wk2-r3 research session):

81. **ORB signals after 10:30 ET (60 min post-open) are loss-making on NQ 5min over 5 years.**
    From 5y MFE/MAE data (n=1030 ORB trades, excl 2022): 9:45-10:30 ET (n=926) blended PF ~1.21;
    10:30-11:00 ET (n=57) PF=0.963; 11:00-11:30 ET (n=27) PF=0.622; 11:30+ ET (n=19) PF=3.213
    (unreliable sample). The 10:30-11:30 window costs ~$2,900 over 5y. Notably, the 10:05-10:30 ET
    window (delayed breakouts: PF 1.43-1.53) is HIGHER quality than the 9:45-55 ET initial window
    (PF 1.17) — delayed breakouts after first-bar noise settles are higher conviction. Rule: ORB
    signal quality degrades past 10:30 ET into the lunch-doldrums period. Proposed B43 tests an
    orb_signal_window_mins=60 cutoff. Don't apply this finding to iFVG signals (different timing
    structure — iFVG fires across all sessions, and B18/B19 already tested session filtering).

82. **iFVG initial MAE distribution shows near-perfect separation between winners and losers.**
    From 5y data (n=2477, excl 2022): trades with MAE<0.25R: WR=93.3%, PF=335 (n=267); MAE 0.25-
    0.5R: WR=83.1%, PF=40; MAE 0.5-0.75R: WR=72.3%, PF=17; MAE 0.75-1R: WR=43.9%, PF=2.6; MAE
    1R+ (stop-outs): WR=0.7%, PF=0.026 (n=1438). This validates the existing swept-extreme stop
    placement: losers overwhelmingly go all the way to the stop. An intrabar early-MAE trailing
    mechanism (e.g., tighten stop after first bar confirms clean run) MIGHT improve outcomes but
    requires per-trade time-series MAE instrumentation (current infrastructure tracks only final MAE
    per trade, not time-of-day MAE evolution). Don't propose MAE-gating without this new
    instrumentation. The clean-run WR/PF is not actionable with existing backtest infrastructure.

Appended 2026-06-13 (B41 combined-engine Phase A pipeline):

83. **Combine-harness monthly pass rate and funded-pipeline account throughput are structurally
    decoupled.** B41 tested combined engine (9.1 trades/month) as Phase A: 10 per-year passes vs
    34 for iFVG-only (4.3 trades/month) despite the B40 combine-harness showing 2x better monthly
    pass rate (10/61 vs 5/61). Root cause: the funded pipeline runs CONTINUOUS attempts; higher trade
    frequency damps per-attempt equity variance → each attempt takes longer to resolve (12.7d vs
    6.0d) → fewer total attempts over 5y (41 vs 162) → fewer total passes (10 vs 34). The 2x
    monthly-quality improvement becomes a 4x throughput penalty in continuous operation. Rule: never
    use the combine-harness monthly pass rate as a proxy for funded-pipeline throughput. Always model
    pipeline throughput with funded_sim's continuous attempt simulation. A strategy that improves
    monthly quality by taking smaller/more-frequent bets may SLOW the pipeline by smoothing the
    equity curve's path to resolution.

Appended 2026-06-13 (B42 deployed config full pipeline):

84. **The deployed Phase A config (combined+close+all-day+r=1.0%) is the strongest 5y Phase A driver
    tested: 42 passes over 5y — more than any prior config.** B42 ran the ACTUAL deployed bot config
    (engine=combined, ifvg_entry_mode=close, partial_profit_r=1.5, swing_stop_lookback=30, killzones=all,
    risk=1.0%, r_multiple=3.5 MNQ) through the funded pipeline for the first time. Result: 42/159 Phase A
    passes, $568 reset cost/funded, cycle 98.1d. Two-phase pipeline: $549/mo sust=3.23x — beats B31 winner
    ($508/mo, 2.85x) on BOTH primary criteria. The mechanism: all-day killzones + combined engine + close mode
    generates ~80-100 trades/month → high monthly P&L variance → more months cross the $3k threshold even at
    conservative r=1.0% sizing. r=1.0% beats r=2.0% on $/mo because higher risk grows reset costs faster than
    it grows passes ($719 vs $568 per funded at r=2.0 vs r=1.0). Rule: in high-frequency configs (combined +
    all-day + close mode), r=1.0% is the Phase A risk optimum; r=2.0% inflates reset costs without proportional
    pass gains.

85. **The deployed Phase A's high signal frequency is a double-edged sword: strongest in trending years,
    loss-making in iFVG drought years (2022 structural risk).** B42 2022 holdout: Phase A PF=0.934,
    net=-$11,824 — the deployed config generates 1097 trades in 2022 (vs <200 for pure iFVG at named sessions).
    The all-day + combined + close mode amplifies iFVG drought losses proportionally to trade frequency. The
    same mechanism that creates 42 Phase A passes in 2023-2026 (trending structure, abundant iFVG setups)
    creates 1097 loss-making trades in 2022 (structural poverty year). 6y pipeline (incl 2022): $424/mo
    sust=2.19x — below B21 baseline. Phase B (ORB-reentry r=0.75) moderates this: ORB held up in 2022
    (PF=1.072) and provides a partial hedge against the Phase A drought exposure. Rule: high-frequency Phase A
    configs are regime-dependent; the 2022 drought is the primary tail risk. Always run the 2022 holdout
    before declaring a high-frequency config a permanent upgrade over the B21 baseline.

Appended 2026-06-15 (B43 ORB late-session signal cutoff):

86. **ORB edge is concentrated in the first 60 minutes post-open; the 10:30-11:30 ET window is
    structurally loss-making and should be suppressed.** B43 tested orb_signal_window_mins=60 and
    =90 (10:30 ET and 11:00 ET cutoffs). The 10:30-11:30 ET window costs ~$2,900 over 5y (PF
    0.622-0.963, n=84 signals). Suppressing it (w=60) removes 8% of total ORB signals while keeping
    all combine passes (10/61 unchanged), improving combine PF from 1.15 to 1.21 (+5.2%), and
    reducing Phase B funded busts from 25 to 22 (-12%). Two-phase sust improves 1.68x→1.91x (+14%).
    Mechanism: early-session ORB signals (9:30-10:30 ET) capture the urgency of the opening move;
    10:30+ ET breakouts attempt to break through a range that is already being "digested" by the
    market — momentum has typically exhausted. Rule: set orb_signal_window_mins=60 for all ORB
    configurations unless there is a specific reason to allow later signals. The cutoff is
    backward-compatible (default=0 = existing behavior) and has no effect on open positions.

Appended 2026-06-15 (wk2-r4 research session):

87. **iFVG signals in the 11:00-14:00 ET window (lunch doldrums) are structurally loss-making over
    5 years: PF<1 in 4 of 5 years, n=232 trades, net=-$12,268.** Per-hour: 11:xx PF=0.932 (n=83),
    12:xx PF=0.752 (n=64), 13:xx PF=0.744 (n=85). The 14:xx hour recovers to PF=1.108 (positive) —
    the dead zone ends at 14:00 ET. Per-year: 2021 PF=0.951, 2023 PF=0.511, 2024 PF=0.631,
    2025 PF=0.941, 2026 PF=1.623 (n=16, sparse). This is NOT the same mechanism as B18 (named-session
    filter that removed overnight/pre-market hours with POSITIVE PF at close mode) — the lunch block
    ONLY removes 11:00-14:00 ET while keeping all overnight/pre-market sessions. B44 tests
    `ifvg_block_hours=[11,12,13]` as a new StrategyParams gate. Mechanism: CME institutional lunch
    break (11:00-13:00 ET) creates low-liquidity mean-reverting price action; iFVG inversions trigger
    but fail to follow through without institutional order flow. Rule: before proposing a block,
    verify PF WITHIN the exact deployed config subset — B18 failed because the session-level block
    removed positive-PF overnight hours; B44 is specific to the documented loss-making hours only.

88. **ORB value is entirely concentrated in the 4h+ (EOD-flatten) cohort; all short-hold buckets
    are deeply loss-making over 5 years.** From 5y MFE/MAE data (n=1030 ORB trades, excl 2022):
    0-30m: WR=4.5%, PF=0.144, net=-$43,125 (n=110); 30-60m: WR=13.1%, PF=0.378, net=-$26,318 (n=99);
    1-2h: WR=13.5%, PF=0.399, net=-$25,333 (n=104); 2-4h: WR=26.7%, PF=0.868, net=-$5,893 (n=131);
    4h+ (EOD flatten): WR=68.6%, PF=4.129, net=+$152,611 (n=573). The 0-2h cohort (n=313) contributes
    net=-$94,776 while the 4h+ cohort generates +$152,611. All early stop-outs are structural losers;
    the entire ORB edge is in day-long holds that flatten at EOD. Long-side EOD flattens are nearly
    2x better PF than short-side (PF=5.46 vs PF=2.99 in 4h+ bucket), consistent with Lesson 8.
    ORB winner holds cluster tightly at p50=365 min, p75=380 min, p90=380 min — virtually all held
    to EOD. Rule: any mechanism that increases early stop-out frequency (tighter stops, lower R) must
    be evaluated for its effect on 4h+ cohort survival, not just average WR. The 0-2h losses are
    the dominant drag — if a filter selectively removes them, it could unlock materially better
    funded-phase economics. B45 tests whether OR width (a coil-compression proxy) predicts which
    days produce early reversals vs durable EOD-flatten trades.

Appended 2026-06-13 (B44 iFVG mid-session block):

89. **Per-hour PF analysis must be validated within the EXACT deployed config (entry mode, allowed_sides,
    engine) — it does not transfer across config variants.** B44 found that wk2-r4's per-hour analysis
    (ifvg_edge, all-sides, all-day) showed 11-13 ET signals at PF=0.744-0.932, net=-$12,268 over 5y.
    This motivated blocking those hours. But in the deployed config (close mode, long-only, combined engine),
    the same hours are PROFITABLE: blocking 11-13 ET reduced combine passes 9→7/61 (15%→11%), combine PF
    1.11→1.00, and long net +$25,210→+$5,863 over 61 months (51 fewer long exits, avg ~$380/trade profit
    lost). Mechanism: close mode fires signals at the FVG inversion confirmation (a LATER and more selective
    entry than ifvg_edge), which captures valid institutional order flow even during the CME lunch window;
    ifvg_edge fires at the zone boundary where false entries dominate at low liquidity. Entry mode
    fundamentally changes the hour-PF distribution. Rule: before proposing an intraday hour block or
    filter, compute PF WITHIN the exact deployed config subset (same entry_mode, same allowed_sides, same
    engine). Never extrapolate from a different config variant, even if the mechanism sounds structural.
    The B18 precedent (named-session filter that removed overnight sessions with POSITIVE close-mode PF)
    was not sufficient warning because B44's data was presented as "per-hour" rather than "per-session,"
    making it appear more granular and reliable — the same trap in a different form.

Appended 2026-06-15 (B45 ORB range-width filter Phase 1):

90. **Current-day OR/ATR ratio is NOT a reliable monotonic predictor of ORB trade quality.**
    B45 Phase 1 (n=1030 matched trades, 5y excl 2022): Wide OR (top 40%, ratio >= 5.28) PF=1.190 vs
    Narrow OR (bottom 40%, ratio < 4.18) PF=1.141 — a 1.044x PF ratio, far below the 1.4x GO threshold.
    The relationship is non-monotonic: the MIDDLE bucket (OR/ATR ratio 4.18-5.28) has the HIGHEST PF
    (Q3=1.439), while both extremes underperform it. The hypothesis (narrow=false breakout, wide=sustained
    move) is structurally plausible but empirically wrong: narrow OR days (ratio < 3.25) produce PF=1.214,
    actually HIGHER than wide OR days (ratio > 6.80, PF=1.154). The hold-time pattern IS real (wide OR
    days: 75% EOD flattens vs narrow 47%), but this structural difference does not translate to better PF.
    B45 joins B5 (prior-day range) as day-level ORB-quality predictors that fail on NQ 5min data.
    Rule: OR width (whether current-day or prior-day) does not predict ORB signal quality; the opening
    range breakout mechanism captures the day's momentum regardless of how compressed the opening range was.
    Notable side finding: long/short PF inverts by OR width (narrow: long PF=1.518 vs short PF=0.821;
    wide: long PF=1.142 vs short PF=1.244). Wide OR days produce more balanced long/short outcomes,
    possibly because large pre-market moves create genuine two-way uncertainty at the regular session open.

Appended 2026-06-15 (wk2-r5 confluence data mining):

91. **London iFVG and NY ORB provide independent directional confirmation of day structure; their
    agreement or disagreement is the strongest cross-engine quality predictor found in this research
    program.** From 5y MFE/MAE data (excl 2022): ORB signals preceded by a same-direction iFVG that
    day have PF=1.689 (n=360, 35% of all ORB trades); ORB signals where all prior same-day iFVG signals
    were in the OPPOSITE direction have PF=0.957 (n=227, 22%, loss-making). Suppressing the opp-only
    ORB signals improves per-year PF in 5/5 years (+1.5% to +12.9%). Similarly, iFVG signals on days
    where the ORB fires in the same direction have PF=1.375; iFVG signals where the ORB fires in the
    opposite direction have PF=0.757 (loss-making in 4/5 years; 2021 exception: PF=1.030, borderline).
    Mechanism: the London iFVG and NY ORB represent institutional order flow from two separate sessions.
    When both sessions agree on direction, there is genuine multi-session directional conviction — both
    signals are reinforcing. When they disagree, one session is fighting against the other — both signals
    fail. This is NOT the day-level regime gate anti-pattern (Lesson 4): the gate is directional (not a
    day-level binary on/off — some trades are still allowed on conflicted days), and both signals are
    structural/institutional (not derived from VIX, volatility, or external-market factors).
    Rule: when two structural engines agree on direction for the same day, signal confidence compounds.
    When they disagree, suppress the conflicted-direction signal or treat it as markedly lower quality.

92. **Cross-engine directional gates must be causal: the earlier-firing engine's output gates the
    later-firing engine's signals.** The iFVG×ORB confluence gate works because iFVG fires London
    session (03:00-07:30 ET) before ORB fires at 09:30-09:45 ET, and ORB fires before any post-ORB
    iFVG re-entries. This ordering makes both gates forward-compatible with no look-ahead: at ORB
    signal time, all prior same-day iFVG directions are already in the books; after ORB fires, its
    direction is known before any subsequent iFVG intraday re-entry fires. Do not attempt to gate
    EARLIER engine signals using the LATER engine's same-day output — that requires future information.
    Rule: document the temporal ordering of engines before implementing any cross-engine gate. Gates
    between engines that fire in overlapping time windows (or in an uncertain order) cannot be made
    causal without introducing lookahead bias.


Appended 2026-06-15 (B46 deployed pipeline w=60 benchmark):

93. **orb_signal_window_mins=60 (B43 candidate) does NOT improve the funded pipeline when partial_r=1.5 is deployed.** At partial_r=0 (B43), w=60 reduced Phase B busts from 25 to 22 and lifted sust 1.68x to 1.91x. At partial_r=1.5 (deployed, B46), bust counts are identical (13 vs 13) and sust is unchanged (3.23x vs 3.23x); w=60 only reduces per-account earnings by $591 (-19%), lowering pipeline $/mo from $549 to $456. Mechanism: partial exits at 1.5R convert full losses to breakeven exits, removing the bust risk that the window cutoff would otherwise prevent. Late-session signals that w=60 removes include profitable EOD flattens that under partial_r=1.5 would have locked in a partial gain before the EOD exit. Validate any signal-reduction mechanism at the deployed partial_r setting, not just at partial_r=0.
    Rule: B43 orb_signal_window_mins=60 is NOT recommended for the deployed funded phase (partial_r=1.5). The B43 combine-phase PF improvement (+5.2%) remains valid but the deployed Phase A uses iFVG, so w=60 is irrelevant there too. Do not enable orb_signal_window_mins=60 on the live bot.

Appended 2026-06-15 (B47 iFVGxORB confluence gate):

94. **A directional confluence gate that suppresses cross-engine signals degrades performance when the gating condition is too conservative (requires ALL prior same-day signals to oppose).** B47 tested gate1 (suppress ORB when ALL prior same-day iFVG signals oppose ORB direction) + gate2 (suppress post-ORB iFVG that oppose ORB direction). Despite the Lesson-91 finding that ORB opp-direction PF=0.957 (loss-making), enabling both gates on the deployed combined engine reduced funded sust from 0.79x to 0.50x (-37%) and combine pass rate from 4/17 to 3/17 on the 2025-2026 test set. 5y net P&L fell $24k ($95k to $71k) on 692 fewer trades (-14%). Mechanism: the "all prior same-day iFVG oppose" condition blocks ORB trades where the iFVG opposition is incidental (unrelated to ORB predictive power), removing profitable trades along with the loss-making ones. The gate2 suppression of post-ORB iFVG opposing ORB adds further over-filtering. Rule: a confluence gate that requires total directional consensus is likely over-restrictive; the subtler question (are MOST prior iFVG signals opposed, or just one?) was never tested. Gate1 alone at a less-than-all threshold may be worth testing, but the total-opposition condition is insufficient to isolate the loss-making subset.

95. **The combined engine running both iFVG and ORB on one account has funded sust=0.79x, far below the separate Phase A (iFVG combine) + Phase B (ORB funded) two-account pipeline at 3.23x (B42).** B47 baseline (combined, deployed params, 5y excl 2022): 4,902 trades, 42 combine passes / 159 attempts, 54 XFA accounts / 53 busts, sust 0.79x. The two-account B42 pipeline (iFVG on practice, ORB on funded) achieves 3.23x. Running both strategies on one account amplifies daily P&L variance (both engines fire the same day), increasing bust frequency without proportionally increasing passes. Rule: the two-account pipeline architecture (Phase A on practice/combine, Phase B on funded) is structurally superior to the combined-engine-on-one-account approach for the funded sustainability objective. Do not conflate "combined engine is useful for combining combine-phase signals on practice" (B40: doubles Phase A pass rate) with "combined engine is good for the funded phase" (B47: halves sustainability vs separate pipelines).
Appended 2026-06-14 (B48 hybrid rank-aware short filter):

96. **Close-mode iFVG rank-1 shorts are loss-making in the deployed config despite being profitable in the ifvg_edge research baseline (PF=1.127).** B48 tested allowing rank-1 iFVG shorts (first short signal of the day) while suppressing rank-2+ shorts (rank-2+ PF=0.858 from research baseline). In close-mode deployed config (ifvg_entry_mode=close, enabled_killzones=all), the hybrid adds ~272 iFVG rank-1 shorts per 61 months with PF 0.84 � deeply negative. Combined combine pass rate dropped from 12/61 (20%) to 11/61 (18%) and PF from 1.21 to 1.09. Mechanism: close-mode fires at the inversion bar close, which for short setups enters at the FVG zone_low (bottom of the inversion zone). This is structurally weaker than the ifvg_edge retrace entry (which waits for price to pull back to the FVG proximal edge). For short setups, the close-mode entry is deeper inside the FVG zone with a tighter stop but structurally less conviction. The rank-1 quality differential observed in ifvg_edge mode does not transfer because the close-mode short signal represents a different (and weaker) price action event.
    Rule: do not re-enable iFVG short signals in the deployed close-mode config based on research-baseline PF data. The long-only filter (allowed_sides=long) is the correct iFVG short suppression mechanism for close-mode, regardless of within-day rank. Any future short re-enable hypothesis must be benchmarked directly in close-mode, not extrapolated from ifvg_edge results.

Appended 2026-06-14 (B50 account-state dynamic risk sizing):

97. **Survival-mode risk reduction is counterproductive for positive-expectancy strategies in MLL-bounded accounts.**
    B50 tested combine_ramp (1.5% early -> 0.75% protect -> 0.5% near MLL) and funded_survival (0.75% normal
    -> 0.4% near MLL) on the B42 deployed baseline ($549/mo, sust 3.23x). All three variants were rejected:
    combine_ramp only: $498/mo sust 2.46x; funded_survival only: $451/mo sust 2.47x; full B50: $399/mo sust
    1.88x. Mechanism (combine_ramp): the 0.75x protect phase after $1,500 gain slows momentum precisely when
    the account is winning, reducing total attempts from 159 to 133 (-16%) and funded account throughput from
    42 to 32 passes. Mechanism (funded_survival): reducing to 0.4% when within $750 of MLL slows daily equity
    accumulation from ~$33/day to ~$17/day -- not enough to escape the danger zone quickly, while losses
    remain large enough to trigger busts, resulting in 17 busts vs 13 baseline. The key insight: for a
    positive-EV strategy, the fastest path out of the MLL danger zone is FULL SIZE, not reduced size. Survival
    mode only benefits strategies where the edge degrades at adverse equity states (e.g., emotional trading,
    margin stress) -- inapplicable to an algorithmic bot. Dynamic risk sizing only adds value when paired
    with a mechanism that improves per-trade edge at the trigger point; path-only rescaling cannot do this.
    Rule: do not propose account-state risk scaling for the funded pipeline without a concrete mechanism that
    improves per-trade edge (not just reduces size) in the adverse-equity regime.

Appended 2026-06-14 (B49 ORB breakout extension Phase 1):

98. **ORB breakout extension (how far the signal bar closes past the OR boundary, in ATR units) is a non-monotonic predictor of PnL per trade.** B49 tested 1030 ORB trades (2021/2023/2024/2025/2026, excl 2022) bucketed by extension quintile. The mid-bucket (Q3: ext 0.481–0.845) achieves the highest PF (1.322), outperforming both the shallowest (Q1: PF=1.163) and deepest (Q5: PF=1.258) buckets. Bottom-40% vs top-40% PF ratio = 1.128, below the 1.4 GO/NO-GO threshold. The extension DOES predict EOD-flatten rate monotonically (Q5: 68% EOD vs Q1: 49%), but this hold-time shift does not translate to better per-trade PnL because shallow-extension EOD flattens are equally profitable when they occur. Short signals specifically improve at high extension (shallow short PF=0.992 vs deep short PF=1.220), but the aggregate remains insufficient.
    This is the third consecutive rejection of ORB day-level quality predictors: prior-day range (B5), current-day OR width (B45), and signal-bar breakout extension (B49) all fail the 1.4x PF ratio threshold. The ORB signal quality appears robust to these range/momentum indicators — the EOD-flatten capture mechanism (4h+ hold PF=4.129, Lesson 88) dominates any quality stratification available from range or breakout magnitude.
    Rule: do not propose ORB filters based on breakout bar extension magnitude relative to the OR boundary. Mid-extension breakouts are empirically the best performers; deep extension does not predict better P&L despite predicting longer hold times. The B45/B49 pattern (non-monotonic PF, peak at middle bucket) is now a recurring signature of these ORB quality proxies.

Appended 2026-06-14 (B51 setup-grade-scaled sizing Phase 1):

99. **The SetupGrader's structural-quality score (0-100, grades A-F) does NOT predict per-trade directional edge on NQ 5min under the deployed close-mode config.**
    B51 Phase 1 (1276 graded iFVG trades, 5y excl 2022, engine=ifvg, close mode, all-day KZ, MNQ overrides):
    Grade distribution: A=2, B=86, C=286, D=617, F=285. PF by grade: B=1.311, C=0.996, D=1.060, F=1.124.
    The C-grade bucket (PF=0.996, loss-making) is WORSE than both D (1.060) and F (1.124) — the ordering
    is non-monotonic, violating the basic premise that higher structural quality predicts better trade outcomes.
    Top-2 (A+B) PF=1.286 vs bottom-2 (D+F) PF=1.080; ratio 1.190 < 1.3 GO/NO-GO threshold. Phase 2 not built.
    Grade distribution is bottom-heavy: 70% of trades score D or F under the deployed config (close-mode entry
    lacks the structural context that scores A/B: no BPR, no P/D with target_clarity_mode=off, sparse delivery FVGs).
    This is the 4th consecutive quality-score rejection: prior-day range (B5), OR width (B45), breakout extension (B49),
    structural grade (B51). The iFVG chain's directional edge appears independent of these structural-richness metrics.
    Rule: do not re-propose quality-score filters for iFVG signals without a fundamentally different quality metric.
    The SetupGrader grade is not a valid sizing or selection signal for the deployed close-mode config.
    The grader may still serve its original purpose (rejecting very-low-quality signals), but its grade ordering
    within the "passing" set has no predictive value for trade outcomes.

Appended 2026-06-14 (wk3-r1 research session):

100. **The iFVG N+1 bar direction is the strongest signal-level discriminator found in this research program: confirmed (N+1 closes with signal) PF=1.619 vs not-confirmed (N+1 closes against signal) PF=0.661, ratio 2.45x, consistent across all 5 tested years.** From 5y data (n=2477, excl 2022): 53.2% of iFVG signals are "not confirmed" (N+1 bar closes against the signal direction within 5 minutes of entry). These not-confirmed trades are the dominant loss driver (PF=0.661, net=-$173,312). The original test design (require N+1 confirmation before entering) was Phase 1 NO-GO because only 46.8% confirm (< 60% volume threshold). REFRAME: the actionable mechanism is an adversity EXIT, not an entry filter. Enter all signals normally, then check at N+1 bar close (5 minutes after entry): if price has moved against the position, exit immediately rather than holding to the full stop. This converts full-stop-out losses to small early-exit losses for the not-confirmed 53.2%, preserving all 46.8% confirmed trades unchanged. Phase 1 data mining (B53) must quantify the average early-exit loss vs held-to-stop loss before building.

101. **Phase A deployed config (B42: swing_stop_lookback=30, target_clarity_mode="off") underperforms its research-corrected version by 50% on monthly combine pass rate (6/61 vs 9/61).** B52 confirmed that setting swing_stop_lookback=0 and target_clarity_mode="reject" in the deployed combined+close+all-day context improves monthly Phase A passes from 6/61 (10%) to 9/61 (15%), PF 1.11. These are config-only changes — no code required, and both fields are already in StrategyParams. Mechanism: target_clarity_mode=reject skips signals where the FVG target would land inside a prior structural impediment, removing low-probability-of-reaching-target setups; swing_stop_lookback=0 anchors the stop to the immediate swept extreme (smaller stop distance), making the r=3.5 target easier to reach. Priority action for Lawrence before the next live combine attempt: set swing_stop_lookback=0 and target_clarity_mode="reject" in bot_config.json. Full funded pipeline impact ($/mo, sust) deferred to a follow-up equity_export.py benchmark.


Appended 2026-06-14 (B53 N+1 adversity early exit gate):

102. **N+1 adverse bar early exits are self-defeating for the iFVG strategy: the 23.9% not-confirmed winners have 4.2x higher average win (,074) than the early exit loss (5), making the sacrificed gross wins (65k) exceed the loser savings (44k).**
    B53 Phase 1 PnL analysis (5y excl 2022, n=2477 iFVG trades, scripts/analyze_b53_early_exit.py): the not-confirmed group (53.2%, N+1 close adverse) contains 315 winners (WR=23.9%) that eventually win despite the early adverse move. Early-exiting these trades at N+1 close converts avg ,074 wins into avg hBc85 losses, costing 65k in gross wins. The savings on 1,003 not-confirmed losers (44k recovered from avg -10 full stops to avg -67 early exits) are insufficient to compensate. Net: not-confirmed net worsens from -73k to -94k (-12.2% vs needed +30%). Aggregate PF degrades from 1.043 to 1.029 (-1.4% vs needed +10%). Per-year: aggregate PF degrades in 4 of 5 years.
    The 2.45x PF split between confirmed and not-confirmed iFVG trades is an OUTCOME PREDICTOR, not an EXIT SIGNAL. The confirmed group earns more not because it filters losers, but because those trades simply go right from the start with higher WR (40.2% vs 23.9%). The not-confirmed group includes too many struggle-then-win trades that look adverse at N+1 but recover.
    Rule: do not re-propose N+1-bar exit mechanisms for iFVG signals without addressing the 23.9% false-negative rate (trades that are N+1-adverse but still win with avg ,074).

Appended 2026-06-14 (wk3-r2 research session):

103. **ORB×iFVG same-day directional alignment is a Phase 1 GO signal: PF ratio 1.48x (> 1.4 threshold), consistent 5/5 years.** From 5y MFE/MAE data (n=1030 ORB excl 2022): Group A+D (any prior same-day iFVG in same direction as ORB, 57.8% of ORB trades): PF=1.427, net=$53,893. Group B+C (no same-direction prior iFVG, 42.2% of ORB trades): PF=0.963, net=$-3,940. Per-year: A+D > B+C in ALL 5 years (2021: 1.928/1.085; 2023: 1.204/1.084; 2024: 1.531/0.870; 2025: 1.398/0.896; 2026: 1.248/0.934). Group B (no prior iFVG, n=210) is essentially break-even at PF=0.990 — ORB without London/pre-market institutional context is random. Group C (all prior iFVG oppose, n=225) is loss-making at PF=0.939. Mechanism: the London iFVG and NY ORB represent independent institutional sessions; when London has established a same-directional sweep+inversion, the NY opening range breakout is reinforcing an active institutional thesis rather than breaking into ambiguity. Rule: build B56 — gate ORB signals to require at least one prior same-direction iFVG signal on the same day. Note: this analysis used the research-baseline mfe_mae data (partial_r=0, orb engine only) — validate under deployed config in Phase 2 benchmark.

104. **iFVG inversion bar volume is non-monotonic with respect to PF — moderate volume (Q2, 65-155 contracts) generates 92% of 5y net, while both the lowest-volume (Q1 PF=1.023) and highest-volume (Q4 PF=0.995) quartiles underperform.** 5y data (n=2477, excl 2022): Q1 (<=64): PF=1.023, Q2 (65-155): PF=1.268, Q3 (155-782): PF=0.885, Q4 (>782): PF=0.995. High-volume (>median 155): PF=0.937 vs low-volume PF=1.138; high/low ratio 0.823 — wrong direction for a high-volume gate. The non-monotonic peak at Q2 matches the OR-width (B45) and breakout-extension (B49) patterns: NQ 5min structural edges resist simple single-dimension filters. Mechanism for high-vol underperformance: high-volume inversion bars may represent contested reversals where aggressive counter-party selling accompanies the inversion, degrading follow-through. However, this cannot be exploited via a clean threshold gate (Q2 requires BOTH an upper and lower bound). Phase 1 NO-GO for a volume filter. Rule: do not propose iFVG inversion bar volume as a standalone quality gate — the non-monotonic pattern invalidates any simple threshold-based filter.

Appended 2026-06-14 (wk3-r2 B55 Phase A pipeline benchmark):

106. **The combine-harness monthly pass rate is not a valid proxy for funded-pipeline throughput when the config change reduces signal emission.** B55 confirmed: target_clarity_mode=reject (B52 finding: 9/61 monthly passes vs 6/61 for deployed) reduces combine attempts from 159 to 69 over 5y by rejecting low-clarity-target signals. Despite higher per-attempt pass rate (38% vs 26%), B55 generates only 26 absolute combine passes vs B42's 42, making pipeline $508/mo sust=2.00x — worse than B42's $549/mo sust=3.23x on BOTH metrics. The mechanism: target_clarity=reject cuts signal volume; fewer signals per month means lower P&L variance; lower variance means each combine attempt takes longer to resolve (14.9d vs 6.5d avg); longer attempts mean fewer total attempts in 5y; fewer attempts → fewer absolute passes → pipeline degrades. This is the same throughput-vs-rate tradeoff as Lesson 83 (combined engine) and Lesson 41 (B41), now confirmed for config-level volume reduction. 2022 holdout exception: B55 PF=1.164 (positive) vs B42's 0.934 (loss-making) — the target_clarity filter genuinely improves regime-robustness in drought years, but this does not compensate for the 5y throughput penalty in the deployment window. Monday action: keep deployed settings (swing_stop_lookback=30, target_clarity_mode=off). Rule: any config change that reduces signal count must be pressure-tested at the pipeline level (funded_sim continuous attempts), not just at the combine-harness level (monthly 61-month simulation). The combine harness normalizes by TIME; the pipeline sim normalizes by THROUGHPUT — they diverge when frequency changes.

Appended 2026-06-14 (B55 Silver Bullet):

Appended 2026-06-13 (B56 SetupGrader per-component audit):

107. **The SetupGrader fvg_singular criterion is INVERTED: scoring singular=True (one clean FVG) as higher quality than singular=False (stacked/multi-FVG zone) is backwards — the data shows the opposite.** 5y audit (n=1276, scripts/audit_grader.py): fvg_singular=True PF=0.962 (n=478) vs singular=False PF=1.145 (n=798), ratio 0.840 (well below 0.85 noise floor). The grader assigns positive score points to singular=True (a "clean" single FVG), but stacked FVG zones with multiple overlapping imbalances outperform clean singles. Mechanism candidate: stacked FVGs represent repeated institutional order flow in the same zone, creating stronger structural demand/supply; a "clean" singular gap is statistically more likely to be a first-touch that fails or a thin zone that gets pierced. Rule: do NOT use fvg_singular=True as a positive signal quality indicator. The criterion must be REMOVED or INVERTED before any grade floor is raised above F.

108. **Two SetupGrader criteria (mom/pd) are structurally vacuous under the deployed iFVG config: 100% True for mom, 100% False for pd.** 5y audit (n=1276): momentum criterion (body_to_atr >= 1.0) is True for ALL 1276 trades — the engine's min_absolute_body=5.0 override pre-filters all weak-body signals before the grader runs, making the grader's momentum check redundant. Premium/discount criterion (pd_ok) is False for ALL 1276 trades — in the deployed long-only setup, price never satisfies the pd_ok condition. Both criteria contribute 0 discriminatory power; the grader effectively starts every signal at 15 pts (momentum baseline) with pd never adding its potential 20 pts. Rule: when re-designing the grader (B57), do not carry forward mom or pd as implemented — they require rethinking in the context of the engine's own pre-filters.

105. **Volume is a prerequisite for funded pipeline sustainability: strategies restricted to < ~20% of trading hours cannot sustain the two-phase pipeline regardless of that hour's raw PF.** B55 confirmed: restricting iFVG signals to 10:00-11:00 ET (1 hour/day, the historically strongest NY-AM hour, wk1-r2 PF=1.235) yields sust=0.42-0.49 at both r=1.0 and r=1.25 — far below the B19 baseline of 1.60. The mechanism is structural: the two-phase pipeline requires consistent monthly Combine pass volume to replace busting XFA accounts; ~5-10 signals/month from a 1-hour window is insufficient to generate that pass volume. The per-hour PF advantage (1.235 vs 1.11 average) is real but too small to compensate for the 80% volume cut. Lesson 89 is confirmed again: the wk1-r2 per-hour PF was measured on the population-level config and did not survive restriction to the specific deploy config under the exact benchmark harness. Rule: a time-concentration hypothesis must demonstrate BOTH improved PF AND maintained >= 40% signal volume retention to have any chance of improving funded pipeline metrics. Silver Bullet joins B18/B44 as evidence that iFVG time-windowing does not add pipeline value.

Appended 2026-06-13 (B56-orb-align ORB×iFVG alignment gate Phase 2):

109. **A Phase 1 GO verdict does not guarantee Phase 2 pipeline improvement when the gate removes a high fraction of an already-sparse signal stream.** B56 ORB×iFVG alignment gate Phase 2 (2026-06-13): gate suppresses ORB when no prior same-direction iFVG has fired that day (groups B+C, 42.2% of ORB trades, PF=0.963). Phase 1 GO was valid (ratio 1.48x > 1.4 threshold, 5/5 years). Phase 2 result: funded accounts average 18.7d life (vs 73.5d baseline), 51/52 accounts bust before payout, pipeline sust=0.82x (unsustainable). Mechanism: ORB already fires at most once per day with reentry; removing 42.2% of days leaves the funded account with too few trades to compound to payout before normal drawdown hits MLL. The per-account earnings collapse from $3,131 to $1,808 because accounts close prematurely with partial losses, not because the remaining trades are worse. Rule: before building any cross-engine gate, estimate the post-gate trade frequency (trades/month × funded_avg_days) and verify it is above the XFA payout threshold (~15 trades minimum). ORB at 4-6 signals/month already sat near the lower bound; a 42% cut pushed it below survival threshold. This confirms and generalizes Lessons 94 (B47) and 105 (B55 Silver Bullet): volume starvation is the dominant failure mode for Phase B gates that don't also reduce the bust trigger frequency proportionally.

Appended 2026-06-14 (B57 composite quality grader — Phase 1 NO-GO):

110. **Validated contextual predictors (side, rank, session hour, engine) are not additively independent — a composite weight-of-evidence score does not outperform the best single predictor on OOS data.** B57 Phase 1 (n=3507 iFVG+ORB trades, train 2021+2023+2024, OOS 2025+2026): composite WoE score (9 features including is_long, is_rank1, is_orb, hour buckets, interaction is_long_ny_am) produced OOS top-decile/bottom-decile PF ratio of 1.081 (threshold 1.30) and top-half PF 1.187 — below the best single predictor (rank-1-only OOS PF 1.234). The WoE for hour_london is NEGATIVE (-0.196) in the combined iFVG+ORB dataset, even though B19 showed London+NY AM improves long-only iFVG funded metrics. This is because the combined population includes loss-making iFVG shorts in London, which dominate the marginal WoE signal. Stacking side+rank+hour through WoE does not break through the rank-1 ceiling because these features share variance (rank-1 ORB signals, which always score rank-1, also drive the is_orb advantage; and the best iFVG signals are rank-1 long in NY AM — exactly what B19 found as the strongest single-filter combination). Rule: composite scores over contextual features are unlikely to beat the best single predictor on NQ 5min because the "good signal" characteristics (long, first-of-day, NY-AM, ORB) are highly correlated — they tend to co-occur. Apply the strongest single filter (B19 long-only+london+ny_am for iFVG funded, B42 deployed for combine) rather than attempting to score a composite.


Appended 2026-06-14 (B57 iFVG r_multiple sensitivity):

111. **Lowering iFVG r_multiple from 3.5 to 2.5 improves the two-phase pipeline on BOTH metrics: $566/mo (+$17) and sust 3.54x (+0.31x vs B42 $549/mo, 3.23x).** B57 (2026-06-14): 61-month combine sweep (deployed config, MNQ) shows r=2.5 passes 11/61 (18%) vs r=3.5 baseline 10/61 (16%); two-phase funded sim (Phase A equity variants + B21 ORB-reentry Phase B, haircut $200) confirms r=2.5 and r=2.0 both beat baseline while r=3.0 is worse. Mechanism: shorter targets produce more frequent Combine wins — lower reset cost per funded account ($545 vs $568 at r=2.5 vs r=3.5) more than offsets any reduction in per-winner magnitude. The r=3.5 MNQ override was set historically without a funded-pipeline sensitivity test. Rule: prefer the lower r_multiple (2.5) for the deployed iFVG combine-phase; the base StrategyParams.r_multiple is already 2.5 — simply removing the MNQ r_multiple override from 3.5 is sufficient. r=2.0 and r=2.5 are equivalent pipeline-wise ($567 vs $566/mo); r=2.5 requires fewer config changes. 2022 holdout confirms the advantage holds in the 2022 bear market (r=2.5: 25.8% pass rate vs r=3.5: 24.6%).


Appended 2026-06-14 (B58 confluence-weighted sizing):

112. **A confluence size ladder (up-size high-count signals, down-size low-count signals) fails when the high-conviction cohort is sparse and the features are correlated.** B58 (2026-06-14): ladder 0.5x/1.0x/1.5x keyed on count of aligning validated edges (long side, rank-1, Silver Bullet hour, combined engine) → $430/mo, sust 0.65x vs flat-size control $797/mo, 0.77x (REJECT: worse on both). Root cause: count>=3 requires all four features to align simultaneously; since they are correlated (rank-1 long signals in NY AM also tend to hit the 10 ET window; ORB context correlates with rank-1 because ORB fires at most once per day), the high-confluence cohort is sparse (~20% of trades). The majority of signals score count<=1 and receive 0.5x size — equivalent to halving the average risk budget on 40-50% of trades. The per-trade edge of high-confluence signals is not materially higher than the general population, so the down-sizing on common trades dominates the up-sizing on rare ones. This generalizes the failure mode of B47 (gate removes volume) to the size axis: any mechanism that reduces average bet size without proportional edge uplift will underperform flat sizing. Rule: before applying any size ladder, verify with MFE/MAE data (B2) that the high-confluence cohort has materially different per-trade PnL distribution at equal risk, not just higher PF from survivor bias. The confluence count IS still computed on every signal (useful for future analysis) and the code ships default-off; it is NOT deployed.

Appended 2026-06-14 (B59 sweep-reentry micro-engine):

113. **Recovery overlay signals (fires after a same-day primary loss) amplify funded account bust risk rather than recover it — unless the whole-day risk budget is explicitly managed.** B59 (2026-06-14): long-only sweep-reentry engine (arm after ORB long stop → sweep session/prior-day low by >= 0.25×ATR → bullish displacement+FVG inversion) tested as funded-phase overlay on top of ORB. Results: at risk=0.25% overlay marginally improves sust (2.0x vs 1.6x, ORB r=2.5) — 1-bust noise. At risk=0.50%: overlay HURTS sust (1.27x vs 2.14x, -41%); at risk=1.0%: both pipeline-negative, overlay slightly worse (0.64x vs 0.68x). Mechanism: the overlay fires AFTER the funded account has already absorbed an ORB loss. Any additional trade on that day (win OR loss) moves the account closer to its Moving Loss Limit, since the day started at -1 loss unit. If the overlay also loses, the account is at -2 losses, dramatically increasing bust probability before the next payout. The trigger chain (ORB long → stop → sweep → displacement+FVG within 10 bars) is also very sparse, so the win side cannot counterbalance the bust-risk amplification at the account level. Rule: before adding any overlay signal that fires on the same day as a primary loss, estimate the combined daily loss distribution and verify that the worst-case tail (both lose) stays above the funded MLL by a safe margin. If not, reduce primary-signal size to create budget for the overlay — do NOT add signals at full independent risk. The inversion quality filter and swept-extreme stop are mechanically correct; the failure is in funded-account risk architecture, not signal design.

Appended 2026-06-14 (B60 ES/MES cross-instrument diagnostic):

114. **The iFVG sweep+inversion edge is NQ/MNQ-specific; it does not transfer to ES/MES.** B60 (2026-06-14): iFVG on ES.v.0 (5y 1-min bars, ATR-normalized thresholds) → PF 0.896 (losing), combine 9.2% pass rate vs MNQ ~21%. The FVG/displacement/swept-extreme pattern family was discovered and validated exclusively on NQ price action. ES has a different institutional footprint, contract size, and daily range distribution. Attempting to apply the same structural rules to ES produces a losing strategy — the pattern does not generalize. Rule: before expanding any strategy to a new instrument, run a 5y equity_export first (cheap); a PF below 1.0 is immediate REJECT without further investigation.

115. **ORB (opening range breakout) has weak but positive cross-instrument edge on ES/MES; funded pipeline sustainability fails at 1-contract sizing.** B60 (2026-06-14): ORB r2.5 on ES → PF 1.075, combine 15.4% pass rate, standalone sust 0.55x. Two-phase pipeline sust (iFVG combine passes / ORB XFA busts) = 9/83 = 0.11x vs MNQ B42 3.23x — 29x gap. The ORB time-of-day breakout structure partially generalizes across index futures (PF > 1), but the funded account economics fail at 1-contract MES ($5/pt) because per-trade dollar P&L is smaller relative to the Moving Loss Limit, causing faster account busts. Do NOT expand the funded pipeline to MES without contract scaling: use ~3-5 MES contracts per account to match the dollar risk of 1 MNQ contract, which would proportionally improve sust.

Appended 2026-06-14 (B61 excursion-ladder exits):

116. **BE-trail at 1.5R (the boundary case after B2 rejected 1.0R) still hurts the NQ ORB funded pipeline: XFA busts jump 54% (13->20) and net/account drops 27% ($3131->$2270).** B61 (2026-06-14): full 5y grid (BE@1.5R, partial@2.0R, partial@2.5R, combinations) on both iFVG Phase A and ORB-reentry Phase B. Every one of the 20 pipeline combos is worse than the fixed-target baseline on both $/mo AND sustainability — stop rule fires. Mechanism: ORB reentry trades have tight risk (r=0.75) and a 2-leg structure that already compresses favorable excursion; early exits clip the distribution tail that funds the pipeline. iFVG Phase A exits are softer failures (pass rate drops 10-19%) because exiting earlier increases reset cost per funded account. Rule: do NOT revisit exit-ladder policies unless a fundamentally different mechanism is proposed (e.g., a trailing ATR stop engaged only AFTER 2.5R MFE, with the standard stop locked until then). The fixed-target policy is the right policy for both iFVG and ORB on NQ. Lesson 20 (BE@1.0R) now extends to the 1.5R boundary; the failure mechanism is the same.

Appended 2026-06-14 (B62 orderflow-proxy confirmation/veto for ORB — Phase 1 NO-GO):

117. **A bar-derived CLV cum-delta proxy is collinear with the breakout condition and carries no independent signal at the ORB breakout.** B62 Phase 1 (n=1030, 5y excl 2022): `delta = volume·(2·(close−low)/(high−low)−1)`, 3-bar signed and volume-normalized to `cd3_ratio∈[−1,1]`. Top/bottom-40% PF ratio = 1.039 (vs 1.40 GO threshold), no year consistency. RVOL (breakout vs prior-20 same-TOD) = 1.037. Combined confirm gate = 1.044. Root cause: a breakout bar *closes beyond the OR edge by definition*, forcing its close into the top/bottom of its own range → positive directional CLV-delta almost tautologically (93.3% of breakouts have cd3_ratio>0; 5th pct only −0.04). The 7% "not-confirmed" minority actually has HIGHER PF (1.361 vs 1.204), so a confirm gate removes a slightly better cohort. Phase 1 NO-GO → no engine built. Rule: any OHLCV-derived "orderflow confirmation" proxy that keys off close-position-in-range will be collinear with whatever price condition already fired the signal — it cannot add independent edge. Real orderflow confirmation needs genuinely independent data (tick/L2 delta). This is the 4th consecutive ORB day/bar-level quality-predictor rejection (B5 prior-day range, B45 OR width, B49 breakout extension, B62 orderflow proxy); the ORB edge lives in the 4h+ EOD-flatten cohort (Lesson 88) and resists single-dimension bar filters.

Appended 2026-06-14 (B63 pipeline-aware funded-only sizing/routing variants):

118. **iFVG long-only close-mode as the funded Phase B engine cycles accounts 3x faster than ORB-reentry (avg 25.7d vs 73.5d) but busts at nearly the same rate per account, destroying pipeline sustainability.** B63 (2026-06-14): B42 Phase A (42 passes) + iFVG-LO r1.25% Phase B → sust=1.08x vs B21 3.23x. The faster cycling produces higher $/mo ($1037 vs $549) ONLY because 1.25% risk is used vs 0.75% for ORB-reentry; risk-equalized performance is not tested. Per-account net ($3054) matches ORB-reentry ($3131), confirming comparable edge, but higher per-trade variance + the $2k XFA MLL means iFVG accounts bust in ~26d rather than ORB's 73d. Rule: sust below 2.0x is fragile — the 3.23x B21 reference provides meaningful safety margin; 1.08x does not. Always verify sust AND $/mo before calling a Phase B improvement; a $/mo increase driven purely by risk escalation is not genuine edge improvement.

119. **The "early win boost" intraday pattern (first-trade win → second-trade PF 1.473x higher than after first-trade loss) is a strong signal-quality discriminator but does not improve funded pipeline sustainability.** B63(a) (2026-06-14): Phase 1 check on 5y combined iFVG+ORB trade data (n=962 two-trade days): PF_after_win=1.394 vs PF_after_loss=0.946, ratio=1.473 (strongest intraday discriminator found in this research program). Phase 2 post-processing: combined engine as funded Phase B, +0.5x boost to second trade after first wins. Result: sust 0.76x (no boost) → 0.91x (with boost); neither clears 1.0 → REJECTED. The direction of the boost is correct (improves both npm and sust) but the combined engine is fundamentally too volatile for XFA MLL rules — boosting size on winning sequences also amplifies bust risk in the frequent losing sequences. Open thread: the 1.473x ratio may be usable as a DAY-FILTER (trade second signal only if first won) rather than a SIZE-MODIFIER — reducing volume but improving per-trade quality may have a better funded-account profile than size escalation.


Appended 2026-06-14 (B65 Markov 2.0 regime FILTER -- Phase 1 NO-GO):

120. **Stride-sampled Markov 2.0 daily regime does not gate NQ 5min intraday trade quality; the signal is too weak and the short direction is backward.** B65 (2026-06-14): 20-day non-overlapping stride sampling (FIX 1) shows true BULL persistence = 0.21 vs overlapping 0.83 -- the method works correctly but the honest signal is weak. Phase 1 PF separation: iFVG longs BULL/BEAR ratio=1.28x (below 1.3x GO threshold, 1/5 years consistent); ORB longs BACKWARD (bear-regime outperforms); shorts backward for both engines. The backward short direction is structurally explained by NQ long-bias (Lesson 8): even in a macro bear regime, intraday demand structure persists at the 5min level, so bear-regime iFVG/ORB shorts underperform rather than outperform. FIX 2 labels are directionally correct but the +-5% threshold yields 67% SIDEWAYS windows (52/77), limiting discriminability. Rule: do NOT test further daily-macro-regime-to-intraday-quality gating mechanisms; this class of feature (B35 daily-bias direction, B5 prior-day range, B65 Markov regime) has now failed 3-for-3 with consistent explanation (NQ structural long bias overwhelms macro context at the 5min level).

Appended 2026-06-14 (B64 SMT divergence NQ vs ES -- Phase 1 NO-GO):

121. **NQ-vs-ES SMT divergence (NQ sweeps its prior level but ES does NOT) does not materially separate iFVG trade quality in the deployed long-only config.** B64 (2026-06-14): Phase 1 labeling of 2453 iFVG trades (5y excl 2022) using sweep-bar detection (argmin NQ.low in 20-bar pre-entry window) + ES 5-bar prior range check. Divergence rate = 28.4%. LONG ratio = 1.166x (PF 1.278 div vs 1.096 no-div, below 1.25x GO threshold); year consistency 2/5 (2021 FAIL, 2023 FAIL, 2024 OK, 2025 FAIL, 2026 OK). SHORT ratio = 1.436x (GO) but deployed config is long-only and short iFVG signals are already weak (B15). The cross-instrument non-confirmation concept is mechanically distinct from prior failures (not a same-instrument shape filter) but does not produce actionable separation at NQ 5min resolution. Root cause: the iFVG sweep/displacement/inversion chain already filters the highest-quality reversal candidates; adding a cross-instrument sanity check on the sweep step provides marginal independent information at 5min granularity where both instruments move in near-lockstep. Rule: SMT divergence from daily/15min charts is the ICT intended application; applied to 5min bar data with OHLCV-only price comparison, the signal does not transfer. This is the 5th consecutive TradeZella/ICT external-claim failure in Phase 1 (B47 confluence gate, B49 breakout extension, B54 pre-RTH alignment, B62 orderflow proxy, B64 SMT divergence); do NOT queue further ICT/SMT mechanism variants without genuinely new Phase 1 evidence.

Appended 2026-06-14 (wk4-r3 research session):

122. **Inversion bar CLV (close-strength within the 5-min inversion bar) is an INVERTED and non-monotonic predictor of iFVG quality.** wk4-r3 Phase 1 (n=2477, 5y excl 2022, 100% bar match): CLV = (close - low) / (high - low) of the entry bar. Quintile PF: Q1 (weakest close) = 1.460, Q2 = 0.967, Q3 = 0.879, Q4 = 0.973, Q5 (strongest close) = 0.986. Top-40%/Bottom-40% ratio = 0.817 -- INVERTED. Per-year: 3/5 years show bottom > top. Mean CLV = 0.514 (closes slightly above bar midpoint on average). The hypothesis (strong close = strong absorption conviction) is backwards for iFVG: weak inversion bar closes (price swept briefly into the FVG zone then pulled back toward bar low, but still inside the zone) have higher PF than strong closes. Non-monotonic and year-inconsistent -- not actionable as a standalone filter. This extends the pattern of non-monotonic single-dimension quality filters: B45 (OR width), B49 (breakout extension), B51 (grade), B62 (ORB CLV), and now inversion bar CLV. Rule: do not propose close-position-within-bar metrics as standalone iFVG quality gates -- they are systematically non-monotonic.

124. **The Phase B orb_r_multiple sweep (target exit R-multiple) is irrelevant when the Phase B engine uses combined (iFVG+ORB) rather than ORB-only.** B67 tested orb_r_multiple={1.5, 2.0, 2.5, 3.0, 3.5} in Phase B (engine=combined, risk=0.75%, partial_r=1.5) vs B42 baseline (Phase B = ORB-only, orb_r_multiple=2.5). All 5 variants are catastrophically worse: busts jump from 13 (ORB-only) to 66-71 (combined), $/mo falls from $549 to $213-$253, sust collapses from 3.23x to 0.59-0.64x. Root cause: the combined engine adds iFVG loss exposure on every trading day; on days where both iFVG and ORB lose, the account absorbs -1.5%+ daily, triggering MLL far faster than ORB-only. The r_multiple setting affects only ~11% of ORB trades (those reaching the full target before EOD, per Lesson 88); engine-level bust frequency dominates. Rule: Phase B funded accounts must use the ORB-only engine. The combined engine is strictly a Phase A (combine-phase practice account) tool. Never run engine=combined on XFA funded accounts.

123. **The B63 combined second-trade PF ratio (1.473x after-win vs after-loss) is specific to exactly-2-trade days and does not generalize to all multi-signal days.** wk4-r3 verification (n=2415 second-signals, all multi-trade days combined, 5y excl 2022): second-trade after-win PF = 1.175 vs after-loss PF = 1.000, ratio = 1.175 (vs B63 original 1.473x). The discrepancy is because B63 used exactly-2-trade days (n=962 second signals) while the full dataset includes rank-3, rank-4, etc. signals where quality degrades further. The iFVG-specific chain (iFVG-after-iFVG): after-win PF = 1.104 vs after-loss PF = 0.928, ratio = 1.190 (2/5 years consistent) -- also NO-GO. Non-first iFVG signals have overall PF = 0.981 (slightly negative), confirming rank-1 dominates the strategy edge. Rule: the B63 after-win/after-loss discriminator is real for exactly-2-trade days but loses strength when extended to higher-rank signals; using it as a filter in the all-day deployed config (where 3-5 signals/day are common) would not reproduce the 1.473x ratio seen in the 2-trade-day subsample.

125. **The DOW day-of-week go criterion (target DOW PF < 1.0 in 3+/5 years AND overall PF < 0.90) can fire spuriously when ALL days are losing with similar PF.** B68 (2026-06-15): in the deployed config (engine=combined, close-mode, both-sides, r_mult=3.5), all five days have PF 0.61-0.65 with no material differentiation. Thursday PF=0.645 is the second-best day (vs non-Thursday weighted average 0.623); blocking Thursday would remove above-average trades and worsen overall PF. The formal criterion was designed for a winning strategy where a single day is uniquely weak -- it does not guard against the case where all days underperform equally. Rule: before proceeding to Phase 2 of any DOW block, verify that the target DOW PF is materially WORSE than the non-target DOW average. If all days are within ~0.05 PF of each other, there is no actionable DOW pattern regardless of whether the criterion fires.

126. **DisplacementEvent.displacement_bar.ts is structurally always 1 bar before the inversion-confirmation bar (Signal.created_at) — it is not a freshness measure.** The 3-bar detection window forces: b2=displacement_bar (inversion happens here), b3=current bar (event fires when b3 closes). gap = b3.ts - b2.ts = 1 bar always. The meaningful FVG-age metric is event.fvg.created_at (timestamp of the bar that originally confirmed the FVG zone). Use that for any staleness analysis.

127. **FVG age at inversion (gap_bars = entry_ts - fvg.created_at) is directionally predictive but fails the 1.30 gate threshold.** B69 (2026-06-15, 1276 trades, 5y excl 2022): fresh (1-3 bars) PF=1.087, mid (4-9 bars) PF=1.176, stale (10+) PF=0.846. Ratio fresh/stale = 1.285 -- MISSES 1.30 threshold. Also non-monotonic (mid > fresh). The displacement_ts field is retained in Signal and trade output for future cross-cuts; do not propose a freshness gate without fresh evidence of a higher ratio or a specific joint filter (e.g., fresh + killzone).

Appended 2026-06-15 (wk5-r1 research session):

128. **The deployed bot_config.json does not set `allowed_sides`, so StrategyParams default "both" governs — trading loss-making iFVG close-mode shorts.** wk5-r1 Phase 1: combine harness with `allowed_sides=long, r_multiple=2.5` yields 14/61 passes (23%), PF 1.19 vs B42 deployed both-sides, r=3.5: 10/61 (16%), PF 1.06. The +4 passes (+40%) come entirely from removing iFVG shorts (ORB shorts remain active — 250 short exits at PF 1.05 still fire via the ORB component, which is not gated by allowed_sides). B15 and B96 established iFVG close-mode shorts are loss-making; this confirms the gap between the intended config (long-only iFVG) and the actual deployed config (both-sides iFVG). Rule: add `"allowed_sides": "long"` to bot_config.json.strategy to stop trading iFVG shorts. This is separate from the ORB component which may trade both directions. The combined effect with r_multiple=2.5 (B57 candidate) has not yet been modeled in the full two-phase pipeline (B71 pending).

Appended 2026-06-15 (B70 Phase B ORB-only orb_r_multiple sweep):

129. **orb_r_multiple=2.5 in the ORB-only Phase B engine is the natural optimum; sweeping the range {1.5, 2.0, 3.0, 3.5} makes both $/mo and sustainability worse.** B70 (2026-06-15, 5y excl 2022, Phase A=B42): r=1.5 gives $496/mo sust=2.10x; r=2.0 gives $520/mo sust=2.00x; r=3.0 gives $505/mo sust=2.21x; r=3.5 gives $535/mo sust=2.10x — all below B42 baseline ($549/mo, sust=3.23x). Root cause: the ORB Phase B exit mechanism is 89% EOD-flatten (Lesson 88); orb_r_multiple only affects the 11% that hit target before EOD. Lowering r clips those by-target exits at a closer price (smaller wins), while also increasing account count (shorter duration) but with worse economics. Raising r makes target harder to reach, converting some early-exits to EOD flattens without benefit. The r=2.5 sweet spot is where ORB's stop geometry (stop below swept extreme) pairs with the EOD-flatten mechanism optimally — the 11% early-exit winners at r=2.5 capture just enough to maximize cycle economics. Contrast with B57 (iFVG Phase A): lower r=2.5 improved Phase A by hitting the $3k combine target more often; that mechanism does not apply to Phase B because XFA payout is not a fixed per-trade R milestone. Rule: do not revisit Phase B orb_r_multiple; it is confirmed at 2.5.

Appended 2026-06-14 (B71 LO+r=2.5 full pipeline benchmark):

130. **allowed_sides=long reduces total Phase A combine attempts 28% (115 vs 159 over 5y excl 2022), netting fewer absolute passes (40 vs 42) despite a higher per-attempt pass rate (35% vs 26%), and reduces pipeline sustainability (3.08x vs 3.23x B42 / 3.54x B57).** B71 (2026-06-14): LO+r=2.5 Phase A gives $571/mo but sust=3.08x vs B57 both-sides r=2.5 at $566/mo sust=3.54x and B42 at $549/mo sust=3.23x. The monthly combine harness (wk5-r1: 14/61=23% vs 10/61=16%) overstated the pipeline benefit because it uses SLOTS (per-calendar-month pass/fail) while the funded pipeline counts ABSOLUTE PASSES over continuous attempts. When signal volume drops (LO removes iFVG shorts), attempt frequency drops (9.0d/attempt vs 6.5d for B42), longer attempts consume more of the calendar, reducing absolute passes even though per-attempt quality improves. Root cause: loss-making iFVG shorts still generate combine attempt opportunities — removing them improves per-attempt PF but reduces throughput. Lesson 83 (harness-pipeline decoupling) is most pronounced when volume changes. Rule: do NOT add allowed_sides=long to deployed config solely on combine harness evidence. B57 (remove MNQ r_multiple override, both-sides) remains the only clean improvement over B42. The combine harness is reliable for config changes that do not materially change signal frequency; it misleads when frequency changes significantly.

Appended 2026-06-14 (B72 iFVG rank-1-only Phase 1 NO-GO):

131. **Rank-1 iFVG long signals (first long of the day in LO close-mode config) do not materially outperform rank-2+ longs: PF=1.162 vs PF=1.091, far below the 1.50 GO threshold. In all three test years (2023-2025), rank-1 PF (0.93-1.03) is LOWER than rank-2+ PF (1.11-1.23), reversing the expected ordering. Only 2/5 years exceed 1.50, both small samples or partial-year.** B72 (2026-06-14, mfe_mae_ifvg_clean.csv filtered long-only, re-ranked within day, 5y excl 2022): the bulk of rank-1's 5y edge comes from 2021 (1.876) and 2026 partial (1.967), likely regime artifacts. The hypothesis that "first signal of the day is highest quality" is falsified in the iFVG close-mode context. The 37% signal reduction alone warrants a very high bar (1.50); the evidence shows no reliable advantage. Rule: do not build a rank-1-only gate; include all daily long iFVG signals in LO config.

Appended 2026-06-14 (wk5-r2 research/ideation session -- Phase 1 falsifications):

132. **ADX(14) at signal bar is a non-monotonic iFVG quality predictor and fails Phase 1 at ratio 1.001x.** wk5-r2 (2026-06-14, 2477 iFVG signals, 5y excl 2022, 5-min bars): Q1 PF=1.119, Q2 PF=0.922, Q3 PF=1.018, Q4 PF=1.120 -- V-shaped, classic non-monotonic pattern. High-ADX threshold (>=30) gate inverted: ADX>=30 PF=1.023 vs ADX<30 PF=1.115 (ratio 0.917 -- gated set WORSE). Rule: trend-strength indicators (ADX) join bar-level magnitude metrics (CLV, body ratio, OR width) as non-monotonic iFVG quality predictors. The iFVG mechanism is structural (zone creation + inversion), not trend-aligned, so trend-strength measures are orthogonal to signal quality. Do not propose ADX-class gates without a mechanism rationale that explains why trend strength should select iFVG inversions.

133. **ORB overnight gap alignment and opening range bias both fail Phase 1 (ratio 1.086x and 1.288x respectively, both below 1.40 threshold).** wk5-r2 (2026-06-14, 1027-1030 ORB trades, 5y excl 2022): gap-aligned (gap direction matches ORB direction) n=511 PF=1.267 vs opposed n=516 PF=1.167, ratio=1.086 -- NO-GO. Range bias (OR midpoint biased toward ORB direction vs prior close) n=196 vs n=49 ratio=1.288 -- below threshold AND small opposed sample. These results extend Lesson 117 (B62 gap-phase finding: "any OHLCV-derived measure collinear with or dominated by the breakout condition") to overnight-gap and range-bias variants. The ORB breakout condition already requires price to close outside the OR -- any prior-price alignment that predicts this is already captured in the breakout filter itself. Rule: do not propose OHLCV-derived context filters for ORB without a mechanism rationale showing orthogonality to the breakout condition.

Appended 2026-06-14 (B73 ORB×iFVG alignment gate in Phase A combined engine):

134. **The ORB×iFVG directional alignment gate improves the monthly combine harness but degrades the continuous funded pipeline — Phase 1 GO + combine-harness GO still ≠ pipeline GO.** B73 (2026-06-14) tested `orb_ifvg_alignment_required` (the B56 gate: suppress ORB when no prior same-direction iFVG fired that day) in the Phase A COMBINED engine, where iFVG fires on all days so the ~8% ORB-volume cut doesn't starve the account (unlike B56 ORB-only, Lesson 109). Combine harness: gate 11/61 (PF 1.12, short PF 0.97) vs baseline 10/61 (PF 1.06, short PF 0.84) — the gate removes loss-making conflicted ORB shorts and improves monthly quality. But the funded pipeline degrades on BOTH metrics at both r-multiples: gate r3.5 $541/mo sust 2.92x vs B42 $549/3.23x; gate r2.5 $559/mo sust 3.15x vs B57 $566/3.54x. Mechanism (identical to Lessons 83/130): the gate thins ORB volume → less daily P&L variance → attempts resolve slower → fewer absolute Phase A passes (42→38, 46→41) at essentially unchanged per-attempt pass rate (26.0% vs 26.4%); since Phase B busts are fixed at 13, sust scales directly with passes and drops. Stop rule fired (gate r3.5 worse than B42 on both). Rule: a quality gate that passes Phase 1 (1.48x PF separation) AND improves the 61-month combine harness can STILL fail the pipeline if it reduces signal frequency — always run the continuous funded_sim, never stop at the harness. The combine harness normalizes by calendar-month slots (rewards monthly PF); the pipeline normalizes by throughput (punishes volume cuts). Do not enable orb_ifvg_alignment_required live; B57 (base r=2.5, no gate) remains the only clean improvement over B42.

Appended 2026-06-14 (B74 per-hour iFVG PF audit, deployed close-mode):

135. **Hour 9ET (09:00-09:30 ET, pre-RTH window) is the most consistently loss-making iFVG hour in deployed close-mode config: PF=0.487, loss-making in ALL 5/5 test years (2021-2026 excl 2022), n=211 (8.9% of iFVG signals).** B74 (2026-06-14, 2376 iFVG trades, --partial-r 0, engine=combined, close-mode, r=2.5, 5y excl 2022). All four hours meeting GO criterion (PF < 0.90, 3+/5 years): 6ET PF=0.578 (3/5), 9ET PF=0.487 (5/5), 13ET PF=0.735 (3/5), 21ET PF=0.788 (3/5). Despite Phase 1 GO, Phase 2 failed: block_9 → $564/mo sust=3.38x; block_6_9 → $560/mo sust=3.15x — both WORSE than B57 ($566/mo, sust=3.54x) on both metrics (stop rule fires). Root cause: blocking 8.9% of iFVG signals creates a marginal throughput penalty (44 vs 46 Phase A passes) that offsets the quality improvement. B57's r=2.5 change is more impactful than any per-hour gate (it globally improves Combine target-hit frequency without cutting volume). The block_9 variant does beat B42 on both metrics ($564 vs $549, 3.38 vs 3.23) — confirming the 9ET hour is genuinely loss-making — but doesn't reach the B57 bar. Rule: the 9ET per-hour loss is real and documented; do not block it in production unless a future config change reduces total iFVG volume to a level where a quality gate adds more than it cuts.

136. **`equity_export.py --trade-csv` requires `--partial-r 0` for accurate per-trade PnL analysis. With partial_r > 0, `_reconstruct_trades` pairs each entry fill with the FIRST exit fill (the partial), capturing only partial-winner PnL for winners and full-loss PnL for losers — making every hour appear loss-making even when the strategy is aggregate-profitable.** B74 infrastructure discovery: initial run with --partial-r 1.5 produced all-hours PF < 0.50 despite aggregate PF=1.08; re-run with --partial-r 0 produced coherent per-hour results. Root cause: with two exit fills per winning trade (partial at 1.5R + final at target), the strict-alternation fill-pairing in _reconstruct_trades creates "trades" that show only +~0.75R wins vs -1.0R full losses, making per-trade statistics incorrect. The equity curve is always correct (uses all fills); the trades list is only correct when partial_r=0 (single exit per trade). Rule: for any per-trade analysis requiring per-trade PnL (per-hour audit, per-DOW audit, per-killzone audit, grade analysis), run equity_export with --partial-r 0 and note this in the journal entry.

Appended 2026-06-14 (wk5-r3 research/ideation session -- inline Phase 1 falsifications):

137. **r_multiple below 2.0 for Phase A iFVG is confirmed sub-optimal; the B57 r-sweep is complete.** wk5-r3 inline (2026-06-14, combine harness 61 months, deployed config): r=1.5 yields 9/61 passes (15%), PF=1.05 -- fewer passes than B42 baseline (10/61, 16%) and B57's 11/61 (18%). The B57 r-sweep ({2.0, 2.5, 3.0, 3.5}) found the combine-harness optimum at r=2.0 (12/61); r=1.5 extends the downward curve below that. Pipeline optimum is confirmed at r=2.0-2.5 (both giving $566-567/mo, sust=3.54x in B57). Rule: do not test Phase A r_multiple below 2.0 -- the monotone decrease in combine passes continues and the funded pipeline economics will not improve.

138. **Displacement bar body/ATR ratio is an INVERTED and non-monotonic iFVG quality predictor (research baseline, LO longs, 5y excl 2022, n=1214).** wk5-r3 inline (2026-06-14): displacement bar approximated as entry_ts - 2 bars (600s). Body = abs(close - open) / ATR(14) at that bar. Top/bottom-40% ratio = 0.914 (INVERTED). Quintile PF: Q1(0.000-0.158)=1.006, Q2(0.159-0.319)=1.303, Q3(0.320-0.519)=1.336, Q4(0.519-0.788)=1.097, Q5(0.791-2.985)=1.004. Mid-range body/ATR (Q2-Q3) outperforms both extremes; the STRONGEST displacement bars (Q5) have the lowest PF. Year consistency: 1/5. Extends the non-monotonic predictor graveyard (Lessons 90/99/117/122) to the displacement bar itself: how forcefully the FVG was created does not predict how well the subsequent inversion trade performs. Root cause: the displacement bar creates the FVG ZONE; the quality of the inversion that triggers entry (independent of zone creation strength) is what determines trade quality. Rule: do not propose displacement bar body/ATR (or equivalent impulse-strength metrics) as iFVG quality gates without genuinely new mechanism rationale.

Appended 2026-06-14 (B75 ORB flatten-time Phase 1):

139. **The ORB force-flatten at 16:09 ET is not a systematic drag on EOD positions: a 15:30 ET flatten saves only +2.0% aggregate over 503 late-open trades (0/5 years meeting the 15% threshold), PHASE 1 NO-GO.** B75 (2026-06-14, mfe_mae_orb_clean.csv, 1030 trades 5y excl 2022): 473 force-flattened trades (16:09 ET) + 30 pre-flatten (15:30-15:59 ET) = 503 open at 15:30. Per-year delta: 2021 -7.7%, 2023 +13.5%, 2024 -4.0%, 2025 +7.2%, 2026 +3.4% -- no consistent direction. The small 30-trade pre-flatten cohort appeared GO (82.5%) but is selection-biased: those 30 trades happened to stop/target in the last 30 minutes after having run adversely from entry -- they are atypically bad, not representative of the 473 force-flattened winners (the 4h+ cohort, Lesson 88). Rule: when testing an earlier-exit hypothesis, always include ALL trades still open at the proposed exit time (force-flattened + pre-flatten); analyzing only the subset that resolved IN the tested window selects for adverse-path trades and systematically overstates the benefit of exiting earlier. Rule: do not propose earlier ORB flatten variants (15:00 ET, 15:30 ET) without per-year data showing consistent directional improvement in the FULL open-at-that-time cohort.

Appended 2026-06-14 (B76 skip-second-iFVG-after-loss):

140. **The B63(a) after-win/after-loss PF discriminator (1.473x for exactly-2-trade days) does not translate to a useful skip mechanism because the removed cohort is near-breakeven in aggregate (PF=0.9935), positive in 3/5 years.** B76 Phase 1 (n=1226 LO longs, 5y excl 2022): skipping rank-2+ iFVG signals on days where rank-1 lost removes 26.8% of trades (328/1226) for only +4.6% PF improvement (1.137->1.189). The removed trades have PF=0.9935 overall, but are profitable in 2021 (1.064), 2023 (1.488), and 2024 (1.101), and loss-making only in 2025 (0.874) and 2026 (0.499, n=43 sparse). Phase A passes fall ~15% (20->17 in combine simulation), scaling B42 two-phase sust from 3.23x to ~2.69x -- below the stop-rule threshold. Stop rule fires. Rule: the after-win/after-loss discriminator is a valid OUTCOME PREDICTOR but not a reliable quality gate because: (1) the removed cohort's aggregate PF is near 1.0 (not net-negative); (2) the near-breakeven character means year-to-year PF of removed trades is noisy (positive in some years, negative in others); (3) 26.8% volume reduction creates pipeline starvation per the established pattern (Lessons 94/105/109/134). Do not re-propose skip/size-boost mechanisms keyed on first-signal daily outcome without a more discriminating filter (e.g., requiring at least 2 consecutive daily losses before skip, or requiring removed PF < 0.85 at Phase 1).

Appended 2026-06-14 (B77 Deployed-config MFE/MAE dataset infrastructure):

141. **The deployed-config combined-engine per-trade excursion dataset (5y excl 2022, n=3238) is at `research/mfe_mae_deployed_combined_clean.csv`. Close-mode iFVG target hit rate (r_mfe >= 2.4) = 18.7% of all 3238 trades; research-baseline LO ifvg_edge longs hit 21.6% (wk5-r3 MFE analysis) -- the ~3pt gap is consistent with close-mode entering later in the inversion bar and capturing a smaller initial leg. Dataset composition: 2376 iFVG + 862 ORB trades, overall PF=1.089 (--partial-r 0, r=2.5 deployed config). The `--trade-csv` flag in `equity_export.py` now writes r_mfe, r_mae, mfe_pts, mae_pts columns alongside existing columns. Lesson 136 applies: always use `--partial-r 0` when generating per-trade data, or the fill-pairing corruption will make every per-trade analysis unreliable. Use `mfe_mae_deployed_combined_clean.csv` as the Phase 1 reference dataset for any future deployed-config per-trade analysis (e.g., per-DOW, per-killzone, bar-characteristic cross-cuts on the actual live strategy geometry).**

Appended 2026-06-14 (wk6-r1 research/ideation session -- deployed MFE/MAE probes):

142. **The 9ET (09:00-09:30 ET pre-RTH) iFVG failure in deployed close-mode config is structural: r_mfe_mean=0.787R vs 1.202R for other hours (-35%); r_mae_mean=1.111R vs 0.925R (+20%); immediate stop rate (r_mfe<0.1)=18.5% vs 11.5%. WR=19.4% vs 35.5%.** wk6-r1 (2026-06-14, mfe_mae_deployed_combined_clean.csv, n=211 9ET vs n=2165 other hours). The 9ET pre-RTH window has both fewer favorable excursions and more adverse excursions than all other hours. These entries are made on bars closing BEFORE RTH opens (9:30 ET); the RTH open creates immediate directional pressure that's not captured in the pre-market inversion. This is a geometry failure (the FVG inversion signal is correct for the pre-market structure, but RTH open dynamics override it), not a signal-quality issue. Rule: do not attempt to salvage 9ET iFVG signals with secondary filters (rank, freshness, MFE confirmation); the excursion anatomy shows the entry geometry itself is compromised pre-RTH.

143. **iFVG and ORB component daily P&L are statistically independent in the deployed combined engine (Pearson r=-0.028, 766 cross-engine days). The observed joint distribution exactly matches the independence assumption: P(both positive)=18.7%, P(both negative)=31.7%, matching the product of marginals (0.412*0.588=0.318).** wk6-r1 (2026-06-14, mfe_mae_deployed_combined_clean.csv). The combined engine provides NO systematic same-day hedging (the engines fire in different sessions and have uncorrelated outcomes) and NO systematic same-day co-crash (both-negative days match random expectation). Implication: the combined engine's DLL risk on bad days is driven by trade frequency (how many iFVG + ORB signals fire the same day), not by correlated outcomes. Rule: do not model the combined engine as a hedge; model it as two independent signal streams sharing DLL budget.

144. **iFVG target-hit trades (r_mfe >= 2.4, n=507) have essentially no post-target tail: median r_mfe=2.70R, p75=2.95R, p90=3.42R. Only 23.1% run to 3.0R, 8.9% to 3.5R. ORB target-hit trades (n=97): median=2.59R, p75=2.72R, 0% reach 4.0R. Confirms fixed-exit policy (B61, Lesson 20) from excursion-data perspective independently of the empirical pipeline test.** wk6-r1 (2026-06-14, mfe_mae_deployed_combined_clean.csv). The thin post-2.5R distribution means a trailing stop after target would capture only 23.1% of winners going to 3.0R while risking giveback of 1.0R on the 76.9% that reverse near 2.5R -- net-negative expected value. ORB is even worse (p75 only 2.72R). Rule: do not re-test trailing-profit exits for iFVG or ORB. The excursion data confirms what B61 found empirically: these instruments do not exhibit significant momentum past the 2.5R target.

Appended 2026-06-14 (wk6-b78 Phase A risk=0.75% sensitivity -- REJECTED):

145. **Reducing Phase A risk% from 1.0% to 0.75% (holding r=2.5) degrades funded-pipeline metrics on BOTH dimensions: $/mo $549->$520 (-5.3%), sust 3.23x->2.46x (-24%). The mechanism is the fixed $3k combine profit target: lower risk% → smaller daily P&L → each combine attempt takes ~33% longer → fewer attempts per year → fewer absolute passes (32 vs 42) → lower sustainability vs same 13 Phase B busts.** B78 (2026-06-14, 5y excl 2022). Phase A at risk=0.75%: 32/109 passes, 9.5d/attempt, $511 reset/funded, 105.8d cycle vs 98.1d at 1.0%. Combine pass rate per monthly window (6/17=35% test, 4/12=33% train) is SIMILAR to control (6/17, 4/12) because the monthly window and fixed target interact differently in a short 1-month window. Rule: do not reduce Phase A risk% below 1.0% in the two-phase pipeline. The optimal Phase A range is 1.0-1.25% at r=2.5, where combine attempt frequency is highest while bust rate remains manageable. Lower risk% is anti-optimal: it prolongs the combine phase without meaningfully reducing funded-account busts.

Appended 2026-06-14 (wk6-b79 iFVG freshness deployed config -- REJECTED Phase 1 NO-GO):

146. **Close-mode iFVG freshness (bars from displacement event to inversion close = gap_bars) shows the correct direction (fresh 1-3 bars PF=0.990, mid 4-9 PF=0.957, stale 10+ PF=0.831) and 3/5 year consistency, but the overall fresh/stale ratio (1.191) falls below the 1.30 Phase 1 GO threshold. Neither fresh nor stale setups are individually profitable in the deployed config — the edge comes from volume and the inversion-confirmation filter, not from freshness selection.** B79 (2026-06-14, n=2372 iFVG trades, deployed close-mode, 5y excl 2022). Freshness direction is real: stale setups (390 trades) lose meaningfully more than fresh (1275 trades). However, since even fresh setups barely break even (PF=0.990), a hard freshness gate would cut 16% of total volume (390 stale trades removed) with insufficient PF gain to clear volume-starvation risk per Lessons 94/105/109/134. B69 (research-baseline) found ratio=1.285; close-mode (one bar later) gives ratio=1.191 — marginally weaker, consistent with the close-mode entry absorbing the freshness signal in the inversion confirmation step itself. Rule: do not gate iFVG signals on displacement-to-inversion bar gap; the signal exists but is too weak relative to the volume cost to implement as a hard filter.

**Lesson 147 (2026-06-14, B80):** Live MFE/MAE tracking in TopstepXBroker uses bar close (not intrabar H/L). The paper broker uses intrabar H/L for accuracy; the live broker only has bar close available per the event model. For dashboard observability (comparing trade quality to the backtest r_mfe/r_mae distribution), close-based tracking is sufficient — the data is directionally correct even if slightly understated vs true intrabar extremes. The tracker initializes on OCO registration (after fill), updates in _on_new_bar before external bar handlers, and clears on exit fill or cancel_all. Rule 13 compliance: log at 1R/2R milestones, pos_mfe_r/pos_mae_r in strategy_state SSE, rendered in OpenPositions and StrategyDebug panels.

Appended 2026-06-14 (wk6-r2 research/ideation session):

148. **Rank-2+ iFVG signals in the SAME direction as the day's rank-1 signal ("continuation") are loss-making (PF=0.785, net=-$104k, 5/5 years); rank-2+ signals in the OPPOSITE direction ("conflict") are marginally profitable (PF=1.052, ratio 1.340). The dominant driver is the short-to-short pair (short→short): PF=0.666, net=-$90k, 4/5 years consistent. The repeat-short loss mechanism is distinct from the conflict-vs-continuation aggregate.** wk6-r2 (2026-06-14, mfe_mae_deployed_combined_clean.csv, n=2376 iFVG trades, 5y excl 2022): direction pair breakdown: long→long PF=0.937 (-$13k), long→short PF=0.994 (-$2k), short→long PF=1.108 (+$30k, 2/5 years consistent), short→short PF=0.666 (-$90k, 4/5 years consistent). The "conflict is better" aggregate is mostly explained by short→short being catastrophic; long→short (the other conflict pair) is only near-breakeven (PF=0.994). Mechanism: close-mode iFVG shorts are broadly loss-making (Lesson 96: PF=0.84 for rank-1 shorts). Repeating a short within the same day means price has bounced back from the first short's entry and created another supply zone — an even weaker structural setup since institutional supply at the prior zone has been partially absorbed. Rule: a gate suppressing same-direction rank-2+ iFVG signals removes 26.6% of iFVG volume (similar to B71's 28%), which risks pipeline starvation per the established pattern (Lessons 94/105/109/134). The most targeted intervention would suppress only short→short repeats (14.6% volume cut) to preserve pipeline throughput while removing the dominant loss driver.

149. **ORB signals that break through the pre-market (08:00-09:29 ET) high (long) or pre-market low (short) have dramatically higher quality than those staying within the pre-market range: PM-break PF=1.857 (n=599, 69.5%) vs within-PM PF=0.886 (n=263, 30.5%), ratio 2.097 overall. The effect is concentrated in 2024-2026 (ratios 1.497-3.563) and inverted in 2021 (0.975) with 2023 near-flat (1.040); year consistency is 3/5.** wk6-r2 (2026-06-14, mfe_mae_deployed_combined_clean.csv + bars_MNQ_dbv_2021_2026.csv, n=862 ORB trades, 5y excl 2022): mechanism — an ORB breakout that also clears a 1.5-hour pre-market high or low represents a MORE SIGNIFICANT structural event than clearing only the 15-minute opening range. Institutions establishing pre-market positions are forced to reposition when their range boundary is violated; within-PM ORB breakouts just oscillate within an already-established structural band. The 2021-2023 inconsistency is a concern: in strongly trending years (2021 NQ bull market), even within-PM ORB signals captured the trend (within-PM PF=2.088 in 2021). The recent dominance of the PM-break advantage (2024-2026) may reflect increased algorithmic respect for pre-market structural levels or higher post-2023 market choppiness making within-PM breakouts fail more. Rule: B82 is a genuine Phase 1 GO (ratio 2.097 >> 1.30, 3/5 years), but Phase 2 must include sensitivity check on 2021-2023 sub-period separately. Volume impact: removes 30.5% of ORB signals — less than B56's 42.2% that caused account starvation, but still near the lower bound of survival threshold (~4 ORB signals/month).

Appended 2026-06-15 (wk6-b81 iFVG direction-continuation gate -- MIXED/NO-GO):

150. **The iFVG within-day direction-continuation gate (suppress same-direction rank-2+ iFVG signals) confirms per-trade quality improvement (PF 1.055->1.095, +3.8%) but produces a net MIXED/NO-GO pipeline result due to volume starvation: the 26.6% signal volume cut reduces Phase A passes 46->43, degrading sust 3.54x->3.31x vs B57 baseline, while $/mo barely moves ($566->$568). The per-trade quality gain is real but insufficient to offset the volume loss at this cut depth.** B81 (2026-06-15, 5y per-year excl 2022, r=2.5, Phase B = B21 ORB-reentry r0.75, haircut $200). Full two-phase pipeline: Phase A 43/142 passes, reset=$495 vs baseline 46/167, reset=$545. Phase B fixed: 13/14 busts, $3,131/acct. Sustainability = passes/Phase_B_busts = 43/13 = 3.31x vs 46/13 = 3.54x. The single-phase PF improvement (1.095 vs 1.055) is a real signal that same-direction repeats are lower quality. However, Phase 2b (shorts-only, 14.6% cut) was also assessed and would yield ~44-45 passes, sust ~3.38-3.46x — still below the B57 3.54x threshold. Rule: iFVG signal quality gates at 15-27% volume removal cannot clear the B57 sust threshold. The starvation pattern is now established across three independent gate studies (B71 28% cut: -0.46x sust; B73 ~8% ORB cut: -0.38x sust; B81 26.6% iFVG cut: -0.23x sust). Volume starvation is the documented ceiling for signal-quality gates in this pipeline configuration. To beat B57 on sust, a gate would need to improve per-attempt pass RATE by more than the absolute-pass volume loss — an extremely hard bar to clear at >15% volume cut.
