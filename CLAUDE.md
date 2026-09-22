# CLAUDE.md — zenith

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

---

## Project overview

**Research and backtesting platform.** Discovers intraday futures strategies on NQ, ES and GC,
validates them against prop-account rules, and teaches the survivors to a human trader through
replay drills.

**There is no live execution.** The live bot is stopped and its broker path has been removed
(see "Repository boundaries"). A backtest produces a simulated trade sequence; nothing places
an order.

**Stack**
- Backend: Python 3.x, FastAPI, asyncio, Polars
- Data: Databento `GLBX.MDP3`, 1-minute bars, 2010-06-06 onward
- Models (research loop only): GPT-6 Astra, Kimi K3, Grok 4.7 — each provider hit directly
- Frontend: React + TypeScript + Tailwind (hacker/matrix theme — `#00ff41` on `#000`)
- Config: `bot_config.json` (persisted) + `.env` (secrets, never committed)

**Layout**
```
app/
  api/server.py         FastAPI routes + SSE stream
  sim/                  Simulation primitives: Bar/Fill types, fill model, tick math
                        (formerly broker/ — no live client remains)
  execution/engine.py   Backtest execution engine + StrategyRunner
  strategy/             Signal detectors — become IR predicates in phase 2
  backtest/             Backtest harness, runner, walk-forward
  optimizer/            Parameter sweeps, walk-forward optimisation
  risk/                 Trailing MLL / DLL / buffer scoring — pure library, no broker coupling
  analytics/            Trade analysis, MFE/MAE
  notifications/        Discord alerting for research results
research/               Research loop: anomaly pass, hypothesis generation, gates, ledger
docs/research-loop/     Specs: IR schema, gates, ledger DDL, account configs, cost model
frontend/src/           React UI — research dashboard and trainer
tests/                  pytest suite
```

---

# Domain invariants

These are not engineering preferences. They are the reason the research output is trustworthy.
If a task seems to require breaking one, stop and say so rather than working around it.

## Architecture

1. **Strategies are data, never code.** A strategy is a JSON document conforming to
   `docs/research-loop/strategy-ir.schema.json`. The engine interprets it. No model-generated
   Python is ever executed. If a strategy cannot be expressed in the IR, the IR is too weak —
   say so; do not add an escape hatch that evaluates arbitrary expressions.

2. **Rules are pooled across instruments, never fitted per-instrument.** One generic rule is
   fitted across NQ, ES and GC together. Per-instrument parameter sets are over-fitting
   (Carver's narrative fallacy). The IR has no per-instrument parameter block.

3. **Blend, do not select.** Gates are a filter that removes garbage. What gets promoted is an
   equal-weighted ensemble of *everything that survives*, not the top-ranked candidate.
   Evidence: 90 variations on gold futures scored 0.07 Sharpe selecting the annual best, 0.20
   picking at random, 0.33 blending all of them. Any code path that sorts candidates by
   performance and takes the top N is a bug.

4. **Account size never enters rule design.** Trading rules and stops depend only on market
   volatility (ATR, realised sigma). Account balance, daily loss limit and trailing drawdown
   enter at position sizing and at the combine simulator — nowhere else. Three layers, strictly
   separated: rule → volatility target → position sizing.

5. **Regime labels are classification only.** `regime_label` exists to include or exclude
   sessions and to group them for validation. It must never appear in the IR predicate
   vocabulary. A label derived from knowing how a day turned out is lookahead bias.

6. **The holdout is off-limits.** The most recent 18 months live at a path the research process
   cannot read, enforced by filesystem permissions. No code in `research/` may reference it.
   It is opened only by explicit human action, and every touch is logged.

## Statistics

7. **The ledger is append-only.** Nothing deletes from `hypotheses`. The trial count (weighted
   by `n_variants_swept`) is what the deflated-Sharpe and cutoff calculations depend on. A sweep
   of 90 variants counts as 90 trials, not 1.

8. **Fitting uses expanding or rolling windows only.** Never fit and test on the same period.
   Expanding-window is the default: fit only on data strictly before the test period.

9. **Both macro-release variants must pass.** Every candidate is backtested with release
   sessions included and excluded. The score is the **worse** of the two. Never report or select
   on whichever number flatters.

10. **A high Sharpe is a red flag, not a result.** Observed Sharpe above ~1.5 triggers
    investigation (negative skew, lookahead, an over-generous cost model), not promotion.
    Realistic after-cost target for this system is 0.8–1.4.

11. **Statistical tests are written by hand, not generated.** Deflated Sharpe, CSCV/PBO and the
    cutoff table are verified against independently computed values. A generated test agrees
    with the implementation's bugs.

---

# Engineering rules

## Rule 1 — Think before coding

State assumptions explicitly. If uncertain, ask rather than guess. Present multiple
interpretations when ambiguity exists. Push back when a simpler approach exists. Stop when
confused; name what's unclear.

**Project specifics:**
- There is no order path. If a task seems to need one, it is a misunderstanding of the project.
- `app/sim/` holds the fill model the backtester depends on. It is not a broker. Do not treat
  its `Fill`/`Bar` types as live-trading artifacts.
- Before changing anything in `app/execution/engine.py`, read `app/backtest/runner.py` — the
  backtester runs through that engine, so a change there changes every backtest result.

## Rule 2 — Simplicity first

Minimum code that solves the problem. Nothing speculative. No abstractions for single-use code.
Would a senior engineer call this overcomplicated? If yes, simplify.

**Project specifics:**
- `BotConfig` is a flat Pydantic model. No sub-models or validators unless the field needs them.
- The frontend is intentionally minimal — SSE for live data, no WebSocket, no Redux.
- The IR predicate vocabulary is deliberately small. Adding a predicate expands the search space
  and therefore the overfitting surface. Justify every addition.

## Rule 3 — Surgical changes

Touch only what you must. Clean up only your own mess. Don't "improve" adjacent code, comments,
or formatting. Don't refactor what isn't broken. Match existing style.

**Project specifics:**
- The Tailwind border pattern is intentional: `gap-px bg-border border border-border` on the
  parent grid; children carry no border. Adding `border border-border` to a child creates 3px
  junctions.
- `MatrixRain` sits at `z-index: -1`; root div is transparent; `html/body` carries
  `background: #000`. This three-part contract is load-bearing.
- Changing a gate threshold changes every historical comparison. Thresholds live in
  `docs/research-loop/gates.md` and the ledger records which was applied — never hardcode one.

## Rule 4 — Goal-driven execution

Define success criteria. Loop until verified. Strong criteria let you loop independently.

**Project specifics:**
- Backtest changes: success = a known strategy reproduces its prior result exactly, or the diff
  is explained and intended.
- IR changes: success = an existing hand-coded strategy expressed in IR backtests bit-identically.
- API changes: success = `curl localhost:8000/api/config` reflects the change AND the running
  process does.
- Frontend: renders at `localhost:5173`, no console errors, `npm run build` clean.

## Rule 5 — Use the model only for judgment calls

Use a model for: forming hypotheses, classifying regimes, summarising results, drafting schemas.
Do NOT use one for: tick math, price transforms, gate arithmetic, significance calculations.
If code can answer, code answers.

**Project specifics:**
- Price arithmetic and cost math are deterministic Python. Never delegated to a prompt.
- Gate pass/fail is arithmetic. A model never decides whether a candidate passed.
- The adversarial reviewer sees the hypothesis and mechanism, never the backtest. A reviewer
  shown a good result will rationalise it.

## Rule 6 — Token budgets are not advisory

Per-task: 4,000 tokens. Per-session: 30,000 tokens. If approaching budget, summarize and start
fresh. Surface the breach; do not silently overrun.

Separately: the research loop has a hard cap of 30–50 hypotheses per calendar year, enforced in
the ledger. When it is spent, the loop stops. That pause is intended behaviour.

## Rule 7 — Surface conflicts, don't average them

If two patterns contradict, pick one (more recent / more tested), explain why, flag the other
for cleanup. Don't blend conflicting patterns.

## Rule 8 — Read before you write

Before adding code, read exports, immediate callers, shared utilities. "Looks orthogonal" is
dangerous. If unsure why code is structured a way, ask.

**Project specifics:**
- Before touching `app/sim/`: 21 strategy files import its `Bar`/`Fill` types. Changing them is
  a breaking change across the whole strategy layer.
- Before touching `app/risk/`: the combine simulator (gate 8) depends on it, and its
  property-based drawdown tests caught real bugs. Keep them passing.
- Before touching any frontend component: check whether it renders inside a `gap-px bg-border`
  grid. If so it must not carry its own outer border.

## Rule 9 — Tests verify intent, not just behavior

Tests must encode WHY behavior matters. A test that can't fail when business logic changes is
wrong.

**Project specifics:**
- Risk tests must fail if the trailing MLL floor is allowed below starting balance, or if a
  daily-loss breach goes undetected. Checking a return type is not a risk test.
- Property-based tests (Hypothesis) for every invariant that can be stated as one: trailing
  drawdown monotonicity, ledger append-only behaviour, IR hash stability under key reordering.
- `Decimal` for all money math, never float. This already caused one 10× tick-value bug.
- Predicates are pure functions of a bar window. Assert no predicate reads a bar at an index
  beyond the current one.

## Rule 10 — Checkpoint after every significant step

Summarize what was done, what's verified, what's left. Don't continue from a state you can't
describe back. If you lose track, stop and restate.

**Project specifics:**
- After a gate change: checkpoint includes "threshold source, ledger field recording it,
  affected historical rows, test added."
- After an IR change: checkpoint includes "schema updated, validator updated, conformance test
  against an existing strategy still bit-identical."

## Rule 11 — Match the codebase's conventions, even if you disagree

Conformance > taste inside the codebase. If a convention is genuinely harmful, surface it;
don't fork silently.

**Project specifics:**
- Python: snake_case, type hints, `log = logging.getLogger(__name__)`, async where I/O is involved.
- Frontend: functional components, hooks for data, Tailwind utilities only, `text-dim` for
  secondary labels, `text-ink` for primary values.
- No comments describing *what* code does — only non-obvious *why*.
- SSE is the data transport. No polling, WebSocket, or local storage for live data.

## Rule 12 — Fail loud

"Completed" is wrong if anything was skipped silently. "Tests pass" is wrong if any were
skipped. Surface uncertainty rather than hiding it.

**Project specifics:**
- A gate that cannot be evaluated is a failure, not a pass. Never default a gate to passing.
- If a backtest silently drops bars, sessions, or trades, surface the count. A quiet drop
  changes every statistic downstream.
- If the frontend loses SSE connection, `connState` must reflect `"disconnected"`.

## Rule 13 — Every research feature must be observable in the UI

A feature that adds state (a tracker, detector, filter, gate, or signal source) is not done
until Lawrence can see it working from the dashboard. Three layers:

**1. Log key state transitions** at DEBUG/INFO — a level locked, a sweep detected, a gate
rejecting a candidate, a tracker reset. One line per event, greppable.

**2. Expose state via SSE** in the `strategy_state` event emitted each bar via `/api/stream`.
Each tracker adds its own key. Keep fields small — just enough to confirm the feature is alive.
```json
{"type":"strategy_state","kz_ranges":{"London":{"high":"103.0","low":"98.0"}},
 "awaiting_sweeps":[{"side":"high","price":"103.0","bars_elapsed":2}],"vp_poc":"101.5"}
```

**3. Render it in `StrategyDebug.tsx`**, collapsed by default, below the signal panel.

**Project specifics:**
- `strategy_state` is emitted in the bar handler in `server.py` after `runner.on_bar()`.
- Don't add fields requiring heavy computation — the bar handler is on the hot path. Read
  pre-computed state; don't recompute.
- `strategy_state` is debug data. Pure read; it must never mutate anything.
- Loop health (PBO, trials consumed against the annual cap, holdout touches remaining) belongs
  on the research dashboard unprompted. Those are the numbers that stop being checked once the
  system runs smoothly.

---

## Repository boundaries

**This repository is research and backtesting only.** The live bot is stopped and is not coming
back here. The following are removed and must never be reintroduced, added as a dependency, or
recreated under another name:

- The TopstepX / ProjectX client and the `project-x-py` dependency
- Any other broker client
- The outbox sync to DigitalOcean
- The Windows Task Scheduler deployment
- Broker credential handling

**Deliberately kept, despite the names:**

- `app/sim/` — fill model, `Bar`/`Fill` types, tick math. The backtester and all 21 strategy
  modules depend on these. Not a broker.
- `app/execution/engine.py` — the backtest execution engine, not a live order router.
- `app/risk/` — pure scoring library for trailing MLL, DLL and buffer. Gate 8 depends on it.
- `app/strategy/` — detectors that become IR predicate implementations.

The pre-teardown state is recoverable at the tag `pre-research-refactor`. Recover from that tag
rather than rewriting removed code from memory.
