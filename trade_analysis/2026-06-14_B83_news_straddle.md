# B83 — News-event straddle (CPI / PPI / FOMC breakout) — Phase 1 REJECT

**Session:** wk6-b83 · **Date:** 2026-06-14 · **Objective:** funded-overlay (Phase 1 falsification)
**Verdict:** **REJECTED at Phase 1** — negative net expectancy at every slippage level and every
event type. Stop rule fires. No Phase 2 engine built, no tuning attempted.

## Hypothesis (Lawrence-requested 2026-06-14)

Scheduled macro releases (CPI, PPI, FOMC) inject a volatility burst at a *known* time. A
pre-placed OCO straddle (buy-stop above / sell-stop below the pre-event reference) should
capture the directional break without predicting the number. This is the **opposite** of the
deployed `ifvg_macro_windows` blackout (which avoids these windows), so it is a genuinely new,
event-driven mechanism — worth a cheap Phase-1 test even at a HIGH (~70%) rejection prior.

## Method (no engine; deterministic 1-min replay)

- **Event calendar** (`data/news_events.csv`, built by `scripts/build_news_events.py`):
  CPI/PPI at 08:30 ET, FOMC statement at 14:00 ET, DST-converted to UTC via `zoneinfo`.
  FOMC from the Federal Reserve calendar (exact). CPI 2023 from a confirmed full list; CPI
  2024/2025/2026 and 2021 H2 from the BLS pattern + confirmed anchors. PPI from the BLS
  schedule (2025 confirmed) + regular pattern (lowest-confidence series). 156 events total.
- **Bars:** `bars/bars_MNQ_dbv_2021_2026.csv` (1-min, free/on-disk — no Databento spend).
  Verified **START-labeled** (bar `ts=T` covers `[T,T+1)`): the first bar 2021-06-13T22:00Z is
  the Sunday 18:00 ET Globex reopen, and the RTH-open volume jump lands exactly on the 13:30Z
  (=09:30 ET) bar.
- **Coverage / holdout:** events within 2021-06-13 → 2026-06-11, **calendar 2022 excluded**
  (frozen holdout). 124 events qualified (47 CPI, 45 PPI, 32 FOMC).
- **No-lookahead contract:** every parameter (ref price, 5-min ATR(14), offset X) is computed
  from bars with `ts ≤ placement` (placement = event − `place_lead_min`=2 min). The event
  datetime is the only forward input and it is a published schedule.
- **Fixed defaults (B83 spec, no tuning):** `place_lead_min`=2, `entry_window_min`=15,
  `offset_atr_mult`=0.5 (X = 0.5·ATR5), `tp_r`=1.0, `stop_mode`=opposite_level (risk ≈ 2X,
  so RR ≈ 1:1), `max_hold_min`=60, `spread_ticks`=1, `slip_ticks_news` swept {2,4,6,8}.
- **Fill model:** first 1-min bar in `[P, P+15)` whose high/low crosses a stop fills that leg
  (OCO cancels the other), adverse slip = `(slip+spread/2)·tick`; gap-through fills at
  `max/min(stop, bar.open)+slip`. A 1-min bar that spans **both** legs → `whipsaw_loss`
  (enter then immediately stopped at the opposite leg; never assume the favorable order — and
  1-min is already the finest data, so no sub-bar resolution is possible). In-trade, a bar that
  hits stop **and** TP resolves as a stop (conservative).
- **Calendar validation (date-error robustness):** per event, `expansion_ratio` =
  range(`[E,E+15]`) / pre-event ATR5. A real release expands vol; this flags mis-dated /
  no-reaction events. Confirming on expansion does **not** select for straddle profit (a big
  expansion can be a clean break *or* a whipsaw) — it only removes near-zero non-events.

## Results

Mean net expectancy (R) per traded event, by slippage:

| slip (ticks) | CPI | PPI | FOMC | ALL (R/trade) | whipsaw% | win% |
|---|---|---|---|---|---|---|
| 2 | −0.014 | −0.149 | −0.342 | **−0.148** | 17 | 44 |
| **4 (GO test)** | −0.080 | −0.218 | −0.361 | **−0.203** | 17 | 43 |
| 6 | −0.145 | −0.240 | −0.379 | **−0.240** | 17 | 42 |
| 8 | −0.209 | −0.259 | −0.396 | **−0.276** | 17 | 41 |

- **Fill rate 100%** at all levels — X = 0.5·ATR5 is small enough that the release bar always
  clears a leg; missed entries are not a factor.
- **Outcome mix (slip=2):** 55 TP / 48 stop / 21 whipsaw → **44% win rate**. With RR ≈ 1:1 you
  need >50% to break even; the post-release move frequently fails to extend a full 2X before
  retracing to the opposite leg.
- **Calendar is clean:** expansion_ratio median **8.2×** ATR5, only **1/124** events below
  1.5× (PPI 2021-09-10). The expansion-confirmed subset (123 events) gives essentially the same
  expectancy (−0.195R at slip=4) — the negative result is **not** a date-error artifact.

## Stop rule

> GO only if mean net expectancy > 0 at slip=4 ticks AND whipsaw rate < 35%; otherwise reject,
> no tuning.

Expectancy at slip=4 is **negative for every event type** (best case CPI −0.080R; ALL −0.203R).
Whipsaw rate (17% ALL, 25% FOMC) is *below* the 35% cap — so whipsaw is not even the binding
constraint. **→ REJECT.** No parameter rescue.

## What we learned

1. **The 5-min news break round-trips faster than a 1:1 RR can monetize.** Win rate after
   realistic slippage is ~43%; an opposite-leg stop (risk ≈ 2X) with tp_r=1.0 needs >50%. The
   directional follow-through after the release bar is too unreliable on NQ to clear a 2X target
   before retracing 2X. This is *priced-too-efficiently*, exactly the spec's primary failure mode.
2. **FOMC is the worst event for the straddle, not the best** (−0.36R, 34% win, 25% whipsaw).
   The 14:00 ET statement spikes then violently mean-reverts on the same bar far more often than
   the 08:30 CPI/PPI prints — the classic "FOMC fakeout." Intuition that the biggest event = the
   best straddle is inverted here.
3. **CPI is the least-bad but still negative** (−0.08R at slip=4). No event type, no slip level,
   and no expansion subset is positive — there is no surviving slice to route to a funded overlay.
4. **The vol-expansion diagnostic is a reusable calendar-validation tool** for any future
   event-driven study: it independently confirmed 123/124 of a partly-hand-assembled calendar
   landed on real volatility events, decoupling "are my dates right" from "is the edge real."
5. Confirms **Lesson 6** (external/retail claims fail to transfer — now >4-for-4) and the
   spec's HIGH rejection prior. News straddles are a popular retail idea; the data settles it.

## Impact on B84 (news-day mode switch)

B84's Phase 2 (the conditional router that turns engines OFF and the straddle ON on event days)
is **moot** — there is no profitable straddle to switch to. B84's **Phase 1a** remains worth
running on its own: tag historical iFVG/ORB trades by news-day vs non-news-day and test whether a
plain **news-day suppression** of the existing engines is a standalone win (extends the deployed
intraday macro-blackout to a full-day blackout). That half has no dependency on B83.

## Artifacts

- `scripts/build_news_events.py` → `data/news_events.csv` (156 events, DST-correct)
- `scripts/news_straddle_phase1.py` → `research/news_straddle_phase1.csv` (per-event detail)
- Code ships nothing to the bot (Phase-1 NO-GO → no engine). `bot_config.json` untouched.
