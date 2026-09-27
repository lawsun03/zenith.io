# Research protocol — repo mapping

Maps the Zenith Research Protocol Spec (2026-09-27) onto this repo. Where the
spec and the repo disagree on naming or layout, the repo wins and the mapping
is recorded here.

## Status by build step

| Step | Spec | State |
|---|---|---|
| 1 | Engine fill model + tests (§5) | **Done** — `PaperBroker(strict_fills=True)`, `tests/engine/test_fills.py`. Seed-strategy reproduction pending (needs data, see below). |
| 2 | Cost model + metrics (§6) | Not started |
| 3 | Protocol lock + ledger tables (§2, §10) | Not started |
| 4 | Vault + holdout service (§3) | Not started |
| 5 | Luck module (§7) | Not started |
| 6 | Pooling + correlation (§8, §9) | Not started |
| 7 | Agent API (§11) | Not started |
| 8 | Graduation, decay, UI (§12–14) | Not started |

## Component mapping

| Spec | Repo |
|---|---|
| `engine` | `app/backtest/runner.py` (`run_backtest`) driving `app/broker/paper.py` (`PaperBroker`). Same broker backs paper mode, so protocol rules sit behind `strict_fills`. |
| CLI | `python -m app.backtest` — strict fills **on** by default; `--legacy-fills` for paper-mode parity. Result JSON records `strict_fills`. |
| `ledger` | Today: `research/JOURNAL.md`, `research/findings.json`, `LESSONS.md`, `BACKLOG.md`. No DB yet — step 3 adds SQLite tables. |
| `protocol` | Today: `research/PROTOCOL.md` (autonomous-loop rules, prose). Step 3 adds `research/protocols/<id>.yaml`. |
| research loop | `research/PROTOCOL.md` + `research/SESSION_PROMPT.md` + `scripts/research_loop.ps1` |
| `costs` | Today: `TICK_SIZE`, `TICK_VALUE`, `DEFAULT_COMMISSION` in `app/broker/paper.py`; `_POINT_VALUE` in `app/broker/pricing.py`. |
| prop sim | `app/backtest/funded_sim.py` (daily granularity) |
| data | `scripts/fetch_bars_databento.py` → `bars/bars_<SYM>.csv` (gitignored). Budget: `research/databento_ledger.txt`. |
| SI/GC study, seed strategies, replay trainer, multi-model agents, vault | Not in the repo. |

## Holdout decision (2026-09-27)

The spec's periods replace the old "2022 frozen holdout" rule:

- Dev: 2015-01-01 → 2022-12-31
- Embargo: 5 sessions
- Holdout: 2023-01-06 → 2026-09-25, **`holdout_status: partially_used`**. The
  autonomous loop evaluated 2023–26 repeatedly (2024 was its train year,
  2025–26 its test), so a holdout pass is weak evidence. Forward tests are the
  clean check.

`research/PROTOCOL.md` guardrail 4 was updated to match.

## §5 fill rules → `strict_fills=True`

| Rule | Implementation | Test |
|---|---|---|
| Resting limit fills at its price only | `_limit_hit` + exit at `bracket.target` | 1, `test_limit_traded_through_one_tick_fills_at_limit` |
| Limit needs trade-through (default 1 tick) | `limit_through_ticks` | 4 (long + short) |
| Stop gapped at open → fill at open + adverse slippage | `_stop_fill` | 2 (short + long) |
| Stop and target in one bar → stop | strict forces pessimistic whipsaw | 3 |
| Time exit / flat_by at close ± slippage | `ExecutionEngine._enforce_flatten` → `PaperBroker.flatten` | 5 |
| No carry across session break or roll | strict: bracket alive at a 5pm-CT session change is closed at the prior close, logged at ERROR | 6 |
| Look-ahead guard | every signal at bar t is reproduced from `bars[:t+1]`; meta-test proves a 1-bar-shifted feature is caught | 7 |

Already true before this change (legacy mode too): targets never fill at a
gapped open, whipsaw defaults to the stop, market entries fill at close + 1 tick.

**Not covered yet:**
- Stop *entries*: `PaperBroker` only takes market entries. The SI 8:25 breakout
  needs resting stop entries; add them with the seed-strategy work.
- Skip a day when one bar breaks both sides of a range: that belongs in the strategy, not the broker.
- Back-adjusted series for multi-day features.
- Look-ahead guard runs on the ORB detector only. The full `run_backtest`
  produces 0 signals on the checked-in fixtures, so a runner-level check would pass
  vacuously. Apply `_causal_violations` to each new family's signal function.

## Blockers for step 1's acceptance number

"SI 8:25 range breakout reproduces 2026 net +0.17R" needs:
1. SI 1-minute bars (none on disk; the cloud session can't reach Databento).
2. The strategy re-implemented with stop entries (see above).
3. For the full spec: 2015+ data for NQ, ES, GC, SI. Only 2021+ is on disk, and
   the Databento cap in `research/databento_ledger.txt` is $20 ($11.20 spent).
