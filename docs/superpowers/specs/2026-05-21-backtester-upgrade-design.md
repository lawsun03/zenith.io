# Backtester Upgrade — Design (Phase 1)

**Goal:** find a static config that has a high probability of passing the Topstep $50K Combine, measured on out-of-sample data, with realistic slippage and commissions.

This is Phase 1 of the broader profitability initiative. Phase 2 (rolling
retune + per-killzone auto-sizing) is explicitly out of scope here.

---

## What changes

Four concrete changes to the backtest stack:

1. **Pagination in `scripts/fetch_bars.py`** so we can pull 365 days
   instead of the API's 20K-bar single-call cap.
2. **Slippage + commission model** in `PaperBroker` / backtest, configurable
   per-instrument.
3. **Fix `hold_seconds` bug** — entry_ts is currently set to the run start
   time instead of the entry bar's timestamp.
4. **Walk-forward harness + param search** that scores configs by their
   combine-pass-rate on OUT-OF-SAMPLE test windows, not in-sample fit.

---

## 1. Data pagination

`scripts/fetch_bars.py` adds a loop:

- Start at `end_time = now`, work backward in 14-day chunks.
- Each chunk calls `client.get_bars(symbol, start_time=..., end_time=...)`.
- Stop when we have N days OR the API returns 0 bars (older than data exists).
- Dedupe by timestamp, sort ascending, write CSV.

Defaults: 365 days for MGC, MES, MNQ. ~26 API calls per symbol, ~5 minutes wall time.

**Why:** without real history, walk-forward is theatre.

---

## 2. Slippage + commissions

Add to `PaperBroker` (it's the broker the backtest uses):

- `slippage_ticks_market: int = 1` — market orders fill at `signal_price ± N ticks`
  against the trader.
- `slippage_ticks_limit: int = 0` — limit orders fill at the limit price IF the
  bar touched it, else not filled.
- `commission_per_side_per_contract: Decimal` — deducted from realized P&L on
  each fill. Default $0.74 for micros (TopstepX published rate).

Defaults are configurable per instrument via a small table in `app/broker/paper.py`.

**Why this matters:** the current backtests fill at exact bar prices and ignore
commissions. On 100 trades/day that's $74/day in unmodeled cost on MGC alone
— enough to flip "+$1700 net" into "-$2000".

---

## 3. `hold_seconds` fix

Current trade CSVs show `hold_seconds=-1762687` because `entry_ts` is set to
`datetime.now()` (the run start) rather than the entry bar's timestamp. Fix in
the trade-reconstruction code (`app/backtest.py` `compute_stats` or wherever
pairs are built).

**Why:** without correct hold times we can't analyze duration-vs-outcome, can't
tell if certain killzones produce shorter/longer winners, can't sanity-check
fills.

---

## 4. Walk-forward + param search

### Window scheme
- **Rolling**, not anchored. (Regime shifts in MGC are real; expanding window
  would dilute recent regime signal.)
- **30 trading days train / 10 trading days test**, step 5 days.
- 365 calendar days ≈ 250 trading days → ~42 train/test pairs.

### Param grid
Start small and meaningful:
- `body_atr_multiple` ∈ {0.8, 1.0, 1.2}
- `swing_lookback` ∈ {2, 3, 4}
- `r_multiple` ∈ {1.5, 2.0, 2.5, 3.0}
- `stop_buffer` ∈ {0.20, 0.30, 0.40}
- `enabled_killzones` ∈ {["london"], ["london","ny_am"], ["london","ny_am","ny_pm"]}

= 3 × 3 × 4 × 3 × 3 = **324 configs**. At ~2s per config per window × 42 windows
= ~7.5h serial runtime. Parallelize across CPU cores → ~1h on a typical laptop.

### Scoring metric: combine-pass simulation

For each (config, test-window) pair, simulate as if the test-window is the start
of a $50K Combine:

- Starting balance $50,000, trailing MLL $2,000, profit target +$3,000.
- Run the strategy across the test window's bars with realistic slippage + commission.
- Outcome ∈ {PASS, FAIL, INCOMPLETE} where:
  - PASS = realized balance hit +$3,000 without breaching MLL
  - FAIL = MLL breached at any point
  - INCOMPLETE = test window ended without hitting either

**Per-config score** = `pass_rate - 2 × fail_rate`. The `-2×` weighting punishes
combine failures harder than non-completions (a fail kills the account; an
incomplete just costs time).

### Output
Two files per param search:
- `walkforward_results.csv` — one row per (config, window) with pass/fail,
  trades, net P&L, max DD, profit factor.
- `walkforward_ranking.txt` — configs sorted by score, with per-window outcome
  breakdown. The top config is what we ship to live.

### What this catches that the current grid misses
- A config that posts +$2,870 in run00 might score 5% pass-rate across the year
  (one lucky 14-day window).
- A config with smaller per-window P&L but consistent pass-rate across 30+ windows
  is the actual winner.

---

## Architecture

New module: `app/optimizer/walkforward.py`

- `class WalkForwardRunner` — given bars, a grid, a window scheme, runs all
  (config, window) pairs in a process pool. Yields results to a CSV.
- `class CombineSimulator` — pure function over a sequence of fills →
  `{PASS, FAIL, INCOMPLETE}`. Used by the runner. Reuses `RiskState` so the
  lockout math is identical to live.

CLI: `python -m app.optimizer.walkforward --bars bars_MGC.csv --grid configs/grid.json --out walkforward/`

The existing `scripts/backtest.py` keeps working for single-config runs — we just
add this layer on top.

---

## Out of scope (Phase 2 / later)

- Multi-instrument joint backtest (need single-instrument working first)
- Rolling retune in production (Phase 2)
- Per-killzone auto-disable/size (Phase 2)
- Bayesian / coordinate-descent search (only if grid runtime becomes a problem)

---

## Success criteria

- `fetch_bars.py` pulls 365 days of MGC bars in one invocation.
- Backtest with default slippage + commission reproduces a known-config result
  within ±5% of a hand-computed expected P&L.
- `hold_seconds` in trade CSVs are correct positive integers.
- Walk-forward run on 365d MGC produces a `walkforward_ranking.txt` showing the
  top 10 configs by combine-pass score.
- The top config has pass-rate ≥ 30% across windows (i.e. it'd pass the Combine
  in ~3 of 10 tries — realistic for a directional strategy).
