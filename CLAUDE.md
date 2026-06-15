# CLAUDE.md — topstep-bot

These rules apply to every task in this project unless explicitly overridden.
Bias: caution over speed on non-trivial work. Use judgment on trivial tasks.

---

## Project overview

TopstepX algo-trading bot. Python/FastAPI backend, React/TypeScript frontend.
Runs locally on Windows — no VPS, no cloud execution, no external state.

**Stack**
- Backend: Python 3.x, FastAPI, asyncio, `project-x-py` SDK
- Frontend: React + TypeScript + Tailwind CSS (hacker/matrix theme — `#00ff41` on `#000`)
- Config: `bot_config.json` (persisted) + `.env` (secrets, never committed)
- Deploy: Windows Task Scheduler via `deploy/windows/`

**Layout**
```
app/
  api/server.py         FastAPI routes + SSE stream
  broker/topstepx.py    TopstepX broker (entry, fill events, bracket-after-fill)
  broker/events.py      Typed fill/order events
  broker/protocol.py    Broker ABC
  strategy/             Signal generation (displacement, liquidity, composer)
  risk/pretrade.py      Pre-trade checks
  notifications/        SMTP email notifier + scheduler
  sync/                 Outbox + sender for external sync
  bot_config.py         BotConfig pydantic model
  main.py               Process entry point
frontend/src/
  components/           React UI components
  hooks/                useStream, useConfig, useKillzone
  App.tsx               Root layout
tests/                  pytest suite
```

---

## Rule 1 — Think before coding

State assumptions explicitly. If uncertain, ask rather than guess.
Present multiple interpretations when ambiguity exists.
Push back when a simpler approach exists.
Stop when confused. Name what's unclear.

**Project specifics:**
- The `project-x-py` SDK has surprising internals (e.g. `place_bracket_order` has a hardcoded 60s wait). Read SDK source in `.venv/Lib/site-packages/project_x_py/` before assuming an SDK call does what its name implies.
- Order placement is real money. Never assume a new order path works without tracing the full call chain.

---

## Rule 2 — Simplicity first

Minimum code that solves the problem. Nothing speculative. No features beyond what was asked.
No abstractions for single-use code.
Test: would a senior engineer say this is overcomplicated? If yes, simplify.

**Project specifics:**
- The bracket-after-fill pattern (`_pending_brackets` dict + fill-event handler) already handles both market and limit entry. Don't add a third path unless the two-path dispatch is genuinely insufficient.
- `BotConfig` is a flat Pydantic model. Don't introduce sub-models or validators unless the field genuinely needs them.
- The frontend is intentionally minimal — SSE for live data, no WebSocket, no Redux. Don't introduce state management libraries.

---

## Rule 3 — Surgical changes

Touch only what you must. Clean up only your own mess.
Don't "improve" adjacent code, comments, or formatting.
Don't refactor what isn't broken. Match existing style.

**Project specifics:**
- The Tailwind border pattern is intentional: `gap-px bg-border border border-border` on the parent grid; children have no border. Don't add `border border-border` to child elements — it creates 3px junctions.
- The `MatrixRain` canvas sits at `z-index: -1`; root div is transparent; `html/body` carries `background: #000`. This three-part contract is load-bearing. Don't add a background to the root div.
- `server.py` hot-applies config changes to the running broker instance (no restart). Any new `BotConfig` field that affects broker behavior needs a corresponding hot-apply block in `PATCH /api/config`.

---

## Rule 4 — Goal-driven execution

Define success criteria. Loop until verified. Don't follow steps blindly.
Strong success criteria let you loop independently.

**Project specifics:**
- For broker changes: success = entry fills → brackets appear on the exchange, stop + target both visible, confirmed via `scripts/sdk_diagnostic.py` or exchange UI.
- For API changes: success = `curl localhost:8000/api/config` reflects the change AND the running broker instance reflects it (check startup log or add a log line).
- For frontend changes: success = the feature renders correctly at `localhost:5173`, no console errors, no TS errors (`npm run build` clean).

---

## Rule 5 — Use the model only for judgment calls

Use Claude for: classifying signal types, drafting config schemas, summarizing SDK behavior, extracting fill event fields.
Do NOT use Claude for: routing orders, retrying network calls, computing tick math, deterministic price transforms.
If code can answer, code answers.

**Project specifics:**
- Price arithmetic (ticks → dollars, stop distance) must be deterministic Python — never delegated to a prompt or inferred.
- The `_pending_brackets` lookup is a dict keyed by `order_id` string. No fuzzy matching, no LLM classification.
- Risk checks in `pretrade.py` are hard gates. They must be deterministic; do not add AI-driven overrides.

---

## Rule 6 — Token budgets are not advisory

Per-task: 4,000 tokens. Per-session: 30,000 tokens.
If approaching budget, summarize and start fresh. Surface the breach. Do not silently overrun.

---

## Rule 7 — Surface conflicts, don't average them

If two patterns contradict, pick one (more recent / more tested). Explain why. Flag the other for cleanup.
Don't blend conflicting patterns.

**Project specifics:**
- If the SDK exposes a convenience method that conflicts with the direct-call pattern (e.g. `place_bracket_order` vs. `place_limit_order` + fill handler), always prefer the direct-call pattern. The bracket wrapper has a hardcoded timeout that breaks live trading.
- If `bot_config.json` and `.env` disagree on a value (e.g. `TOPSTEP_BOT_ENTRY_MODE` env var vs. `entry_mode` in config), the broker reads `self.entry_mode` which is set from `BotConfig`. Env var is a fallback only. Don't add a third source.

---

## Rule 8 — Read before you write

Before adding code, read exports, immediate callers, shared utilities.
"Looks orthogonal" is dangerous. If unsure why code is structured a way, ask.

**Project specifics:**
- Before touching `server.py`: read `main.py` to understand how `_broker`, `_engine`, and `_bot_cfg` globals are initialized and when they're `None`.
- Before touching `topstepx.py`: read `broker/protocol.py` (the ABC) and `broker/events.py` (fill/order types). Don't add methods that bypass the protocol.
- Before touching any frontend component: check if it's rendered inside a `gap-px bg-border` grid. If so, it must not carry its own outer border.
- Before reading SDK source: check `scripts/sdk_diagnostic.py` first — it may already have the answer.

---

## Rule 9 — Tests verify intent, not just behavior

Tests must encode WHY behavior matters, not just WHAT it does.
A test that can't fail when business logic changes is wrong.

**Project specifics:**
- Risk tests in `tests/test_risk.py` must fail if `pretrade.py` accidentally allows a trade that violates daily loss limit. A test that just checks the return type is not a risk test.
- Bracket tests must assert that stop and target orders are placed with the correct prices and sizes — not just that `_place_bracket_after_fill` was called.
- If a test mocks the broker, document explicitly which behavior it cannot catch.

---

## Rule 10 — Checkpoint after every significant step

Summarize what was done, what's verified, what's left.
Don't continue from a state you can't describe back.
If you lose track, stop and restate.

**Project specifics:**
- After any broker change: checkpoint includes "entry order type, fill event wiring, bracket placement, verified path."
- After any config change: checkpoint includes "field added to BotConfig, persisted in save_bot_config, returned in GET /api/config, hot-applied in PATCH /api/config, added to frontend types.ts and ConfigPanel form."
- A change is not done until the frontend reflects it and the bot doesn't require a restart to pick it up.

---

## Rule 11 — Match the codebase's conventions, even if you disagree

Conformance > taste inside the codebase. If you genuinely think a convention is harmful, surface it. Don't fork silently.

**Project specifics:**
- Python: snake_case, type hints, `log = logging.getLogger(__name__)`, async where I/O is involved.
- Frontend: functional components, hooks for data, Tailwind utility classes only (no inline `style=` except for canvas/chart internals), `text-dim` for secondary labels, `text-ink` for primary values.
- No comments that describe *what* code does — only comments for non-obvious *why* (SDK workarounds, timing constraints, Tailwind layout contracts).
- SSE is the data transport. Don't introduce polling, WebSocket, or local storage for live data.

---

## Rule 12 — Fail loud

"Completed" is wrong if anything was skipped silently.
"Tests pass" is wrong if any were skipped.
Default to surfacing uncertainty, not hiding it.

**Project specifics:**
- If a broker order call fails, log at ERROR level with the full response. Never swallow an exception in `place_bracket`, `place_limit_bracket`, or `_place_bracket_after_fill`.
- If `EmailNotifier.enabled` is False at startup, the log must say so. It does — don't remove that log line.
- If `_pending_brackets` contains an entry that never received a fill (order rejected, timed out), surface it. Don't silently drop it.
- If the frontend loses SSE connection, `connState` must reflect `"disconnected"` — don't mask reconnect latency by holding the last known state as "connected."

---

## Rule 13 — Every strategy feature must be observable in the UI

A feature that adds strategy state (a new tracker, detector, filter, or signal source) is not done until Lawrence can see it working from the dashboard. Three required layers:

**1. Backend: log key state transitions**
Log at `DEBUG` or `INFO` when meaningful state changes: a level locked, a sweep detected, a filter blocking a signal, a tracker reset. One line per event, enough to grep the log and see the feature firing.

**2. Backend: expose state via SSE**
Add the feature's live state to the `strategy_state` SSE event (emitted each bar via `/api/stream`). This event is the X-ray into what the strategy is currently "seeing." Each tracker adds its own key. Example shape:
```json
{
  "type": "strategy_state",
  "kz_ranges":  {"London": {"high": "103.0", "low": "98.0"}},
  "awaiting_sweeps": [{"side": "high", "price": "103.0", "bars_elapsed": 2}],
  "vp_poc": "101.5"
}
```
If `strategy_state` doesn't exist yet, create it. Keep each field small — just enough to confirm the feature is alive.

**3. Frontend: render it in the debug state panel**
The dashboard has (or will have) a collapsible `StrategyDebug` panel that subscribes to `strategy_state` and renders each field. Add a section for the new feature alongside existing ones. It doesn't need to be pretty — a labeled value or a short list is fine. The goal is: Lawrence opens the dashboard during a live/paper run and can see the feature's current state without looking at logs.

**Checkpoint wording for Rule 10:**
After any strategy feature: checkpoint includes "state logged at key transitions, exposed in strategy_state SSE field, rendered in StrategyDebug panel, verified visible in dashboard during paper run."

**Project specifics:**
- The `strategy_state` event is emitted inside the bar handler in `server.py` after `runner.on_bar()` completes, using the same SSE queue as other events.
- The debug panel is `frontend/src/components/StrategyDebug.tsx` (create it if absent). It sits below the signal panel in `App.tsx`, collapsed by default.
- Don't add `strategy_state` fields that require heavy computation — the bar handler is on the hot path. Read pre-computed state from the tracker, don't recompute it.
- `strategy_state` is debug data. It must never trigger a trade or mutate broker state. Pure read.
