# CPI-day Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In one persistent process, suppress the base `combined` engine's new entries on CPI trading days while the B92 CPI-straddle scheduler owns the day; run the base engine normally every other day. Default-off behind `cpi_day_router_enabled`.

**Architecture:** A single pure helper module (`app/strategy/cpi_day.py`) is the one source of truth for "is today a CPI day" (ET date membership against `data/news_events.csv`). The pretrade gate gains a composable `CPI_DAY_BLOCK` check driven by a `cpi_day_active` boolean the execution engine computes per signal. `main.py` constructs the existing `NewsStraddleScheduler` and feeds the engine the CPI date set whenever the flag is on — `engine` stays `"combined"`. Rule 13 observability surfaces router state in `strategy_state` → `StrategyDebug`.

**Tech Stack:** Python 3.12 (FastAPI/asyncio backend), `zoneinfo` (stdlib, `ET = ZoneInfo("America/New_York")` already in `app/strategy/killzone.py`), pytest, React/TypeScript + Tailwind frontend.

**Spec:** `docs/superpowers/specs/2026-06-15-cpi-day-router-design.md`

---

## File structure

- **Create** `app/strategy/cpi_day.py` — pure helpers: `load_cpi_dates`, `is_cpi_day`, `next_cpi_date`, `router_state`. Single source of truth for CPI-day logic. Depends only on `zoneinfo`, `app.strategy.killzone.ET`, and `app.strategy.news_straddle.load_event_times`.
- **Create** `tests/test_cpi_day.py` — unit tests incl. the ET/UTC boundary case.
- **Modify** `app/risk/pretrade.py` — add `cpi_day_active: bool = False` param + `CPI_DAY_BLOCK` gate.
- **Modify** `tests/test_risk.py` — gate tests (entry denied on CPI day, exit allowed, non-CPI allowed).
- **Modify** `app/bot_config.py` — add `cpi_day_router_enabled: bool = False`.
- **Modify** `app/execution/engine.py` — accept `cpi_event_dates`, compute `cpi_day_active`, pass to `check()`.
- **Modify** `app/main.py` — load CPI dates + construct scheduler when flag on; pass dates to engine; publish router state.
- **Modify** `app/api/journal.py` — `publish_strategy_state` gains `cpi_day_router` param.
- **Modify** `frontend/src/types.ts` — `cpi_day_router` field on the strategy-state type.
- **Modify** `frontend/src/components/StrategyDebug.tsx` — render the router section.

Run all python tests with the venv interpreter: `.venv/Scripts/python.exe -m pytest ...`

---

## Task 1: CPI-day helper module

**Files:**
- Create: `app/strategy/cpi_day.py`
- Test: `tests/test_cpi_day.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_cpi_day.py
from datetime import datetime, timezone, date

from app.strategy.cpi_day import is_cpi_day, next_cpi_date, router_state


# A CPI release is 08:30 ET. In UTC that is 12:30 (EDT) — same calendar day in ET.
CPI_DATES = frozenset({date(2026, 7, 15), date(2026, 8, 12)})


def test_is_cpi_day_true_on_release_date():
    ts = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)  # 10:00 ET on a CPI day
    assert is_cpi_day(ts, CPI_DATES) is True


def test_is_cpi_day_false_day_before_and_after():
    assert is_cpi_day(datetime(2026, 7, 14, 14, 0, tzinfo=timezone.utc), CPI_DATES) is False
    assert is_cpi_day(datetime(2026, 7, 16, 14, 0, tzinfo=timezone.utc), CPI_DATES) is False


def test_is_cpi_day_et_vs_utc_boundary():
    # 2026-07-16 00:30 UTC is 2026-07-15 20:30 ET — still the CPI calendar day in ET.
    # A naive UTC-date check would wrongly read this as 07-16 and miss it.
    ts = datetime(2026, 7, 16, 0, 30, tzinfo=timezone.utc)
    assert is_cpi_day(ts, CPI_DATES) is True


def test_is_cpi_day_empty_set_is_false():
    assert is_cpi_day(datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc), frozenset()) is False


def test_is_cpi_day_naive_ts_assumed_utc():
    # Defensive: a naive ts is treated as UTC (matches killzone.py convention).
    assert is_cpi_day(datetime(2026, 7, 15, 14, 0), CPI_DATES) is True


def test_next_cpi_date_returns_today_if_cpi_day():
    ts = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    assert next_cpi_date(ts, CPI_DATES) == date(2026, 7, 15)


def test_next_cpi_date_returns_future_when_none_today():
    ts = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    assert next_cpi_date(ts, CPI_DATES) == date(2026, 8, 12)


def test_next_cpi_date_none_when_all_past():
    ts = datetime(2026, 9, 1, 14, 0, tzinfo=timezone.utc)
    assert next_cpi_date(ts, CPI_DATES) is None


def test_router_state_shape_on_cpi_day():
    ts = datetime(2026, 7, 15, 14, 0, tzinfo=timezone.utc)
    st = router_state(ts, CPI_DATES)
    assert st == {
        "today_is_cpi_day": True,
        "next_cpi_date": "2026-07-15",
        "base_entries_suppressed": True,
    }


def test_router_state_shape_off_day():
    ts = datetime(2026, 7, 20, 14, 0, tzinfo=timezone.utc)
    st = router_state(ts, CPI_DATES)
    assert st["today_is_cpi_day"] is False
    assert st["base_entries_suppressed"] is False
    assert st["next_cpi_date"] == "2026-08-12"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cpi_day.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.strategy.cpi_day'`

- [ ] **Step 3: Write the helper module**

```python
# app/strategy/cpi_day.py
"""CPI-day router helpers (single source of truth for "is today a CPI day").

A CPI release prints 08:30 ET, so a "CPI day" is the America/New_York calendar
date of the release. These are pure functions of (timestamp, date-set); no I/O
except load_cpi_dates, which reuses the same news_events.csv the live straddle
scheduler reads. Keeping ET-date logic in one place avoids a UTC-vs-ET off-by-one
between the pretrade gate and the dashboard panel (see test_is_cpi_day_et_vs_utc_boundary).
"""
from __future__ import annotations

from datetime import datetime, timezone, date

from app.strategy.killzone import ET
from app.strategy.news_straddle import load_event_times


def _et_date(ts: datetime) -> date:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(ET).date()


def load_cpi_dates(path: str, event_type: str = "CPI") -> "frozenset[date]":
    """ET calendar dates of every `event_type` release in news_events.csv.

    Returns an empty set if the file is missing (load_event_times already
    degrades to [] on a missing path), so a misconfigured path = "no CPI days"
    = base engine runs every day, never a crash.
    """
    return frozenset(_et_date(t) for t in load_event_times(path, event_type))


def is_cpi_day(ts: datetime, cpi_dates: "frozenset[date]") -> bool:
    if not cpi_dates:
        return False
    return _et_date(ts) in cpi_dates


def next_cpi_date(ts: datetime, cpi_dates: "frozenset[date]") -> "date | None":
    today = _et_date(ts)
    future = sorted(d for d in cpi_dates if d >= today)
    return future[0] if future else None


def router_state(ts: datetime, cpi_dates: "frozenset[date]") -> dict:
    """Rule-13 dashboard view. Pure read; never mutates anything."""
    active = is_cpi_day(ts, cpi_dates)
    nxt = next_cpi_date(ts, cpi_dates)
    return {
        "today_is_cpi_day": active,
        "next_cpi_date": nxt.isoformat() if nxt is not None else None,
        "base_entries_suppressed": active,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cpi_day.py -v`
Expected: PASS (10 tests)

- [ ] **Step 5: Commit**

```bash
git add app/strategy/cpi_day.py tests/test_cpi_day.py
git commit -m "feat(cpi-router): pure CPI-day helpers (ET-date source of truth)"
```

---

## Task 2: Pretrade CPI_DAY_BLOCK gate

**Files:**
- Modify: `app/risk/pretrade.py` (signature of `check()` ~line 60; new gate block after the phase governor)
- Test: `tests/test_risk.py`

- [ ] **Step 1: Write the failing tests**

Add a new test class to `tests/test_risk.py`. It reuses the module's existing `make_long_order()` helper, the `RiskState(config=fifty_k_combine())` pattern, and the already-imported `Allow`/`Deny`/`ProposedOrder`/`check`/`Decimal`. A fresh `RiskState(config=fifty_k_combine())` has `locked_out=None`, full contract headroom, and a valid combine config — i.e. a clean entry passes unless a gate denies it.

```python
# tests/test_risk.py (append after class TestPretradeGate)

class TestCpiDayRouter:
    def test_cpi_day_blocks_base_entry(self):
        # WHY: on a CPI day the straddle owns the session; a base entry must be
        # denied so the two strategies never trade the same day.
        state = RiskState(config=fifty_k_combine())
        decision = check(make_long_order(), state, cpi_day_active=True)
        assert isinstance(decision, Deny)
        assert decision.reason_code == "CPI_DAY_BLOCK"

    def test_cpi_day_allows_exit(self):
        # WHY: closing/flattening an existing position must never be blocked,
        # even on a CPI day.
        state = RiskState(config=fifty_k_combine())
        exit_order = ProposedOrder(
            instrument="MGC", side="short", size=1,
            entry=Decimal("2400"), stop=Decimal("2402"), target=Decimal("2394"),
            is_entry=False,
        )
        decision = check(exit_order, state, cpi_day_active=True)
        assert isinstance(decision, Allow)

    def test_non_cpi_day_allows_base_entry(self):
        # WHY: the router must not leak into normal days — a clean entry on a
        # non-CPI day must pass. This test fails if the gate over-blocks.
        state = RiskState(config=fifty_k_combine())
        decision = check(make_long_order(), state, cpi_day_active=False)
        assert isinstance(decision, Allow)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_risk.py -k cpi_day -v`
Expected: FAIL — `check()` got an unexpected keyword argument `cpi_day_active`

- [ ] **Step 3: Add the param and the gate**

In `app/risk/pretrade.py`, extend the `check()` signature (currently ends with `ts: datetime | None = None,`):

```python
def check(
    order: ProposedOrder,
    state: RiskState,
    phase: PhaseTracker | None = None,
    ts: datetime | None = None,
    cpi_day_active: bool = False,
) -> Decision:
```

Then insert this block immediately AFTER the phase-governor section (`# 1b. Phase governor ...`) and BEFORE `# 2. Sanity:`:

```python
    # ------------------------------------------------------------
    # 1c. CPI-day router: on a CPI trading day the base engine takes no new
    #     entries — the CPI straddle owns the day (cpi_day_router_enabled).
    #     Exits always fall through (you must always be allowed to flatten).
    # ------------------------------------------------------------
    if cpi_day_active and order.is_entry:
        return Deny(
            reason_code="CPI_DAY_BLOCK",
            message="CPI day — base engine suppressed; CPI straddle owns the session.",
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_risk.py -k cpi_day -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Commit**

```bash
git add app/risk/pretrade.py tests/test_risk.py
git commit -m "feat(cpi-router): CPI_DAY_BLOCK pretrade gate (entry-only)"
```

---

## Task 3: Config flag

**Files:**
- Modify: `app/bot_config.py` (the `news_straddle_*` block in `StrategyParams`)

- [ ] **Step 1: Add the field**

In `app/bot_config.py`, directly after the existing `news_straddle_arm_lead_seconds: int = 120` line, add:

```python
    # CPI-day router (in-process day-gate). Default-OFF. When True, the base
    # engine takes NO new entries on CPI trading days (pretrade CPI_DAY_BLOCK)
    # and the news_straddle scheduler is constructed to own those days, while
    # engine stays "combined". Phase-agnostic (combine and shadow alike).
    cpi_day_router_enabled: bool = False
```

- [ ] **Step 2: Verify it loads and defaults False**

Run: `.venv/Scripts/python.exe -c "from app.bot_config import StrategyParams; print(StrategyParams().cpi_day_router_enabled)"`
Expected: `False`

- [ ] **Step 3: Commit**

```bash
git add app/bot_config.py
git commit -m "feat(cpi-router): add cpi_day_router_enabled config flag (default off)"
```

---

## Task 4: Execution-engine wiring

**Files:**
- Modify: `app/execution/engine.py` (`__init__` ~line 411; `_act_on_signal` ~line 1022)
- Test: `tests/test_engine.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/test_engine.py`. It reuses the module's existing `PaperBroker`, `make_runner()`, `_signal(...)`, `RiskState`/`fifty_k_combine`, and the `@pytest.mark.asyncio` + `await engine._act_on_signal(...)` pattern (see `test_opposite_signal_reverses_even_with_headroom`). The `_signal` helper pins `created_at=in_ny_am(0)` = 2026-05-11 ET, so the CPI date set must contain `date(2026, 5, 11)`. Add `from datetime import date` to the test file's imports if not already present.

```python
@pytest.mark.asyncio
async def test_engine_blocks_base_entry_on_cpi_day():
    # WHY: with cpi_event_dates covering the signal's ET date, _act_on_signal must
    # hit the CPI_DAY_BLOCK gate and place no order (straddle owns CPI days).
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    engine = ExecutionEngine(
        broker, state, [make_runner()], replay_mode=True,
        cpi_event_dates=frozenset({date(2026, 5, 11)}),  # in_ny_am(0) is 2026-05-11 ET
    )
    await broker.connect()
    await engine.start()

    outcome = await engine._act_on_signal(_signal("1900", "1895", "1910", side="long"))
    assert outcome.placed is False


@pytest.mark.asyncio
async def test_engine_allows_entry_on_non_cpi_day():
    # WHY: the router must not leak into normal days — same signal, empty CPI set,
    # the entry proceeds to placement.
    broker = PaperBroker(starting_balance=Decimal("50000"))
    state = RiskState(config=fifty_k_combine())
    engine = ExecutionEngine(
        broker, state, [make_runner()], replay_mode=True,
        cpi_event_dates=frozenset(),  # router off => never blocks
    )
    await broker.connect()
    await engine.start()

    outcome = await engine._act_on_signal(_signal("1900", "1895", "1910", side="long"))
    assert outcome.placed is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py -k cpi_day -v`
Expected: FAIL — `ExecutionEngine.__init__` got an unexpected keyword argument `cpi_event_dates`

- [ ] **Step 3: Wire the engine**

In `app/execution/engine.py`:

(a) Add the import near the other strategy imports at the top of the file:

```python
from app.strategy.cpi_day import is_cpi_day
```

(b) Add a constructor parameter. In `__init__`, after `phase: "PhaseTracker | None" = None,` add:

```python
        cpi_event_dates: "frozenset[date] | None" = None,
```

(c) Store it. After the `self.phase = phase` assignment add:

```python
        # CPI-day router: ET dates on which the base engine takes no new entries.
        # Empty/None => router off => never blocks (cpi_day_router_enabled gates
        # whether main.py passes a populated set). Read-only after construction.
        self._cpi_dates = cpi_event_dates or frozenset()
```

(d) Drive the gate. In `_act_on_signal`, change the `check(...)` call (currently `decision = check(order, self.risk_state, phase=self.phase, ts=signal.created_at)`) to:

```python
        cpi_active = is_cpi_day(signal.created_at, self._cpi_dates)
        decision = check(
            order, self.risk_state, phase=self.phase,
            ts=signal.created_at, cpi_day_active=cpi_active,
        )
```

`date` is already referenced in this module (`self._phase_day: "date | None"`); if it is not imported, add `from datetime import date` to the datetime import line.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_engine.py -k cpi_day -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add app/execution/engine.py tests/test_engine.py
git commit -m "feat(cpi-router): engine computes cpi_day_active, drives CPI_DAY_BLOCK gate"
```

---

## Task 5: main.py wiring (load dates, construct scheduler, feed engine)

**Files:**
- Modify: `app/main.py` (engine construction ~line 1229; scheduler block ~line 1255)

- [ ] **Step 1: Load CPI dates before the engine is constructed**

In `app/main.py`, immediately BEFORE `engine = ExecutionEngine(` (line ~1229) insert:

```python
    # CPI-day router (B92 straddle ownership on CPI days). Default-off; only a
    # populated date set when cpi_day_router_enabled. Empty set => never blocks.
    from app.strategy.cpi_day import load_cpi_dates
    _router_on = bot_cfg.strategy.cpi_day_router_enabled
    cpi_dates = (
        load_cpi_dates(bot_cfg.strategy.news_straddle_events_path,
                       bot_cfg.strategy.news_straddle_event_type)
        if _router_on else frozenset()
    )
    if _router_on:
        log.warning(
            "CPI-day router ENABLED: %d CPI day(s) loaded — base engine suppressed "
            "on those days, CPI straddle owns the session (engine stays '%s').",
            len(cpi_dates), bot_cfg.strategy.engine,
        )
```

- [ ] **Step 2: Pass the dates into the engine**

In the `ExecutionEngine(` call, after the `phase=(...)` argument, add:

```python
        cpi_event_dates=cpi_dates,
```

- [ ] **Step 3: Construct the scheduler when the router is on**

Change the scheduler guard. Replace:

```python
    if _ns.engine == "news_straddle" and _ns.news_straddle_live_enabled:
```

with:

```python
    if (_ns.engine == "news_straddle" and _ns.news_straddle_live_enabled) or _ns.cpi_day_router_enabled:
```

(`_ns = bot_cfg.strategy` is already assigned just above this guard. The scheduler self-arms only at CPI event times, so on non-CPI days it is inert; on CPI days it places the OCO while the base engine is gated off.)

- [ ] **Step 4: Verify the app imports and constructs cleanly (router off — no regression)**

Run: `.venv/Scripts/python.exe -c "import app.main"`
Expected: no error (import-time only; full wiring is covered by the suite in Task 7).

- [ ] **Step 5: Commit**

```bash
git add app/main.py
git commit -m "feat(cpi-router): main.py loads CPI dates, feeds engine, constructs scheduler when enabled"
```

---

## Task 6: Rule-13 observability (SSE field + panel)

**Files:**
- Modify: `app/api/journal.py` (`publish_strategy_state` ~line 297)
- Modify: `app/main.py` (publisher call ~line 605, and pass `cpi_dates` into `_make_strategy_state_publisher`)
- Modify: `frontend/src/types.ts` (~line 245, beside `news_straddle`)
- Modify: `frontend/src/components/StrategyDebug.tsx` (~line 97, beside the News Straddle block)

- [ ] **Step 1: Add the journal param**

In `app/api/journal.py`, add a parameter to `publish_strategy_state` (after `news_straddle: "dict | None" = None,`):

```python
        cpi_day_router: "dict | None" = None,
```

and after the `if news_straddle is not None: payload["news_straddle"] = news_straddle` lines, add:

```python
        if cpi_day_router is not None:
            payload["cpi_day_router"] = cpi_day_router
```

- [ ] **Step 2: Compute and publish router state per bar**

In `app/main.py`, update `_make_strategy_state_publisher` to accept the date set. Change its signature (currently `def _make_strategy_state_publisher(journal, engine, execution_instrument="", broker=None, news_straddle_scheduler=None):`) to add `cpi_dates=None`. Inside its `on_bar`, just before the `journal.publish_strategy_state(` call, add:

```python
        from app.strategy.cpi_day import router_state as _cpi_router_state
        cpi_router = _cpi_router_state(bar.ts, cpi_dates) if cpi_dates else None
```

and add to the `publish_strategy_state(...)` kwargs:

```python
            cpi_day_router=cpi_router,
```

Then at the call site (line ~1281) pass the dates through:

```python
    broker.on_bar(_make_strategy_state_publisher(journal, engine, execution_instrument=exec_instr, broker=broker, news_straddle_scheduler=news_straddle_scheduler, cpi_dates=cpi_dates))
```

- [ ] **Step 3: Verify backend still imports + suite still green for the touched modules**

Run: `.venv/Scripts/python.exe -m pytest tests/test_cpi_day.py tests/test_risk.py -q`
Expected: PASS

- [ ] **Step 4: Add the frontend type**

In `frontend/src/types.ts`, directly after the `news_straddle?: {...} | null` field, add:

```typescript
  cpi_day_router?: {
    today_is_cpi_day: boolean
    next_cpi_date: string | null
    base_entries_suppressed: boolean
  } | null
```

- [ ] **Step 5: Render the panel section**

In `frontend/src/components/StrategyDebug.tsx`, directly after the closing `)}` of the News Straddle block (the one ending at line ~122), add:

```tsx
          {state.cpi_day_router != null && (
            <div>
              <div className="px-3 pt-2 pb-1 text-[9px] text-faint font-mono tracking-widest uppercase">
                CPI-Day Router
              </div>
              <LabeledValue
                label="today"
                value={state.cpi_day_router.today_is_cpi_day ? 'CPI DAY — base suppressed' : 'normal — base active'}
              />
              <LabeledValue
                label="next CPI"
                value={state.cpi_day_router.next_cpi_date ?? '—'}
              />
            </div>
          )}
```

- [ ] **Step 6: Build the frontend (must be clean — the bot serves the built static files)**

Run: `cd frontend; npm run build`
Expected: build succeeds, no TS errors.

- [ ] **Step 7: Commit**

```bash
git add app/api/journal.py app/main.py frontend/src/types.ts frontend/src/components/StrategyDebug.tsx
git commit -m "feat(cpi-router): Rule-13 observability — cpi_day_router SSE field + StrategyDebug panel"
```

---

## Task 7: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Run the full python suite**

Run: `.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (was 787 passed, 2 skipped before this work; expect +~16 new tests, still 0 failures).

- [ ] **Step 2: Confirm router-off is a true no-op**

Run: `.venv/Scripts/python.exe -c "from app.bot_config import StrategyParams; assert StrategyParams().cpi_day_router_enabled is False; print('default off OK')"`
Expected: `default off OK`

- [ ] **Step 3: Frontend build clean**

Run: `cd frontend; npm run build`
Expected: success, no TS errors.

- [ ] **Step 4: Final commit (if anything uncommitted)**

```bash
git add -A
git commit -m "test(cpi-router): full suite + frontend build green"
```

---

## Post-implementation (operational — NOT code, do not automate)

1. Append the next real CPI release date(s) to `data/news_events.csv` from the **official BLS calendar** (current file ends 2026-06-10). Format: `CPI,<ISO-8601 UTC>` e.g. `CPI,2026-07-15T12:30:00+00:00`. Do not fabricate the date.
2. Enable on the practice/shadow account: set `cpi_day_router_enabled: true` in `bot_config.json`, restart the bot (scheduler + engine date-set are wired at startup, not hot-applied via PATCH).
3. Observe one CPI cycle in the dashboard: StrategyDebug shows "CPI DAY — base suppressed", the News Straddle panel shows the OCO `armed`, and the log shows `CPI-day router ENABLED` + `news_straddle OCO armed`.
4. When satisfied, set `cpi_day_router_enabled: true` for the combine phase — same flag, same code path, no change.

## Known limitations (carried from spec — do not "fix" silently)

- The straddle's resting OCO bypasses the pretrade risk gate (daily-loss, max-contracts) — pre-existing B92 behavior. Acceptable at 1 contract; route through pretrade before sizing up.
- Client-side OCO: simultaneous dual-leg fills can briefly open a naked second position. Inherent; exchange-native OCO is the only full fix.
