# Claude Code prompts, one per phase

**How to use this file.**

1. Copy `CLAUDE.md` and `docs/research-loop/` into the repo root. Commit them before starting.
2. Run **one phase per Claude Code session.** Start a fresh session at each phase boundary —
   that is what keeps context small and cost predictable.
3. Paste the phase prompt verbatim. Do not paste the design doc; these prompts are the settled
   output of it.
4. Do not move to the next phase until the acceptance test in the current one passes.

Phases 0–2 are the defences. They exist before the generator that could abuse them. Building
phase 5 first will produce months of beautiful, meaningless backtests.

---

## Phase 0a — Teardown — ✅ DONE (commits 1c0e373, 59a5ea1)

Completed 2026-09-22. Recorded here because the original prompt was **wrong** and
should not be re-run.

**What the original prompt got wrong:** it said to delete "the execution engine, paper
and live broker abstractions." In this repo those are the *backtester's* foundation —
`app/sim/events.py` holds the `Bar`/`Fill` types that 21 strategy modules import,
`app/sim/paper.py` is the fill model `backtest/runner.py` depends on, and
`app/execution/engine.py` is the backtest execution engine. Deleting them would have
broken every backtest. `app/main.py` was also not purely live: `backtest/runner.py`
imported `_aggregate_bars` and `_tf_to_seconds` from it.

**What was actually done:**

1. Tagged `pre-research-refactor` at 41af4eb.
2. Renamed `app/broker/` → `app/sim/` (142 files) — the package holds simulation
   primitives, not a broker. Suite unchanged: 929 passed.
3. Extracted `app/builders.py` from `app/main.py` — the 13 non-live functions
   (`_build_runner`, `_aggregate_bars`, journalers, state publisher). Deleted the
   daemon half (`_run_live`, `_run_paper`, `_build_broker`, `_fetch_live_state`,
   pid lock, signal handlers, `_async_main`).
4. Extracted `app/strategy/event_times.py` — `load_event_times` was inside the
   deleted straddle module but is generic macro-release infrastructure the research
   loop needs.
5. Deleted: `app/sim/topstepx.py`, `app/sync/`, `deploy/windows/`, the news_straddle
   strategy/scheduler/backtest-engine and its config fields, three ProjectX scripts,
   12 live-coupled test modules — 26 files total.
6. Removed the `/api/accounts` live route and the `Outbox` type imports.

**Result:** 829 passed, 1 failed, 7 skipped. The single failure
(`test_chart_health::test_archive_exists_and_nonempty`, a missing data archive) also
failed before the refactor. Four pre-existing failures were *fixed* — they were
`ModuleNotFound` on `project_x_py` via the `app.main` import chain.

**Known dead code left deliberately** (Rule 3 — surgical changes):
- `frontend/src/components/{ConfigPanel,PhaseBanner}.tsx` still fetch `/api/accounts`,
  but both are gated behind `config.mode === 'live'`, which can no longer be true.
- `frontend/src/types.ts` and `StrategyDebug.tsx` still carry an optional
  `news_straddle` field.
- `app/execution/reconciler.py` reconciles broker state and has no caller now.
- `app/builders.py` retains `_make_bar_journaler`, `_make_strategy_state_publisher`
  and `_setup_logging`, which have no callers since the daemon was removed but are
  needed when the research dashboard is wired up (CLAUDE.md Rule 13).

---

## Phase 0 — Data spine

```
Read CLAUDE.md and docs/research-loop/README.md first.

Build the data spine for the strategy discovery loop.

Scope:
1. A Databento GLBX.MDP3 ingestion layer for NQ, ES and GC, 1-minute bars, from 2010-06-06
   to present. Cache raw pulls to local Parquet; never re-download what is already on disk.
2. Continuous-contract roll logic. Use volume-based roll with a configurable offset. Produce
   both back-adjusted and unadjusted series — back-adjusted for indicator calculation,
   unadjusted for anything price-level-dependent.
3. A Polars loading API that returns a typed frame for (instrument, date range, adjustment).
4. Fold definitions, cut ONCE and frozen to a committed JSON file:
   - Expanding-window walk-forward folds across the corpus
   - Four disjoint robustness blocks: 2010-13, 2014-17, 2018-21, 2022-25
   - An 18-month holdout at the end of the series
5. Holdout enforcement: the holdout data lives at a path that code under research/ cannot read.
   Enforce with filesystem permissions and a unit test that asserts the read fails.

Acceptance test — I will check these:
- Roll logic reproduces a known continuous series for GC within one tick at every roll date.
- The frozen folds file exists, is committed, and no code path regenerates it at runtime.
- A test asserts that research/ code raises on attempting to read the holdout path.
- Loading 16 years of 1-minute bars for all three instruments completes in under 30 seconds
  from warm cache.

Do NOT:
- Build any strategy logic, indicators or backtesting. That is phase 2 and later.
- Add a ProjectX, Tradovate or any broker integration. There is no live path in this project.
- Regenerate folds dynamically for any reason.

Write the tests first. Run them. Commit at each green run.
```

---

## Phase 1 — Statistics kit and ledger

```
Read CLAUDE.md, docs/research-loop/README.md, docs/research-loop/gates.md and
docs/research-loop/ledger.sql first.

Build the statistics kit. This phase is the reason the rest of the project is trustworthy —
prioritise correctness over speed everywhere.

Scope:
1. Apply docs/research-loop/ledger.sql. Build a thin Python API over it: append_hypothesis(),
   record_gate_result(), trial_count_at(timestamp), and the canned view queries.
   Verify the append-only triggers actually fire.
2. Canonical IR hashing: sha256 over the IR with keys sorted recursively, so that two
   semantically identical documents with different key order hash identically.
3. Deflated Sharpe ratio (Bailey & López de Prado). Inputs: observed SR, number of trials,
   skew, kurtosis, sample length.
4. Combinatorially symmetric cross-validation (CSCV) producing a probability of backtest
   overfitting (PBO).
5. The Carver cutoff table from docs/research-loop/gates.md as a lookup with interpolation
   across years-of-data and trial-count.
6. A trial budget enforcer: a hard cap of 50 hypotheses per calendar year, read from the
   ledger, raising when exhausted. Not a warning — a raise.

I am writing the tests for items 3, 4 and 5 myself. Generate the implementations and leave
`tests/test_statistics.py` with clearly-named empty test stubs and docstrings describing
the expected inputs and outputs. Do not fill in expected values — a test you write will agree
with your implementation's bugs.

Write full tests yourself for items 1, 2 and 6, including property-based tests (Hypothesis)
for: IR hash stability under key reordering, ledger append-only behaviour, and monotonicity of
the cutoff table in both dimensions.

Acceptance test:
- Deleting a hypotheses row raises.
- Rewriting ir_hash, model_version or n_variants_swept on an existing row raises.
- Two IR documents differing only in key order produce the same hash.
- trial_count_at() correctly sums n_variants_swept, not row count.
- The budget enforcer raises on the 51st hypothesis in a year.

Do NOT build any hypothesis generation, model API calls or strategy logic.
```

---

## Phase 2 — Strategy IR

```
Read CLAUDE.md and docs/research-loop/strategy-ir.schema.json first.

Build the strategy IR interpreter.

Scope:
1. A validator for the schema, including the rules the JSON Schema cannot express:
   - entry composes only from predicates marked recognizable: true
   - no reference anywhere to regime_label, account balance, DLL or trailing drawdown
   - instruments is always all three (pooled fitting is mandatory)
2. A pure-function evaluator for every predicate in the vocabulary. Each takes a bar window and
   returns a boolean (or a scaled forecast value when forecast.scaled is true). No hidden state,
   no I/O, no lookahead — assert in tests that no predicate reads a bar at index > current.
3. An execution engine that walks bars, evaluates entry/exit/stop/target, and emits a trade
   sequence. Decimal for all money math.
4. The sizing layer, strictly separate: takes a trade sequence plus a volatility target plus
   an account config, and returns contract counts. This is the only module that reads
   docs/research-loop/accounts/*.json.

Acceptance test — this is the one that matters:
Express our existing hand-coded iFVG sweep strategy and the 10 AM open line strategy purely in
IR, and demonstrate that each backtests BIT-IDENTICALLY to the existing implementation over the
full corpus. Same trades, same timestamps, same fills, same P&L to the cent.

If the IR cannot express one of them, stop and tell me which predicate is missing. Do not add a
generic "eval this expression" escape hatch — that would defeat the entire design.

Do NOT build gates, hypothesis generation, or any model API integration.
```

---

## Phase 3 — Anomaly pass and regime labels

```
Read CLAUDE.md and docs/research-loop/README.md first. Phases 0-2 are complete.

Build the anomaly detection pass and regime labelling.

Scope:
1. A Polars feature computation over the corpus, per session per instrument. Features:
   realised-vol percentile against a trailing window; opening range size vs prior day range;
   maximum excursion beyond prior day high/low before reversal; overnight-to-RTH follow-through;
   volume profile skew; gap size and fill rate. Add others if justified — document why.
2. A ranking that scores each session by how unusual it was across those features, producing an
   anomaly table sorted descending.
3. Grok integration (grok-4.7, xAI API) for regime labelling: for each of the top ~10% of
   flagged sessions, use X Search and web search to determine what happened that day. Emit a
   structured label {category, description, confidence, sources} where category is one of:
   scheduled_macro (CPI/PPI/NFP/FOMC/PCE), quarterly_event (opex, roll, rebalance),
   idiosyncratic_shock, or none_identified.
4. Write labels into the anomaly table. This is a one-time backfill plus a small incremental
   nightly job.

CRITICAL — regime labels are classification metadata ONLY. They may be used to include or
exclude sessions from a validation sample, and to group sessions for separate validation. They
must NEVER be readable from the IR predicate vocabulary. A label derived from knowing how a day
turned out is lookahead bias. Add a test asserting the IR validator rejects any document
referencing a regime field.

Acceptance test:
- The nightly job produces a ranked, labelled table for three instruments in under 60 seconds.
- Spot-check: 2020-03-16 is labelled idiosyncratic_shock or scheduled_macro with high
  confidence, not none_identified.
- The IR validator rejects a document attempting to reference regime_label.

Budget: cap the Grok backfill at $150 of X Search spend. Log spend as you go and stop at the cap.
```

---

## Phase 4 — Gate pipeline and ensemble builder

```
Read CLAUDE.md and docs/research-loop/gates.md in full first. Phases 0-3 are complete.

Build the gate pipeline.

Scope:
1. Gates 0 through 9 exactly as specified in gates.md, in order, cheapest-first, short-circuiting
   on first failure. Every gate writes its measured value AND the threshold applied to the
   ledger's gate_results, including gates never reached.
2. The dual macro-release backtest: every candidate runs twice, with release sessions in and out.
   Both must pass. The recorded score is min() of the two. Recorded as ONE trial.
3. The combine simulator (gate 8): Monte Carlo resampling of trade order, run through the rules
   in accounts/topstep-50k.json, returning payout probability. Reuse the existing risk module's
   trailing-MLL implementation rather than rewriting it.
4. The ensemble builder: survivors within a strategy family are combined into an EQUAL-WEIGHTED
   ensemble. The ensemble is what gate 8 scores and what gets promoted.

The single most important thing in this phase: there must be NO code path that sorts survivors
by performance and promotes the top N. Selection destroys value — 0.07 Sharpe selecting the
annual best versus 0.33 blending everything, with random selection at 0.20 in between. The gates
filter garbage; they do not rank. Add a test that fails if a sort-by-performance-then-truncate
appears in the promotion path.

Acceptance test:
- A strategy trading twice a week is rejected at gate 1, not later.
- A strategy with 1.2× cost headroom is rejected at gate 2.
- A known-good strategy clears to gate 10 and produces an ensemble.
- A candidate whose with-releases Sharpe is 1.1 and without-releases is 0.4 is scored at 0.4.
- Ledger rows contain gate_results for every gate evaluated, with thresholds.
```

---

## Phase 5 — Hypothesis loop

```
Read CLAUDE.md and docs/research-loop/README.md first. Phases 0-4 are complete.

Build the autonomous hypothesis generation loop. This is comparatively little code and a lot of
prompt iteration — keep the orchestration layer thin enough that swapping a model is a config
change, not a rewrite.

Scope:
1. Provider clients for OpenAI (gpt-6-astra), Moonshot (kimi-k3) and xAI (grok-4.7), each hit
   directly rather than through a gateway. Keys from environment only.
2. The four-stage cycle:
   a. Kimi K3 reads the ranked, labelled anomaly table plus bar context, and triages to a
      shortlist worth reasoning about.
   b. Astra takes one triaged anomaly and produces a falsifiable hypothesis: a stated causal
      mechanism, plus a strategy IR document.
   c. Kimi K3 adversarially reviews. CRITICAL: the reviewer sees the mechanism, the instrument
      and the session window — and NOT the backtest. It argues against the idea on priors and
      states what would falsify it. A reviewer that has seen a good backtest will rationalise it.
   d. Kimi K3 enumerates IR variants around a surviving hypothesis.
3. Every stage writes to the ledger: model name, exact version, prompt template hash,
   temperature, the mechanism, and the falsifier.
4. Prompt templates live in the repo as versioned files. Hash them and store the hash per row.
5. The trial budget enforcer from phase 1 gates the whole loop. When the annual cap is hit, the
   loop stops. That pause is intended behaviour, not a bug to route around.

Acceptance test:
- A full unattended cycle runs end to end and writes candidates and rejections to the ledger.
- Every ledger row has a non-null mechanism, falsifier, model_version and prompt_hash.
- The adversarial review stage cannot access backtest results — assert this in a test.
- The loop halts cleanly when the annual trial cap is reached.

Do NOT let generated IR bypass the phase-2 validator. Do NOT execute any model-generated code.
```

---

## Phase 6 — Trainer

```
Read CLAUDE.md first. Phases 0-5 are complete. This can proceed in parallel with 4 and 5 once
the phase-2 IR is stable.

Build the replay trainer. This is the terminal output of the whole system: a validated ensemble
becomes a drill you practise until you can execute it by hand.

THE SCORING RULE IS THE WHOLE DESIGN. You are graded against the strategy's rules, never against
what the market did. A trade that follows the IR and loses is CORRECT. A trade that ignores the
IR and wins is WRONG. Show the market outcome afterwards as information, visually separated,
and never aggregate it into the score. Outcome-based scoring teaches the user to override the
system on hunches, which is the exact habit that destroys discretionary execution of a
systematic edge.

Scope:
1. Bar-by-bar replay from the Databento tape with the future hidden. Terminal aesthetic
   consistent with the existing dashboard theme.
2. Three scored questions per decision point, in order: is this a setup; if so which direction;
   where does the stop go. Score each independently — they fail differently and need different
   remediation.
3. Weighted drill queue sampling: bias toward decision points the user got wrong previously,
   toward near-miss setups the strategy declines (learning what is NOT a trade is half the
   skill, and a uniform sample is almost all no-trade sessions), and allow filtering by the
   Grok regime label so CPI sessions can be drilled as a block.
4. Divergence tracking: over a drill set, what fraction of the strategy's trades did the user
   take, and what would their version's equity curve look like versus the strategy's. Write to
   drill_sessions. Include a free-text notes field for what they felt on the ones they got wrong.

Acceptance test:
- Drilling a validated ensemble produces a fidelity score, and it trends across sessions.
- A user decision matching the IR on a losing trade scores as correct.
- The market outcome is never a term in fidelity_score — assert in a test.
```

---

## Phase 6a — Commit phases 1–6

Run as soon as Phase 6 finishes, in the same session.

```
Phase 6 is done. Before anything else, commit all uncommitted work as separate commits, one
per phase, running the full test suite before each:

1. Phase 1 — ledger + stats: research/ledger/, research/stats/, their tests. Two files were
   corrected outside this session and must be kept exactly as they are: research/stats/
   deflated_sharpe.py (fixed formula + required sr_variance_across_trials argument) and
   tests/test_statistics.py (hand-computed expected values from the published papers — do
   not edit it).
2. Phase 2 — strategy IR: research/ir/, the IR schema change, IR tests.
3. Phase 3 — anomaly pass + regime labels: research/anomaly/, scripts/run_anomaly_pass.py,
   anomaly tests.
4. Phase 4 — gates + ensemble: research/gates/ (including the significance.py and
   pipeline.py changes that pass sr_variance_across_trials), gate tests, and the gate 6
   section in docs/research-loop/gates.md.
5. Phase 5 — hypothesis loop: research/loop/, scripts/run_hypothesis_loop.py, loop tests.
6. Phase 6 — trainer: research/trainer/, frontend changes, trainer tests.

The research/data changes, .env.example, .gitignore and ledger.sql go in whichever commit
they belong to. If a file doesn't clearly belong to one phase, ask me instead of guessing.
Never commit .env, Parquet caches, downloaded data, or anything under the holdout path.
Show me `git log --oneline` and the final suite result when done.
```

---

## Phase 6b — Wire the gates into the loop

New session. Required before the loop is ever run for real: today the loop records
hypotheses (spending the annual trial budget) that nothing ever tests.

```
Read CLAUDE.md and docs/research-loop/gates.md (including the gate 6 section) first.

Gap: research/loop/cycle.py generates candidates and writes them to the ledger, but nothing
runs them through research.gates.pipeline.evaluate_candidate. Wire it in.

Scope:
1. After a candidate's IR passes validation, backtest it twice (macro-release sessions
   included and excluded) with research.ir.engine across NQ/ES/GC on the frozen folds, build
   the two CandidateInputs, and call evaluate_candidate.
2. Pipeline arguments are properties of the whole search. Compute them once per loop state,
   not per candidate:
   - n_trials = research.ledger.api.trial_count_at(now). Variant-weighted, never a row count.
   - years = the corpus length actually used, excluding the holdout.
   - sr_variance_across_trials = empirical_sr_variance over the PER-TRADE (non-annualized)
     OOS Sharpe of every ledger trial that has one, once at least 20 exist; before that,
     null_sr_variance(n_obs). If the ledger only stores annualized Sharpe, de-annualize it
     with each trial's trades_per_year or add a column — tell me which you did. Record which
     source was used, and its value, alongside the gate 6 result.
   - loop_pbo = CSCV over the aligned daily OOS returns of all recorded candidates when there
     are at least 2; None otherwise.
3. Write everything with record_gate_results and record_candidate_scores: gate_results,
   first_failed_gate, sharpe_is/oos/decay/deflated, sr_cutoff_applied, trial_count_at_test,
   sharpe_with_releases/without_releases, combine_payout_prob, outcome.
4. Survivors join the equal-weighted ensemble for their family. No ranking, no top-N.

Acceptance test — in-memory ledger, mocked model clients, no real budget spent:
- A fixture candidate runs end to end; its ledger row has all 10 gate results with thresholds.
- A known-bad candidate stops at the right gate; later gates are recorded as unreached.
- Gate 6 uses null_sr_variance below 20 recorded trials and empirical_sr_variance at 20+.
  Test both.
- A survivor lands in an ensemble, and nothing sorts survivors by performance.

Do NOT run scripts/run_hypothesis_loop.py against the real ledger or real model APIs.
```

---

## Phase 7 — Ledger chat agent

```
Read CLAUDE.md and docs/research-loop/ledger.sql first. All prior phases complete.

Build a conversational layer over the research ledger, backed by Kimi K3.

Scope:
1. Read-only SQL access to the ledger, plus the canned views already defined in ledger.sql.
   The agent queries and narrates; it does not receive bulk rows dumped into context.
2. Answer questions like: what has been tested on GC and what happened to each; which gate
   rejects the most candidates and has that changed; show me everything that cleared gate 6 but
   died at the combine simulator and what they have in common; what is the out-of-sample decay
   distribution over the last three months; have we already tested something like this.
3. On open, surface loop health unprompted: current PBO, trials consumed against the annual
   budget, holdout touches remaining, rejection-by-gate breakdown. These are the numbers that
   say whether anything the loop produces means anything, and they are exactly what stops
   getting checked once the system runs smoothly.
4. Every answer cites the ledger rows it came from.

Constraints: read-only database credentials, enforced at the connection level not by prompt.
No write path. The agent must never be able to modify outcome, gate results, or the trial count.

Acceptance test:
- "What has been tested on GC?" returns an accurate answer citing row ids.
- Health numbers appear without being asked for.
- An attempt to get the agent to update a row fails at the connection layer.
```

---

## Phase 7a — Commit 6b + 7, reset the demo ledger

```
Commit the uncommitted work as two commits, running the full suite before each:
1. Phase 6b — gate wiring: research/loop/gate_runner.py and the cycle.py, pipeline.py,
   ir_validity.py, ledger api/db and ledger.sql changes it needed, tests/test_loop_gate_wiring.py,
   tests/test_loop_cycle.py, scripts/run_hypothesis_loop.py.
2. Phase 7 — ledger chat agent: research/ledger_agent/, scripts/ledger_chat.py,
   tests/test_ledger_agent_*.py.
If a file belongs to both, ask me.

Then reset the real ledger. var/ledger/research.db holds one demo row (mechanism "demo",
ensemble family "trainer-demo") made to demo the trainer. It counts as a trial and is marked
"blended" with empty gate_results, which is impossible for a real candidate. No real research
has run yet, so:
- Move (do not delete) var/ledger/research.db and its -wal/-shm files to
  var/ledger/archive/demo-2026-09-23.db.
- Only the hypothesis loop and gate runner may write hypotheses/ensembles rows at
  LEDGER_DB_PATH; the trainer may write drill_sessions/drill_decisions. Demos, fixtures and
  tests never touch LEDGER_DB_PATH. Add a test that enforces this.
- Confirm the next run creates a fresh ledger with ledger.sql applied and a trial count of 0.
```

---

## Phase 8 — First real run (you, in a terminal)

1. Add OPENAI_API_KEY, MOONSHOT_API_KEY and XAI_API_KEY to `.env`.
2. `python scripts/run_anomaly_pass.py --no-label` — free; builds and ranks the table.
3. `python scripts/run_anomaly_pass.py` — Grok regime labels; stops at its spend cap.
4. `python scripts/run_hypothesis_loop.py --max-shortlist 1 --max-variants 3` — at most 3 trials.
5. `python scripts/ledger_chat.py` — check health and what happened to each row.
6. Audit, in a fresh Claude Code session:

```
Read CLAUDE.md. Audit the first real run in var/ledger/research.db, read-only (open it with
mode=ro). For every hypotheses row, report pass/fail on each check:
- gate_results has 10 entries, each with measured and threshold; gates after the first
  failure are marked unreached.
- trial_count_at_test equals the variant-weighted count before that row, and
  n_variants_swept matches param_grid.
- gate 6 records which V[{SR_n}] source was used (null under 20 trials).
- sharpe_recorded is min(with-releases, without-releases).
- mechanism, falsifier, model_version and prompt_hash are real values, not placeholders.
- any "blended" row cleared gates 0-9 and points to an ensemble.
- holdout_touched is 0.
Change no code and no data. If a check fails, name the invariant and the row id.
```

---

## Phase 9 — Gate 10: human review and the holdout

Needed before anything reaches the trainer. Build it once the audit is clean.

```
Read CLAUDE.md (rule 6, the holdout) and docs/research-loop/gates.md (gate 10) first.

Build gate 10, the human review step. Nothing reaches the trainer without it. Put it outside
research/ (e.g. scripts/review.py plus an API route), because research/ must never read the
holdout.
1. A review view listing ensembles whose members all cleared gates 0-9, showing each
   member's mechanism, falsifier, gate results, disjoint-block results, both macro-release
   Sharpes and combine payout probability.
2. An explicit "open holdout" action for one ensemble: backtest it once on the 18-month
   holdout, set holdout_touched=1 on its member rows, record the result, timestamp and who.
   Require a typed confirmation. Refuse once 12 touches have been used this calendar year.
3. Approve or reject. Only approved ensembles can be drilled; the trainer refuses the rest.

Acceptance test:
- research/ code still cannot read the holdout path (the existing test still passes).
- The review action is the only code path that can.
- The 13th touch in a year is refused.
- The trainer refuses an unapproved ensemble.
```

