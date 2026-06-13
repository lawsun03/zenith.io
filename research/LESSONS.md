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
