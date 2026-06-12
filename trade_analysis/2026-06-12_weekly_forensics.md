# Weekly live-data forensics — week of 2026-06-08 → 2026-06-12 (B4)

Session: autonomous research loop, 2026-06-12T23:38Z. Inputs: `logs/2026-06-08..12.log`,
`trades/*.csv` (rolling + daily), M26 1-min bars `bars/bars_MNQ_parity.csv`
(06-09T18:18Z → 06-12T18:17Z, fetched same-day before the front-contract roll),
replays via `scripts/parity_check.py` with the canonical deployed config.

## Headline verdicts

1. **Execution slippage is small and well-modeled: recommend `slippage_ticks_market: 2`**
   (currently modeled 1 tick). The scary numbers in the trades.csv `slippage`
   column are a **semantics artifact**, not execution quality.
2. **The "111.5-point slippage" trade (06-12) had ~0.5 pt of real slippage.**
   `signal_entry` is the FVG proximal edge by design (`app/strategy/composer.py:398`);
   `entry_mode=market` fills at confirmation-bar close. The fill-relative bracket
   shifted the whole plan +111.5 pts (stop 29339.75 vs planned 29228.25).
3. **Live excursion data 06-07 → 06-11 is unusable**: `ExcursionTracker.on_bar`
   (`app/execution/excursion.py:87-95`) updates every open window with every bar,
   no instrument filter — MGC/MES/MNQ prices cross-contaminate mfe/mae/outcome.
   06-12 onward (MNQ-only deployment) is clean.
4. **Rolling `trades/trades.csv` lost 22 rows** (06-10T01:32Z → 06-12T14:59Z gap):
   rows were written correctly, then destroyed by a git working-tree restore
   (reflog shows `reset: moving to HEAD` 06-10 23:35 PT). Daily files survived
   (untracked). `trades/` is *intentionally* tracked per .gitignore — policy conflict
   to resolve, not a writer bug.
5. **Parity replay confirms the 06-12 ORB miss** (lesson 10): combined-engine
   replay fires ORB long 14:09Z @ 29577.75 (OR 29264.5–29502.5); live cut over
   to `combined` at ~14:22Z, 13 min after the breakout bar. Price was +60 pts
   by 18:17Z.
6. **Roll-week trap (new infra lesson):** `scripts/fetch_bars.py --symbol MNQ`
   on 06-12 ~23:40Z returned the NEW front contract (U26) for the entire 7-day
   lookback — ~+292 pts above the M26 prices live actually traded. Same fetch
   at 18:17Z same day returned M26. Parity/forensics bars must be archived
   same-week; during roll week the free fetch is not what live traded.

## 1. True execution slippage (fill vs decision-time market)

Decision price = close of the 1-min M26 bar ending at the entry minute.
All MNQ live entries inside the bar window (n=10):

| ts (UTC) | side | fill | mkt @ decision | adverse (pts) |
|---|---|---|---|---|
| 06-09T18:34:01 | short | 29001.75 | 29004.00 | 2.25 |
| 06-09T22:19:01 | short | 29079.00 | 29081.75 | 2.75 |
| 06-09T23:34:00 | long  | 29009.00 | 29007.75 | 1.25 |
| 06-09T23:40:01 | long  | 29012.50 | 29009.50 | 3.00 |
| 06-10T01:22:00 | short | 29073.50 | 29072.25 | −1.25 |
| 06-10T01:32:00 | short | 29046.75 | 29047.50 | 0.75 |
| 06-11T00:04:01 | short | 28371.25 | 28368.75 | −2.50 |
| 06-11T12:25:01 | short | 28774.50 | 28775.00 | 0.50 |
| 06-11T14:20:00 | short | 28760.25 | 28760.75 | 0.50 |
| 06-12T14:59:59 | long  | 29633.00 | 29632.50 | 0.50 |

Mean adverse **0.78 pts (3.1 ticks)**, median 0.75. The worse fills are the
06-09/early-06-10 *forming-bar era* mid-bar chases (multi-instrument, old
config). Canonical-config era (06-11 →, closed-bar): n=3, **0.50 pts = 2 ticks**
flat. Backtest models 1 tick (`slippage_ticks_market=1`).

**Recommendation (Lawrence, Monday):** set `slippage_ticks_market: 2` in
backtest configs. Cost honesty: +1 tick/entry ≈ $0.50/contract — immaterial to
any single result but compounds across 60–90-trade Combine months. Sample is
small (n=10, one week); revisit after another forensics pass.

## 2. The 06-12 "111.5-pt slippage" trade (oid 3123518079), reconstructed

- 14:40–14:50Z: selloff sweeps the 29486.25 level (1-min low 29369 at 14:47Z).
- 14:50–15:00Z: violent rally back to ~29632 (the displacement + inversion).
- 15:00:00Z: confirming 5-min bar closes; grade F(7); market order placed
  15:00:00.152Z, filled 29633.0 — **0.5 pt from the bar close**. One order, no
  retries, no staleness (the 07:22 PT restart's only stale signal — an ORB from
  backfill — was correctly skipped at 07:25).
- `signal_entry` 29521.5 = FVG proximal edge (zone 29447.0–29521.5). The
  journaling column `slippage = fill − signal_entry` (`app/journaling.py:178-191`)
  reports 111.5, conflating *plan deviation* (how far price ran past the
  retrace level before confirmation) with *execution slippage* (~0.5).
- Consequence that IS real: brackets are placed fill-relative
  (stop_offset −293.25 → stop 29339.75 vs planned 29228.25; target 30659.4 vs
  30547.9). The entire bracket rode 111.5 pts higher than the planned geometry.
  The replay models the same fill (PaperBroker fills at bar close + 1 tick,
  re-anchors brackets — `app/broker/paper.py:223-253`), so **backtest↔live fill
  parity is ~1 tick here; the backtest is not flattered by this trade.**
- Existing guard for runaway plan deviation: `max_entry_slippage_frac` exists
  in both live and PaperBroker, currently disabled (0). Whether to cap plan
  deviation is a strategy question (it would have skipped this trade — which
  was +126 MFE before data end), not an execution bug.

## 3. Excursion ledger contamination (multi-instrument era)

`ExcursionWindow` has no instrument field; `on_bar` applies every bar to every
window. Smoking-gun arithmetic from `trades/excursions_2026-06-11.csv`:
- `3112451307` long MNQ ref 28730.75, mae 24635.25 → implied low 4095.5 = MGC.
- `3112451421` long MES ref 7305.25, mfe 21443.25 → implied high 28748.5 = MNQ.
`reached_target`/`stop_hit`/`outcome` are corrupted the same way (an MES window
scored `win` off an MNQ high). **Blast radius:** excursions files 06-08, 06-10,
06-11 + those date ranges of the rolling file; rejection-ledger windows too.
**Clean:** 06-12 onward (MNQ-only) and earlier single-instrument days.
Fix (new backlog item B11): add `instrument` to the window, filter in `on_bar`,
normalize contract symbol (`CON.F.US.MNQ.M26`) vs bar symbol (`MNQ`), defining-
behavior test. Until then: do NOT use pre-06-12 multi-instrument excursion rows.

## 4. Rolling trades.csv gap

`_append_fill_csv` (`app/journaling.py:194-260`) writes rolling + daily files in
one loop; zero failure log-lines all week. `git diff trades/trades.csv` = exactly
+1 row (the 06-12 entry) over commit `f239ef7` (06-09 18:53 PT), whose last row
matches the gap start. Reflog shows tree restores 06-10 23:35 PT / 23:45 PT and
06-11 15:46 PT. Verdict: a `git reset --hard`-class restore reverted the tracked
ledger; untracked daily files kept the data (22 rows recoverable from
`trades_2026-06-10.csv`). Conflict to resolve (B12): `.gitignore` says trades/
is intentionally tracked for cloud analysis, but tracked runtime ledgers get
wiped by any tree restore. Options: commit-on-write, sync via outbox instead,
or untrack rolling + keep daily files tracked.

## 5. Signal parity, 06-11 and 06-12 (canonical config era)

- **06-11**: live took 2 shorts — 12:25Z (forming-bar signal, mid-bar entry;
  forming-bar entries were still ON live that day, confirmed OFF at the 06-12
  07:22 restart) and 14:20Z. Closed-bar replay (ifvg) takes neither and instead
  one long 17:34Z @ 28855.25 that live missed (state divergence: live restarts
  at 10:26 PT + position/dedup state from the morning shorts). The 14:20Z short
  was closed at 14:23:19Z @ 28917.25 (−$314) — one minute after the engine-
  cutover restart, not by its bracket (stop sat at 29048).
- **06-12**: replay (ifvg or combined) emits the same 14:59/15:00Z long with
  identical entry ref 29521.5 → **signal parity exact** on the one live trade.
  Combined replay additionally emits the 14:09Z ORB long that the 14:22Z
  cutover missed (lesson 10, now replay-confirmed).

## Artifacts

- M26 week bars archived: `bars/live_archive/bars_MNQ_M26_1min_20260609_20260612.csv`
  (local only — `bars/` is gitignored; re-fetch is NOT possible post-roll).
- U26 fetch kept for contrast: `bars/live_archive/bars_MNQ_U26_1min_20260605_20260612.csv`.
- Replays: `parity_check.py --bars bars/bars_MNQ_parity.csv --date 2026-06-11 --engine ifvg`,
  `--date 2026-06-12 --engine ifvg|combined`.
