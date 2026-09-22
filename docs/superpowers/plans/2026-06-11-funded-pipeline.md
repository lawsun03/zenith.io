# Funded-Pipeline Mode Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the bot account-phase-aware (Combine → XFA funded pipeline): ship the 4:10 PM ET flatten rule (live violation today), add a phase tracker + deterministic risk governor behind a `practice` default, and a rules-accurate funded simulator wired into backtest reports.

**Architecture:** One source of truth for Topstep rule math: `app/risk/account_phase.py` holds `CombineRules`/`XfaRules` (parsed from `bot_config.json` `phase_rules`) and a `PhaseTracker` state machine. The live governor (`app/risk/pretrade.py`) and the backtest simulator (`app/backtest/funded_sim.py`) both consume `PhaseTracker` — no duplicated rule math. The flatten rule lives in the engine (bar-driven, works in replay and live) plus a wall-clock task for live. All rule numbers live in config, never in logic (Topstep changed rules 8× Nov 2025–Apr 2026).

**Tech Stack:** Python 3 / pydantic (BotConfig), pytest, existing ExecutionEngine/RiskState plumbing.

**Spec:** `C:\Users\Lawrence\Downloads\HANDOFF_funded_pipeline.md` (copy into `docs/superpowers/specs/2026-06-11-funded-pipeline-spec.md` in Task 0).

**Sequencing rationale:** Phase A (flatten) ships first — the live bot held 66 trades through the 4:10 PM ET close in the test data; that's a rule violation today. Phase B (tracker + governor) is zero-behavior-change until `account_phase` is flipped from `"practice"`. Phase C (simulator + report) replaces the misleading "Combine target: PASSED" line.

**Existing anchors (verified 2026-06-11):**
- `app/execution/engine.py:502` `ExecutionEngine.start()` — registers handlers; add wall-clock flatten task here.
- `app/execution/engine.py:540` `_handle_bar` — bar entry point; add bar-driven flatten + entry-cutoff.
- `app/execution/engine.py:861` `decision = check(order, self.risk_state)` — pretrade call site.
- `app/risk/pretrade.py:56` `check(order, state)` — pure gate function; gains optional `phase`/`ts` params.
- `app/risk/state.py:296` `in_trading_window` + `CT` tz helper — exists, currently unused by live path.
- `app/backtest/runner.py` `_trading_day_ct(ts)` — 5pm CT trading-day helper (added 2026-06-11).
- `app/backtest/report.py:18` — the "Combine target: PASSED" line to replace.
- `app/sim/pricing.py:38` `_point_value(instrument)` — $/point table.
- Equity CSV format: header `ts,equity`, rows `2025-01-02T14:54:00+00:00,49999.26`.
- `fifty_k_combine()` in `app/risk/config.py:83` — existing account config (DLL $1k is the bot's own soft rule; Topstep removed DLL Aug 2024 — keep ours).

---

## Phase A — Flatten rule (ship first)

### Task 1: Flatten config fields on BotConfig

**Files:**
- Modify: `app/bot_config.py` (BotConfig class, after `max_entry_slippage_frac` ~line 109; and the `save_bot_config` dict ~line 174)
- Test: `tests/test_flatten_window.py` (create)

- [ ] **Step 1: Write the failing test**

```python
"""Flatten-rule config + window math.

Why: Topstep requires flat by 3:10 PM CT (4:10 PM ET); the bot held 66
positions through that window in the 2025-26 test data. Times are config,
not code (Topstep changed rules 8x in 6 months), and DST-correct via
America/Chicago — never fixed UTC offsets.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.bot_config import BotConfig


def test_flatten_config_defaults():
    cfg = BotConfig()
    assert cfg.flatten_enabled is True
    assert cfg.flatten_time_ct == "15:05"      # 4:05 PM ET, 5 min buffer
    assert cfg.entry_cutoff_time_ct == "14:30" # 3:30 PM ET
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_flatten_window.py -v`
Expected: FAIL — `AttributeError: 'BotConfig' object has no attribute 'flatten_enabled'`

- [ ] **Step 3: Add the fields**

In `app/bot_config.py`, inside `BotConfig` right after `max_entry_slippage_frac`:

```python
    # Topstep flatten rule: must be flat by 3:10 PM CT (4:10 PM ET).
    # flatten_time_ct = hard-flatten time with buffer; entry_cutoff_time_ct =
    # no new entries after this. Strings "HH:MM" in America/Chicago local time.
    flatten_enabled: bool = True
    flatten_time_ct: str = "15:05"
    entry_cutoff_time_ct: str = "14:30"
```

In `save_bot_config`'s dict (next to `"max_entry_slippage_frac"`):

```python
        "flatten_enabled": config.flatten_enabled,
        "flatten_time_ct": config.flatten_time_ct,
        "entry_cutoff_time_ct": config.entry_cutoff_time_ct,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_flatten_window.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py tests/test_flatten_window.py
git commit -m "feat: flatten-rule config fields (3:10 PM CT Topstep close)"
```

### Task 2: Window helpers — `in_flatten_window` / `past_entry_cutoff`

**Files:**
- Create: `app/risk/flatten.py`
- Test: `tests/test_flatten_window.py` (extend)

- [ ] **Step 1: Write the failing tests** (append to `tests/test_flatten_window.py`)

```python
from app.risk.flatten import in_flatten_window, past_entry_cutoff


def _utc(y, mo, d, h, mi):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


class TestFlattenWindow:
    # 2026-01-15 is CST (UTC-6): 15:05 CT == 21:05 UTC
    def test_before_flatten_time_not_in_window(self):
        assert not in_flatten_window(_utc(2026, 1, 15, 21, 4), "15:05")

    def test_at_flatten_time_in_window(self):
        assert in_flatten_window(_utc(2026, 1, 15, 21, 5), "15:05")

    def test_after_session_close_not_in_window(self):
        # 17:00 CT = next trading day; flatten window ended
        assert not in_flatten_window(_utc(2026, 1, 15, 23, 0), "15:05")

    def test_dst_boundary_uses_cdt(self):
        # 2026-07-15 is CDT (UTC-5): 15:05 CT == 20:05 UTC
        assert in_flatten_window(_utc(2026, 7, 15, 20, 5), "15:05")
        assert not in_flatten_window(_utc(2026, 7, 15, 20, 4), "15:05")


class TestEntryCutoff:
    def test_before_cutoff_allowed(self):
        assert not past_entry_cutoff(_utc(2026, 1, 15, 20, 29), "14:30")

    def test_after_cutoff_blocked(self):
        assert past_entry_cutoff(_utc(2026, 1, 15, 20, 30), "14:30")

    def test_evening_session_after_5pm_ct_allowed(self):
        # 18:00 CT = new trading day, overnight trading is allowed
        assert not past_entry_cutoff(_utc(2026, 1, 16, 0, 0), "14:30")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_flatten_window.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.risk.flatten'`

- [ ] **Step 3: Implement `app/risk/flatten.py`**

```python
"""
Topstep flatten-window math. Pure functions, DST-correct via America/Chicago.

The trading day runs 5:00 PM CT -> 3:10 PM CT next day. The flatten window
is [flatten_time_ct, 17:00 CT): inside it all positions must be closed and
no entries may open. The entry cutoff starts earlier so trades whose
expected hold spans the close are never opened.
"""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
_SESSION_OPEN = time(17, 0)  # 5:00 PM CT — new trading day


def _parse_hhmm(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def in_flatten_window(ts: datetime, flatten_time_ct: str) -> bool:
    """True when positions must be flat: [flatten_time, 5:00 PM CT)."""
    t = ts.astimezone(CT).time()
    return _parse_hhmm(flatten_time_ct) <= t < _SESSION_OPEN


def past_entry_cutoff(ts: datetime, entry_cutoff_time_ct: str) -> bool:
    """True when new entries are blocked: [entry_cutoff, 5:00 PM CT)."""
    t = ts.astimezone(CT).time()
    return _parse_hhmm(entry_cutoff_time_ct) <= t < _SESSION_OPEN
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_flatten_window.py -v`
Expected: PASS (all)

- [ ] **Step 5: Commit**

```bash
git add app/risk/flatten.py tests/test_flatten_window.py
git commit -m "feat: DST-correct flatten/entry-cutoff window helpers"
```

### Task 3: Engine wiring — bar-driven flatten + entry cutoff + live timer

**Files:**
- Modify: `app/execution/engine.py` (`__init__` ~line 408–430, `start()` :502, `stop()` :534, `_handle_bar` :540, entry gate before `check()` call at :861)
- Modify: `app/api/server.py` `_hot_apply` (~line 532 block) — hot-apply the three config fields
- Modify: `app/main.py` — pass the three fields to ExecutionEngine where it's constructed (search `ExecutionEngine(`)
- Test: `tests/test_flatten_window.py` (extend)

- [ ] **Step 1: Write the failing test** (append)

```python
import asyncio
from decimal import Decimal

from app.sim.paper import PaperBroker
from app.sim.events import Bar


def _bar(ts, price=100.0):
    return Bar(
        instrument="MGC", timeframe="1min", ts=ts,
        open=Decimal(str(price)), high=Decimal(str(price + 1)),
        low=Decimal(str(price - 1)), close=Decimal(str(price)), volume=10,
    )


def test_engine_flattens_open_position_in_window():
    """A position open at 15:05 CT must be flattened by the next bar.

    Why: 66 trades in the 2025-26 data were held through the 4:10 PM ET
    close — a Topstep rule violation that fails real accounts.
    """
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState

    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    engine = ExecutionEngine(
        broker=broker, risk_state=RiskState(config=fifty_k_combine()),
        runners=[], replay_mode=True,
        flatten_enabled=True, flatten_time_ct="15:05", entry_cutoff_time_ct="14:30",
    )

    async def go():
        await broker.connect()
        await engine.start()
        # Open a position at 20:00 UTC (14:00 CT, before cutoff)
        await broker.inject_bar(_bar(_utc(2026, 1, 15, 20, 0)))
        await broker.place_bracket("MGC", "long", 1,
                                   Decimal("100"), Decimal("95"), Decimal("110"))
        assert len(broker.open_brackets()) == 1
        # Bar lands inside the flatten window: 21:06 UTC == 15:06 CT
        await broker.inject_bar(_bar(_utc(2026, 1, 15, 21, 6)))
        return broker.open_brackets()

    remaining = asyncio.run(go())
    assert remaining == [], "engine must flatten open positions in the window"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_flatten_window.py::test_engine_flattens_open_position_in_window -v`
Expected: FAIL — `TypeError: ExecutionEngine.__init__() got an unexpected keyword argument 'flatten_enabled'`

- [ ] **Step 3: Implement engine changes**

In `ExecutionEngine.__init__` (add params after `max_contracts_override`, store like the others):

```python
        flatten_enabled: bool = True,
        flatten_time_ct: str = "15:05",
        entry_cutoff_time_ct: str = "14:30",
```
```python
        self.flatten_enabled = flatten_enabled            # hot-applied via PATCH /api/config
        self.flatten_time_ct = flatten_time_ct
        self.entry_cutoff_time_ct = entry_cutoff_time_ct
        self._flatten_task: asyncio.Task | None = None
        self._flattened_today: str | None = None  # trading-day key, avoid re-flatten spam
```

At the TOP of `_handle_bar` (line ~547, before stale-bar/VP logic), add:

```python
        await self._enforce_flatten(bar.ts)
```

New method on the engine (place near `_handle_bar`); import at top of file:
`from app.risk.flatten import in_flatten_window, past_entry_cutoff`:

```python
    async def _enforce_flatten(self, ts: datetime) -> None:
        """Topstep flatten rule: flat by 3:10 PM CT — we act at flatten_time_ct.

        Bar-driven so it works identically in replay and live; live also has
        a wall-clock task because 5min bars can arrive late.
        """
        if not self.flatten_enabled:
            return
        if not in_flatten_window(ts, self.flatten_time_ct):
            return
        day_key = ts.astimezone(timezone.utc).date().isoformat()
        if self._flattened_today == day_key:
            return
        if self.risk_state.open_contracts == 0:
            self._flattened_today = day_key
            return
        log.warning("FLATTEN WINDOW: closing all positions at %s (rule: flat by 3:10 PM CT)", ts)
        for inst in self._open_instruments():
            try:
                await self.broker.cancel_all(inst)
                await self.broker.flatten(inst)
            except Exception:
                log.exception("flatten-window close failed for %s", inst)
        self._flattened_today = day_key

    def _open_instruments(self) -> list[str]:
        """Instruments with open positions, per broker truth where available."""
        seen = getattr(self.broker, "open_brackets", None)
        if callable(seen):
            return sorted({b["instrument"] for b in self.broker.open_brackets()})
        return [r.instrument for r in self.runners]
```

NOTE: check the actual `Broker` protocol — if `cancel_all`/`flatten` have
different names on `app/sim/protocol.py`, use the protocol names. Verify
`open_brackets()` exists on PaperBroker (it does) and guard with `getattr`
for TopstepXBroker (use runner instruments as fallback, as shown).

Entry cutoff — in the signal path just BEFORE `decision = check(order, self.risk_state)` (line ~861):

```python
        if self.flatten_enabled and past_entry_cutoff(signal.created_at, self.entry_cutoff_time_ct):
            log.info("Entry blocked: past %s CT entry cutoff (flatten rule)", self.entry_cutoff_time_ct)
            return OrderOutcome(placed=False, reason="flatten_window")
```

NOTE: read the surrounding function to match the actual deny/return pattern
used for `Deny` (there is an existing deny path right after `check()` —
mirror its return shape and logging exactly).

Wall-clock task for live — in `start()` after handler registration:

```python
        if not self.replay_mode and self._flatten_task is None:
            self._flatten_task = asyncio.create_task(self._flatten_clock())
```

```python
    async def _flatten_clock(self) -> None:
        """Live backup for bar-driven flatten: check every 30s of wall time."""
        while True:
            await asyncio.sleep(30)
            try:
                await self._enforce_flatten(datetime.now(timezone.utc))
            except Exception:
                log.exception("flatten clock check failed")
```

In `stop()`: cancel `self._flatten_task` if set, then set it to `None`.

In `app/api/server.py` `_hot_apply` (inside the `if _engine is not None:` block):

```python
            _engine.flatten_enabled = body.flatten_enabled
            _engine.flatten_time_ct = body.flatten_time_ct
            _engine.entry_cutoff_time_ct = body.entry_cutoff_time_ct
```

In `app/main.py`, find every `ExecutionEngine(` construction and pass:

```python
        flatten_enabled=bot_cfg.flatten_enabled,
        flatten_time_ct=bot_cfg.flatten_time_ct,
        entry_cutoff_time_ct=bot_cfg.entry_cutoff_time_ct,
```

- [ ] **Step 4: Run the full flatten tests + suite**

Run: `.venv\Scripts\python.exe -m pytest tests/test_flatten_window.py -v` → PASS
Run: `.venv\Scripts\python.exe -m pytest tests/ -q` → only the 11 known pre-existing failures (see memory `project_known_failing_tests`)

- [ ] **Step 5: Frontend — config fields visible (Rule 10)**

Add to `frontend/src/types.ts` BotConfig type: `flatten_enabled: boolean`, `flatten_time_ct: string`, `entry_cutoff_time_ct: string`.
Add to `frontend/src/components/ConfigPanel.tsx`: a toggle + two text fields in the risk section (mirror how `max_entry_slippage_frac` is rendered), and include all three in the save payload.
Run: `cd frontend; npm run build` → clean.

- [ ] **Step 6: Commit**

```bash
git add app/execution/engine.py app/api/server.py app/main.py frontend/src tests/test_flatten_window.py
git commit -m "feat: enforce Topstep flatten rule - bar-driven + live wall-clock"
```

### Task 4: Backtest impact check (no code — verification)

- [ ] Run one wf2 config with the flatten rule active and record the delta:

```
.venv\Scripts\python.exe scripts\backtest.py --bars bars\bars_MNQ_test_2025_2026.csv --instrument MNQ --timeframe 5min --no-risk-limits --output-dir backtest_results\wf2_test_winner_flatten
```

Compare `summary.txt` to `backtest_results/wf2_test_winner/summary.txt`. The 66 held-through-close trades now flatten at 15:05 CT. Record the PF/net delta in the commit message of Task 10's analysis doc. If PF degrades badly (< 1.05), STOP and surface — the edge may depend on holds that are illegal, which changes the whole pipeline decision.

---

## Phase B — Phase tracker + risk governor (zero behavior change until opted in)

### Task 5: `PhaseRules` + `PhaseTracker` state machine

**Files:**
- Create: `app/risk/account_phase.py`
- Test: `tests/test_account_phase.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
"""PhaseTracker — Topstep Combine/XFA rule state machine.

Why each test exists:
- MLL ratchet semantics decide whether the account lives or dies; EOD vs
  intraday is a config switch because Topstep's docs are ambiguous (2026).
- XFA's $0 lock at +$2k is the most valuable milestone in the pipeline.
- Winning-day accounting gates payouts; off-by-$1 errors cost real money.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.risk.account_phase import CombineRules, PhaseTracker, XfaRules

D = Decimal
T0 = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)  # Monday 09:00 CT


def day(n: int) -> datetime:
    return T0 + timedelta(days=n)


def combine_tracker(trailing: str = "intraday") -> PhaseTracker:
    return PhaseTracker(phase="combine",
                        combine=CombineRules(mll_trailing=trailing),
                        xfa=XfaRules())


def xfa_tracker() -> PhaseTracker:
    return PhaseTracker(phase="xfa", combine=CombineRules(), xfa=XfaRules())


class TestCombineMLL:
    def test_initial_mll(self):
        t = combine_tracker()
        assert t.mll == D("48000")

    def test_intraday_trailing_ratchets_immediately(self):
        t = combine_tracker("intraday")
        t.on_pnl(D("500"), day(0))
        assert t.mll == D("48500")

    def test_eod_trailing_ratchets_only_on_roll(self):
        t = combine_tracker("eod")
        t.on_pnl(D("500"), day(0))
        assert t.mll == D("48000")          # not yet
        t.roll_day(day(1))
        assert t.mll == D("48500")          # ratcheted at EOD

    def test_mll_caps_at_starting_balance(self):
        t = combine_tracker("intraday")
        t.on_pnl(D("2500"), day(0))
        assert t.mll == D("50000")

    def test_dead_when_balance_touches_mll(self):
        t = combine_tracker("intraday")
        t.on_pnl(D("-2000"), day(0))
        assert t.is_dead()


class TestCombinePass:
    def test_target_with_consistency_ok(self):
        t = combine_tracker()
        t.on_pnl(D("1400"), day(0)); t.roll_day(day(1))
        t.on_pnl(D("1400"), day(1)); t.roll_day(day(2))
        t.on_pnl(D("400"), day(2))
        assert t.target_reached()           # 3200 total, best day 1400 < 50%

    def test_big_day_delays_not_fails(self):
        t = combine_tracker()
        t.on_pnl(D("2000"), day(0)); t.roll_day(day(1))
        t.on_pnl(D("1100"), day(1))
        assert not t.target_reached()       # 3100 total, best 2000 >= 50%
        t.roll_day(day(2))
        t.on_pnl(D("1000"), day(2))
        assert t.target_reached()           # 4100 total, best 2000 < 50%


class TestXFA:
    def test_winning_day_threshold_exact(self):
        t = xfa_tracker()
        t.on_pnl(D("149"), day(0)); t.roll_day(day(1))
        assert t.winning_days == 0          # +$149 is NOT a winning day
        t.on_pnl(D("150"), day(1)); t.roll_day(day(2))
        assert t.winning_days == 1          # +$150 IS

    def test_mll_locks_at_zero_once_2k_reached(self):
        t = xfa_tracker()
        assert t.mll == D("-2000")
        t.on_pnl(D("2000"), day(0)); t.roll_day(day(1))
        assert t.mll == D("0")
        t.on_pnl(D("3000"), day(1)); t.roll_day(day(2))
        assert t.mll == D("0")              # locked — never trails above 0

    def test_payout_resets_winning_days_and_halves_balance(self):
        t = xfa_tracker()
        for n in range(5):
            t.on_pnl(D("700"), day(n)); t.roll_day(day(n + 1))
        assert t.winning_days == 5 and t.balance == D("3500")
        amount = t.request_payout()
        assert amount == D("1750")          # 50% of balance, under cap
        assert t.balance == D("1750")
        assert t.winning_days == 0          # counter resets each cycle
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_account_phase.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement `app/risk/account_phase.py`**

```python
"""
Account-phase state machine: Topstep Combine / XFA rule math.

Single source of truth — the live governor (pretrade) and the backtest
funded simulator both import THIS module. Rule numbers come from config
(bot_config.json "phase_rules"), never hardcoded in logic: Topstep changed
rules 8 times between Nov 2025 and Apr 2026.

Granularity caveat: callers feed realized P&L deltas (fills). Open-trade
unrealized excursion is invisible, so MLL touches are UNDERSTATED — pair
sizing decisions with the pretrade cushion governor, which bounds the
worst-case open-trade loss.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import Literal

log = logging.getLogger(__name__)

PhaseName = Literal["practice", "combine", "xfa", "live"]


@dataclass(frozen=True)
class CombineRules:
    starting_balance: Decimal = Decimal("50000")
    profit_target: Decimal = Decimal("3000")
    mll_distance: Decimal = Decimal("2000")
    mll_trailing: str = "intraday"          # "eod" | "intraday" (intraday = conservative)
    best_day_cap_frac: Decimal = Decimal("0.45")
    stop_at_target: bool = True


@dataclass(frozen=True)
class XfaRules:
    starting_balance: Decimal = Decimal("0")
    mll_distance: Decimal = Decimal("2000")
    mll_lock_at: Decimal = Decimal("2000")  # balance level where MLL locks at $0
    winning_day_threshold: Decimal = Decimal("150")
    payout_path: str = "standard"           # "standard" | "consistency"
    payout_winning_days: int = 5
    payout_request_floor: Decimal = Decimal("3000")
    payout_cap: Decimal = Decimal("5000")
    payout_fraction: Decimal = Decimal("0.5")


@dataclass
class PhaseTracker:
    phase: PhaseName
    combine: CombineRules = field(default_factory=CombineRules)
    xfa: XfaRules = field(default_factory=XfaRules)

    balance: Decimal = field(init=False)
    high_water: Decimal = field(init=False)       # intraday equity high
    eod_high_water: Decimal = field(init=False)   # ratchets only at roll_day
    today_pnl: Decimal = Decimal("0")
    best_day: Decimal = Decimal("0")              # best COMPLETED day; today checked live
    winning_days: int = 0                         # current payout cycle (xfa)
    mll_locked_at_zero: bool = False              # xfa permanent lock
    post_payout_half_risk: bool = False

    def __post_init__(self) -> None:
        start = (self.combine.starting_balance if self.phase == "combine"
                 else self.xfa.starting_balance)
        self.balance = start
        self.high_water = start
        self.eod_high_water = start

    # -- properties -----------------------------------------------------
    @property
    def total_profit(self) -> Decimal:
        start = (self.combine.starting_balance if self.phase == "combine"
                 else self.xfa.starting_balance)
        return self.balance - start

    @property
    def best_day_live(self) -> Decimal:
        """Best day including today's running P&L (conservative)."""
        return max(self.best_day, self.today_pnl)

    @property
    def mll(self) -> Decimal | None:
        if self.phase == "combine":
            anchor = (self.high_water if self.combine.mll_trailing == "intraday"
                      else self.eod_high_water)
            return min(anchor - self.combine.mll_distance,
                       self.combine.starting_balance)
        if self.phase == "xfa":
            if self.mll_locked_at_zero:
                return Decimal("0")
            return self.eod_high_water - self.xfa.mll_distance  # XFA trails EOD
        return None  # practice / live: no Topstep MLL

    @property
    def cushion(self) -> Decimal | None:
        m = self.mll
        return None if m is None else self.balance - m

    # -- transitions -----------------------------------------------------
    def on_pnl(self, delta: Decimal, ts: datetime) -> None:
        """Realized P&L delta (a fill, commission-inclusive)."""
        self.balance += delta
        self.today_pnl += delta
        if self.balance > self.high_water:
            self.high_water = self.balance

    def roll_day(self, ts: datetime) -> None:
        """5:00 PM CT day roll: finalize today, ratchet EOD anchors."""
        if self.today_pnl > self.best_day:
            self.best_day = self.today_pnl
        if (self.phase == "xfa"
                and self.today_pnl >= self.xfa.winning_day_threshold):
            self.winning_days += 1
        if self.balance > self.eod_high_water:
            self.eod_high_water = self.balance
        if (self.phase == "xfa" and not self.mll_locked_at_zero
                and self.eod_high_water >= self.xfa.mll_lock_at):
            self.mll_locked_at_zero = True
            log.info("XFA MLL locked at $0 (balance reached %s)", self.eod_high_water)
        if (self.post_payout_half_risk
                and self.balance >= self.xfa.payout_request_floor):
            self.post_payout_half_risk = False
        self.today_pnl = Decimal("0")

    def is_dead(self) -> bool:
        m = self.mll
        return m is not None and self.balance <= m

    def target_reached(self) -> bool:
        """Combine pass: target hit AND best day < 50% of total profit."""
        if self.phase != "combine":
            return False
        profit = self.total_profit
        return (profit >= self.combine.profit_target
                and self.best_day_live < profit / 2)

    def payout_eligible(self) -> bool:
        return (self.phase == "xfa"
                and self.winning_days >= self.xfa.payout_winning_days
                and self.balance >= self.xfa.payout_request_floor)

    def request_payout(self) -> Decimal:
        """Withdraw payout_fraction of balance (capped); resets the cycle."""
        amount = min(self.balance * self.xfa.payout_fraction, self.xfa.payout_cap)
        self.balance -= amount
        self.winning_days = 0
        self.post_payout_half_risk = True
        log.info("XFA payout %s, balance now %s (half-risk until %s)",
                 amount, self.balance, self.xfa.payout_request_floor)
        return amount
```

- [ ] **Step 4: Run tests** → PASS. Also full suite → only known failures.

- [ ] **Step 5: Commit**

```bash
git add app/risk/account_phase.py tests/test_account_phase.py
git commit -m "feat: PhaseTracker - Combine/XFA rule state machine"
```

### Task 6: `phase_rules` config on BotConfig + parser

**Files:**
- Modify: `app/bot_config.py` (BotConfig + save_bot_config)
- Test: `tests/test_account_phase.py` (extend)

- [ ] **Step 1: Failing test** (append)

```python
def test_phase_rules_from_bot_config():
    """phase_rules round-trips through BotConfig with defaults."""
    from app.bot_config import BotConfig
    from app.risk.account_phase import tracker_from_config

    cfg = BotConfig()
    assert cfg.account_phase == "practice"   # zero behavior change by default
    t = tracker_from_config(cfg)
    assert t.phase == "practice"
    cfg2 = BotConfig(account_phase="combine",
                     phase_rules={"combine": {"best_day_cap_frac": "0.40"}})
    t2 = tracker_from_config(cfg2)
    assert t2.combine.best_day_cap_frac == Decimal("0.40")
    assert t2.combine.profit_target == Decimal("3000")  # defaults survive partial dict
```

- [ ] **Step 2: Run** → FAIL (`account_phase` not a field).

- [ ] **Step 3: Implement**

`app/bot_config.py`, in BotConfig (after the flatten fields from Task 1):

```python
    # Funded-pipeline phase: "practice" = current behavior (default).
    # Rule numbers live here, not in code — Topstep changes them often.
    account_phase: str = "practice"
    phase_rules: dict = Field(default_factory=dict)
```

In `save_bot_config`'s dict: `"account_phase": config.account_phase, "phase_rules": config.phase_rules,`

In `app/risk/account_phase.py`, append:

```python
def tracker_from_config(cfg) -> PhaseTracker:
    """Build a PhaseTracker from BotConfig.account_phase + .phase_rules.

    Partial dicts are fine — dataclass defaults fill the gaps. Decimal
    fields accept strings (config JSON stores numbers as strings).
    """
    raw_c = dict(cfg.phase_rules.get("combine", {}))
    raw_x = dict(cfg.phase_rules.get("xfa", {}))
    def _conv(rules_cls, raw):
        kwargs = {}
        for f in rules_cls.__dataclass_fields__.values():
            if f.name in raw:
                v = raw[f.name]
                kwargs[f.name] = Decimal(str(v)) if f.type == "Decimal" else (
                    int(v) if f.type == "int" else (bool(v) if f.type == "bool" else str(v)))
        return rules_cls(**kwargs)
    return PhaseTracker(phase=cfg.account_phase,
                        combine=_conv(CombineRules, raw_c),
                        xfa=_conv(XfaRules, raw_x))
```

NOTE: dataclass `f.type` is a string under `from __future__ import annotations` —
the comparisons above rely on that. If they misbehave, switch to a per-field
explicit mapping (it's only ~15 fields).

- [ ] **Step 4: Run tests** → PASS. **Step 5: Commit**

```bash
git add app/bot_config.py app/risk/account_phase.py tests/test_account_phase.py
git commit -m "feat: account_phase + phase_rules config, tracker_from_config"
```

### Task 7: Risk governor gates in pretrade

**Files:**
- Modify: `app/risk/pretrade.py` (ProposedOrder + check signature + gates)
- Modify: `app/execution/engine.py:861` call site (pass phase tracker, grade, ts)
- Test: `tests/test_risk_governor.py` (create)

- [ ] **Step 1: Failing tests**

```python
"""Risk governor — phase-aware hard gates (no AI overrides, Rule 5).

Encodes the funded-pipeline spec:
a) MLL cushion: <$1k -> half size; <$500 -> block; worst-case single-trade
   loss <= 40% of cushion.
b) Stop-at-target (combine): a pass given back is the most expensive outcome.
c) Best-day cap (combine): one monster day delays the pass via consistency.
d) Winning-day tighten (xfa): >= 2x threshold -> A-grades only.
e) Post-payout half risk (xfa).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.risk.account_phase import CombineRules, PhaseTracker, XfaRules
from app.risk.config import fifty_k_combine
from app.risk.pretrade import Allow, Deny, ProposedOrder, check
from app.risk.state import RiskState

D = Decimal
TS = datetime(2026, 1, 5, 15, 0, tzinfo=timezone.utc)


def order(size=2, entry="2400", stop="2395", grade="B"):
    # MGC: $10/point => stop distance 5 pts = $50/contract
    return ProposedOrder(instrument="MGC", side="long", size=size,
                         entry=D(entry), stop=D(stop), target=D("2410"),
                         setup_grade=grade)


def state():
    return RiskState(config=fifty_k_combine())


def combine(profit="0", best_day="0"):
    t = PhaseTracker(phase="combine", combine=CombineRules(), xfa=XfaRules())
    t.on_pnl(D(profit), TS)
    t.best_day = D(best_day)
    return t


def test_no_phase_means_no_new_gates():
    assert isinstance(check(order(), state()), Allow)


def test_cushion_below_500_blocks_entries():
    t = combine(profit="-1501")            # cushion 499
    d = check(order(), state(), phase=t, ts=TS)
    assert isinstance(d, Deny) and d.reason_code == "MLL_CUSHION"


def test_worst_case_loss_capped_at_40pct_of_cushion():
    t = combine(profit="-1000")            # cushion 1000 -> half-risk zone
    # 40% of 1000 = $400; $50/contract -> max 8; half-risk halves to 4;
    # requested 2 still fits.
    a = check(order(size=2), state(), phase=t, ts=TS)
    assert isinstance(a, Allow) and a.allowed_size == 2
    # requested 20 gets cut to 4 (8 by 40%-cap, halved by cushion zone)
    a2 = check(order(size=20), state(), phase=t, ts=TS)
    assert isinstance(a2, Allow) and a2.allowed_size == 4


def test_stop_at_target_blocks_entries():
    t = combine()
    t.on_pnl(D("1600"), TS); t.roll_day(TS)
    t.on_pnl(D("1600"), TS)                # 3200 profit, best 1600 < 50%
    assert t.target_reached()
    d = check(order(), state(), phase=t, ts=TS)
    assert isinstance(d, Deny) and d.reason_code == "TARGET_REACHED"


def test_best_day_cap_stops_the_day():
    t = combine()
    t.on_pnl(D("1500"), TS)                # today 1500, total 1500 -> ratio 1.0
    d = check(order(), state(), phase=t, ts=TS)
    assert isinstance(d, Deny) and d.reason_code == "BEST_DAY_CAP"


def test_xfa_tighten_after_2x_threshold_blocks_b_grades():
    t = PhaseTracker(phase="xfa", combine=CombineRules(), xfa=XfaRules())
    t.on_pnl(D("2500"), TS); t.roll_day(TS)   # lock MLL at 0, cushion ok
    t.on_pnl(D("300"), TS)                    # today >= 2 x 150
    d = check(order(grade="B"), state(), phase=t, ts=TS)
    assert isinstance(d, Deny) and d.reason_code == "WINNING_DAY_LOCK"
    a = check(order(grade="A"), state(), phase=t, ts=TS)
    assert isinstance(a, Allow)
```

- [ ] **Step 2: Run** → FAIL (`setup_grade` not a field).

- [ ] **Step 3: Implement in `app/risk/pretrade.py`**

Add to ProposedOrder: `setup_grade: str = ""  # "A".."F" or "" when ungraded`.

Extend the signature: `def check(order, state, phase=None, ts=None)` with import
`from app.risk.account_phase import PhaseTracker` (top of file) and
`from app.sim.pricing import _point_value`. Insert AFTER the lockout gate
(1) and BEFORE the sanity gate (2):

```python
    # ------------------------------------------------------------
    # 1b. Phase governor (combine/xfa only; practice/live skip).
    #     Deterministic hard gates — no overrides (Rule 5).
    # ------------------------------------------------------------
    if phase is not None and phase.phase in ("combine", "xfa") and order.is_entry:
        cushion = phase.cushion
        if cushion is not None and cushion < Decimal("500"):
            return Deny(reason_code="MLL_CUSHION",
                        message=f"Cushion {cushion} < $500 — surviving to next ratchet.")
        if phase.phase == "combine":
            if phase.combine.stop_at_target and phase.target_reached():
                return Deny(reason_code="TARGET_REACHED",
                            message="Combine passed — stop trading, do not give it back.")
            denom = phase.total_profit
            if denom > 0 and phase.today_pnl > 0 \
                    and phase.today_pnl >= phase.combine.best_day_cap_frac * denom:
                return Deny(reason_code="BEST_DAY_CAP",
                            message=f"Today {phase.today_pnl} would breach the "
                                    f"consistency cap — done for the day.")
        if phase.phase == "xfa":
            if (phase.today_pnl >= 2 * phase.xfa.winning_day_threshold
                    and order.setup_grade not in ("A",)):
                return Deny(reason_code="WINNING_DAY_LOCK",
                            message=f"Today {phase.today_pnl} — protecting the "
                                    f"winning day; A-grade setups only.")
```

Then in gate (3) — sizing — add the cushion-derived caps just before
`allowed = min(order.size, headroom)`:

```python
        if phase is not None and phase.cushion is not None:
            cushion = phase.cushion
            stop_dist = abs(order.entry - order.stop)
            pv = _point_value(order.instrument)
            if stop_dist > 0 and pv > 0:
                # Worst-case single-trade loss must be <= 40% of cushion.
                cap = int((Decimal("0.40") * cushion) / (stop_dist * pv))
                if cushion < Decimal("1000") or phase.post_payout_half_risk:
                    cap = cap // 2
                if cap <= 0:
                    return Deny(reason_code="MLL_CUSHION",
                                message=f"No size fits 40% of cushion {cushion}.")
                headroom = min(headroom, cap)
```

NOTE: read gate (3) carefully and merge — `headroom` must remain the variable
that flows into `allowed = min(order.size, headroom)`.

Engine call site (`engine.py:861` area): the engine needs a tracker attribute.
Add to `ExecutionEngine.__init__`: `phase: "PhaseTracker | None" = None`,
stored as `self.phase`. Update the call:

```python
        order = ProposedOrder(..., setup_grade=(signal.setup_grade.grade if signal.setup_grade else ""))
        decision = check(order, self.risk_state, phase=self.phase, ts=signal.created_at)
```

(Read the existing ProposedOrder construction a few lines above :861 and add
only the `setup_grade=` kwarg — keep the rest exactly as is.)

- [ ] **Step 4: Run** `pytest tests/test_risk_governor.py tests/test_risk.py -v` → PASS (existing risk tests must not break: `check(order, state)` with no phase is unchanged).

- [ ] **Step 5: Commit**

```bash
git add app/risk/pretrade.py app/execution/engine.py tests/test_risk_governor.py
git commit -m "feat: phase-aware risk governor gates in pretrade"
```

### Task 8: Live wiring — tracker updates, day roll, reconcile, observability

**Files:**
- Modify: `app/main.py` (build tracker via `tracker_from_config`, pass to engine)
- Modify: `app/execution/engine.py` (`_handle_fill`: feed `phase.on_pnl`; day-roll via `_trading_day_ct`)
- Modify: `app/api/server.py` (`_hot_apply`: rebuild tracker on phase config change; `strategy_state` SSE: add `phase` block)
- Modify: `frontend/src/components/StrategyDebug.tsx` (render phase block), `frontend/src/types.ts`
- Test: `tests/test_account_phase.py` (extend)

- [ ] **Step 1: Failing test** (append)

```python
def test_engine_feeds_fills_to_phase_tracker():
    """Every realized fill delta must reach the tracker — the governor's
    cushion math is only as good as the balance it sees."""
    import asyncio
    from app.sim.events import Fill
    from app.execution.engine import ExecutionEngine
    from app.risk.config import fifty_k_combine
    from app.risk.state import RiskState
    from app.sim.paper import PaperBroker

    tracker = PhaseTracker(phase="combine", combine=CombineRules(), xfa=XfaRules())
    broker = PaperBroker(slippage_ticks_market=0, commission_per_side=Decimal("0"))
    engine = ExecutionEngine(broker=broker, risk_state=RiskState(config=fifty_k_combine()),
                             runners=[], replay_mode=True, phase=tracker)

    async def go():
        await broker.connect()
        await engine.start()
        await broker.inject_bar(_bar_helper(T0, 100))
        await broker.place_bracket("MGC", "long", 1, Decimal("100"), Decimal("95"), Decimal("110"))
        # target bar: exit fill with realized pnl flows through engine._handle_fill
        await broker.inject_bar(_bar_helper(T0 + timedelta(minutes=1), 111))

    asyncio.run(go())
    assert tracker.balance > Decimal("50000")
```

(Define `_bar_helper(ts, price)` mirroring Task 3's `_bar`. Adjust the exact
assertion if commissions are nonzero — they're set to 0 here.)

- [ ] **Step 2: Run** → FAIL (engine has no `phase` kwarg until Task 7's change; if Task 7 done, fails on tracker not fed).

- [ ] **Step 3: Implement**

`engine._handle_fill` (find it via `def _handle_fill`): after the existing
`risk_state` update from the fill (search for `realized_pnl_delta`), add:

```python
        if self.phase is not None and fill.realized_pnl_delta != 0:
            self.phase.on_pnl(fill.realized_pnl_delta, fill.ts)
```

Day roll — in `_enforce_flatten`/`_handle_bar` path add (engine `__init__`:
`self._phase_day = None`); at top of `_handle_bar` next to the flatten check:

```python
        if self.phase is not None:
            from app.backtest.runner import _trading_day_ct  # module-level import in practice
            td = _trading_day_ct(bar.ts)
            if self._phase_day is None:
                self._phase_day = td
            elif td != self._phase_day:
                self.phase.roll_day(bar.ts)
                self._phase_day = td
```

(Move `_trading_day_ct` to `app/risk/flatten.py` instead and import it from
both places — backtest.runner re-exports it. One definition only: DRY.)

`app/main.py`: where the engine is built, add
`phase=tracker_from_config(bot_cfg) if bot_cfg.account_phase != "practice" else None`
with import `from app.risk.account_phase import tracker_from_config`.
Reconcile-from-broker (startup): after broker connect, if phase is not None,
log a WARNING that tracker starts from configured starting_balance and must
be manually verified against the TopstepX dashboard on first run. (Full
broker-truth reconciliation reuses the reconciler patterns — defer to a
follow-up; surface it loudly rather than silently trusting local state.)

`server.py` `_hot_apply`: after `_engine.strategy_cfg = body.strategy`:

```python
            from app.risk.account_phase import tracker_from_config
            if body.account_phase != "practice":
                if _engine.phase is None or _engine.phase.phase != body.account_phase:
                    _engine.phase = tracker_from_config(body)  # fresh tracker on phase change
            else:
                _engine.phase = None
```

`strategy_state` SSE (find where the `strategy_state` event dict is built in
server.py): add

```python
        "phase": (None if _engine is None or _engine.phase is None else {
            "name": _engine.phase.phase,
            "balance": str(_engine.phase.balance),
            "mll": str(_engine.phase.mll),
            "cushion": str(_engine.phase.cushion),
            "today_pnl": str(_engine.phase.today_pnl),
            "best_day": str(_engine.phase.best_day_live),
            "winning_days": _engine.phase.winning_days,
            "target_reached": _engine.phase.target_reached(),
        }),
```

`StrategyDebug.tsx`: add a "Account Phase" section rendering those fields
(labeled values, `text-dim` labels / `text-ink` values, no outer border —
the panel sits in a `gap-px bg-border` grid). `types.ts`: extend the
strategy_state type with the `phase` block (nullable).

- [ ] **Step 4: Run** new test + full suite → PASS / known failures only. `cd frontend; npm run build` → clean.

- [ ] **Step 5: Commit**

```bash
git add app/main.py app/execution/engine.py app/api/server.py app/risk/flatten.py frontend/src tests/test_account_phase.py
git commit -m "feat: wire PhaseTracker into engine + SSE/StrategyDebug observability"
```

---

## Phase C — Rules-accurate funded simulator

### Task 9: `funded_sim` core — sequential Combines + XFA lifecycle

**Files:**
- Create: `app/backtest/funded_sim.py`
- Create: `scripts/funded_sim.py` (thin CLI)
- Test: `tests/test_funded_sim.py` (create)

- [ ] **Step 1: Failing tests**

```python
"""Funded-pipeline simulator — sequential Combines + XFA chain.

Why: period-total backtest P&L is misleading; what matters is
pass/bust/payout against real account rules. The sim shares PhaseTracker
with the live governor — one source of truth for rule math.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

from app.backtest.funded_sim import simulate_combines, simulate_xfa_chain

D = Decimal
T0 = datetime(2026, 1, 5, 18, 0, tzinfo=timezone.utc)  # 12:00 CT


def daily(pnls: list[str]) -> list[tuple[datetime, Decimal]]:
    """One equity point per day from a P&L list, starting at $50k-relative 0."""
    out, ts = [], T0
    for p in pnls:
        out.append((ts, D(p)))
        ts += timedelta(days=1)
    return out


def test_combine_pass_counts():
    # +1.4k x2 days then +0.4k -> pass on day 3 (consistency ok), then flat
    days = ["1400", "1400", "400"] + ["0"] * 5
    res = simulate_combines(daily(days))
    assert res["passes"] == 1 and res["busts"] == 0


def test_combine_bust_then_new_attempt():
    # -2k day busts attempt 1 immediately; attempt 2 starts next day and passes
    days = ["-2000", "1400", "1400", "400"]
    res = simulate_combines(daily(days))
    assert res["busts"] == 1 and res["passes"] == 1
    assert res["attempts"] == 2


def test_xfa_payout_and_bust_chain():
    # 5 winning days of +700 -> payout 1750; then -2k+ run busts the account
    days = ["700"] * 5 + ["-900", "-900"]
    res = simulate_xfa_chain(daily(days))
    assert res["gross_payouts"] == D("1750")
    assert res["busts"] == 1
```

- [ ] **Step 2: Run** → FAIL (module missing).

- [ ] **Step 3: Implement `app/backtest/funded_sim.py`**

```python
"""
Rules-accurate funded-pipeline simulator.

Input: a daily P&L series derived from a backtest equity curve. Replays it
through PhaseTracker (the SAME module the live governor uses) to produce
pass/bust/payout numbers instead of period-total P&L.

Caveats (document in every report):
- Daily granularity: intraday MLL touches between fills are invisible;
  busts are UNDERSTATED. Configure an adverse-excursion haircut once the
  intrabar recorder exists.
- Replaying the same P&L sequence across attempts assumes sizing doesn't
  change with balance — true for fixed-contract runs, approximate otherwise.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from statistics import median

from app.risk.account_phase import CombineRules, PhaseTracker, XfaRules


def daily_pnls_from_equity(
    equity_curve: list[tuple[datetime, Decimal]],
) -> list[tuple[datetime, Decimal]]:
    """Collapse a per-fill equity curve into per-trading-day P&L deltas."""
    from app.risk.flatten import trading_day_ct  # single source (moved in Task 8)
    days: dict = {}
    order: list = []
    prev = None
    for ts, eq in equity_curve:
        if prev is None:
            prev = eq
            base_day = trading_day_ct(ts)
            days[base_day] = Decimal("0")
            order.append((base_day, ts))
            continue
        d = trading_day_ct(ts)
        if d not in days:
            days[d] = Decimal("0")
            order.append((d, ts))
        days[d] += eq - prev
        prev = eq
    return [(ts, days[d]) for d, ts in order]


def simulate_combines(
    daily_pnl: list[tuple[datetime, Decimal]],
    rules: CombineRules | None = None,
) -> dict:
    """Sequential Combine attempts over the series. Bust -> new attempt next day."""
    rules = rules or CombineRules()
    attempts = passes = busts = 0
    days_to_pass: list[int] = []
    tracker: PhaseTracker | None = None
    days_in_attempt = 0
    for ts, pnl in daily_pnl:
        if tracker is None:
            tracker = PhaseTracker(phase="combine", combine=rules, xfa=XfaRules())
            attempts += 1
            days_in_attempt = 0
        tracker.on_pnl(pnl, ts)
        days_in_attempt += 1
        if tracker.is_dead():
            busts += 1
            tracker = None
            continue
        tracker.roll_day(ts)
        if tracker.target_reached():
            passes += 1
            days_to_pass.append(days_in_attempt)
            tracker = None
    return {
        "attempts": attempts, "passes": passes, "busts": busts,
        "median_days_to_pass": (median(days_to_pass) if days_to_pass else None),
    }


def simulate_xfa_chain(
    daily_pnl: list[tuple[datetime, Decimal]],
    rules: XfaRules | None = None,
) -> dict:
    """Sequential XFA accounts: bust -> next account starts the following day."""
    rules = rules or XfaRules()
    accounts = busts = 0
    gross_payouts = Decimal("0")
    first_payout_days: list[int] = []
    tracker: PhaseTracker | None = None
    days_in_account = 0
    had_payout = False
    for ts, pnl in daily_pnl:
        if tracker is None:
            tracker = PhaseTracker(phase="xfa", combine=CombineRules(), xfa=rules)
            accounts += 1
            days_in_account = 0
            had_payout = False
        tracker.on_pnl(pnl, ts)
        days_in_account += 1
        if tracker.is_dead():
            busts += 1
            tracker = None
            continue
        tracker.roll_day(ts)
        if tracker.payout_eligible():
            gross_payouts += tracker.request_payout()
            if not had_payout:
                first_payout_days.append(days_in_account)
                had_payout = True
    return {
        "accounts": accounts, "busts": busts,
        "gross_payouts": gross_payouts,
        "net_payouts": gross_payouts * Decimal("0.90"),
        "median_days_to_first_payout": (median(first_payout_days) if first_payout_days else None),
    }


def format_pipeline_summary(equity_curve) -> str:
    daily = daily_pnls_from_equity(equity_curve)
    c = simulate_combines(daily)
    x = simulate_xfa_chain(daily)
    return (
        f"COMBINE: attempts {c['attempts']} | passes {c['passes']} | "
        f"busts {c['busts']} | median days-to-pass {c['median_days_to_pass']}\n"
        f"XFA:     accounts {x['accounts']} | busts {x['busts']} | "
        f"payouts ${x['gross_payouts']:.0f} gross / ${x['net_payouts']:.0f} net (90%)\n"
        f"(daily granularity — intraday MLL touches understated)"
    )
```

`scripts/funded_sim.py` (thin CLI):

```python
"""CLI: funded-pipeline sim over a backtest equity CSV (header: ts,equity)."""
from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from decimal import Decimal
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from app.backtest.funded_sim import format_pipeline_summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("equity_csv")
    args = ap.parse_args()
    curve = []
    with open(args.equity_csv, newline="") as f:
        for row in csv.DictReader(f):
            curve.append((datetime.fromisoformat(row["ts"]), Decimal(row["equity"])))
    print(format_pipeline_summary(curve))


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run** `pytest tests/test_funded_sim.py -v` → PASS.
Then a smoke run: `.venv\Scripts\python.exe scripts\funded_sim.py backtest_results\wf2_test_winner\equity.csv` — outputs the COMBINE/XFA block (sanity: numbers in the ballpark of the handoff's 4 passes / 4–6 busts / ~$13.4k payouts; investigate large deviations before continuing).

- [ ] **Step 5: Commit**

```bash
git add app/backtest/funded_sim.py scripts/funded_sim.py tests/test_funded_sim.py
git commit -m "feat: rules-accurate funded-pipeline simulator (shares PhaseTracker)"
```

### Task 10: Report integration + analysis doc

**Files:**
- Modify: `app/backtest/report.py:18` (replace the misleading line)
- Test: existing report tests (find via `grep -l format_summary tests/`)
- Create: `trade_analysis/2026-06-12_funded_pipeline_baseline.md`

- [ ] **Step 1: Replace the line.** In `format_summary`, change

```python
        f"Combine target:    {'PASSED' if stats.passed_combine else 'DID NOT PASS'}",
```

to a funded-pipeline block (format_summary gains the equity curve via `stats.equity_curve`, already on BacktestStats):

```python
        *_pipeline_lines(stats),
```

with, in the same file:

```python
def _pipeline_lines(stats) -> list[str]:
    """Pass/bust/payout summary — replaces the misleading period-total
    'Combine target: PASSED' verdict."""
    try:
        from app.backtest.funded_sim import format_pipeline_summary
        return format_pipeline_summary(stats.equity_curve).splitlines()
    except Exception as e:  # report must never crash a backtest
        return [f"(funded-pipeline summary unavailable: {e})"]
```

- [ ] **Step 2: Run any report/backtest tests + a real run**

`pytest tests/ -q -k "report or backtest"` → no new failures.
`.venv\Scripts\python.exe scripts\backtest.py --bars bars\bars_MNQ_test_2025_2026.csv --instrument MNQ --timeframe 5min --no-risk-limits` → summary shows COMBINE/XFA lines, no "Combine target" line.

- [ ] **Step 3: Re-run wf2 configs and write the analysis doc.**
Run wf2_test baseline + winner (commands in `trade_analysis/2026-06-10_mnq5min_walkforward.md`) with the flatten rule active; record in `trade_analysis/2026-06-12_funded_pipeline_baseline.md`: the pass/bust/payout table per config, the flatten-rule P&L delta from Task 4, and the per-regime note (passes cluster early-2025/spring-2026; mid-period busts). End with the go/no-go framing: budget 2–3 attempts (~$150–300 fees) per funded account at ~40% pass odds.

- [ ] **Step 4: Commit**

```bash
git add app/backtest/report.py trade_analysis/2026-06-12_funded_pipeline_baseline.md
git commit -m "feat: backtest reports print funded-pipeline pass/bust/payout summary"
```

---

## Self-review notes (done at plan time)

- **Spec coverage:** flatten (§2f → Tasks 1–4), phase machine (§1 → Tasks 5–6), governor gates a–e (§2 → Task 7), live wiring + reconcile caveat (§1 → Task 8), simulator + report (§3 → Tasks 9–10), tests (§4 → embedded per task). Gap deliberately deferred: full broker-truth reconciliation of the tracker (Task 8 logs a loud warning instead — follow-up project; surfacing > silent trust).
- **MLL touch on unrealized:** spec says MLL touch includes unrealized; both tracker and sim are fill/day-granular. Mitigated by (a) the 40%-of-cushion sizing gate bounding open-trade risk, (b) the documented haircut hook. Do not claim intraday accuracy in any report.
- **`passed_combine` field:** left on BacktestStats (other callers may read it); only the report line is replaced.
- **Verify before hardcoding:** Topstep numbers in Tasks 5–6 defaults match the handoff (verified 2026-06-11 per its header); they live in config and the spec warns to re-verify at help.topstep.com before going live.
