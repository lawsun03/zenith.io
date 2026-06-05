# Forming-Bar-Aware Backtest Replay — Design Spec

**Date:** 2026-05-27
**Status:** Approved (pending spec review)
**Parent:** `docs/superpowers/specs/2026-05-27-backtest-data-pipeline-scope.md` (sub-project B)
**Goal:** Make the backtest reproduce live signal generation by replaying sub-minute (5s) data as forming bars and resolving bracket fills at 5s resolution — closing the fidelity gap where completed-1min-bar replay generates ~0.3 signals/day vs ~5/day live.

---

## Problem

Live entries fire mostly on the intra-minute **forming-bar path** (`ExecutionEngine._poll_forming_bars` → `StrategyRunner.try_signal_from_forming`), which `replay_mode=True` disables and `PaperBroker` cannot model. Result: the completed-bar backtest under-samples live by ~15-40× (measured 2026-05-27: 14 completed-bar signals over 43 days vs ~5/day live). Every parameter analysis this week (EMA filter, killzones, partials) hit "I can't trust these numbers" because of this gap. This sub-project fixes it.

## Decisions (from brainstorming)

1. **Fill resolution:** sub-minute (5s). Replay 5s samples in time order; resolve stop/target/partial against each sample's high/low only *after* the entry sample. Correctly handles same-minute entry+exit (a bug in the completed-bar path).
2. **Input contract:** the existing intrabar CSV schema (`sample_ts, bar_minute, open, high, low, close, volume`) — what `TopstepXBroker` records live and what any purchased data is reformatted into. Completed 1-min bars are derived as each minute's last sample.
3. **Architecture (Approach A):** a dedicated sample-driven replay driver in a new backtest-only module + a focused `PaperBroker` sample-fill extension. The live path and the existing completed-bar backtest are untouched.
4. **Success bar:** count + character match vs the live log (±1 signal, sides + entry times within ~1 min) — exact match is unattainable given quote-mid-5s vs trade-based-1s/1s-poll data differences.

## Non-goals

- No change to the live trading path (`ExecutionEngine`, `TopstepXBroker`, risk).
- No change to the existing completed-bar `run_backtest` / `inject_bar` behavior — it stays green.
- No parameter optimization here — that comes after B + data depth exist.
- No historical data acquisition (sub-project C) — B is validated against the one captured session (`intrabar_MGC.csv`).

---

## Architecture

New backtest-only module `app/backtest/intrabar_replay.py`, plus a `PaperBroker` extension. Reuses `BacktestConfig`, `_compute_stats`, `_reconstruct_trades`, and `broker.on_fill` capture from `app/backtest/runner.py`.

### Components

**Loader** — `load_intrabar_csv(path) -> IntrabarData`:
- Parses the 5s snapshot CSV into time-ordered samples (each: `sample_ts`, `bar_minute`, OHLC as Decimal, volume).
- Groups samples per `bar_minute` (chronological).
- Derives the completed 1-min bar per minute as that minute's **last sample** (its running OHLC equals the full-minute OHLC: open preserved, high/low are running max/min, close is latest).
- Returns: ordered minutes, `{minute: [forming Bars]}`, `{minute: completed Bar}`.

**Replay driver** — `run_intrabar_backtest(cfg: BacktestConfig, data: IntrabarData) -> BacktestResult`:
- Builds `PaperBroker` (slippage/commission/`partial_profit_r` from cfg) and the runner (`_build_runner` from cfg.strategy_params — faithful VP/sizing/EMA/killzones).
- Captures fills via `broker.on_fill` (same as `run_backtest`).
- Runs the loop (Section "Replay loop"), then computes stats via the existing `_compute_stats` / `_reconstruct_trades`.

**PaperBroker sample-fill path** — `resolve_sample(bar) -> None`:
- Resolves every **open** bracket against the single sample's high/low (stop/target/partial), emitting exit fills. Shares logic with `inject_bar` via an extracted `_resolve_bracket(bracket, high, low, ts)` helper.

### Load-bearing boundary
Completed bars drive **strategy state only** (`runner.on_bar`). Fills resolve **only on samples**. Because a minute's samples' highs/lows aggregate to its completed bar, there is no double-resolution.

---

## Replay loop, ordering & dedup

Mirrors the live engine's two paths.

Per 5s sample, in time order:
1. `broker.resolve_sample(sample)` — resolve open brackets against this sample's high/low (emit stop/target/partial exits).
2. Forming eval — `runner.try_signal_from_forming(forming_bar)`, deduped by `b2.ts` (a `set`, mirroring the engine's `_forming_signal_fired` "one signal per displacement bar"). If a signal fires and passes the VP gate (when `cfg.strategy_params.vp_enabled` and `runner.vp.has_prior_profile()`, apply `runner.vp.apply(signal, cfg.strategy_params)`), call `broker.place_bracket(...)` → entry fills at the sample price + existing slippage.

At each minute close (after that minute's last sample): `runner.on_bar(completed_bar)` advances liquidity/displacement/EMA state, ages `_awaiting`, and fires any completed-bar signal (placed the same way).

**Causality:** resolve-then-enter ordering means an entry placed on sample S is eligible for resolution only from S+1 — so same-minute entry+exit is correct, and the entry minute's pre-entry price never triggers a false exit.

**Dedup:** track fired `b2.ts` in a set. The composer also self-limits (clears `_awaiting` on fire); we replicate the engine's explicit dedup to match live.

---

## Sample fill model (incl. partials)

Extract today's `inject_bar` resolution into a shared helper `_resolve_bracket(bracket, high, low, ts)` used by both `inject_bar` (completed-bar path, unchanged) and `resolve_sample`. Per sample, per open bracket, in this order:

1. **Partial** — if `partial_profit_r > 0`, not yet `partial_filled`, and the partial target is touched (`high ≥ partial_target` long / `low ≤ partial_target` short): close `partial_size`, move stop to break-even, mark `partial_filled`.
2. **Stop / target** — stop if `low ≤ stop` (long) / `high ≥ stop` (short); target symmetric. Pessimistic stop-first if both touched in the same sample. Fill price = the stop/target level.

Entry fill = the forming sample's price + existing market slippage (as `place_bracket` does today). Fidelity gains: at 5s resolution the "both hit in one bar" pessimism rarely triggers, and partials resolve across samples (partial → BE-stop → final target in time order), matching live.

---

## Validation harness & success criteria

`scripts/validate_intrabar_replay.py` runs the replay on `intrabar_MGC.csv` with the config the live bot ran that session and prints signals (count, side, entry minute) plus a diff against the live log's ground truth.

Success criteria (Rule 4):
1. **Count + character match** — reproduces the live session's signals within ±1, sides and entry times matching to ~1 min. (Today's log: 1 placed + 3 EMA-blocked at `trend_ema_period=50`; with the filter off, ~4 signals.)
2. **Rate sanity** — several signals/session, not ~0.3/day.
3. **No regression** — the existing completed-bar backtest and its tests stay green.
4. **Determinism** — same input → identical output.

VP caveat: the validated session had VP active at boot then wiped mid-session by a reload, so validation runs VP-off and relies on the ±1 tolerance.

---

## Testing (Rule 9)

`tests/test_intrabar_replay.py`:
- **Loader** — synthetic CSV → correct per-minute grouping and derived completed bars (open=first, high=max, low=min, close=last).
- **`resolve_sample`** — long bracket: `low ≤ stop` → stop exit at stop price; `high ≥ target` → target exit; both-in-one-sample → pessimistic stop-first; short symmetric.
- **Partials across samples** — partial target on sample A → partial fill + stop→BE; final target on sample B → remainder exit (asserts the stop-size == position-size invariant from the partials spec holds through the transition).
- **Same-minute entry+exit** — forming entry on sample S, stop on S+1: both fills emitted; the minute's pre-entry price does **not** trigger a false exit.
- **Dedup** — two consecutive samples satisfying the FVG for the same `b2` → exactly one signal.
- **Driver smoke + determinism** — small synthetic sample set → expected `BacktestResult` (`bars_processed`, trades); two runs → identical fills.

The Section "Validation" script is the acceptance gate, not a unit test (it depends on the captured `intrabar_MGC.csv`).

---

## Risks

- **Driver drifts from the engine's forming logic.** Mitigated by the count+character validation against the live log, and by reusing `runner.try_signal_from_forming` / `runner.on_bar` / composer directly rather than reimplementing detection.
- **5s granularity misses sub-5s timing.** Accepted: 5s is what the recorder captures; fill price uses the sample's stop/target level, so the error is timing (≤5s), not price.
- **Quote-mid vs trade-bar bars.** The intrabar recorder builds forming bars from quote mids, so derived completed bars differ slightly from live's REST trade bars — hence ±1 tolerance, not exact match.
- **Single validation session.** Only one captured session exists; broader confidence needs sub-project A/C (more data). B's correctness (loop/fills) is unit-tested independently of data volume.
