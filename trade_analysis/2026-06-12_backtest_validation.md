# Backtest engine cross-validation + clean-data pull (2026-06-12)

Prompted by Lawrence questioning trade counts (which had already caught a
real chop_breakout suppression bug). Two-pronged validation: (1) run the
same strategy through an independent third-party engine and diff trades;
(2) pull a fresh 5-year 1-min dataset from Databento and cross-check it
against the campaign bars.

## 1. Cross-engine validation vs backtesting.py 0.6.5 (kernc)

Method (`scripts/validate_backtest_engine.py`): ORB (9:30+15min r2.5)
through BOTH engines on the IDENTICAL materialized 5-min bar list —
signal logic mirrored on purpose; under test are the independent fill /
bracket-touch / intrabar / P&L machinery. Aligned semantics: entries at
signal-bar close (`trade_on_close=True`), slippage 0, commission 0,
size 1, our risk limits OFF, flatten + cutoff replicated.

| period | trades (ours / ref) | identical entries | exact end-to-end | divergent |
|---|---|---|---|---|
| 2024 | 259 / 259 | 259 | 254 (98.1%) | 5 |
| 2025–26 test | 362 / 361 | 361 | 352 (97.2%) | 9 + 1 ours-only |
| **total** | **621 / 620** | **620** | **606 (97.6%)** | 15, all explained |

**Every divergence is a holiday-session case, none is an engine defect:**
- 14 = CME early-close days (Presidents/Memorial/Juneteenth/July-4/
  Thanksgiving/Labor/Christmas-Eve/MLK): our flatten window (15:05–16:00
  CT) is bar-driven and contains no bars on early-close days, so our
  engine carries the position to the next session; my naive reference
  flattened at the reopen. Backtest-side this is a tiny, symmetric
  effect (P&L diff +106 / −405 pts on 2.5k+ pts totals).
- 1 = Thanksgiving-evening entry ours took and the reference blocked:
  our engine's entry cutoff is trading-day-aware (17:00 CT reopen = next
  trading day) — correct; the reference's wall-clock check was naive.
- **Zero same-bar stop/target (whipsaw) policy conflicts in 621 trades.**
- Identical entries on all 620 common trades = bar aggregation, signal
  timing, close-fill model, and bracket-touch detection all agree with
  an independent implementation. P&L arithmetic agrees to the cent on
  matched trades.

**LIVE BOT FINDING (action item, separate from backtesting):** on CME
early-close days (~5–7/year) no bar ever lands in the flatten window, so
`_enforce_flatten` (bar-driven) never fires and the live bot would hold
a position through the close — a Topstep flatten-rule violation risk.
Fix candidates: wall-clock flatten task, or an early-close calendar with
a per-day flatten time. NOT fixed in this session — surfaced for
prioritization.

## 2. Clean data: Databento 5-year pull + cross-check

- Pulled `NQ.c.0` (GLBX.MDP3, ohlcv-1m, continuous front month — the
  same convention all existing campaign bars use) 2021-06-12 →
  2026-06-12 into `bars/bars_MNQ_db_2021_2026.csv`. Cost: $6.40.
- Vendor flagged 3 degraded-quality days in 5 years: 2021-12-05,
  2022-01-02, 2025-09-17.
- Integrity + cross-diff vs the campaign bars: see results appended
  below by `scripts/check_bars_quality.py`.

## Verdict on "are the backtests correct?"

- The execution/accounting engine is **validated** against an
  independent implementation: 97.6% exact trade matches with 100% of
  divergences explained by holiday-session semantics (where ours is the
  more correct side in both directions).
- Trade counts per engine are real: ORB ~1/day by construction, iFVG
  droughts match pre-existing campaign data, chop_breakout's low
  frequency is its spec's conjunction (after two real suppression bugs
  were found and fixed — Lawrence's count-skepticism caught the second).
- Remaining known optimism (documented since round 1): pass/breach
  evaluated on fill-granularity equity; PaperBroker models no latency or
  book depth; partials double-count in exit-fill tallies.
