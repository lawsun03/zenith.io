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
| 4 | Vault + holdout service (§3), `strategy_hash` (§4) | **Code done; OS isolation pending Lawrence's pick** — `app/research/vault.py`, `holdout.py`, `identity.py`, `seeds.py`. Acceptance (2) and (3) pass in tests. (1) needs the Windows setup below. |
| 5 | Luck module (§7) | Not started |
| 6 | Pooling + correlation (§8, §9) | Not started |
| 7 | Agent API (§11) | Not started |
| 8 | Graduation, decay, UI (§12–14) | Not started |

## Component mapping

| Spec | Repo |
|---|---|
| `engine` | `app/backtest/runner.py` (`run_backtest`) driving `app/sim/paper.py` (`PaperBroker`). Same broker backs paper mode, so protocol rules sit behind `strict_fills`. |
| CLI | `python -m app.backtest` — strict fills **on** by default; `--legacy-fills` for paper-mode parity. Result JSON records `strict_fills`. |
| `ledger` | `app/research/ledger.py` `Ledger` → `research/ledger.db` (SQLite, gitignored, local). All §10 tables exist (schema v2). Writers: `protocols` (step 3), `holdout_evals`, `families` (seeds) (step 4). Prose history stays in `research/JOURNAL.md`, `research/findings.json`, `LESSONS.md`, `BACKLOG.md`. |
| `protocol` | `research/protocols/<id>.yaml` + `app/research/protocol.py`. `zenith protocol lock <id>` is `python -m app.research protocol lock <id>` (also `verify`). `research/PROTOCOL.md` stays the autonomous loop's prose rules. |
| `agent_api` | `app/research/agent_api.py` `AgentAPI`: `get_protocol`, `request_holdout` (queues only). Write/lock raise `PermissionError`. Refuses to construct when it can list the vault. |
| `vault` (storage) | `app/research/vault.py`: `vault ingest` splits `bars/bars_<SYM>.csv` into `data/dev/<protocol>/` (gitignored) and `$ZENITH_VAULT_DIR/<protocol>/holdout/`. `vault check` is the worker-side isolation check. |
| `vault_service` | `app/research/holdout.py`: `request_holdout()` (ledger only, the worker's side) and `VaultService.evaluate/release` (needs the vault, the human's side). CLI: `python -m app.research holdout queue\|release\|decline\|evaluate`. No HTTP route yet (see step 4). |
| `strategy_hash` (§4) | `app/research/identity.py`. |
| research loop | `research/PROTOCOL.md` + `research/SESSION_PROMPT.md` + `scripts/research_loop.ps1` |
| `costs` | `app/backtest/costs.py` `CostSpec`. It reads tick, tick value and commission from `app/sim/paper.py` (one source) and adds slippage by order type: market 1, stop 1, limit 0. |
| `metrics` | `app/backtest/metrics.py` `research_metrics()`. The CLI writes it as `research_metrics`, and each trade gets `gross_R`, `net_R`, `net_R_stress` and `cost_R`. |
| prop sim | `app/backtest/prop_sim.py` (per trade, rules from `prop_rules/<account>.yaml`, written as `research_metrics.prop_sim`). `app/backtest/funded_sim.py` stays as the daily-granularity combine/XFA chain (`funded_pipeline`). |
| data | `scripts/fetch_bars_databento.py` → `bars/bars_<SYM>.csv` (gitignored). Budget: `research/databento_ledger.txt`. |
| SI/GC study, seed strategy code, replay trainer, multi-model agents | Not in the repo. The seed *families* are registered by `python -m app.research seeds register <protocol>`. |

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
  costs, and each instrument's `tick`/`tick_value` equals `app/sim/paper.py`.
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
  4–8). *(Step 4: `families`, `variants` and `holdout_evals` now have writers, and `strategy_hash` exists.)*
- The OS read-only bit doesn't stop root or an admin. The hash check is the
  real gate.
- This container has no `project_x_py`. The end-to-end CLI check stubbed it:
  the protocol run wrote `protocol_id`, `protocol_hash` and `prop_sim`, and
  the unlocked, edited and off-universe runs were refused. The 0-trade fixture
  bars mean `prop_sim` ran on no trades end to end. The unit tests cover
  trades.

## §3 vault + §4 strategy identity (step 4)

**strategy_hash** (`app/research/identity.py`): SHA-256 over canonical JSON of
`engine_commit` (git HEAD), `code_hash`, `params` (sorted keys), `universe`
(sorted) and `protocol_id`.
- `engine_commit()` refuses when `app/` has uncommitted or untracked changes,
  because the commit wouldn't describe the code that ran.
- `code_hash()` defaults to every `.py` under `app/strategy/`. It hashes each
  relative path plus the file's bytes with CRLF normalised to LF, so a Windows
  `autocrlf` checkout and a Linux checkout agree.
- `variants` gained a `code_hash` column so the vault can say which part changed.

**Ingest** (`python -m app.research vault ingest <protocol> [--bars-dir bars] [--dev-dir data/dev] [--replace]`):
- The split is by CME session (`trading_day_ct`, 5pm CT roll). Dev covers
  `dev.start..dev.end` and goes to `data/dev/<protocol>/bars_<SYM>.csv`.
  Holdout goes to `<vault>/<protocol>/holdout/bars_<SYM>.csv`, with a
  `manifest.json` on each side (row counts, session span, sha256).
- **Embargo:** the first `embargo_days` sessions after dev end are dropped.
  They're taken from the union of all instruments' sessions, so every
  instrument shares one holdout start. The holdout starts at the later of
  that and `holdout.start`. For `metals-intraday-v1` that is the session
  after the 5th post-2022 session (around 2023-01-10), not 2023-01-06. The
  stricter date wins, and the effective start is written to both manifests.
- Bars outside dev and holdout are dropped, including anything after
  `holdout.end`. An existing split is refused unless `--replace` is passed.
  Out-of-order timestamps raise an error.
- **The source CSV is left in place,** and ingest logs a WARNING per
  instrument. `bars/bars_<SYM>.csv` still holds the holdout sessions, and the
  current autonomous loop and scripts read `bars/`. Moving it is part of the
  isolation setup below, not something ingest does silently.

**Holdout service** (`app/research/holdout.py`, the spec's `vault_service`):
- The spec's `POST /holdout/evaluate` is split by who can run it:
  - `request_holdout()` needs only the ledger. The worker calls it through
    `AgentAPI.request_holdout(strategy_hash, protocol_id)`. It records a
    `pending` row, or a `refused` row and raises `HoldoutRefused`.
  - `VaultService` only constructs in a process that can list the vault.
    `evaluate(strategy_hash, protocol_id, requested_by)` is the human's direct
    call. `release(eval_id, approved_by)` evaluates a queued agent request.
    `holdout decline` declines one.
  - The approval queue is `holdout queue` (CLI) for now; the UI list is step 8.
    An agent can't release its own request, because releasing needs vault read
    access, which the OS setup denies the worker. That is why there is no HTTP
    endpoint: an unauthenticated localhost route would let any local process
    claim to be the human. Step 8's UI calls `release()` inside the
    human-owned server process.
- **Refusals**, each recorded as a `refused` row with the reason:
  - an unknown hash;
  - a family registered under another protocol;
  - `holdout_status: burned`;
  - an existing `pending` or `evaluated` row for this (strategy, protocol);
  - a `holdout_contaminated` family (repo addition: evaluating it would spend
    a burn count for no evidence);
  - not passed dev.
  At release time it also refuses when the current checkout doesn't hash to
  the requested `strategy_hash`, when vault bars are missing for a universe
  instrument, and when the engine crashes. None of these count toward the
  burn. An unlocked or edited protocol raises `ProtocolError` with no row,
  because there is no locked protocol for the row to reference.
- **Result:** `{per_instrument: §6 research_metrics (incl. prop_sim), pooled:
  summarize_r over all instruments' trades concatenated, criteria: §2
  pass_criteria_holdout}`. Stored in `holdout_evals.metrics_json` and
  returned. No bars, trades or equity curves: a test checks that a
  trade-only sentinel never reaches the result or the ledger.
- **Holdout criteria:** pooled `net_R_mean ≥ net_R_min` and
  `net_R_mean ≥ dev_net_R_mean − max_shortfall_vs_dev_se × SE(holdout)`.
  Fewer than 2 holdout trades fails.
- **Burn counter:** only `evaluated` rows count. In the same `BEGIN
  IMMEDIATE` transaction as the row update, `holdout_evals_used += 1`, and
  `sealed` becomes `partially_used`. At `holdout_evals_before_burned` the
  status becomes `burned`. If another eval burned the holdout while this one
  ran, this eval is refused and its metrics are discarded.
- **Default runner:** `run_bot_config_variant` treats `params_json` as a
  `BotConfig` dump and runs the same `_run_backtest` path as `python -m
  app.backtest --protocol` (strict fills, protocol costs). Step 7's
  `run_dev` must use the same function.

**"Passed dev" is stubbed against `trials`, not blocked on step 5.**
`dev_result()` reads the latest `trials` row with `instrument='pooled'` and
`period='dev'` for the strategy. It passes only if `passed_dev=1`,
`fdr_pass=1`, `dsr IS NOT NULL` and `metrics_json.net_R_mean` is present.
Nothing writes that row yet, so today every strategy is refused (fail
closed). Step 5 must write exactly that row. The `trials` shape is the
contract, so step 5 needs no change here.

**Ledger schema v2** (auto-migrates v1 on open):
- `holdout_evals` gained `caller_kind` (human/agent), `model_id`, `status`
  (pending/evaluated/refused/declined), `reason` and `decided_at`.
- The table-level `UNIQUE(strategy_hash, protocol_id)` became a partial
  unique index over `pending`/`evaluated`, so refusals can be logged too.
- New triggers: decided rows are immutable, `burned` is terminal, and
  `holdout_evals_used` never decreases.
- `families.holdout_contaminated` and `variants.code_hash` are new columns.
- Protocol validation now requires `budgets.holdout_evals_per_strategy == 1`
  (the index enforces one) and `holdout_evals_before_burned ≥ 1`.

**Seeds:** `python -m app.research seeds register <protocol>` registers
`gold-pdhl-sweep@<protocol>` and `silver-825-orb@<protocol>` with
`source_type=user`, the §12 rules as the hypothesis, and
`holdout_contaminated=1`. It is idempotent. It needs a locked protocol,
because `families.protocol_id` is a foreign key, and **`metals-intraday-v1`
isn't locked yet, so nothing is registered in your ledger.** Variants come
with the seed code (step 8).

### Vault isolation: needs an OS decision (not set up)

The code reads the vault path from `ZENITH_VAULT_DIR` and never assumes a
location. Proposed options:

| | Setup | Vault hidden from worker | Human gate real | Ledger safe from agents |
|---|---|---|---|---|
| **A (recommended)** | New standard (non-admin) Windows user `zenith-research`. Agents and the research loop run as it. Vault at e.g. `C:\ZenithVault`, NTFS ACL with inheritance off: Lawrence + SYSTEM only. Raw `bars/` moves into the vault; the worker gets `data/dev/` only. | yes | yes: agents can't read the bars, so they can't release | possible later: step 7 can make `ledger.db` writable only by Lawrence |
| B | New `zenith-vault` user owns the vault. The holdout CLI runs as it (`runas`). Agents keep running as Lawrence. | only for non-elevated processes | no: an agent running as Lawrence can do anything Lawrence can | no |
| C | Vault path outside the repo, no ACL | no, convention only | no | no |

Only A meets acceptance (1) and the spec's "if the worker can read `vault/`,
the setup is wrong." Once a choice is made, the check on the Windows box is
`python -m app.research vault check <protocol>` run as the research user. It
must print `OK`. `AgentAPI` also refuses to construct if it can list the vault.

### Not covered yet (step 4)
- **Acceptance (1) is unverified on Windows:** the OS setup hasn't been chosen
  or done. In tests the guard is exercised with a readable directory (it
  refuses), with a mocked `PermissionError` (it passes), and with a real
  `chmod 000` directory as a non-root user (it passes; skipped when running
  as root).
- `bars/bars_<SYM>.csv` with 2023–26 data is still readable in the repo. The
  vault is only meaningful after that file moves (option A).
- `ledger.db` is writable by whoever runs the worker, so an agent could forge
  a `trials` pass row or edit rows with the triggers dropped. Step 7 has to
  put ledger writes behind the agent API.
- With `ZENITH_VAULT_DIR` unset, `AgentAPI` logs a WARNING and skips the
  isolation check.
- Pooled metrics concatenate trades. The equal-weight-by-instrument pool (§8)
  is step 6. `prop_sim` is per instrument only.
- The spec says graduated strategies may see holdout trades. Not built; step 8.
- No UI: the approvals queue and the holdout meter are step 8.
