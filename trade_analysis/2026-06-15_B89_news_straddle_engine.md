# B89 — news_straddle engine: build + oracle parity (CPI breakout-straddle)

**Session:** wk7-b89 (2026-06-15) · **Verdict:** SHIPPED (Phase 1: engine built,
validated; default-off). Live resting-OCO build + funded-overlay eval queued as
follow-ons (B92, B93).

## What was built

A default-off `news_straddle` engine implementing the Lawrence-spec CPI
breakout-straddle that B85 confirmed on 1-second data (PF ~6, 5/5 years):

- `app/strategy/news_straddle.py` — `NewsStraddleDetector` (streaming, consumes
  closed `Bar`s, emits `Signal`), `NewsStraddleRunner` (duck-types the engine
  surface), `NewsStraddleComposer` (no-op `on_stop_loss` — one trade per event),
  `load_event_times()` (reads CPI timestamps from `data/news_events.csv`).
- Wired `engine="news_straddle"` into **both** `_build_runner`s
  (`app/main.py` live + `app/backtest/runner.py`).
- `StrategyParams`: `news_straddle_offset_ticks=60`, `news_straddle_tp_r=3.0`,
  `news_straddle_event_type="CPI"`, `news_straddle_events_path` — all default-off
  (`engine` defaults to `"ifvg"`).
- `tests/test_news_straddle.py` (12 tests, TDD) + `scripts/news_straddle_engine_parity.py`.

### Mechanism (validated)
For each release: lock the 15-min PRE-release range (no lookahead — the bar
stamped AT the release is the first ENTRY bar). Arm OCO: buy_stop = range_high +
60t, sell_stop = range_low − 60t. First leg tagged fires; sibling cancelled
(one trade/event). Stop = broken range boundary (R = 60t = 15.0 pts, TIGHT).
Target = tp_r × R (3R default; 4R exposed). Both legs in one bar = whipsaw (no
directional signal). Max-hold flatten via `exit_request` (mirrors oracle 180-min).

## The load-bearing architecture conflict (Rule 7)

The entry is a **resting STOP order that fills AT the stop level**. But
`PaperBroker.place_bracket` fills entries at **market (last bar close) + slippage**,
not at `signal.entry` — the deliberate stale-FVG fix (paper.py:236, 2026-06-10
parity post-mortem). On a CPI release bar, the gap between the break level and the
bar close is exactly the spike B85 measured, so `run_backtest` would mis-fill and
destroy the tight-stop edge.

**Resolution (not blended):** the oracle (`scripts/news_straddle_cpi_1s.py`, B85)
remains the **expectancy source of truth**; the engine carries the **signal
logic**. Validation = signal-geometry parity against the oracle, not pipeline P&L.
The standard combine/funded harnesses are intentionally NOT run on this engine —
they would report fantasy numbers.

## Validation: engine vs oracle on 47 real 1s CPI windows

`scripts/news_straddle_engine_parity.py` drives the detector over
`bars/bars_NQ_1s_cpi_windows.csv` and compares each event's decision to the oracle:

```
Parity: 47 agree / 0 mismatch
Oracle headline (tp_r=3.0): n=46 win%=67 PF=5.99 R/trade=+1.680 totR=+77.3
```

- **46/46 directional events**: engine fires the same side the oracle traded.
- **2/2 whipsaws** (2023-11-14, 2023-12-12): engine flags whipsaw, no signal.
- **1 benign classification nuance** (2026-04-10): oracle=nofill (0R) vs
  engine=whipsaw (both = no directional trade; a 1-bar boundary difference in the
  fast-spike second). Economically equivalent non-trade; not a directional miss.

The 1s headline (win% 67, PF 5.99) is *better* than the 1-min spec estimate
(63% / PF 5.0) — the entry-bar resolution did NOT eat the edge (B85's open
question, now closed for the engine too). Years-positive consistency holds.

## Why this is "shipped" not a P&L claim

Nothing is enabled live (`bot_config.json` untouched; `engine` default `"ifvg"`).
The deliverable is the **validated, reusable signal core** for the live resting-OCO
deployment — the live path computes the same range/levels and places real exchange
stop entries. No expectancy is re-claimed here beyond B85's; this session proves
the *engine* reproduces that oracle exactly.

## Follow-ons queued

- **B92** — live resting-OCO build: wall-clock scheduler (~2 min pre-CPI from
  `data/news_events.csv`), `place_oco_stop_entries` (two exchange stop orders +
  OCO), cancel-sibling-on-fill, reuse `_pending_brackets`/`_place_bracket_after_fill`
  for stop+target, EOD flatten. Real-money order path → trace full chain, TDD,
  default-off. Rule-13 UI (strategy_state + StrategyDebug) lands here.
- **B93** — funded-overlay framing: does adding CPI-straddle days on top of the
  B42 pipeline raise $/mo without busting? Compute from the oracle's per-event R
  (already available); also report standalone combine numbers for completeness.
