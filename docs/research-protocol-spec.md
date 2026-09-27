# Zenith — Research Protocol Spec

**Purpose:** make Zenith's strategy research honest by construction. Every idea is tested against rules fixed in advance. Dev-period results are compared with what luck alone would produce. The holdout period is sealed and can be used once per strategy. Anything that graduates is watched for decay.

**Audience:** Claude Code, working in the Zenith repo (Python/Polars pipeline, walk-forward engine, FastAPI/React "Terminal" frontend, Discord alerts, research ledger, multi-model research loop).

**Instruction to Claude Code:** read the existing repo first. Map each section below onto the modules that already exist (engine, ledger, research loop, replay trainer) and extend them rather than duplicating them. Where this spec and the existing code disagree on naming or layout, keep the repo's conventions and note the mapping in `docs/research-protocol.md`.

---

## 0. Why this exists (lessons from the SI/GC study, Sep 2026)

A manual study of ~6,000 strategy variants on 11.7 years of 1-minute silver and gold data produced these lessons. Each one is a requirement below.

1. **Most ideas died on costs, not on a lack of signal.** Several families had a real edge before costs of +0.05–0.15R per trade, and a fixed ~2-tick slippage plus commission erased it whenever stops were small. Gross and net results must always be shown side by side, together with cost as a fraction of risk. (§5, §6)
2. **The holdout got burned.** 2023–26, and then 2026, were re-examined many times while selecting candidates, so they stopped being out-of-sample. The holdout must be unreadable by the research process and usable once per strategy. (§3)
3. **Luck is the default explanation.** Out of 4,114 variants, 21 hit t > 2 in 2026 alone, against ~95 expected by chance. Top performers picked on Jan–May 2026 averaged +0.33R per trade there and +0.03R on May–Sep. Every result needs a luck baseline next to it. (§7)
4. **A fill-model bug inflated results.** Long targets were filled at the next bar's open when it gapped past the target. A resting limit order fills at its own price. The engine needs explicit fill rules and regression tests. (§5)
5. **Single-instrument results are fragile.** Pool the same rules across instruments. (§8)
6. **Fully autonomous idea generation wasted tokens. Seeded ideas worked.** Every family must record where the idea came from. (§11)

**Non-goals:** live or paper order execution, since execution stays manual through the replay trainer; LLM-generated trading signals, such as "read the FOMC statement," which can't be backtested honestly; and portfolio "core" holdings, since prop accounts are flat daily.

---

## 1. Components (new or extended)

| Component | What it does |
|---|---|
| `protocol` | Pre-registered, hash-locked research rules (YAML) |
| `vault` | Holds holdout data. Research code cannot read it. Exposes a one-shot evaluation service |
| `costs` | Per-instrument cost model: commission, slippage by order type |
| `engine` (extend) | Fill model and bar-ambiguity rules, with regression tests |
| `metrics` | Gross/net R, cost-to-risk, per-year, drawdown, prop-combine simulation |
| `luck` | Null baselines, deflated Sharpe, family-level false discovery rate (FDR) |
| `pooling` | Same rules across the instrument universe, results per instrument and pooled |
| `ledger` (extend) | Runs, protocols, families, variants, holdout evaluations, graduations, forward trades |
| `agent_api` | The only interface research agents use: budgeted and logged |
| `graduation` | Human gate, rules card, signal list for the replay trainer |
| `decay` | Forward-test tracking, kill switches, monthly live-vs-backtest review, Discord alerts |
| `ui` | Protocol view, family funnel, strategy page, holdout meter, forward-test panel |

---

## 2. Protocol (pre-registration)

**File:** `research/protocols/<protocol_id>.yaml`

**Lifecycle:**

1. Human creates or edits a draft.
2. `zenith protocol lock <id>` canonicalizes the YAML (sorted keys), computes a SHA-256 hash, stores it in the ledger, and marks the file read-only.
3. Every run records `protocol_id` and `protocol_hash`. The engine refuses to run if the file's current hash doesn't match the ledger.
4. Any change requires a new protocol version. Results from different protocol versions are never mixed in one leaderboard.
5. Research agents can read protocols but have no write access. Enforce this in `agent_api`, not by convention.

**Schema (defaults are examples; the human sets real values):**

```yaml
protocol_id: metals-intraday-v1
created: 2026-09-27
owner: lawrence
universe: [NQ, ES, GC, SI]          # evaluate every variant on all of these
bar_data: databento_glbx_ohlcv_1m
periods:
  dev:     {start: 2015-01-01, end: 2022-12-31}
  embargo_days: 5                    # gap between dev and holdout (multi-day features)
  holdout: {start: 2023-01-06, end: 2026-09-25}
  holdout_status: sealed             # sealed | partially_used | burned
session:
  timezone: America/New_York
  flat_by: "16:10"                   # prop rule; verify against current Topstep rules
  no_trades_across_roll: true
costs:                                # per round trip per contract
  MNQ: {tick: 0.25, tick_value: 0.50, commission: 1.50, slip_ticks: {market: 1, stop: 1, limit: 0}}
  MES: {tick: 0.25, tick_value: 1.25, commission: 1.50, slip_ticks: {market: 1, stop: 1, limit: 0}}
  MGC: {tick: 0.10, tick_value: 1.00, commission: 1.50, slip_ticks: {market: 1, stop: 1, limit: 0}}
  SIL: {tick: 0.005, tick_value: 5.00, commission: 2.00, slip_ticks: {market: 1, stop: 1, limit: 0}}
  # slip_ticks are per side; a stop entry + stop exit = 2 ticks round trip
cost_stress_multiplier: 2.0           # every result is also reported with 2x slippage
requirements:
  min_trades_per_week_combined: 3     # across the universe; owner requirement
  min_trades_dev_per_instrument: 150
pass_criteria_dev:
  pooled_gross_t_min: 2.0
  pooled_net_R_min: 0.0               # after 1x costs
  pooled_net_R_stress_min: 0.0        # after cost_stress_multiplier
  instruments_net_positive_min: 3     # of 4
  years_net_positive_frac_min: 0.6
  drop_best_year_net_R_min: 0.0       # still >= 0 with the best year removed
  dsr_min: 0.95                       # deflated Sharpe probability (§7)
  family_fdr_q: 0.10                  # Benjamini-Hochberg within the family
pass_criteria_holdout:
  net_R_min: 0.0
  max_shortfall_vs_dev_se: 2.0        # holdout mean >= dev mean - 2 * SE(holdout)
budgets:
  max_variants_per_family: 200
  max_families_per_run: 10
  holdout_evals_per_strategy: 1
  holdout_evals_before_burned: 25     # after this many, holdout_status -> burned
correlation:
  max_daily_pnl_corr_with_graduated: 0.5
prop_sim:
  enabled: true
  account: topstep_50k                # parameters in prop_rules/topstep_50k.yaml; verify current rules
kill_switch:
  drawdown_multiple_of_worst_backtest: 1.5
  review_at_trades: [30, 60]
  stop_if_mean_R_below_at_60: 0.0
```

`prop_rules/*.yaml` holds each firm's current rules (profit target, trailing max loss, daily loss limit, consistency rule). They're kept as data, never hard-coded, and the file records the date it was last checked against the firm's website.

---

## 3. Holdout vault

**Goal:** the research process physically can't see holdout bars, and every look at them is counted.

- **Storage:** at ingest, split parquet files into `data/dev/` and `vault/holdout/`. The vault directory belongs to a separate OS user or container volume that isn't mounted into the research worker. If the worker can read `vault/`, the setup is wrong.
- **Service:** `vault_service` exposes exactly one call:
  - `POST /holdout/evaluate {strategy_hash, protocol_id}` → returns metrics only: the same metric set as §6, per instrument and pooled, with no bars, trade lists or equity curves until the strategy has graduated.
  - It refuses if this `strategy_hash` was already evaluated under this protocol, if the protocol's `holdout_status` is `burned`, or if the strategy hasn't passed dev criteria.
  - Every call is written to `ledger.holdout_evals` with timestamp, caller (human or agent), and result.
- **Human gate:** holdout calls from an agent go to an approval queue in the UI. A human click releases them.
- **Burn counter:** when `holdout_evals` for a protocol reaches `holdout_evals_before_burned`, set `holdout_status: burned`. From then on, new families need a new protocol with a later holdout window, or they go straight to a forward test.
- **Embargo:** drop `embargo_days` sessions between dev and holdout, so rolling features can't leak dev information into the holdout.
- **Existing candidates:** register the two metals candidates (gold prior-day high/low sweep, silver 8:25 five-minute range breakout, §12) with `holdout_contaminated: true`. Their only clean test is the forward test.

---

## 4. Strategy identity and trial counting

- `family_id`: one idea, e.g. "prior-day high/low sweep reversal." It has a required `source` (§11).
- `variant_id`: one parameter set within a family.
- `strategy_hash` = SHA-256 of (engine git commit, strategy code hash, canonical params, universe, protocol_id).
- **Every executed variant is a trial,** including failures, crashes that produced results, and variants the agent later discards. The trial count per family feeds §7. No deleting trials.

---

## 5. Engine correctness (with regression tests)

**Fill model:**

| Order | Fill rule |
|---|---|
| Market at bar close | close ± slippage ticks (adverse) |
| Stop entry / stop-loss | trigger at stop price; if the bar **opens beyond** the stop, fill at the open; then apply adverse slippage |
| Resting limit (entry or target) | fill **at the limit price only**, never better, even if the next bar opens past it; requires price to trade **through** the limit by ≥ 1 tick (configurable) |
| Time exit | bar close ± slippage |

**Ambiguity rules:**

- If one bar touches both stop and target, assume the stop was hit first.
- If a stop-entry bar also touches the stop-loss, the trade is a loss on that bar.
- A bar that breaks both sides of a range is skipped for that day (configurable).

**No look-ahead:**

- Signals use data through the close of bar *t*. Fills happen at the close of *t* (market) or later (stop/limit orders).
- Features from another instrument, such as gold used to filter silver, must use that instrument's bar *t-1* close or earlier, unless both instruments' bars close at the same moment and the fill is at that close.
- Daily indicators (ATR, prior-day high/low, trend) use only completed sessions.

**Sessions and rolls:**

- Session = 18:00–17:00 ET, with correct DST handling. Force flat at `flat_by`.
- No position may be held across a contract roll. Multi-day features use a back-adjusted series.

**Required tests** (`tests/engine/test_fills.py`):

1. A long target where the next bar gaps above the target → fill equals the target price. *(Regression for the bug found in the SI study.)*
2. A short stop where the bar opens above the stop → fill equals the open plus slippage.
3. Stop and target touched in the same bar → the stop is taken.
4. A limit touched exactly but not traded through → no fill (with the default of 1 tick through).
5. A trade open at `flat_by` → closed at that bar's close.
6. A day spanning a roll → no carry.
7. Look-ahead guard: shift any feature one bar earlier, and the test must detect the resulting change in signals.

---

## 6. Metrics (per variant, per instrument, pooled)

Report every one of these with **and** without costs. There is no costs-off-only view anywhere in the UI.

- `n_trades`, `trades_per_week`
- `gross_R_mean`, `net_R_mean`, `net_R_mean_stress` (2× slippage)
- `cost_R_median`: median cost as a fraction of the trade's risk. **Flag if > 0.15.**
- `gross_t`, `net_t`, `win_rate`, `payoff_ratio`
- `max_drawdown_R`, `longest_losing_streak`
- `per_year` table (n, gross R, net R) and `years_positive_frac`
- `drop_best_year_net_R`
- `median_risk_usd` per micro contract, and `risk_vs_prop_mll`: how many average losses before the trailing max loss is hit.
- `prop_sim`: replay the variant's trades through the prop rules file (`prop_rules/*.yaml`), starting a new combine at each month start. Report pass rate, median days to pass, and fail reasons (max-loss limit or consistency rule).

---

## 7. Luck baseline (always shown next to results)

For each family, compute and store:

1. **Null distribution by random entry:** for each variant, generate K = 200 synthetic strategies with the same instrument, the same count of trades per day, entries at random minutes drawn from the variant's actual entry-time histogram, random direction, and the same stop/target/time-exit rules and costs. The variant's percentile within this null is its `null_pct`.
2. **Expected number of passes by chance:** apply the dev pass criteria to the null strategies. Report `expected_passes_null` next to `observed_passes` in the family funnel.
3. **Deflated Sharpe ratio** (Bailey & López de Prado), on per-trade net R:
   - `SR` = mean/std of per-trade net R, with `T` trades, skew `γ3` and kurtosis `γ4`.
   - `N` = number of trials in the family (§4). `V` = variance of `SR` across the family's variants.
   - `SR0 = sqrt(V) * ((1-γ)*Φ⁻¹(1-1/N) + γ*Φ⁻¹(1-1/(N*e)))`, where `γ` ≈ 0.5772 (Euler–Mascheroni).
   - `DSR = Φ( (SR - SR0) * sqrt(T-1) / sqrt(1 - γ3*SR + (γ4-1)/4*SR²) )`
4. **Family-level FDR:** Benjamini–Hochberg on one-sided p-values of `net_t` within the family, at `family_fdr_q`.

A variant passes dev only if **all** of the §2 criteria hold, including DSR ≥ `dsr_min` and surviving the FDR correction.

---

## 8. Pooling across instruments

- Parameters must be expressed in scale-free units: fractions of ATR, ticks derived from ATR, clock times. Hard-coded point values are rejected at registration.
- Each variant runs unchanged on every instrument in `universe`. It's reported per instrument, plus pooled (per-trade R concatenated; also equal-weighted by instrument).
- Instrument-specific sessions (e.g., SI opens 8:25, GC 8:20, equities 9:30) are allowed only as a declared mapping in the family definition, e.g. `open_time: {SI: "08:25", GC: "08:20", NQ: "09:30", ES: "09:30"}`, set before testing.
- Dev pass requires `instruments_net_positive_min` of N instruments to be net positive, in addition to the pooled thresholds.

---

## 9. Correlation and redundancy

- When checking a candidate against graduated strategies, compute daily P&L correlation (in R) over the dev period.
- If the correlation is above `max_daily_pnl_corr_with_graduated`, flag it as redundant. A human can still approve it, but the UI shows the overlap.
- Within a family, cluster variants whose trades overlap on more than 70% of days, and report one representative per cluster.

---

## 10. Ledger schema additions

Use the existing store (SQLite/Postgres), adding tables or columns as needed:

- `protocols(id, hash, yaml, locked_at, holdout_status, holdout_evals_used)`
- `families(id, name, source_type, source_ref, hypothesis, created_by, protocol_id)`
- `variants(id, family_id, strategy_hash, params_json, engine_commit, created_by, created_at)`
- `trials(variant_id, instrument, period, metrics_json, null_pct, dsr, fdr_pass, passed_dev)`
- `holdout_evals(strategy_hash, protocol_id, requested_by, approved_by, ts, metrics_json, passed)`
- `graduations(strategy_hash, approved_by, ts, rules_card_path, holdout_contaminated)`
- `forward_trades(strategy_hash, date, side, taken, entry, stop, target, exit, exit_reason, contracts, commission_usd, rules_followed, notes)`
- `decay_events(strategy_hash, ts, type, detail)`

The existing conversational agent over the ledger should be able to answer questions like "how many trials in family X, and how many would luck predict?" and "which strategies used the holdout, and who approved it?"

---

## 11. Agent API (Claude Code, GPT-6 Astra, Kimi K3, Grok)

Agents never touch files, data or the vault directly for research runs. They call:

- `get_protocol(id)`: read-only.
- `register_family(name, hypothesis, source_type, source_ref, param_space)`
  - `source_type`: `user | paper | book | video | derived`
  - `source_ref`: a URL, citation or note. Required.
  - `param_space` must fit within `max_variants_per_family`.
- `run_dev(family_id, variant_params[])`: runs, logs trials, returns §6 and §7 metrics.
- `get_family_funnel(family_id)`
- `request_holdout(strategy_hash)`: goes to the human approval queue (§3).
- `write_note(family_id, text)`: research notes. Agents can read their own and others' notes. This is the "self-improving ledger."

Budgets are enforced server-side. Each call records `agent_name` and `model_id`, so results can be compared per model.

---

## 12. Graduation and forward test

**Graduation** requires dev pass, holdout pass (or `holdout_contaminated: true` with an explicit human override), and human approval.

On graduation, generate:

1. **A rules card** (`graduated/<hash>/rules.md`): exact entry, stop, target and time rules in plain English, the ones a human executes in the replay trainer.
2. **A signal list** for the last 12 months (CSV/parquet): date, side, entry time, entry, stop, target, exit time, exit, reason, R. The replay trainer's drill mode uses it to check that the human reads the rules the same way the engine does.
3. **A forward-test plan**, taken from the protocol's `kill_switch` block and filled in with backtest numbers: worst drawdown in R, longest losing streak, and expected mean R with its standard error.

**Seed graduations** (`holdout_contaminated: true`):

- **Gold prior-day high/low sweep (MGC).**
  - Window 08:25–12:00 ET. Gold trades beyond the prior full-session high or low.
  - Within 30 one-minute bars, a 1-minute close lands back inside the level, at least 0.05×ATR14 away from the sweep extreme. Enter at market on that close.
  - Stop 1 tick beyond the extreme; target 1.5R; exit at 13:25. First signal per day only.
  - Backtest after costs: 2015–22 –0.03R, 2023–25 –0.06R, 2026 +0.26R per trade.
- **Silver 8:25 five-minute range breakout (SIL).**
  - Range = 08:25–08:29 high/low. Stop orders 1 tick beyond each side, active 08:30–10:00; first fill wins.
  - Stop 1 tick beyond the opposite side; target 1.5R; exit at 13:25. Skip the day if one bar breaks both sides.
  - Backtest after costs: 2015–22 –0.23R, 2023–25 0.00R, 2026 +0.17R per trade.

Re-implement both in the engine, confirm they reproduce these numbers within ±0.02R, then graduate them.

---

## 13. Decay monitor

**Inputs:** `forward_trades` from the log (manual entry in the UI, or import from the forward-test workbook's CSV).

**Daily:** update R, cumulative R and drawdown for each graduated strategy.

**Kill switches** (Discord alert plus a UI banner):

- Drawdown ≥ `drawdown_multiple_of_worst_backtest` × the worst backtest drawdown → status `STOP`.
- At each `review_at_trades` point: compute live mean R and its z-score against the backtest mean. At 60 trades, if mean R < `stop_if_mean_R_below_at_60` → `STOP`.
- Rule-adherence rate below 90% → `REVIEW_EXECUTION`. This means the live results aren't testing the strategy.

**Monthly:** a live-vs-backtest report per strategy, showing distribution overlap, a cost check (actual fills vs assumed slippage), and trades per week vs expected.

**Quarterly:** re-run each graduated strategy on the newest data appended to dev, under a new protocol version, and flag decay.

---

## 14. UI ("Terminal" theme, existing CSS variables)

- **Protocol:** YAML view, lock status, hash, holdout status and usage meter (e.g. `7/25 used`).
- **Family funnel:** trials → pass dev → *expected by luck* → holdout requested → holdout passed → graduated. The luck number sits right next to the observed count, in the same size.
- **Strategy page:**
  - Gross and net R side by side, with the cost-to-risk ratio and a >0.15 warning.
  - Per-year bars and a per-instrument table.
  - The null distribution histogram with this variant marked.
  - DSR, prop-sim pass rate, and risk in $ per micro contract.
- **Approvals queue:** pending holdout requests and graduations.
- **Forward test:** a per-strategy panel with equity curve in R, kill-switch status, review countdown, and adherence rate.

---

## 15. Build order and acceptance criteria

1. **Engine fill model and tests (§5).** Acceptance: all tests pass. Re-running the SI 8:25 range breakout reproduces 2026 net +0.17R (not +0.25R).
2. **Cost model and metrics (§6).** Acceptance: gross, net and stressed-net results appear for every run, and `cost_R_median` is populated.
3. **Protocol lock (§2) and ledger tables (§10).** Acceptance: the engine refuses to run on a hash mismatch, and agents get a permission error writing a protocol.
4. **Vault and holdout service (§3).** Acceptance: the research worker can't list `vault/`, a second holdout call on the same strategy is refused, and the burn counter works.
5. **Luck module (§7).** Acceptance: on a family of purely random-entry "strategies," observed passes ≈ expected passes, and DSR < 0.95 for ≥ 95% of them.
6. **Pooling (§8) and correlation (§9).**
7. **Agent API (§11).** Acceptance: every agent run is budgeted, logged with the model id, and has a family `source_ref`.
8. **Graduation artifacts, seed graduations (§12), decay monitor (§13), UI (§14).**

**Definition of done:** a new idea from a paper can go all the way from `register_family` → dev results with luck baseline → one-shot holdout → human graduation → rules card and signal list → forward-test tracking with kill switches, without anyone looking at holdout bars along the way.
