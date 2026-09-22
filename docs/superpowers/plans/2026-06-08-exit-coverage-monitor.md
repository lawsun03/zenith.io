# Exit-Coverage Monitor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Continuously verify every open contract has a working stop AND target on the exchange; when one doesn't, escalate grace → re-attach the missing leg(s) → flatten if the stop can't be restored.

**Architecture:** Extend the existing `Reconciler` tick. After the contract-count check passes (counts match broker), query exchange working orders per open position via a new `Broker.exit_coverage()` method. Uncovered contracts trigger remediation through two new thin broker primitives (`place_protective_stop`, `place_protective_target`). Exchange working orders are ground truth — internal tracking is never consulted.

**Tech Stack:** Python 3.x, asyncio, `project-x-py` SDK (`orders.search_open_orders`), FastAPI (config endpoints), React/TypeScript (config form). Tests: pytest + pytest-asyncio, run via `.venv/Scripts/python.exe -m pytest`.

---

## File Structure

| File | Responsibility | Action |
|---|---|---|
| `app/sim/events.py` | `ExitCoverage` dataclass (broker data type, lives by `BrokerPosition`) | Modify |
| `app/sim/protocol.py` | Protocol method signatures: `exit_coverage`, `place_protective_stop`, `place_protective_target` | Modify |
| `app/sim/topstepx.py` | Real implementations against the SDK | Modify |
| `app/sim/paper.py` | Paper/no-op implementations (always covered) so backtests never remediate | Modify |
| `app/bot_config.py` | New config fields: `emergency_stop_distance`, `emergency_target_r`, `naked_grace_seconds` | Modify |
| `app/execution/reconciler.py` | Detection (grace) + escalating remediation + report field | Modify |
| `app/main.py` | Build `ReconcilerConfig` emergency fields from `bot_cfg` | Modify |
| `app/api/server.py` | GET returns + PATCH hot-applies new config to the reconciler | Modify |
| `frontend/src/types.ts` | Type the new config fields | Modify |
| `frontend/src/components/ConfigPanel.tsx` | Render the new config inputs | Modify |
| `tests/test_exit_coverage.py` | Broker `exit_coverage` + protective-order tests | Create |
| `tests/test_reconciler.py` | Naked-detection grace + remediation + precedence tests | Modify |

**Notes carried from the spec:**
- Detection: a position is covered iff `covered_stop >= size AND covered_target >= size`. The sum-based check is robust to the partial ladder.
- Re-attach restores only the *missing* leg(s). Flatten fires **only** when the stop can't be restored; a target failure logs/notifies but never flattens.
- Emergency orders are plain protective orders — **not** registered in `_exit_groups` (we lack original entry context and the partial/BE state machine must not drive them). This is intentional.
- Contract-count drift runs first and takes precedence; exit-coverage only runs when counts match.

---

## Task 1: `ExitCoverage` dataclass

**Files:**
- Modify: `app/sim/events.py` (add after `BrokerPosition`, ~line 113)
- Test: `tests/test_exit_coverage.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_exit_coverage.py`:

```python
from decimal import Decimal

from app.sim.events import ExitCoverage


def test_fully_covered_when_stop_and_target_meet_size():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=2, covered_target=2,
    )
    assert cov.fully_covered is True


def test_naked_when_stop_missing():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=0, covered_target=2,
    )
    assert cov.fully_covered is False


def test_naked_when_target_missing():
    cov = ExitCoverage(
        instrument="MGC", position_size=2, side="long",
        avg_price=Decimal("2400.0"), covered_stop=2, covered_target=0,
    )
    assert cov.fully_covered is False


def test_flat_position_is_always_covered():
    cov = ExitCoverage(
        instrument="MGC", position_size=0, side="",
        avg_price=Decimal("0"), covered_stop=0, covered_target=0,
    )
    assert cov.fully_covered is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py -q`
Expected: FAIL — `ImportError: cannot import name 'ExitCoverage'`.

- [ ] **Step 3: Add the dataclass**

In `app/sim/events.py`, immediately after the `BrokerPosition` dataclass, add:

```python
@dataclass(frozen=True)
class ExitCoverage:
    """How well an open position is protected by working exchange orders.

    Ground-truth snapshot used by the reconciler's exit-coverage monitor.
    Sizes are summed across all working orders on the closing side, so the
    check is robust to the partial-exit ladder (partial + final legs sum to
    full size).
    """

    instrument: str
    position_size: int      # absolute contracts open; 0 = flat
    side: str               # "long" | "short" | "" when flat
    avg_price: Decimal      # broker average entry; basis for emergency prices
    covered_stop: int       # Σ size of working stop orders on the closing side
    covered_target: int     # Σ size of working limit orders on the closing side

    @property
    def fully_covered(self) -> bool:
        if self.position_size == 0:
            return True
        return (
            self.covered_stop >= self.position_size
            and self.covered_target >= self.position_size
        )
```

Confirm `from dataclasses import dataclass` and `from decimal import Decimal` are already imported at the top of `events.py` (they are — `BrokerPosition` uses both).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py -q`
Expected: PASS (4 passed).

- [ ] **Step 5: Commit**

```bash
git add app/sim/events.py tests/test_exit_coverage.py
git commit -m "feat(broker): add ExitCoverage dataclass for exit-coverage monitor"
```

---

## Task 2: Protocol method signatures

**Files:**
- Modify: `app/sim/protocol.py` (add to the `Broker` Protocol, after `get_positions`, ~line 56)

- [ ] **Step 1: Add the import and three method stubs**

In `app/sim/protocol.py`, update the events import line (currently
`from .events import Bar, BracketResult, BrokerPosition, Fill, MarkToMarket, Side`) to include `ExitCoverage`:

```python
from .events import Bar, BracketResult, BrokerPosition, ExitCoverage, Fill, MarkToMarket, Side
```

Then add these three methods to the `Broker` Protocol, right after `get_positions`:

```python
    async def exit_coverage(self, instrument: str) -> ExitCoverage:
        """Working-order coverage for the open position in `instrument`.

        Queries the exchange for live stop/target orders on the closing side.
        Used by the reconciler to detect positions with no protective exit.
        """
        ...

    async def place_protective_stop(
        self, instrument: str, size: int, price: Decimal
    ) -> bool:
        """Place a plain protective stop on the closing side of the current
        position at `price`, sized `size`. Returns True on success.

        Emergency use only — NOT registered in the bracket/partial state
        machine. If the position is already flat, no-ops and returns True.
        """
        ...

    async def place_protective_target(
        self, instrument: str, size: int, price: Decimal
    ) -> bool:
        """Place a plain protective limit (take-profit) on the closing side
        of the current position at `price`, sized `size`. Returns True on
        success. Emergency use only — not registered in the state machine."""
        ...
```

- [ ] **Step 2: Verify the protocol imports cleanly**

Run: `.venv/Scripts/python.exe -c "import app.sim.protocol"`
Expected: no output, exit 0.

- [ ] **Step 3: Commit**

```bash
git add app/sim/protocol.py
git commit -m "feat(broker): add exit-coverage + protective-order methods to Broker protocol"
```

---

## Task 3: `TopstepXBroker.exit_coverage`

**Files:**
- Modify: `app/sim/topstepx.py` (add methods near `get_positions`/`flatten`; add module constants near the SIDE imports)
- Test: `tests/test_exit_coverage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_exit_coverage.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from app.sim.events import BrokerPosition


def _order(order_type, side, size, status=1):
    o = MagicMock()
    o.type = order_type      # 1=Limit, 4=Stop
    o.side = side            # 0=Bid/Buy, 1=Ask/Sell
    o.size = size
    o.status = status        # 1=Open
    return o


def _coverage_broker(position, open_orders):
    """Stub TopstepXBroker for exit_coverage. position is a BrokerPosition
    or None; open_orders is the list returned by search_open_orders."""
    from app.sim.topstepx import TopstepXBroker

    broker = object.__new__(TopstepXBroker)
    broker._connected = True
    broker._extra_suites = {}
    broker._instruments = [position.instrument] if position else ["MGC"]
    broker.get_positions = AsyncMock(return_value=[position] if position else [])
    suite = MagicMock()
    suite.instrument_id = "CON.F.US.MGC.Q25"
    suite.orders.search_open_orders = AsyncMock(return_value=open_orders)
    broker._suite = suite
    return broker


@pytest.mark.asyncio
async def test_exit_coverage_long_fully_covered():
    pos = BrokerPosition(
        instrument="MGC", side="long", size=2,
        average_price=Decimal("2400.0"), unrealized_pnl=Decimal("0"),
    )
    # Long closes with SELL (side=1): one stop(4) size 2 + one limit(1) size 2.
    orders = [_order(4, 1, 2), _order(1, 1, 2)]
    broker = _coverage_broker(pos, orders)
    cov = await broker.exit_coverage("MGC")
    assert cov.position_size == 2
    assert cov.side == "long"
    assert cov.covered_stop == 2
    assert cov.covered_target == 2
    assert cov.fully_covered is True


@pytest.mark.asyncio
async def test_exit_coverage_ignores_wrong_side_and_closed_orders():
    pos = BrokerPosition(
        instrument="MGC", side="long", size=2,
        average_price=Decimal("2400.0"), unrealized_pnl=Decimal("0"),
    )
    orders = [
        _order(4, 0, 2),            # stop on BUY side — wrong side, ignore
        _order(4, 1, 2, status=3),  # cancelled — ignore
        _order(1, 1, 2),            # valid target
    ]
    broker = _coverage_broker(pos, orders)
    cov = await broker.exit_coverage("MGC")
    assert cov.covered_stop == 0     # the only stop was wrong-side/closed
    assert cov.covered_target == 2
    assert cov.fully_covered is False


@pytest.mark.asyncio
async def test_exit_coverage_flat_when_no_position():
    broker = _coverage_broker(None, [])
    cov = await broker.exit_coverage("MGC")
    assert cov.position_size == 0
    assert cov.fully_covered is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py -q`
Expected: FAIL — `AttributeError: 'TopstepXBroker' object has no attribute 'exit_coverage'`.

- [ ] **Step 3: Add module constants + implement `exit_coverage`**

In `app/sim/topstepx.py`, add module-level constants after the `SIDE_SELL` import block (after line ~37):

```python
# SDK OrderType codes: 1=Limit, 2=Market, 3=StopLimit, 4=Stop, 5=TrailingStop.
# Any stop variant protects the downside; a plain limit is a take-profit.
_STOP_ORDER_TYPES = frozenset({3, 4, 5})
_LIMIT_ORDER_TYPE = 1
_OPEN_ORDER_STATUS = 1  # SDK OrderStatus.OPEN
```

Then add the method (place it next to `get_positions`, after `_get_suite_for` is available):

```python
    async def exit_coverage(self, instrument: str) -> ExitCoverage:
        self._require_connected()
        positions = await self.get_positions()
        pos = next((p for p in positions if p.instrument == instrument), None)
        if pos is None or pos.size == 0:
            return ExitCoverage(
                instrument=instrument, position_size=0, side="",
                avg_price=Decimal("0"), covered_stop=0, covered_target=0,
            )

        # Closing side: a long is closed by SELL, a short by BUY. Working
        # exit orders must be on that side to actually protect the position.
        close_sdk_side = SIDE_SELL if pos.side == "long" else SIDE_BUY

        suite = self._get_suite_for(instrument)
        orders = await suite.orders.search_open_orders(
            contract_id=suite.instrument_id
        )

        covered_stop = 0
        covered_target = 0
        for o in orders:
            if getattr(o, "status", None) != _OPEN_ORDER_STATUS:
                continue
            if getattr(o, "side", None) != close_sdk_side:
                continue
            otype = getattr(o, "type", None)
            osize = int(getattr(o, "size", 0) or 0)
            if otype in _STOP_ORDER_TYPES:
                covered_stop += osize
            elif otype == _LIMIT_ORDER_TYPE:
                covered_target += osize

        return ExitCoverage(
            instrument=instrument,
            position_size=int(pos.size),
            side=pos.side,
            avg_price=pos.average_price,
            covered_stop=covered_stop,
            covered_target=covered_target,
        )
```

Add `ExitCoverage` to the events import at the top of `topstepx.py`:
`from .events import Bar, BracketResult, BrokerPosition, ExitCoverage, Fill, MarkToMarket, Side`

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py -q`
Expected: PASS (7 passed total).

- [ ] **Step 5: Commit**

```bash
git add app/sim/topstepx.py tests/test_exit_coverage.py
git commit -m "feat(broker): implement TopstepXBroker.exit_coverage via search_open_orders"
```

---

## Task 4: `TopstepXBroker` protective-order primitives

**Files:**
- Modify: `app/sim/topstepx.py` (add after `exit_coverage`)
- Test: `tests/test_exit_coverage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_exit_coverage.py`:

```python
def _placer_broker(position):
    from app.sim.topstepx import TopstepXBroker

    broker = object.__new__(TopstepXBroker)
    broker._connected = True
    broker._extra_suites = {}
    broker._instruments = ["MGC"]
    broker.get_positions = AsyncMock(return_value=[position] if position else [])
    resp = MagicMock(success=True, orderId="emrg1")
    suite = MagicMock()
    suite.instrument_id = "CON.F.US.MGC.Q25"
    suite.orders.place_stop_order = AsyncMock(return_value=resp)
    suite.orders.place_limit_order = AsyncMock(return_value=resp)
    suite.client.account_info = MagicMock(id=42)
    broker._suite = suite
    return broker


@pytest.mark.asyncio
async def test_place_protective_stop_uses_close_side_for_long():
    pos = BrokerPosition(
        instrument="MGC", side="long", size=2,
        average_price=Decimal("2400.0"), unrealized_pnl=Decimal("0"),
    )
    broker = _placer_broker(pos)
    ok = await broker.place_protective_stop("MGC", 2, Decimal("2397.0"))
    assert ok is True
    # Long closes with SELL (1). Args: (instrument_id, side, size, price, account)
    args = broker._suite.orders.place_stop_order.call_args.args
    assert args[1] == 1            # SIDE_SELL
    assert args[2] == 2            # size
    assert args[3] == 2397.0       # price as float


@pytest.mark.asyncio
async def test_place_protective_stop_noops_when_flat():
    broker = _placer_broker(None)
    ok = await broker.place_protective_stop("MGC", 2, Decimal("2397.0"))
    assert ok is True
    broker._suite.orders.place_stop_order.assert_not_called()


@pytest.mark.asyncio
async def test_place_protective_target_uses_close_side_for_short():
    pos = BrokerPosition(
        instrument="MNQ", side="short", size=1,
        average_price=Decimal("20000.0"), unrealized_pnl=Decimal("0"),
    )
    broker = _placer_broker(pos)
    broker._instruments = ["MNQ"]
    ok = await broker.place_protective_target("MNQ", 1, Decimal("19920.0"))
    assert ok is True
    args = broker._suite.orders.place_limit_order.call_args.args
    assert args[1] == 0            # SIDE_BUY closes a short
    assert args[2] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py -q`
Expected: FAIL — `AttributeError: ... has no attribute 'place_protective_stop'`.

- [ ] **Step 3: Implement the primitives**

In `app/sim/topstepx.py`, after `exit_coverage`, add:

```python
    async def _close_side_for(self, instrument: str) -> "int | None":
        """SDK side that CLOSES the current position, or None if flat."""
        positions = await self.get_positions()
        pos = next((p for p in positions if p.instrument == instrument), None)
        if pos is None or pos.size == 0:
            return None
        return SIDE_SELL if pos.side == "long" else SIDE_BUY

    async def place_protective_stop(
        self, instrument: str, size: int, price: Decimal
    ) -> bool:
        self._require_connected()
        close_sdk_side = await self._close_side_for(instrument)
        if close_sdk_side is None:
            log.info("place_protective_stop(%s): position flat — no-op", instrument)
            return True
        suite = self._get_suite_for(instrument)
        oid = await self._place_stop(
            close_sdk_side, size, price, self._get_account_id(), suite=suite
        )
        if oid is None:
            log.error(
                "place_protective_stop(%s): stop REJECTED size=%d price=%s",
                instrument, size, price,
            )
            return False
        log.error(
            "EMERGENCY protective stop placed on %s: order=%s size=%d price=%s",
            instrument, oid, size, price,
        )
        return True

    async def place_protective_target(
        self, instrument: str, size: int, price: Decimal
    ) -> bool:
        self._require_connected()
        close_sdk_side = await self._close_side_for(instrument)
        if close_sdk_side is None:
            log.info("place_protective_target(%s): position flat — no-op", instrument)
            return True
        suite = self._get_suite_for(instrument)
        oid = await self._place_limit(
            close_sdk_side, size, price, self._get_account_id(), suite=suite
        )
        if oid is None:
            log.error(
                "place_protective_target(%s): target REJECTED size=%d price=%s",
                instrument, size, price,
            )
            return False
        log.error(
            "EMERGENCY protective target placed on %s: order=%s size=%d price=%s",
            instrument, oid, size, price,
        )
        return True
```

(`_get_account_id`, `_place_stop`, `_place_limit`, `_get_suite_for` already exist — confirmed at topstepx.py:302, 783, 797, 227. The ERROR-level log on success is intentional: an emergency exit firing is a loud event the operator must see, per Rule 12.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py -q`
Expected: PASS (10 passed total).

- [ ] **Step 5: Commit**

```bash
git add app/sim/topstepx.py tests/test_exit_coverage.py
git commit -m "feat(broker): add protective stop/target primitives for emergency re-attach"
```

---

## Task 5: `PaperBroker` implementations

**Files:**
- Modify: `app/sim/paper.py` (add after `get_positions`, ~line 156)
- Test: `tests/test_exit_coverage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_exit_coverage.py`:

```python
@pytest.mark.asyncio
async def test_paper_broker_reports_full_coverage():
    """Paper brackets are simulated and never naked — backtests must not
    trigger emergency remediation."""
    from app.sim.paper import PaperBroker

    broker = PaperBroker.__new__(PaperBroker)
    pos = BrokerPosition(
        instrument="MES", side="long", size=3,
        average_price=Decimal("5300.0"), unrealized_pnl=Decimal("0"),
    )
    broker.get_positions = AsyncMock(return_value=[pos])
    cov = await broker.exit_coverage("MES")
    assert cov.position_size == 3
    assert cov.fully_covered is True

    assert await broker.place_protective_stop("MES", 3, Decimal("5295.0")) is True
    assert await broker.place_protective_target("MES", 3, Decimal("5310.0")) is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py::test_paper_broker_reports_full_coverage -q`
Expected: FAIL — `AttributeError: 'PaperBroker' object has no attribute 'exit_coverage'`.

- [ ] **Step 3: Implement on PaperBroker**

In `app/sim/paper.py`, add the `ExitCoverage` import (extend the existing events import) and these methods after `get_positions`:

```python
    async def exit_coverage(self, instrument: str) -> ExitCoverage:
        """Paper positions carry simulated brackets — always fully covered."""
        positions = await self.get_positions()
        pos = next((p for p in positions if p.instrument == instrument), None)
        if pos is None or pos.size == 0:
            return ExitCoverage(
                instrument=instrument, position_size=0, side="",
                avg_price=Decimal("0"), covered_stop=0, covered_target=0,
            )
        size = int(pos.size)
        return ExitCoverage(
            instrument=instrument, position_size=size, side=pos.side,
            avg_price=pos.average_price, covered_stop=size, covered_target=size,
        )

    async def place_protective_stop(
        self, instrument: str, size: int, price: Decimal
    ) -> bool:
        return True

    async def place_protective_target(
        self, instrument: str, size: int, price: Decimal
    ) -> bool:
        return True
```

Confirm `Decimal` and `ExitCoverage` are imported at the top of `paper.py` (add `ExitCoverage` to the `from .events import ...` line; `Decimal` is already imported).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py -q`
Expected: PASS (11 passed total).

- [ ] **Step 5: Commit**

```bash
git add app/sim/paper.py tests/test_exit_coverage.py
git commit -m "feat(broker): PaperBroker reports full exit coverage (no backtest remediation)"
```

---

## Task 6: Config fields in `BotConfig`

**Files:**
- Modify: `app/bot_config.py` (add fields to the `BotConfig` model)
- Test: `tests/test_exit_coverage.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_exit_coverage.py`:

```python
def test_botconfig_emergency_defaults():
    from app.bot_config import BotConfig

    cfg = BotConfig()
    assert cfg.emergency_stop_distance["MGC"] == Decimal("3.0")
    assert cfg.emergency_stop_distance["MNQ"] == Decimal("40.0")
    assert cfg.emergency_stop_distance["MES"] == Decimal("5.0")
    assert cfg.emergency_target_r == Decimal("2.0")
    assert cfg.naked_grace_seconds == 15.0


def test_botconfig_emergency_roundtrips_json():
    from app.bot_config import BotConfig

    cfg = BotConfig(emergency_target_r=Decimal("1.5"), naked_grace_seconds=20.0)
    restored = BotConfig.model_validate_json(cfg.model_dump_json())
    assert restored.emergency_target_r == Decimal("1.5")
    assert restored.naked_grace_seconds == 20.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py -k botconfig_emergency -q`
Expected: FAIL — `AttributeError`/`KeyError` on `emergency_stop_distance`.

- [ ] **Step 3: Add the fields**

In `app/bot_config.py`, add to the `BotConfig` model (near other Decimal fields). Pydantic needs `Field(default_factory=...)` for the mutable dict default:

```python
    # --- Exit-coverage monitor (naked-position protection) ---
    emergency_stop_distance: dict[str, Decimal] = Field(
        default_factory=lambda: {
            "MGC": Decimal("3.0"),
            "MNQ": Decimal("40.0"),
            "MES": Decimal("5.0"),
        }
    )  # price points from broker avg entry for an emergency re-attached stop
    emergency_target_r: Decimal = Decimal("2.0")   # target dist = R × stop dist
    naked_grace_seconds: float = 15.0              # suppress fill→bracket race
```

Confirm `from pydantic import ... Field` is imported (add `Field` if missing) and `Decimal` is imported (it is — used by existing fields).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py -k botconfig_emergency -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add app/bot_config.py tests/test_exit_coverage.py
git commit -m "feat(config): add emergency stop/target + naked grace fields to BotConfig"
```

---

## Task 7: Reconciler — detection with grace

**Files:**
- Modify: `app/execution/reconciler.py` (`ReconcilerConfig`, `ReconcileReport`, `Reconciler.__init__`, `tick`, new `_check_exit_coverage`)
- Test: `tests/test_reconciler.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_reconciler.py` (reuse existing helpers if present; this is self-contained):

```python
import pytest
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from app.sim.events import BrokerPosition, ExitCoverage
from app.execution.reconciler import Reconciler, ReconcilerConfig
from app.risk.state import RiskState
from app.risk.config import fifty_k_combine


def _naked_reconciler(coverage, position):
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[position])
    broker.account_balance = AsyncMock(return_value=Decimal("50000"))
    broker.exit_coverage = AsyncMock(return_value=coverage)
    broker.place_protective_stop = AsyncMock(return_value=True)
    broker.place_protective_target = AsyncMock(return_value=True)
    broker.flatten = AsyncMock(return_value=True)

    risk = RiskState(config=fifty_k_combine())
    risk.realized_balance = Decimal("50000")
    risk.open_contracts = (position.size if position.side == "long" else -position.size)

    rec = Reconciler(
        broker=broker, risk_state=risk,
        config=ReconcilerConfig(
            grace_first_tick=False,
            grace_period_after_order_seconds=0,
            naked_grace_seconds=15.0,
            emergency_stop_distance={"MGC": Decimal("3.0")},
            emergency_target_r=Decimal("2.0"),
        ),
    )
    rec._first_tick_done = True
    return rec, broker


def _long_pos():
    return BrokerPosition(
        instrument="MGC", side="long", size=2,
        average_price=Decimal("2400.0"), unrealized_pnl=Decimal("0"),
    )


@pytest.mark.asyncio
async def test_first_naked_tick_is_grace_no_action():
    cov = ExitCoverage("MGC", 2, "long", Decimal("2400.0"), covered_stop=0, covered_target=2)
    rec, broker = _naked_reconciler(cov, _long_pos())
    report = await rec.tick()
    # Grace: recorded but NOT acted on. No emergency order, no flatten.
    broker.place_protective_stop.assert_not_called()
    broker.flatten.assert_not_called()
    assert "MGC" in rec._naked_since
    assert report.drift_kind != "naked_position"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reconciler.py::test_first_naked_tick_is_grace_no_action -q`
Expected: FAIL — `TypeError: ReconcilerConfig.__init__() got an unexpected keyword 'naked_grace_seconds'`.

- [ ] **Step 3: Extend config, report, init; add detection to tick**

In `app/execution/reconciler.py`:

(a) Add to `ReconcilerConfig`:

```python
    # --- Exit-coverage monitor ---
    naked_grace_seconds: float = 15.0
    emergency_stop_distance: dict = field(default_factory=dict)  # instrument -> Decimal points
    emergency_target_r: Decimal = Decimal("2.0")
```

(`field` and `Decimal` are already imported in this module.)

(b) Update `ReconcileReport.drift_kind` docstring comment to include `"naked_position"` and add a field:

```python
    drift_kind: Optional[str]  # "contract_count" | "balance" | "naked_position" | None
    flattened: bool
    naked_instruments: list = field(default_factory=list)
    notes: str = ""
```

(c) In `Reconciler.__init__`, add:

```python
        # Per-instrument timestamp of when a position was first seen naked.
        # Drives the grace window before emergency remediation.
        self._naked_since: dict[str, datetime] = {}
```

(d) In `tick()`, insert the exit-coverage check immediately AFTER the contract-count
drift block (after the `if broker_contracts != internal_contracts:` block that returns —
reaching here means counts match) and BEFORE the balance-drift block:

```python
        # ----- Exit-coverage: counts match, but is every contract protected? -----
        naked_report = await self._check_exit_coverage(ts, broker_positions, broker_balance)
        if naked_report is not None:
            self._last_report = naked_report
            return naked_report
```

(e) Add the detection method (remediation call is filled in Task 8; for now define
`_remediate_naked` as a stub that will be replaced — but to keep this task self-contained,
implement detection so the grace test passes and have remediation no-op-return for now):

```python
    async def _check_exit_coverage(
        self, ts: datetime, broker_positions: list, broker_balance: Decimal
    ) -> "Optional[ReconcileReport]":
        """For each open position, verify exchange exit coverage. Grace on first
        sighting; remediate once past naked_grace_seconds. Returns a naked report
        if any instrument was remediated this tick, else None."""
        acted: list[str] = []
        for p in broker_positions:
            if p.size == 0:
                continue
            cov = await self.broker.exit_coverage(p.instrument)
            if cov.fully_covered:
                self._naked_since.pop(p.instrument, None)
                continue
            first = self._naked_since.get(p.instrument)
            if first is None:
                self._naked_since[p.instrument] = ts
                log.warning(
                    "Exit-coverage: %s NAKED (stop %d/%d, target %d/%d) — "
                    "grace started (%.0fs).",
                    p.instrument, cov.covered_stop, cov.position_size,
                    cov.covered_target, cov.position_size,
                    self.config.naked_grace_seconds,
                )
                continue
            if (ts - first).total_seconds() < self.config.naked_grace_seconds:
                continue
            log.error(
                "Exit-coverage: %s STILL NAKED past grace — remediating "
                "(stop %d/%d, target %d/%d).",
                p.instrument, cov.covered_stop, cov.position_size,
                cov.covered_target, cov.position_size,
            )
            await self._remediate_naked(cov)
            self._naked_since.pop(p.instrument, None)
            acted.append(p.instrument)

        if not acted:
            return None
        return ReconcileReport(
            ts=ts,
            broker_open_contracts=sum(
                (p.size if p.side == "long" else -p.size) for p in broker_positions
            ),
            broker_balance=broker_balance,
            internal_open_contracts=self.risk_state.open_contracts,
            internal_balance=self.risk_state.realized_balance,
            drift_detected=True,
            drift_kind="naked_position",
            flattened=False,
            naked_instruments=acted,
            notes=f"emergency exit re-attach for {acted}",
        )

    async def _remediate_naked(self, cov) -> None:
        """Filled in Task 8."""
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reconciler.py::test_first_naked_tick_is_grace_no_action -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/execution/reconciler.py tests/test_reconciler.py
git commit -m "feat(reconciler): detect naked positions via exit_coverage with grace window"
```

---

## Task 8: Reconciler — escalating remediation

**Files:**
- Modify: `app/execution/reconciler.py` (replace `_remediate_naked` stub)
- Test: `tests/test_reconciler.py`

- [ ] **Step 1: Write the failing tests (parametrized MGC/MNQ/MES + precedence)**

Append to `tests/test_reconciler.py`:

```python
def _naked_reconciler_multi(instrument, avg, stop_dist, side, position_size,
                            covered_stop, covered_target,
                            stop_ok=True, target_ok=True):
    pos = BrokerPosition(
        instrument=instrument, side=side, size=position_size,
        average_price=avg, unrealized_pnl=Decimal("0"),
    )
    cov = ExitCoverage(instrument, position_size, side, avg, covered_stop, covered_target)
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[pos])
    broker.account_balance = AsyncMock(return_value=Decimal("50000"))
    broker.exit_coverage = AsyncMock(return_value=cov)
    broker.place_protective_stop = AsyncMock(return_value=stop_ok)
    broker.place_protective_target = AsyncMock(return_value=target_ok)
    broker.flatten = AsyncMock(return_value=True)

    risk = RiskState(config=fifty_k_combine())
    risk.realized_balance = Decimal("50000")
    risk.open_contracts = position_size if side == "long" else -position_size

    rec = Reconciler(
        broker=broker, risk_state=risk,
        config=ReconcilerConfig(
            grace_first_tick=False, grace_period_after_order_seconds=0,
            naked_grace_seconds=15.0,
            emergency_stop_distance={instrument: stop_dist},
            emergency_target_r=Decimal("2.0"),
        ),
    )
    rec._first_tick_done = True
    # Pre-age the naked timestamp so this tick is PAST grace.
    rec._naked_since[instrument] = datetime.now(timezone.utc) - timedelta(seconds=60)
    return rec, broker, cov


@pytest.mark.parametrize("instrument,avg,stop_dist,side", [
    ("MGC", Decimal("2400.0"), Decimal("3.0"), "long"),
    ("MNQ", Decimal("20000.0"), Decimal("40.0"), "short"),
    ("MES", Decimal("5300.0"), Decimal("5.0"), "long"),
])
@pytest.mark.asyncio
async def test_reattach_missing_stop_at_emergency_distance(instrument, avg, stop_dist, side):
    # Target present, stop missing → re-attach stop only, no flatten.
    rec, broker, _ = _naked_reconciler_multi(
        instrument, avg, stop_dist, side, position_size=2,
        covered_stop=0, covered_target=2,
    )
    report = await rec.tick()
    broker.place_protective_stop.assert_awaited_once()
    args = broker.place_protective_stop.call_args.args
    assert args[0] == instrument
    assert args[1] == 2  # stop_gap
    expected_stop = avg - stop_dist if side == "long" else avg + stop_dist
    assert args[2] == expected_stop
    broker.place_protective_target.assert_not_called()  # target already covered
    broker.flatten.assert_not_called()
    assert report.drift_kind == "naked_position"
    assert instrument in report.naked_instruments


@pytest.mark.parametrize("instrument,avg,stop_dist,side", [
    ("MGC", Decimal("2400.0"), Decimal("3.0"), "long"),
    ("MNQ", Decimal("20000.0"), Decimal("40.0"), "short"),
    ("MES", Decimal("5300.0"), Decimal("5.0"), "long"),
])
@pytest.mark.asyncio
async def test_reattach_missing_target_only(instrument, avg, stop_dist, side):
    rec, broker, _ = _naked_reconciler_multi(
        instrument, avg, stop_dist, side, position_size=2,
        covered_stop=2, covered_target=0,
    )
    await rec.tick()
    broker.place_protective_stop.assert_not_called()  # stop already covered
    broker.place_protective_target.assert_awaited_once()
    args = broker.place_protective_target.call_args.args
    expected_target = (avg + Decimal("2.0") * stop_dist) if side == "long" \
        else (avg - Decimal("2.0") * stop_dist)
    assert args[2] == expected_target
    broker.flatten.assert_not_called()  # missing target is not a capital risk


@pytest.mark.asyncio
async def test_stop_reattach_failure_triggers_flatten():
    rec, broker, _ = _naked_reconciler_multi(
        "MGC", Decimal("2400.0"), Decimal("3.0"), "long", position_size=2,
        covered_stop=0, covered_target=2, stop_ok=False,
    )
    await rec.tick()
    broker.place_protective_stop.assert_awaited_once()
    broker.flatten.assert_awaited_once_with("MGC")  # instrument-scoped flatten


@pytest.mark.asyncio
async def test_target_reattach_failure_does_not_flatten():
    rec, broker, _ = _naked_reconciler_multi(
        "MGC", Decimal("2400.0"), Decimal("3.0"), "long", position_size=2,
        covered_stop=2, covered_target=0, target_ok=False,
    )
    await rec.tick()
    broker.flatten.assert_not_called()


@pytest.mark.asyncio
async def test_no_emergency_distance_configured_flattens():
    rec, broker, _ = _naked_reconciler_multi(
        "MGC", Decimal("2400.0"), Decimal("3.0"), "long", position_size=2,
        covered_stop=0, covered_target=2,
    )
    rec.config.emergency_stop_distance = {}  # no distance for MGC
    await rec.tick()
    broker.place_protective_stop.assert_not_called()
    broker.flatten.assert_awaited_once_with("MGC")


@pytest.mark.asyncio
async def test_contract_drift_takes_precedence_over_naked():
    # Counts diverge AND position naked → drift path (flatten+lockout), NOT naked path.
    rec, broker, _ = _naked_reconciler_multi(
        "MGC", Decimal("2400.0"), Decimal("3.0"), "long", position_size=2,
        covered_stop=0, covered_target=0,
    )
    rec.risk_state.open_contracts = 0  # internal disagrees with broker (2)
    report = await rec.tick()
    assert report.drift_kind == "contract_count"
    broker.place_protective_stop.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reconciler.py -k "reattach or flatten or precedence or emergency_distance" -q`
Expected: FAIL — `_remediate_naked` is a stub, so no protective calls happen.

- [ ] **Step 3: Implement `_remediate_naked`**

Replace the `_remediate_naked` stub in `app/execution/reconciler.py`:

```python
    async def _remediate_naked(self, cov) -> None:
        """Re-attach only the missing leg(s). Flatten ONLY if the stop cannot
        be restored — a missing target is not a capital risk."""
        stop_gap = cov.position_size - cov.covered_stop
        target_gap = cov.position_size - cov.covered_target
        dist = self.config.emergency_stop_distance.get(cov.instrument)

        if stop_gap > 0:
            if dist is None:
                log.error(
                    "Exit-coverage: no emergency_stop_distance for %s — "
                    "cannot re-attach a safe stop. Flattening.", cov.instrument,
                )
                await self.broker.flatten(cov.instrument)
                self._notify_naked(cov, action="flattened (no stop distance)")
                return
            stop_price = (
                cov.avg_price - dist if cov.side == "long" else cov.avg_price + dist
            )
            ok = await self.broker.place_protective_stop(
                cov.instrument, stop_gap, stop_price
            )
            if not ok:
                log.error(
                    "Exit-coverage: emergency stop re-attach FAILED on %s — "
                    "flattening.", cov.instrument,
                )
                await self.broker.flatten(cov.instrument)
                self._notify_naked(cov, action="flattened (stop re-attach failed)")
                return

        if target_gap > 0 and dist is not None:
            target_price = (
                cov.avg_price + self.config.emergency_target_r * dist
                if cov.side == "long"
                else cov.avg_price - self.config.emergency_target_r * dist
            )
            ok = await self.broker.place_protective_target(
                cov.instrument, target_gap, target_price
            )
            if not ok:
                # No capital risk — log + notify, do NOT flatten.
                log.error(
                    "Exit-coverage: emergency target re-attach failed on %s "
                    "(non-fatal).", cov.instrument,
                )

        self._notify_naked(cov, action="re-attached missing exit leg(s)")

    def _notify_naked(self, cov, action: str) -> None:
        if self.notifier is not None and self.notifier.enabled:
            asyncio.create_task(self.notifier.send(
                subject=f"NAKED POSITION on {cov.instrument} — {action}",
                body=(
                    f"Exit-coverage monitor found {cov.instrument} unprotected.\n\n"
                    f"  Position size:   {cov.position_size} ({cov.side})\n"
                    f"  Avg price:       {cov.avg_price}\n"
                    f"  Stop covered:    {cov.covered_stop}/{cov.position_size}\n"
                    f"  Target covered:  {cov.covered_target}/{cov.position_size}\n"
                    f"  Action taken:    {action}\n"
                ),
            ))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/Scripts/python.exe -m pytest tests/test_reconciler.py -k "reattach or flatten or precedence or emergency_distance or naked" -q`
Expected: PASS (all naked/remediation tests green).

- [ ] **Step 5: Commit**

```bash
git add app/execution/reconciler.py tests/test_reconciler.py
git commit -m "feat(reconciler): escalating naked-position remediation (re-attach -> flatten)"
```

---

## Task 9: Wire config through main.py + API + frontend

**Files:**
- Modify: `app/main.py` (build `ReconcilerConfig` emergency fields from `bot_cfg`)
- Modify: `app/api/server.py` (GET returns them; PATCH hot-applies to the reconciler)
- Modify: `frontend/src/types.ts`, `frontend/src/components/ConfigPanel.tsx`
- Test: `tests/test_reconciler.py` (config-from-bot_cfg wiring) + manual frontend check

- [ ] **Step 1: Pass emergency fields into `ReconcilerConfig` in main.py**

In `app/main.py`, in the `ReconcilerConfig(...)` construction (~line 897), add:

```python
        config=ReconcilerConfig(
            interval_seconds=cfg.reconcile_interval_seconds,
            balance_tolerance=Decimal("50"),
            grace_first_tick=True,
            grace_period_after_order_seconds=60.0,
            naked_grace_seconds=bot_cfg.naked_grace_seconds,
            emergency_stop_distance=dict(bot_cfg.emergency_stop_distance),
            emergency_target_r=bot_cfg.emergency_target_r,
        ),
```

- [ ] **Step 2: Hot-apply in PATCH /api/config**

In `app/api/server.py`, in the PATCH handler after the existing `_broker`/`_engine`
hot-apply block (~line 506), add (the `reconciler` param is in closure scope, used already
at line 290):

```python
        # Hot-apply exit-coverage settings to the running reconciler so naked
        # remediation distances/grace change without a restart (Rule 10).
        reconciler.config.naked_grace_seconds = body.naked_grace_seconds
        reconciler.config.emergency_stop_distance = dict(body.emergency_stop_distance)
        reconciler.config.emergency_target_r = body.emergency_target_r
```

GET /api/config already serializes the full `BotConfig` (it returns the saved model), so the
new fields appear automatically — verify in Step 5.

- [ ] **Step 3: Add the wiring test**

Append to `tests/test_reconciler.py`:

```python
@pytest.mark.asyncio
async def test_reconciler_config_carries_emergency_fields():
    from app.bot_config import BotConfig
    bot_cfg = BotConfig()
    cfg = ReconcilerConfig(
        naked_grace_seconds=bot_cfg.naked_grace_seconds,
        emergency_stop_distance=dict(bot_cfg.emergency_stop_distance),
        emergency_target_r=bot_cfg.emergency_target_r,
    )
    assert cfg.emergency_stop_distance["MNQ"] == Decimal("40.0")
    assert cfg.emergency_target_r == Decimal("2.0")
    assert cfg.naked_grace_seconds == 15.0
```

Run: `.venv/Scripts/python.exe -m pytest tests/test_reconciler.py::test_reconciler_config_carries_emergency_fields -q`
Expected: PASS.

- [ ] **Step 4: Frontend types + form**

In `frontend/src/types.ts`, add to the config interface (near `partial_profit_r`):

```typescript
  emergency_stop_distance?: Record<string, number>
  emergency_target_r?: number
  naked_grace_seconds?: number
```

In `frontend/src/components/ConfigPanel.tsx`, add inputs in the form (match the existing
`text-dim` label / numeric input pattern used for `partial_profit_r`). Render one numeric
input per instrument in `emergency_stop_distance` (derive keys from the configured
`instruments`), plus a numeric input for `emergency_target_r` and `naked_grace_seconds`.
Follow the existing controlled-input + PATCH-on-save pattern; do not add new state libs.

- [ ] **Step 5: Verify end to end**

```bash
# Backend: full suite green
.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py tests/test_reconciler.py -q
# Frontend: clean build (serves the static bundle the bot hosts)
cd frontend && npm run build
```
Expected: pytest all green; `npm run build` completes with no TS errors.

Then manually confirm config round-trips:
```bash
curl -s localhost:8000/api/config | python -m json.tool | grep -A4 emergency
```
Expected: `emergency_stop_distance`, `emergency_target_r`, `naked_grace_seconds` present.

- [ ] **Step 6: Commit**

```bash
git add app/main.py app/api/server.py frontend/src/types.ts frontend/src/components/ConfigPanel.tsx tests/test_reconciler.py
git commit -m "feat: wire exit-coverage config through main, API hot-apply, and config panel"
```

---

## Task 10: Final verification + checkpoint

- [ ] **Step 1: Run the full backend suite**

Run: `.venv/Scripts/python.exe -m pytest tests/test_exit_coverage.py tests/test_reconciler.py tests/test_partial_exit.py -q`
Expected: all green. (Pre-existing unrelated failures noted in memory `project_known_failing_tests` are out of scope — confirm no NEW failures in these three files.)

- [ ] **Step 2: Frontend build**

Run: `cd frontend && npm run build`
Expected: no TypeScript errors.

- [ ] **Step 3: Checkpoint (Rule 10)**

Confirm and record:
- `exit_coverage` queries exchange working orders, sums stop/target by closing side — verified by `tests/test_exit_coverage.py`.
- Protective primitives place on the correct close side; no-op when flat — verified.
- PaperBroker reports full coverage — backtests never remediate — verified.
- Reconciler: grace on first naked tick; re-attach missing leg past grace; flatten only on stop failure or missing distance; contract-drift precedence — verified across MGC/MNQ/MES.
- Config persisted, hot-applied to the reconciler, surfaced in GET /api/config and ConfigPanel.

- [ ] **Step 4: Update the todo status**

Edit `todos/reconciler-naked-position.md`: set `status: done` and add a one-line note that the exit-coverage monitor shipped (referencing this plan). Commit:

```bash
git add todos/reconciler-naked-position.md
git commit -m "docs: mark naked-position todo done (exit-coverage monitor shipped)"
```

---

## Self-Review notes (addressed)

- **Spec coverage:** detection (Task 3) ✓; stop-and-target scope (Task 3 `fully_covered`) ✓; exchange-as-truth (Task 3 `search_open_orders`) ✓; escalating remediation (Tasks 7–8) ✓; re-attach missing leg only + flatten-only-on-stop-failure (Task 8) ✓; per-instrument config + target R + grace (Task 6) ✓; hot-apply (Task 9) ✓; observability/loud logs + notifier (Tasks 4, 8) ✓; `naked_instruments` report (Task 7) ✓; PaperBroker no-remediation (Task 5) ✓; contract-drift precedence (Task 8 test) ✓; tests parametrized MGC/MNQ/MES (Task 8) ✓.
- **Type consistency:** `ExitCoverage(instrument, position_size, side, avg_price, covered_stop, covered_target)` used identically in Tasks 1, 3, 5, 7, 8. `place_protective_stop/target(instrument, size, price)` consistent across protocol (Task 2), topstepx (Task 4), paper (Task 5), reconciler calls (Task 8). `ReconcilerConfig` fields `naked_grace_seconds`/`emergency_stop_distance`/`emergency_target_r` consistent Tasks 7→9.
- **Emergency orders intentionally not registered in `_exit_groups`** — documented in Task 4 and the spec; a reviewer must not "fix" this.
