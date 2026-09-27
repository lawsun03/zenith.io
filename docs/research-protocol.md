# Research protocol — repo mapping

Maps the Zenith Research Protocol Spec (2026-09-27, `docs/research-protocol-spec.md`) onto this repo. Where the
spec and the repo disagree on naming or layout, the repo wins and the mapping
is recorded here.

## Status by build step

| Step | Spec | State |
|---|---|---|
| 1 | Engine fill model + tests (§5) | **Done** — `PaperBroker(strict_fills=True)`, `tests/engine/test_fills.py`. Seed-strategy reproduction pending (needs data, see below). |
| 2 | Cost model + metrics (§6) | **Done** — `app/backtest/costs.py`, `app/backtest/metrics.py`, `research_metrics` in every CLI result. `prop_sim` landed with step 3. |
| 3 | Protocol lock + ledger tables (§2, §10) | **Done** — `app/research/`, `research/protocols/metals-intraday-v1.yaml` (draft, **not locked**), `prop_rules/topstep_50k.yaml`, `app/backtest/prop_sim.py`. |
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
| `ledger` | `app/research/ledger.py` `Ledger` → `research/ledger.db` (SQLite, gitignored, local). All §10 tables exist; step 3 writes only `protocols`. Prose history stays in `research/JOURNAL.md`, `research/findings.json`, `LESSONS.md`, `BACKLOG.md`. |
| `protocol` | `research/protocols/<id>.yaml` + `app/research/protocol.py`. `zenith protocol lock <id>` is `python -m app.research protocol lock <id>` (also `verify`). `research/PROTOCOL.md` stays the autonomous loop's prose rules. |
| `agent_api` | `app/research/agent_api.py` `AgentAPI`: `get_protocol` only so far; write/lock raise `PermissionError`. |
| research loop | `research/PROTOCOL.md` + `research/SESSION_PROMPT.md` + `scripts/research_loop.ps1` |
| `costs` | `app/backtest/costs.py` `CostSpec`. It reads tick, tick value and commission from `app/broker/paper.py` (one source) and adds slippage by order type: market 1, stop 1, limit 0. |
| `metrics` | `app/backtest/metrics.py` `research_metrics()`. The CLI writes it as `research_metrics`, and each trade gets `gross_R`, `net_R`, `net_R_stress` and `cost_R`. |
| prop sim | `app/backtest/prop_sim.py` (per trade, rules from `prop_rules/<account>.yaml`, written as `research_metrics.prop_sim`). `app/backtest/funded_sim.py` stays as the daily-granularity combine/XFA chain (`funded_pipeline`). |
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

## §6 metrics (step 2)

- **Gross R** is the fill prices with the broker's applied slippage removed.
  **Net R** then charges the `CostSpec` (slippage by order type plus round-trip
  commission). **Net R stress** multiplies slippage by 2; commission is never
  stressed.
- The costs are consistent: net R × risk in dollars equals the broker's own
  realized P&L to the cent (`test_net_R_matches_broker_realized_pnl_to_the_cent`).
- `cost_R_flag` is set when the median cost exceeds 0.15R.
- R is per contract. Exit type comes from the fill's `is_stop` flag: stops and
  forced flattens are slipped, targets are not. Every entry is a market order.

**Not covered yet:**
- Partial exits: the runner pairs each entry with one exit, so trades with a
  partial are misreported. Research runs should leave `partial_profit_r` at 0.
- SIL has no `DEFAULT_COMMISSION` entry, so it falls back to $0.74 per side.
  The spec's example is $2.00 round trip. Set the real figure before trusting
  SIL `net_R`.
- The swing engines (`ob_swing`, `fvg_swing`, `ifvg_swing`, `sweep_swing`) hold
  overnight by design. Under strict fills the session guard closes them at 5pm CT
  (with an ERROR log). Use `--legacy-fills` for those engines, or treat them as
  outside the protocol, which requires flat daily.

## §2 protocol lock + §10 ledger (step 3)

- **Lock:** `python -m app.research protocol lock <id> [--by lawrence]` parses
  the YAML, validates it, hashes the canonical form (JSON, sorted keys) with
  SHA-256, writes the row to `protocols`, and makes the file read-only.
  Comments and whitespace aren't hashed. Re-locking an unchanged file does
  nothing. A changed file under a locked id is refused, so bump the id
  (`-v2`).
- **Validation at lock:** required keys are present, `protocol_id` matches the
  file name, `holdout_status` is a known value, every universe instrument has
  costs, and each instrument's `tick`/`tick_value` equals `app/broker/paper.py`.
  A mismatched tick value would stop net R reconciling with the simulated P&L.
  Commission and slippage may differ from the broker.
- **Run gate:** `python -m app.backtest --protocol <id> [--ledger path]` verifies
  the hash before loading anything and exits 2 on no lock, a mismatch, an
  instrument outside `universe`, or `--legacy-fills`. The result JSON records
  `protocol_id` and `protocol_hash` (null without `--protocol`). Costs and
  `cost_stress_multiplier` then come from the protocol, not the broker tables.
- **Ledger guarantees (SQLite triggers):** a protocol's `hash`/`yaml` can't be
  updated and its row can't be deleted. `trials` and `holdout_evals` rows
  can't be deleted. `families.source_ref` must be non-blank.
  `holdout_evals` is unique on `(strategy_hash, protocol_id)`, which gives step
  4's one-shot rule a database backstop.
- **holdout_status:** the YAML value seeds the ledger at lock. After that the
  ledger is authoritative, because step 4's burn counter can't edit a locked
  file.
- **Agents:** `AgentAPI(agent_name, model_id, ledger)`. `get_protocol` goes
  through the same hash gate. `write_protocol`/`lock_protocol` raise
  `PermissionError` and log the agent and model.

**Repo deviations from the spec schema:**
- `universe` lists the traded micros (`MNQ, MES, MGC, SIL`), not the full
  contracts, because the universe name is also the costs key and the
  backtest `--instrument`.
- `prop_sim.risk_per_trade_usd` (default 200) is new. It is the dollar risk
  per trade for the combine replay.
- Holdout starts as `partially_used`, per the holdout decision above.

**prop_sim:**
- A new combine starts on the first day of each month and runs until it
  passes, fails or the data ends. Combines overlap.
- Each trade risks `risk_per_trade_usd`, floored to whole contracts at the
  trade's own risk per contract. A trade too wide for one contract is skipped
  and counted.
- Trailing max loss, the profit target and the 50% consistency rule run
  through `PhaseTracker`, the live governor's module. The daily loss limit is
  handled in `prop_sim`, with action `stop_day` or `fail`.
- Outcomes: `passed`, `failed_max_loss`, `failed_daily_loss`, `unresolved`,
  and `unresolved_consistency_blocked` (target reached but best day ≥ 50%).
  `pass_rate` is computed over resolved combines only.
- `risk_vs_prop_mll` now reads the rules file's `max_loss`.

**Not covered yet:**
- **`metals-intraday-v1` is a draft.** Lawrence sets the real values and runs
  the lock; nothing was locked in this session.
- **`prop_rules/topstep_50k.yaml` has `last_checked: null`.** Its values are
  copied from the `CombineRules` defaults, and the $1,000 Combine DLL is
  unverified. Every `prop_sim` result carries a warning until the file is
  checked against help.topstep.com.
- `consistency_best_day_frac` must be 0.5, because `account_phase` hardcodes
  Topstep's rule. Another firm's fraction needs that module changed first.
- `prop_sim` sees P&L only at trade exit, so intratrade max-loss and DLL
  touches are understated.
- `--protocol` is optional, so the autonomous loop keeps running without one.
  Step 7 makes it mandatory for agents.
- `families`, `variants`, `trials`, `holdout_evals`, `graduations`,
  `forward_trades` and `decay_events` exist but have no writers yet (steps
  4–8). No `strategy_hash` helper exists yet (§4); add it with `run_dev`.
- The OS read-only bit doesn't stop root or an admin. The hash check is the
  real gate.
- This container has no `project_x_py`. The end-to-end CLI check stubbed it:
  the protocol run wrote `protocol_id`, `protocol_hash` and `prop_sim`, and
  the unlocked, edited and off-universe runs were refused. The 0-trade fixture
  bars mean `prop_sim` ran on no trades end to end. The unit tests cover
  trades.
