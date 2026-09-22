# FOMC Gold Straddle — Multi-Event Straddle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a 20-contract MGC straddle on the practice account for the Wed 2026-06-17 14:00 ET FOMC decision, while the existing CPI→MNQ straddle stays unchanged — via a per-event straddle spec list.

**Architecture:** Add a `news_straddle_events` list of per-event specs (event_type, instrument, offset_ticks, tp_r, contracts, suppress_base) to `StrategyParams`. A pure resolver turns it (or the legacy single-event fields, for back-compat) into a list of `ResolvedStraddleSpec`. `main.py` builds one tested `NewsStraddleScheduler` per spec, suppresses the base engine only on dates whose spec sets `suppress_base=true`, and the SSE/frontend render a list of scheduler states.

**Tech Stack:** Python 3 / Pydantic / asyncio (backend), pytest, React+TypeScript (StrategyDebug panel).

**Spec:** `docs/superpowers/specs/2026-06-15-fomc-gold-straddle-multi-event-design.md`

---

## File structure

- `app/bot_config.py` — new `NewsStraddleEvent` model + `news_straddle_events` field on `StrategyParams`.
- `app/strategy/news_straddle.py` — `ResolvedStraddleSpec` dataclass + `resolve_straddle_specs()` (back-compat resolver) + `build_news_straddle_schedulers()` (constructs N schedulers).
- `app/strategy/cpi_day.py` — `suppress_dates()` (union of dates whose spec suppresses base) + `all_event_dates()` (union of all enabled event dates, for the dashboard).
- `app/main.py` — wire the list: build N schedulers, per-event suppression, list lifecycle (start/stop), list SSE.
- `frontend/src/types.ts` + `frontend/src/components/StrategyDebug.tsx` — render `news_straddle` as a list.
- `data/news_events.csv` — add the verified FOMC 06-17 row.
- `bot_config.json` — add `news_straddle_events` list + `MGC` to `instruments`.
- Tests: `tests/test_news_straddle_multi_event.py` (new).

---

### Task 1: Per-event config model

**Files:**
- Modify: `app/bot_config.py` (add model above `class StrategyParams`; add field near `news_straddle_*`, currently `bot_config.py:150-159`)
- Test: `tests/test_news_straddle_multi_event.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_news_straddle_multi_event.py
from decimal import Decimal
from app.bot_config import StrategyParams, NewsStraddleEvent


def test_news_straddle_events_parses_from_dicts():
    sp = StrategyParams(news_straddle_events=[
        {"event_type": "CPI", "instrument": "MNQ", "offset_ticks": 60, "tp_r": "3.0",
         "contracts": 1, "suppress_base": False},
        {"event_type": "FOMC", "instrument": "MGC", "offset_ticks": 20, "tp_r": "3.0",
         "contracts": 20, "suppress_base": True},
    ])
    assert len(sp.news_straddle_events) == 2
    fomc = sp.news_straddle_events[1]
    assert isinstance(fomc, NewsStraddleEvent)
    assert fomc.instrument == "MGC"
    assert fomc.offset_ticks == 20
    assert fomc.tp_r == Decimal("3.0")
    assert fomc.contracts == 20
    assert fomc.suppress_base is True


def test_news_straddle_events_defaults_empty():
    sp = StrategyParams()
    assert sp.news_straddle_events == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_news_straddle_multi_event.py -v`
Expected: FAIL with `ImportError: cannot import name 'NewsStraddleEvent'`

- [ ] **Step 3: Add the model and field**

In `app/bot_config.py`, add the model immediately before `class StrategyParams(BaseModel):`:

```python
class NewsStraddleEvent(BaseModel):
    """One scheduled news event the live straddle arms on. Lets CPI→MNQ and
    FOMC→MGC coexist in one process with per-event instrument, offset, and size."""
    event_type: str                       # matches news_events.csv event_type column
    instrument: str
    offset_ticks: int = 60                # stop-entry offset past the range; R = offset
    tp_r: Decimal = Decimal("3.0")
    contracts: int = 1
    suppress_base: bool = False           # block the base engine on this event's days
```

Then add the field inside `StrategyParams`, directly after the `news_straddle_arm_lead_seconds` line (currently `bot_config.py:159`):

```python
    # Per-event straddle specs. When non-empty, this is the source of truth and
    # the legacy single news_straddle_* fields above are ignored (resolve_straddle_specs).
    # Empty (default) = legacy single-event behavior, so existing CPI/MNQ live config is untouched.
    news_straddle_events: list[NewsStraddleEvent] = Field(default_factory=list)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_news_straddle_multi_event.py -v`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py tests/test_news_straddle_multi_event.py
git commit -m "feat: NewsStraddleEvent per-event straddle config model"
```

---

### Task 2: Spec resolver (back-compat)

**Files:**
- Modify: `app/strategy/news_straddle.py` (add after `load_event_times`, currently ends `news_straddle.py:77`)
- Test: `tests/test_news_straddle_multi_event.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_news_straddle_multi_event.py
from app.strategy.news_straddle import resolve_straddle_specs, ResolvedStraddleSpec


def test_resolve_uses_events_list_when_present():
    sp = StrategyParams(news_straddle_events=[
        {"event_type": "CPI", "instrument": "MNQ", "offset_ticks": 60, "contracts": 1},
        {"event_type": "FOMC", "instrument": "MGC", "offset_ticks": 20, "contracts": 20,
         "suppress_base": True},
    ])
    specs = resolve_straddle_specs(sp, default_instrument="MNQ")
    assert [s.instrument for s in specs] == ["MNQ", "MGC"]
    assert specs[1].event_type == "FOMC"
    assert specs[1].contracts == 20
    assert specs[1].suppress_base is True


def test_resolve_falls_back_to_legacy_single_event():
    sp = StrategyParams(news_straddle_event_type="CPI", news_straddle_offset_ticks=60,
                        news_straddle_contracts=1, cpi_base_suppress=False)
    specs = resolve_straddle_specs(sp, default_instrument="MNQ")
    assert len(specs) == 1
    assert specs[0].event_type == "CPI"
    assert specs[0].instrument == "MNQ"
    assert specs[0].offset_ticks == 60
    assert specs[0].suppress_base is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_news_straddle_multi_event.py -v`
Expected: FAIL with `ImportError: cannot import name 'resolve_straddle_specs'`

- [ ] **Step 3: Implement the resolver**

Append to `app/strategy/news_straddle.py` (it already imports `dataclass` and `datetime`; add `Decimal` import at top if absent: `from decimal import Decimal`):

```python
@dataclass(frozen=True)
class ResolvedStraddleSpec:
    event_type: str
    instrument: str
    offset_ticks: int
    tp_r: Decimal
    contracts: int
    suppress_base: bool


def resolve_straddle_specs(strategy_cfg, default_instrument: str) -> "list[ResolvedStraddleSpec]":
    """Per-event specs when news_straddle_events is populated; otherwise a single
    spec built from the legacy single-event fields (back-compat — keeps the existing
    CPI/MNQ live config working when the list is empty)."""
    events = getattr(strategy_cfg, "news_straddle_events", None) or []
    if events:
        return [
            ResolvedStraddleSpec(
                event_type=e.event_type, instrument=e.instrument,
                offset_ticks=e.offset_ticks, tp_r=e.tp_r,
                contracts=e.contracts, suppress_base=e.suppress_base,
            )
            for e in events
        ]
    return [ResolvedStraddleSpec(
        event_type=strategy_cfg.news_straddle_event_type,
        instrument=default_instrument,
        offset_ticks=strategy_cfg.news_straddle_offset_ticks,
        tp_r=strategy_cfg.news_straddle_tp_r,
        contracts=strategy_cfg.news_straddle_contracts,
        suppress_base=strategy_cfg.cpi_base_suppress,
    )]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_news_straddle_multi_event.py -v`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add app/strategy/news_straddle.py tests/test_news_straddle_multi_event.py
git commit -m "feat: resolve_straddle_specs with legacy single-event fallback"
```

---

### Task 3: Per-event suppression + all-event dates

**Files:**
- Modify: `app/strategy/cpi_day.py` (add at end; file currently ends `cpi_day.py:63`)
- Test: `tests/test_news_straddle_multi_event.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_news_straddle_multi_event.py
from datetime import date
from app.strategy.cpi_day import suppress_dates, all_event_dates
from app.strategy.news_straddle import ResolvedStraddleSpec
from decimal import Decimal as D


def _specs():
    return [
        ResolvedStraddleSpec("CPI", "MNQ", 60, D("3.0"), 1, suppress_base=False),
        ResolvedStraddleSpec("FOMC", "MGC", 20, D("3.0"), 20, suppress_base=True),
    ]


def test_suppress_dates_only_includes_suppress_base_specs(tmp_path):
    csv = tmp_path / "news.csv"
    csv.write_text(
        "event_type,ts_utc\n"
        "CPI,2026-07-14T12:30:00+00:00\n"
        "FOMC,2026-06-17T18:00:00+00:00\n"
    )
    sd = suppress_dates(_specs(), str(csv))
    assert sd == frozenset({date(2026, 6, 17)})          # FOMC only (suppress_base=True)
    assert date(2026, 7, 14) not in sd                    # CPI stays additive


def test_all_event_dates_is_union(tmp_path):
    csv = tmp_path / "news.csv"
    csv.write_text(
        "event_type,ts_utc\n"
        "CPI,2026-07-14T12:30:00+00:00\n"
        "FOMC,2026-06-17T18:00:00+00:00\n"
    )
    ad = all_event_dates(_specs(), str(csv))
    assert ad == frozenset({date(2026, 7, 14), date(2026, 6, 17)})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_news_straddle_multi_event.py -k "suppress_dates or all_event_dates" -v`
Expected: FAIL with `ImportError: cannot import name 'suppress_dates'`

- [ ] **Step 3: Implement both functions**

Append to `app/strategy/cpi_day.py` (it already imports `_et_date`, `load_event_times`, `date`):

```python
def suppress_dates(specs, path: str) -> frozenset[date]:
    """ET dates on which the BASE engine is blocked: the union of event dates for
    every spec with suppress_base=True. Per-event, so CPI can stay additive while
    FOMC suppresses the base."""
    out: set[date] = set()
    for s in specs:
        if s.suppress_base:
            out |= {_et_date(t) for t in load_event_times(path, s.event_type)}
    return frozenset(out)


def all_event_dates(specs, path: str) -> frozenset[date]:
    """ET dates of every enabled straddle event (any spec), for the dashboard
    'today_is_event_day' / 'next_event_date' read."""
    out: set[date] = set()
    for s in specs:
        out |= {_et_date(t) for t in load_event_times(path, s.event_type)}
    return frozenset(out)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_news_straddle_multi_event.py -v`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add app/strategy/cpi_day.py tests/test_news_straddle_multi_event.py
git commit -m "feat: per-event suppress_dates + all_event_dates for multi-event router"
```

---

### Task 4: Scheduler builder

**Files:**
- Modify: `app/strategy/news_straddle.py` (append after `resolve_straddle_specs`)
- Test: `tests/test_news_straddle_multi_event.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_news_straddle_multi_event.py
from app.strategy.news_straddle import build_news_straddle_schedulers


def test_build_schedulers_one_per_spec_with_right_params(tmp_path):
    csv = tmp_path / "news.csv"
    csv.write_text(
        "event_type,ts_utc\n"
        "CPI,2026-07-14T12:30:00+00:00\n"
        "FOMC,2026-06-17T18:00:00+00:00\n"
    )
    specs = [
        ResolvedStraddleSpec("CPI", "MNQ", 60, D("3.0"), 1, suppress_base=False),
        ResolvedStraddleSpec("FOMC", "MGC", 20, D("3.0"), 20, suppress_base=True),
    ]
    scheds = build_news_straddle_schedulers(
        broker=object(), specs=specs, events_path=str(csv), arm_lead_seconds=120,
    )
    assert [s.instrument for s in scheds] == ["MNQ", "MGC"]
    assert scheds[1].size == 20
    # offset = offset_ticks * tick; MGC tick = 0.10 → 20 * 0.10 = 2.0
    assert scheds[1].offset == D("2.0")
    # MNQ tick = 0.25 → 60 * 0.25 = 15.0
    assert scheds[0].offset == D("15.0")
    # each scheduler only loaded its own event_type
    assert len(scheds[1]._events) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_news_straddle_multi_event.py -k build_schedulers -v`
Expected: FAIL with `ImportError: cannot import name 'build_news_straddle_schedulers'`

- [ ] **Step 3: Implement the builder**

Append to `app/strategy/news_straddle.py`:

```python
def build_news_straddle_schedulers(broker, specs, events_path: str, arm_lead_seconds: int):
    """One NewsStraddleScheduler per resolved spec — each single-instrument with its
    own offset/tp_r/size and only its own event_type's release times."""
    from app.sim.paper import TICK_SIZE
    from app.notifications.news_straddle_scheduler import NewsStraddleScheduler

    schedulers = []
    for s in specs:
        schedulers.append(NewsStraddleScheduler(
            broker,
            instrument=s.instrument,
            event_times=load_event_times(events_path, s.event_type),
            offset_ticks=s.offset_ticks,
            tp_r=s.tp_r,
            tick=TICK_SIZE.get(s.instrument, Decimal("0.25")),
            size=s.contracts,
            arm_lead_seconds=arm_lead_seconds,
        ))
    return schedulers
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./.venv/Scripts/python.exe -m pytest tests/test_news_straddle_multi_event.py -v`
Expected: PASS (7 passed)

- [ ] **Step 5: Commit**

```bash
git add app/strategy/news_straddle.py tests/test_news_straddle_multi_event.py
git commit -m "feat: build_news_straddle_schedulers (one scheduler per event spec)"
```

---

### Task 5: Wire main.py to the spec list

**Files:**
- Modify: `app/main.py` — router block (`main.py:1253-1267`), engine `cpi_event_dates` (`main.py:1287`), scheduler construction (`main.py:1296-1322`), lifecycle (`main.py:1431-1432`, `main.py:1513-1516`), publisher (`main.py:558`, `main.py:638`).

> No new unit test — this is integration of already-tested pure functions (Tasks 1–4). Verified by the regression + pre-flight in Task 8. Make these edits exactly.

- [ ] **Step 1: Replace the router date-set block**

Replace `main.py:1253-1267` (the `from app.strategy.cpi_day import load_cpi_dates, engine_cpi_dates` block through the `log.warning(... )` end) with:

```python
    # Multi-event straddle router. Default-off; populated only when cpi_day_router_enabled.
    from app.strategy.cpi_day import suppress_dates, all_event_dates
    from app.strategy.news_straddle import resolve_straddle_specs
    _router_on = bot_cfg.strategy.cpi_day_router_enabled
    _straddle_specs = (
        resolve_straddle_specs(bot_cfg.strategy, cfg.instrument) if _router_on else []
    )
    _events_path = bot_cfg.strategy.news_straddle_events_path
    base_suppress_dates = suppress_dates(_straddle_specs, _events_path) if _router_on else frozenset()
    cpi_dates = all_event_dates(_straddle_specs, _events_path) if _router_on else frozenset()
    if _router_on:
        log.warning(
            "Straddle router ENABLED: %d event spec(s) %s; base suppressed on %d day(s).",
            len(_straddle_specs),
            [(s.event_type, s.instrument, s.contracts) for s in _straddle_specs],
            len(base_suppress_dates),
        )
```

- [ ] **Step 2: Update the engine's suppression date set**

At `main.py:1287`, change:

```python
        cpi_event_dates=engine_cpi_dates(bot_cfg.strategy.cpi_base_suppress, cpi_dates),
```
to:
```python
        cpi_event_dates=base_suppress_dates,
```

- [ ] **Step 3: Replace single-scheduler construction with the list**

Replace `main.py:1296-1319` (from `news_straddle_scheduler = None` through the end of its `log.warning(...)`) with:

```python
    # news_straddle LIVE resting-OCO schedulers (B92), one per event spec.
    news_straddle_schedulers = []
    _ns = bot_cfg.strategy
    if (_ns.engine == "news_straddle" and _ns.news_straddle_live_enabled) or _ns.cpi_day_router_enabled:
        from app.strategy.news_straddle import build_news_straddle_schedulers
        news_straddle_schedulers = build_news_straddle_schedulers(
            broker, _straddle_specs if _straddle_specs
            else resolve_straddle_specs(_ns, cfg.instrument),
            _ns.news_straddle_events_path, _ns.news_straddle_arm_lead_seconds,
        )
        for _sch in news_straddle_schedulers:
            broker.on_bar(_sch.on_bar)
            log.warning(
                "news_straddle LIVE: %d events, %d contract(s), arm %ds on %s "
                "(offset %s) — resting OCO straddle.",
                len(_sch._events), _sch.size, _sch.arm_lead_seconds, _sch.instrument, _sch.offset,
            )
```

- [ ] **Step 4: Update the publisher call site**

At `main.py:1322`, change `news_straddle_scheduler=news_straddle_scheduler` to `news_straddle_schedulers=news_straddle_schedulers` and `base_suppress=bot_cfg.strategy.cpi_base_suppress` stays. Full replacement of that line:

```python
    broker.on_bar(_make_strategy_state_publisher(journal, engine, execution_instrument=exec_instr, broker=broker, news_straddle_schedulers=news_straddle_schedulers, cpi_dates=cpi_dates, base_suppress=bot_cfg.strategy.cpi_base_suppress))
```

- [ ] **Step 5: Update the publisher signature + emit a list**

At `main.py:558`, change the param `news_straddle_scheduler: Any = None` to `news_straddle_schedulers: Any = None`. At `main.py:638`, change:

```python
            news_straddle=news_straddle_scheduler.state() if news_straddle_scheduler is not None else None,
```
to:
```python
            news_straddle=[s.state() for s in news_straddle_schedulers] if news_straddle_schedulers else None,
```

- [ ] **Step 6: Update lifecycle start/stop to loop**

At `main.py:1431-1432`, change:
```python
        if news_straddle_scheduler is not None:
            await news_straddle_scheduler.start()
```
to:
```python
        for _sch in news_straddle_schedulers:
            await _sch.start()
```

At `main.py:1513-1516`, change the `if news_straddle_scheduler is not None:` / `await news_straddle_scheduler.stop()` block to:
```python
        for _sch in news_straddle_schedulers:
            await _sch.stop()
```
(keep whatever surrounding try/except wrapping currently exists; only swap the single-scheduler guard for the loop).

- [ ] **Step 7: Verify the module imports and a config with 2 specs builds 2 schedulers**

Run:
```bash
./.venv/Scripts/python.exe -c "
from app.bot_config import StrategyParams
from app.strategy.news_straddle import resolve_straddle_specs, build_news_straddle_schedulers
sp = StrategyParams(cpi_day_router_enabled=True, news_straddle_events=[
  {'event_type':'CPI','instrument':'MNQ','offset_ticks':60,'contracts':1},
  {'event_type':'FOMC','instrument':'MGC','offset_ticks':20,'contracts':20,'suppress_base':True}])
specs = resolve_straddle_specs(sp, 'MNQ')
sch = build_news_straddle_schedulers(object(), specs, 'data/news_events.csv', 120)
print('schedulers:', [(s.instrument, s.size, str(s.offset)) for s in sch])
"
```
Expected: `schedulers: [('MNQ', 1, '15.0'), ('MGC', 20, '2.0')]`

- [ ] **Step 8: Commit**

```bash
git add app/main.py
git commit -m "feat: wire main.py to per-event straddle spec list (N schedulers + per-event suppression)"
```

---

### Task 6: Frontend — render the straddle list (Rule 13)

**Files:**
- Modify: `frontend/src/types.ts:245` (the `news_straddle?:` object → array)
- Modify: `frontend/src/components/StrategyDebug.tsx:97-110` (map over list)

- [ ] **Step 1: Read the current type + component blocks**

Run: `./.venv/Scripts/python.exe -c "print(open('frontend/src/types.ts').read()[:0])"` — instead, open `frontend/src/types.ts` around line 245 and `frontend/src/components/StrategyDebug.tsx` lines 95-115 and confirm the exact current shape before editing.

- [ ] **Step 2: Change the type to an array**

In `frontend/src/types.ts`, change the `news_straddle?: { ... }` object (starting line 245) to an array of the same shape:

```typescript
  news_straddle?: Array<{
    instrument: string;
    offset: string;
    tp_r: string;
    size: number;
    events: Array<{ ts: string; status: string; range_high: string | null; range_low: string | null }>;
  }>;
```

- [ ] **Step 3: Map over the list in StrategyDebug**

In `frontend/src/components/StrategyDebug.tsx`, replace the single `state.news_straddle != null && ( ... )` block (lines 97-110) with a `.map`, keying each card by `instrument`:

```tsx
          {state.news_straddle != null && state.news_straddle.map((ns) => (
            <div key={ns.instrument}>
              <SectionTitle>News Straddle ({ns.instrument})</SectionTitle>
              <LabeledValue label="setup" value={`${ns.offset}pt → ${ns.tp_r}R`} />
              <LabeledValue label="contracts" value={String(ns.size)} />
              {ns.events
                .filter((e) => e.status !== "pending")
                .slice(-3)
                .map((e) => (
                  <LabeledValue key={e.ts} label={e.ts.slice(5, 16)} value={e.status} />
                ))}
            </div>
          ))}
```

> Match the existing JSX element names exactly (`SectionTitle`, `LabeledValue`) — confirm them in Step 1; if the current code uses different names, reuse those. Keep the surrounding container/markup unchanged (no new outer border per the Tailwind grid contract).

- [ ] **Step 4: Build the frontend**

Run: `cd frontend && npm run build`
Expected: build succeeds, **0 TypeScript errors**. (The bot serves the built static bundle — per the frozen-bars lesson, the panel will not reflect changes until this runs.)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/StrategyDebug.tsx frontend/src/api/static 2>/dev/null; git add frontend/src
git commit -m "feat: StrategyDebug renders a list of news-straddle schedulers"
```

---

### Task 7: Calibration confirm + config/data

**Files:**
- Modify: `data/news_events.csv` (add FOMC 06-17 row)
- Modify: `bot_config.json` (`news_straddle_events` list + `MGC` in `instruments`)

- [ ] **Step 1: Re-verify the FOMC date/time against the Fed**

Confirm Wed **2026-06-17, 14:00 ET** decision against federalreserve.gov/monetarypolicy/fomccalendars.htm. 14:00 EDT = 18:00 UTC.

- [ ] **Step 2: Re-confirm the gold offset is current**

Run the calibration and confirm 0.5×ATR for the recent regime is ≈ 20 MGC ticks:
```bash
./.venv/Scripts/python.exe scripts/calibrate_gold_straddle_offset.py
```
If `scripts/calibrate_gold_straddle_offset.py` does not exist, reuse the one-off from the design session (resample `bars/bars_GC_1s_fomc_windows.csv` to 5-min over [release−20m, release−2m], 0.5×median(true-range)/0.10). Expected: recent-regime ≈ 19–21 ticks → keep `offset_ticks=20`. If the recent ATR has moved materially, update the FOMC spec offset in Step 4 and note it.

- [ ] **Step 3: Add the FOMC event row**

Add this line to `data/news_events.csv` (use the Edit tool, NOT PowerShell — Unicode/encoding trap):
```
FOMC,2026-06-17T18:00:00+00:00
```

- [ ] **Step 4: Set the per-event specs and add MGC in bot_config.json**

Edit `bot_config.json`: add `"MGC"` to `instruments` → `["MNQ", "MGC"]`, and add inside `"strategy"` (keep `cpi_day_router_enabled: true`):
```json
      "news_straddle_events": [
        { "event_type": "CPI",  "instrument": "MNQ", "offset_ticks": 60, "tp_r": "3.0", "contracts": 1,  "suppress_base": false },
        { "event_type": "FOMC", "instrument": "MGC", "offset_ticks": 20, "tp_r": "3.0", "contracts": 20, "suppress_base": true }
      ]
```

- [ ] **Step 5: Verify config loads and resolves to 2 specs**

Run:
```bash
./.venv/Scripts/python.exe -c "
from pathlib import Path
from app.bot_config import load_bot_config
from app.strategy.news_straddle import resolve_straddle_specs
from app.strategy.cpi_day import suppress_dates
cfg = load_bot_config(Path('bot_config.json'))
specs = resolve_straddle_specs(cfg.strategy, cfg.instrument)
print('instruments:', cfg.instruments)
print('specs:', [(s.event_type, s.instrument, s.contracts, s.suppress_base) for s in specs])
print('base suppressed on:', sorted(suppress_dates(specs, cfg.strategy.news_straddle_events_path)))
"
```
Expected: `instruments: ['MNQ', 'MGC']`; specs lists CPI/MNQ/1/False and FOMC/MGC/20/True; base suppressed includes `2026-06-17` and NOT `2026-07-14`.

- [ ] **Step 6: Commit**

```bash
git add data/news_events.csv bot_config.json scripts/calibrate_gold_straddle_offset.py 2>/dev/null; git add data/news_events.csv bot_config.json
git commit -m "feat: FOMC 06-17 event + MGC 20-lot straddle spec; add MGC subscription"
```

---

### Task 8: Full regression + pre-flight

**Files:** none (verification only)

- [ ] **Step 1: Run the full suite**

Run: `./.venv/Scripts/python.exe -m pytest -q`
Expected: all pass (baseline was 804). Investigate any failure before proceeding — especially anything touching `news_straddle`, `cpi_day`, or `main` wiring.

- [ ] **Step 2: Confirm legacy back-compat is intact**

Run:
```bash
./.venv/Scripts/python.exe -c "
from app.bot_config import StrategyParams
from app.strategy.news_straddle import resolve_straddle_specs
sp = StrategyParams(cpi_day_router_enabled=True)  # empty events list
specs = resolve_straddle_specs(sp, 'MNQ')
print('legacy fallback:', len(specs), specs[0].event_type, specs[0].instrument, specs[0].offset_ticks)
"
```
Expected: `legacy fallback: 1 CPI MNQ 60` (proves an empty list reproduces today's single CPI/MNQ behavior).

- [ ] **Step 3: Restart the bot (market closed / flat only) and read the startup log**

Restart via the run-bot skill. In the startup log, confirm:
- `Straddle router ENABLED: 2 event spec(s) [('CPI','MNQ',1),('FOMC','MGC',20)]; base suppressed on 1 day(s).`
- two `news_straddle LIVE:` lines — one MNQ (offset 15.0), one MGC (offset 2.0, 20 contracts).
- MGC subscribed (bar feed) alongside MNQ.

- [ ] **Step 4: Pre-flight on the 17th (before 13:58 ET)**

On Wed before 13:58 ET, confirm in the dashboard StrategyDebug panel that the FOMC/MGC straddle shows status `pending` for the 18:00 UTC event and that the MNQ base shows blocked for the ET day. At ~13:58 ET the MGC scheduler should arm (log line + dashboard status `armed` with a locked range). Watch the 14:00 ET print live.

- [ ] **Step 5: Final commit (if any verification fixups were needed)**

```bash
git add -A
git commit -m "test: multi-event straddle regression + pre-flight verified"
```

---

## Self-review notes

- **Spec coverage:** config schema (T1), resolver+back-compat (T2), per-event suppression (T3), per-instrument scheduler (T4), main wiring incl. subscription/lifecycle/SSE (T5), Rule-13 frontend (T6), offset calibration + data + 20-lot FOMC spec (T7), regression+pre-flight (T8). All spec sections mapped.
- **Type consistency:** `ResolvedStraddleSpec(event_type, instrument, offset_ticks, tp_r, contracts, suppress_base)` is used identically in T2/T3/T4/T5. `suppress_dates`/`all_event_dates` signatures match call sites in T5. `news_straddle_schedulers` (plural) replaces every `news_straddle_scheduler` reference in T5 (signature, call site, emit, start, stop).
- **Back-compat:** empty `news_straddle_events` → legacy single CPI/MNQ path (T2 test + T8 step 2), so the change is fail-safe.
