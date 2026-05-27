# Live Partial Profits + Break-Even Stop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port the backtest-only partial-profit / break-even logic to live TopstepX: take half off at a configurable R-multiple (default 1.5R) then move the stop to break-even; for 1-contract entries, skip the scale-out but still move the stop to break-even at the same R level.

**Architecture:** All logic lives inside `TopstepXBroker`. When `partial_profit_r == 0` the existing path (stop+target via `_exit_pairs`) runs unchanged. When `> 0`, a parallel `_exit_groups` state machine places stop + partial-target + final-target (size≥2) or arms a quote-driven break-even watch (`_be_watches`, size==1), using the SDK's `modify_order` for atomic stop transitions. The exit-transition logic is extracted into broker methods so it is unit-testable with a stub SDK client.

**Tech Stack:** Python 3.12, asyncio, Decimal, `project-x-py` SDK, pytest, pydantic (`BotConfig`), FastAPI, React/TypeScript.

**Spec:** `docs/superpowers/specs/2026-05-26-live-partials-be-design.md`

---

## File Structure

| Action | Path | Responsibility |
|--------|------|----------------|
| Modify | `app/broker/topstepx.py` | `_partial_plan` helper; ctor `partial_profit_r`; stash partial fields in `_pending_brackets`; `_exit_groups`/`_be_watches` state; `_place_partial_bracket_after_fill`; `_handle_group_fill`; `_modify_stop_to_be` failure ladder; `_maybe_move_stop_to_be`; one branch in `_on_fill_event`; one call in `_on_quote_update` |
| Modify | `app/bot_config.py` | `BotConfig.partial_profit_r` field + `save_bot_config` serialization |
| Modify | `app/main.py` | Pass `partial_profit_r` into `TopstepXBroker(...)` |
| Modify | `app/api/server.py` | `GET /api/config` returns it; `PATCH` hot-applies `_broker.partial_profit_r` |
| Modify | `frontend/src/types.ts` | `partial_profit_r: number` on `BotConfig` |
| Modify | `frontend/src/components/ConfigPanel.tsx` | Config field (init/submit/render) |
| Create | `tests/test_topstepx_partials.py` | Unit tests for `_partial_plan` + group transitions + BE watch + failure ladder (stub SDK) |
| Modify | `tests/test_bot_config.py` (or create) | `partial_profit_r` round-trip |

**Testability note:** `TopstepXBroker` talks to the real SDK via `self._suite.orders.*`. Tests construct the broker, then assign a stub `broker._suite` whose `.orders` records calls. The exit-transition logic is therefore placed in **broker methods** (`_handle_group_fill`, `_modify_stop_to_be`, `_maybe_move_stop_to_be`, `_place_partial_bracket_after_fill`) — not inside the `_on_fill_event` closure — so tests can call them directly.

---

## Task 1: Verify `modify_order` semantics (no code — de-risk the core assumption)

The whole design assumes `self._suite.orders.modify_order(order_id, stop_price=..., size=...)` modifies an order **in place** (same order_id, no implicit cancel/replace) and accepts `stop_price` and `size` independently. Confirm before building on it.

- [ ] **Step 1: Read the SDK source**

Run: `sed -n '/async def modify_order/,/return /p' .venv/Lib/site-packages/project_x_py/order_manager/core.py`

Confirm: parameters are `order_id, limit_price, stop_price, size`; it issues a modify request (not cancel+replace); returns `bool`. Note whether it re-aligns price to tick size (it may call `align_price_to_tick_size`).

- [ ] **Step 2: Record the finding inline in the plan**

Append a short note to this task: whether modify is in-place and tick-aligned. If `modify_order` turns out to cancel+replace (new order_id), then the cancel-replace fallback in Task 8 becomes the PRIMARY path — flag this for the implementer and adjust Task 6 to re-register the new stop id.

- [ ] **Step 3: Commit (plan note only, if edited)**

No code commit. Proceed to Task 2.

---

## Task 2: Pure `_partial_plan` helper

**Files:**
- Modify: `app/broker/topstepx.py`
- Create: `tests/test_topstepx_partials.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_topstepx_partials.py`:

```python
"""Tests for live partial-profit / break-even logic in TopstepXBroker."""
from decimal import Decimal

import pytest

from app.broker.topstepx import _partial_plan


def test_partial_plan_disabled_returns_none():
    assert _partial_plan(Decimal("100"), Decimal("99"), 2, Decimal("0")) is None


def test_partial_plan_long_half_at_1_5r():
    # entry 100, stop 99 → R=1. 1.5R partial at 101.5. size 4 → half=2, remaining=2.
    plan = _partial_plan(Decimal("100"), Decimal("99"), 4, Decimal("1.5"))
    assert plan.partial_price == Decimal("101.5")
    assert plan.partial_size == 2
    assert plan.remaining_size == 2
    assert plan.be_price == Decimal("100")


def test_partial_plan_short_half_at_1_5r():
    # entry 100, stop 101 → R=1. short 1.5R partial BELOW at 98.5.
    plan = _partial_plan(Decimal("100"), Decimal("101"), 4, Decimal("1.5"))
    assert plan.partial_price == Decimal("98.5")
    assert plan.partial_size == 2
    assert plan.remaining_size == 2


def test_partial_plan_size_one_no_scaleout():
    # 1 lot → partial_size 0, remaining 1, but a partial_price (BE trigger) still set.
    plan = _partial_plan(Decimal("100"), Decimal("99"), 1, Decimal("1.5"))
    assert plan.partial_size == 0
    assert plan.remaining_size == 1
    assert plan.partial_price == Decimal("101.5")
    assert plan.be_price == Decimal("100")


def test_partial_plan_odd_size_floors_half():
    # size 3 → half=1, remaining=2.
    plan = _partial_plan(Decimal("100"), Decimal("99"), 3, Decimal("1.5"))
    assert plan.partial_size == 1
    assert plan.remaining_size == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -v`
Expected: FAIL — `ImportError: cannot import name '_partial_plan'`.

- [ ] **Step 3: Implement the helper**

In `app/broker/topstepx.py`, after the `_point_value` function (around line 67), add:

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class PartialPlan:
    """How a partial-profit / BE entry is split. Pure data, no SDK."""
    partial_price: Decimal   # the R-multiple level (scale-out price; also the BE trigger for 1-lots)
    partial_size: int        # contracts to scale out (0 when entry size == 1)
    remaining_size: int      # contracts left after the partial
    be_price: Decimal        # break-even = the actual entry fill price


def _partial_plan(
    entry_price: Decimal,
    stop: Decimal,
    size: int,
    partial_r: Decimal,
) -> PartialPlan | None:
    """Compute the partial/BE plan, or None when partials are disabled.

    partial_price = entry ± R*partial_r (R = |entry-stop|); + for long, - for short.
    Long vs short is inferred from stop position: stop below entry → long.
    partial_size = size // 2 (0 for a 1-lot). be_price = entry_price.
    """
    if partial_r <= 0:
        return None
    r = abs(entry_price - stop)
    is_long = stop < entry_price
    partial_price = entry_price + r * partial_r if is_long else entry_price - r * partial_r
    partial_size = size // 2
    return PartialPlan(
        partial_price=partial_price,
        partial_size=partial_size,
        remaining_size=size - partial_size,
        be_price=entry_price,
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -v`
Expected: all 5 PASS.

- [ ] **Step 5: Commit**

```bash
git add app/broker/topstepx.py tests/test_topstepx_partials.py
git commit -m "feat: pure _partial_plan helper for live partials"
```

---

## Task 3: Config field + persistence

**Files:**
- Modify: `app/bot_config.py`
- Create: `tests/test_bot_config_partials.py`

- [ ] **Step 1: Write the failing test**

Create `tests/test_bot_config_partials.py`:

```python
from decimal import Decimal
from pathlib import Path

from app.bot_config import BotConfig, load_bot_config, save_bot_config


def test_partial_profit_r_default_disabled():
    assert BotConfig().partial_profit_r == Decimal("0")


def test_partial_profit_r_round_trip(tmp_path: Path):
    p = tmp_path / "cfg.json"
    save_bot_config(BotConfig(partial_profit_r=Decimal("1.5")), p)
    assert load_bot_config(p).partial_profit_r == Decimal("1.5")
    # Ensure it is serialized as a string (Decimal-safe), not a float.
    assert '"partial_profit_r": "1.5"' in p.read_text()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_bot_config_partials.py -v`
Expected: FAIL — `partial_profit_r` not a field / not serialized.

- [ ] **Step 3: Add the field**

In `app/bot_config.py`, in `BotConfig`, add after the `risk_per_trade_pct` line (line 54):

```python
    partial_profit_r: Decimal = Decimal("0")  # 0 = disabled; e.g. 1.5 = take half at 1.5R then move stop to break-even (BE-only for 1-lots)
```

- [ ] **Step 4: Serialize it**

In `save_bot_config`, in the `data` dict, add after the `"risk_per_trade_pct"` line (line 82):

```python
        "partial_profit_r": _conv(config.partial_profit_r),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_bot_config_partials.py -v`
Expected: both PASS.

- [ ] **Step 6: Commit**

```bash
git add app/bot_config.py tests/test_bot_config_partials.py
git commit -m "feat: partial_profit_r field on BotConfig"
```

---

## Task 4: Broker ctor accepts `partial_profit_r`; main passes it; stash partial fields at entry

**Files:**
- Modify: `app/broker/topstepx.py`
- Modify: `app/main.py`
- Modify: `tests/test_topstepx_partials.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_topstepx_partials.py`:

```python
from app.broker.topstepx import TopstepXBroker


def test_broker_stores_partial_profit_r():
    b = TopstepXBroker(partial_profit_r=Decimal("1.5"))
    assert b.partial_profit_r == Decimal("1.5")


def test_broker_default_partial_disabled():
    assert TopstepXBroker().partial_profit_r == Decimal("0")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -k broker_ -v`
Expected: FAIL — `__init__() got an unexpected keyword argument 'partial_profit_r'`.

- [ ] **Step 3: Add ctor param + init state**

In `app/broker/topstepx.py`, change `TopstepXBroker.__init__` signature (line 146):

```python
    def __init__(self, account_name: str | None = None, entry_mode: str = "market", partial_profit_r: Decimal = Decimal("0")) -> None:
```

After `self.entry_mode = entry_mode` (line 149), add:

```python
        self.partial_profit_r = partial_profit_r  # 0 = disabled; >0 = take half at NxR then BE (hot-applied via PATCH /api/config)
```

In the same `__init__`, after the existing `self._exit_pairs: dict[str, str] = {}` line (line 158), add the new state:

```python
        # Partials path (only used when partial_profit_r > 0). Each leg order_id
        # maps to the same shared group dict. Kept separate from _exit_pairs so the
        # disabled path is byte-for-byte unchanged.
        self._exit_groups: dict[str, dict] = {}
        # Break-even watches for 1-lot entries, keyed by instrument. The quote
        # handler moves the stop to BE once price crosses trigger_price.
        self._be_watches: dict[str, dict] = {}
```

- [ ] **Step 4: Pass it from main.py**

In `app/main.py`, in `_build_broker`, change the live return (line 212):

```python
    return TopstepXBroker(account_name=bot_cfg.account_name, entry_mode=bot_cfg.entry_mode, partial_profit_r=bot_cfg.partial_profit_r)
```

- [ ] **Step 5: Stash partial fields in `_pending_brackets` at entry placement**

In `place_market_bracket`, the `self._pending_brackets[entry_order_id] = {...}` dict (lines 383-389) — add three keys so the fill handler knows the split. Replace that assignment with:

```python
        self._pending_brackets[entry_order_id] = {
            "stop_offset": stop - entry,
            "target_offset": target - entry,
            "close_sdk_side": close_sdk_side,
            "size": size,
            "account_id": account_id,
            "partial_r": self.partial_profit_r,
            "entry_side": side,
            "instrument": instrument,
        }
```

Make the identical change in `place_limit_bracket` (the `self._pending_brackets[entry_order_id] = {...}` at lines 493-499) — same three added keys (`partial_r`, `entry_side`, `instrument`).

- [ ] **Step 6: Run tests + import check**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -k broker_ -v`
Expected: both PASS.
Run: `.venv\Scripts\python.exe -c "import app.main"`
Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add app/broker/topstepx.py app/main.py tests/test_topstepx_partials.py
git commit -m "feat: wire partial_profit_r into broker ctor + entry bracket data"
```

---

## Task 5: Place the partial legs (size≥2) and register the exit group

**Files:**
- Modify: `app/broker/topstepx.py`
- Modify: `tests/test_topstepx_partials.py`

This task adds `_place_partial_bracket_after_fill` and a stub-SDK test harness. It does NOT yet wire the dispatch in `_on_fill_event` (Task 6 does the fill transitions; Task 7 the size-1 path; the entry-fill branch is wired in Task 6 Step 5).

- [ ] **Step 1: Write the failing test (with the reusable stub harness)**

Append to `tests/test_topstepx_partials.py`:

```python
import asyncio
from app.broker.events import Fill


class FakeResp:
    def __init__(self, order_id, success=True):
        self.orderId = order_id
        self.success = success


class FakeOrders:
    """Records SDK order calls and returns deterministic ids."""
    def __init__(self):
        self.calls = []          # list of (method, kwargs)
        self.stop_seq = iter(["STOP1", "STOP2"])
        self.limit_seq = iter(["PART1", "TGT1", "TGT2"])
        self.modify_ok = True
        self.next_modify_returns = None  # override list for failure tests

    async def place_stop_order(self, instrument_id, side, size, price, account_id):
        oid = next(self.stop_seq)
        self.calls.append(("stop", {"oid": oid, "size": size, "price": price}))
        return FakeResp(oid)

    async def place_limit_order(self, instrument_id, side, size, price, account_id):
        oid = next(self.limit_seq)
        self.calls.append(("limit", {"oid": oid, "size": size, "price": price}))
        return FakeResp(oid)

    async def modify_order(self, order_id, limit_price=None, stop_price=None, size=None):
        self.calls.append(("modify", {"order_id": order_id, "stop_price": stop_price, "size": size}))
        if self.next_modify_returns is not None:
            return self.next_modify_returns.pop(0)
        return self.modify_ok

    async def cancel_order(self, order_id):
        self.calls.append(("cancel", {"order_id": int(order_id)}))
        return FakeResp(order_id)

    async def place_market_order(self, contract_id, side, size):
        self.calls.append(("market", {"size": size, "side": side}))
        return FakeResp("FLAT1")


class FakeSuite:
    def __init__(self):
        self.orders = FakeOrders()
        self.instrument_id = "CON.F.US.MGC.M26"


def _broker_with_stub(partial_r="1.5"):
    b = TopstepXBroker(partial_profit_r=Decimal(partial_r))
    b._suite = FakeSuite()
    b._instruments = ["MGC"]
    return b


def test_place_partial_legs_size4_places_three_orders():
    b = _broker_with_stub()
    bracket = {
        "fill_price": Decimal("100"), "stop_offset": Decimal("-1"),
        "target_offset": Decimal("5"), "close_sdk_side": 1, "size": 4,
        "account_id": 1, "partial_r": Decimal("1.5"), "entry_side": "long",
        "instrument": "MGC",
    }
    asyncio.run(b._place_partial_bracket_after_fill(bracket))
    methods = [c[0] for c in b._suite.orders.calls]
    assert methods.count("stop") == 1
    assert methods.count("limit") == 2  # partial + final target
    # Group registered under all three leg ids.
    assert len(set(b._exit_groups.values())) == 1   # one shared group object
    assert "STOP1" in b._exit_groups and "PART1" in b._exit_groups and "TGT1" in b._exit_groups
    g = b._exit_groups["STOP1"]
    assert g["partial_size"] == 2 and g["remaining_size"] == 2
    assert g["be_price"] == Decimal("100")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -k place_partial -v`
Expected: FAIL — `_place_partial_bracket_after_fill` not defined.

- [ ] **Step 3: Implement `_place_partial_bracket_after_fill`**

In `app/broker/topstepx.py`, add this method to `TopstepXBroker` immediately AFTER `_place_bracket_after_fill` (after line 617):

```python
    async def _place_partial_bracket_after_fill(self, bracket: dict) -> None:
        """Partials path: place stop + partial-target + final-target (size>=2),
        or stop + target + arm a BE-watch (size==1). Registers an exit group so
        _handle_group_fill can drive the partial->BE transition.

        Stop is placed FIRST so the whole position is protected before anything
        else. Mirrors _place_bracket_after_fill's fill-price fallback.
        """
        fill_price = bracket["fill_price"]
        if not fill_price or fill_price == Decimal("0"):
            try:
                positions = await self.get_positions()
                if positions:
                    fill_price = positions[0].average_price
            except Exception:
                log.exception("_place_partial_bracket_after_fill: could not get averagePrice")
            if not fill_price or fill_price == Decimal("0"):
                log.error("_place_partial_bracket_after_fill: fill_price still zero — falling back to plain bracket")
                await self._place_bracket_after_fill(bracket)
                return

        stop = fill_price + bracket["stop_offset"]
        target = fill_price + bracket["target_offset"]
        close_sdk_side = bracket["close_sdk_side"]
        size = bracket["size"]
        account_id = bracket["account_id"]
        instrument = bracket["instrument"]
        entry_side = bracket["entry_side"]

        plan = _partial_plan(fill_price, stop, size, bracket["partial_r"])
        if plan is None:
            await self._place_bracket_after_fill(bracket)
            return

        # 1) Stop (full size) FIRST.
        stop_id = await self._place_stop(close_sdk_side, size, stop, account_id)
        if stop_id is None:
            log.error("_place_partial_bracket_after_fill: stop placement failed — position UNPROTECTED")
            return

        # 2) Final target at remaining size.
        target_id = await self._place_limit(close_sdk_side, plan.remaining_size, target, account_id)

        # 3) Partial-target leg (size>=2 only).
        partial_id = None
        if plan.partial_size > 0:
            partial_id = await self._place_limit(close_sdk_side, plan.partial_size, plan.partial_price, account_id)
            if partial_id is None:
                # Degrade to a full-size 2-leg bracket: bump target back to full size.
                log.error("partial-target placement failed — degrading to plain bracket")
                if target_id is not None:
                    await self._cancel_order(target_id)
                target_id = await self._place_limit(close_sdk_side, size, target, account_id)
                plan = None  # signal: no partial this trade

        if target_id is None:
            log.error("_place_partial_bracket_after_fill: target placement failed")
            return

        group = {
            "instrument": instrument,
            "entry_price": fill_price,
            "entry_side": entry_side,
            "stop_id": stop_id,
            "partial_id": partial_id,
            "target_id": target_id,
            "be_price": fill_price,
            "partial_size": plan.partial_size if plan else 0,
            "remaining_size": plan.remaining_size if plan else size,
            "partial_filled": False,
        }
        for oid in (stop_id, partial_id, target_id):
            if oid is not None:
                self._exit_groups[oid] = group
        log.info(
            "Partial group registered: stop=%s partial=%s target=%s entry=%s side=%s",
            stop_id, partial_id, target_id, fill_price, entry_side,
        )

        # size==1: no partial leg — arm a BE-watch on the quote stream.
        if plan and plan.partial_size == 0:
            self._be_watches[instrument] = {
                "instrument": instrument,
                "side": entry_side,
                "trigger_price": plan.partial_price,
                "be_price": plan.be_price,
                "stop_id": stop_id,
                "armed": True,
            }

        # Re-process any leg fill that arrived before the group was registered.
        for oid in (stop_id, partial_id, target_id):
            if oid is None:
                continue
            early = self._early_fills.pop(oid, None)
            if early is not None:
                log.info("Replaying early group-leg fill: order=%s", oid)
                asyncio.create_task(self._handle_group_fill(early))

    async def _place_stop(self, close_sdk_side, size, price, account_id) -> str | None:
        try:
            resp = await self._suite.orders.place_stop_order(
                self._suite.instrument_id, close_sdk_side, size, float(price), account_id)
            if getattr(resp, "success", False):
                oid = str(resp.orderId)
                log.info("Stop placed: order=%s @ %s size=%d", oid, price, size)
                return oid
            log.error("Stop order rejected: price=%s resp=%s", price, resp)
        except Exception:
            log.exception("_place_stop failed")
        return None

    async def _place_limit(self, close_sdk_side, size, price, account_id) -> str | None:
        try:
            resp = await self._suite.orders.place_limit_order(
                self._suite.instrument_id, close_sdk_side, size, float(price), account_id)
            if getattr(resp, "success", False):
                oid = str(resp.orderId)
                log.info("Limit (exit) placed: order=%s @ %s size=%d", oid, price, size)
                return oid
            log.error("Limit (exit) rejected: price=%s resp=%s", price, resp)
        except Exception:
            log.exception("_place_limit failed")
        return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -k place_partial -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/broker/topstepx.py tests/test_topstepx_partials.py
git commit -m "feat: place partial/final/stop legs + register exit group (size>=2)"
```

---

## Task 6: Group fill transitions + dispatch in `_on_fill_event`

**Files:**
- Modify: `app/broker/topstepx.py`
- Modify: `tests/test_topstepx_partials.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_topstepx_partials.py`:

```python
def _exit_fill(order_id, price, instrument="CON.F.US.MGC.M26", side="short", size=2):
    from datetime import datetime, timezone
    return Fill(
        ts=datetime(2026, 5, 26, 12, 0, tzinfo=timezone.utc),
        instrument=instrument, side=side, fill_price=Decimal(str(price)),
        size=size, is_entry=False, realized_pnl_delta=Decimal("0"),
        contracts_delta=-size, broker_order_id=order_id,
    )


def _register_group(b):
    """Place a size-4 long group so we can fire fills at its legs."""
    bracket = {
        "fill_price": Decimal("100"), "stop_offset": Decimal("-1"),
        "target_offset": Decimal("5"), "close_sdk_side": 1, "size": 4,
        "account_id": 1, "partial_r": Decimal("1.5"), "entry_side": "long",
        "instrument": "MGC",
    }
    asyncio.run(b._place_partial_bracket_after_fill(bracket))
    return b


def test_partial_fill_moves_stop_to_be_and_resizes():
    b = _register_group(_broker_with_stub())
    b._suite.orders.calls.clear()
    asyncio.run(b._handle_group_fill(_exit_fill("PART1", "101.5", size=2)))
    modifies = [c for c in b._suite.orders.calls if c[0] == "modify"]
    assert len(modifies) == 1
    assert modifies[0][1]["stop_price"] == 100.0   # BE
    assert modifies[0][1]["size"] == 2             # remaining
    g = b._exit_groups["STOP1"]
    assert g["partial_filled"] is True
    assert "PART1" not in b._exit_groups   # partial leg consumed


def test_final_target_after_partial_cancels_stop():
    b = _register_group(_broker_with_stub())
    asyncio.run(b._handle_group_fill(_exit_fill("PART1", "101.5", size=2)))
    b._suite.orders.calls.clear()
    asyncio.run(b._handle_group_fill(_exit_fill("TGT1", "105", size=2)))
    cancels = [c for c in b._suite.orders.calls if c[0] == "cancel"]
    assert any(c[1]["order_id"] == int("STOP1".strip("STOP") or 1) or True for c in cancels)  # stop cancelled
    assert b._exit_groups == {}  # group cleared


def test_stop_before_partial_cancels_both_targets():
    b = _register_group(_broker_with_stub())
    b._suite.orders.calls.clear()
    asyncio.run(b._handle_group_fill(_exit_fill("STOP1", "99", side="short", size=4)))
    cancels = [c for c in b._suite.orders.calls if c[0] == "cancel"]
    assert len(cancels) == 2   # partial + final both cancelled
    assert b._exit_groups == {}
```

NOTE: `cancel_order` in the stub is called with `int(order_id)`; ids like "PART1" aren't ints. Update the stub's `cancel_order` to record the raw id instead. In `FakeOrders.cancel_order`, change the recorded value to `{"order_id": order_id}` (raw string) and the assertions above compare against raw ids. Adjust `test_final_target_after_partial_cancels_stop` to: `assert any(c[1]["order_id"] == "STOP1" for c in cancels)`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -k "partial_fill or final_target or stop_before" -v`
Expected: FAIL — `_handle_group_fill` not defined.

- [ ] **Step 3: Fix the stub's cancel_order to record raw id**

In `tests/test_topstepx_partials.py`, in `FakeOrders.cancel_order`, change the recorded dict to use the raw id:

```python
    async def cancel_order(self, order_id):
        self.calls.append(("cancel", {"order_id": order_id}))
        return FakeResp(order_id)
```

And in `test_final_target_after_partial_cancels_stop` replace the cancel assertion with:

```python
    assert any(c[1]["order_id"] == "STOP1" for c in cancels)
    assert b._exit_groups == {}
```

- [ ] **Step 4: Implement `_handle_group_fill`**

In `app/broker/topstepx.py`, add to `TopstepXBroker` after `_place_partial_bracket_after_fill`:

```python
    async def _handle_group_fill(self, fill: Fill) -> None:
        """Drive the partial/BE state machine when an exit-group leg fills.

        Emits a corrected EXIT Fill (is_entry=False, real P&L) to all handlers so
        the engine/journal/CSV record it, then performs the OCO/modify actions.
        """
        order_id = fill.broker_order_id
        group = self._exit_groups.get(order_id)
        if group is None:
            log.warning("_handle_group_fill: %s not in any group — ignoring", order_id)
            return

        pv = _point_value(fill.instrument)
        entry_price = group["entry_price"]
        entry_side = group["entry_side"]

        def _pnl(exit_price: Decimal, qty: int) -> Decimal:
            if entry_side == "long":
                return (exit_price - entry_price) * qty * pv
            return (entry_price - exit_price) * qty * pv

        is_partial = (order_id == group["partial_id"])
        is_stop = (order_id == group["stop_id"])
        is_target = (order_id == group["target_id"])

        if is_partial:
            # Scale-out filled → move stop to BE + resize to remaining.
            qty = group["partial_size"]
            await self._emit_group_exit(fill, _pnl(fill.fill_price, qty), qty, is_stop=False)
            group["partial_filled"] = True
            del self._exit_groups[order_id]   # partial leg is one-shot
            group["partial_id"] = None
            await self._modify_stop_to_be(group)
            return

        if is_stop:
            qty = group["remaining_size"] if group["partial_filled"] else (
                group["partial_size"] + group["remaining_size"])
            await self._emit_group_exit(fill, _pnl(fill.fill_price, qty), qty, is_stop=True)
            await self._cancel_group_siblings(group, filled_id=order_id)
            self._clear_group(group)
            return

        if is_target:
            qty = group["remaining_size"]
            await self._emit_group_exit(fill, _pnl(fill.fill_price, qty), qty, is_stop=False)
            await self._cancel_group_siblings(group, filled_id=order_id)
            self._clear_group(group)
            return

    async def _emit_group_exit(self, fill: Fill, pnl: Decimal, qty: int, is_stop: bool) -> None:
        """Fan out a corrected EXIT fill for a group leg."""
        corrected = Fill(
            ts=fill.ts, instrument=fill.instrument, side=fill.side,
            fill_price=fill.fill_price, size=qty, is_entry=False,
            realized_pnl_delta=pnl,
            contracts_delta=(-qty if fill.side == "short" else qty),
            broker_order_id=fill.broker_order_id, is_stop=is_stop,
        )
        log.info("Group exit: order=%s pnl=%s qty=%d is_stop=%s",
                 fill.broker_order_id, pnl, qty, is_stop)
        await self._fanout(self._fill_handlers, corrected)
        await self._emit_equity_snapshot(corrected.ts)

    async def _cancel_group_siblings(self, group: dict, filled_id: str) -> None:
        """Cancel every still-live leg in the group except the one that filled."""
        for key in ("stop_id", "partial_id", "target_id"):
            oid = group.get(key)
            if oid is not None and oid != filled_id and oid in self._exit_groups:
                asyncio.create_task(self._cancel_order(oid))

    def _clear_group(self, group: dict) -> None:
        for key in ("stop_id", "partial_id", "target_id"):
            oid = group.get(key)
            if oid is not None:
                self._exit_groups.pop(oid, None)
```

NOTE: `_modify_stop_to_be` is implemented in Task 8. For this task, add a temporary minimal version so the partial test passes; Task 8 replaces it with the failure ladder:

```python
    async def _modify_stop_to_be(self, group: dict) -> None:
        await self._suite.orders.modify_order(
            order_id=group["stop_id"], stop_price=float(group["be_price"]),
            size=group["remaining_size"])
```

- [ ] **Step 5: Wire dispatch in `_on_fill_event` and the entry-fill branch**

In `app/broker/topstepx.py`, inside `_on_fill_event` (in `subscribe`), find the entry-fill branch `if order_id and order_id in self._pending_brackets:` (line 1077). It currently always calls `_place_bracket_after_fill`. Change the body that creates the task to choose the partials path:

Replace:
```python
                    asyncio.create_task(self._place_bracket_after_fill(bracket_data))
```
with:
```python
                    if bracket_data.get("partial_r", Decimal("0")) > 0:
                        asyncio.create_task(self._place_partial_bracket_after_fill(bracket_data))
                    else:
                        asyncio.create_task(self._place_bracket_after_fill(bracket_data))
```

Then add a new dispatch branch for group exits. After the `elif order_id and order_id in self._exit_pairs:` block (ends ~line 1119), add:

```python
                elif order_id and order_id in self._exit_groups:
                    await self._handle_group_fill(fill)
                    return
```

Also apply the same partials-path choice in `place_market_bracket`'s early-fill replay (line 406): replace `asyncio.create_task(self._place_bracket_after_fill(bracket_data))` with the same `if bracket_data.get("partial_r", Decimal("0")) > 0:` dispatch.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/broker/topstepx.py tests/test_topstepx_partials.py
git commit -m "feat: exit-group fill transitions + dispatch (partial->BE, OCO)"
```

---

## Task 7: Size-1 break-even watch via the quote stream

**Files:**
- Modify: `app/broker/topstepx.py`
- Modify: `tests/test_topstepx_partials.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_topstepx_partials.py`:

```python
def _register_1lot(b, side="long"):
    stop_off = Decimal("-1") if side == "long" else Decimal("1")
    tgt_off = Decimal("5") if side == "long" else Decimal("-5")
    bracket = {
        "fill_price": Decimal("100"), "stop_offset": stop_off,
        "target_offset": tgt_off, "close_sdk_side": 1 if side == "long" else 0,
        "size": 1, "account_id": 1, "partial_r": Decimal("1.5"),
        "entry_side": side, "instrument": "MGC",
    }
    asyncio.run(b._place_partial_bracket_after_fill(bracket))
    return b


def test_1lot_arms_be_watch_no_partial_leg():
    b = _register_1lot(_broker_with_stub())
    methods = [c[0] for c in b._suite.orders.calls]
    assert methods.count("limit") == 1   # only the final target, no partial leg
    assert "MGC" in b._be_watches
    assert b._be_watches["MGC"]["trigger_price"] == Decimal("101.5")


def test_1lot_be_watch_moves_stop_when_price_crosses():
    b = _register_1lot(_broker_with_stub())
    b._suite.orders.calls.clear()
    # Price below trigger → no move.
    asyncio.run(b._maybe_move_stop_to_be("MGC", Decimal("101.0")))
    assert not any(c[0] == "modify" for c in b._suite.orders.calls)
    # Price crosses 101.5 → modify stop to BE, disarm.
    asyncio.run(b._maybe_move_stop_to_be("MGC", Decimal("101.5")))
    modifies = [c for c in b._suite.orders.calls if c[0] == "modify"]
    assert len(modifies) == 1 and modifies[0][1]["stop_price"] == 100.0
    assert b._be_watches["MGC"]["armed"] is False
    # Further crossings do nothing.
    asyncio.run(b._maybe_move_stop_to_be("MGC", Decimal("102")))
    assert len([c for c in b._suite.orders.calls if c[0] == "modify"]) == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -k "1lot" -v`
Expected: FAIL — `_maybe_move_stop_to_be` not defined.

- [ ] **Step 3: Implement `_maybe_move_stop_to_be`**

In `app/broker/topstepx.py`, add to `TopstepXBroker`:

```python
    async def _maybe_move_stop_to_be(self, instrument: str, price: Decimal) -> None:
        """1-lot BE move: when price crosses the trigger, modify the stop to BE.
        Called from the quote handler. Cheap no-op when no armed watch exists."""
        watch = self._be_watches.get(instrument)
        if watch is None or not watch["armed"]:
            return
        crossed = (
            (watch["side"] == "long" and price >= watch["trigger_price"])
            or (watch["side"] == "short" and price <= watch["trigger_price"])
        )
        if not crossed:
            return
        watch["armed"] = False
        try:
            ok = await self._suite.orders.modify_order(
                order_id=watch["stop_id"], stop_price=float(watch["be_price"]))
            if ok:
                log.info("BE move (1-lot): stop=%s -> %s", watch["stop_id"], watch["be_price"])
            else:
                log.error("BE move (1-lot) modify returned falsy for stop=%s — original stop still active", watch["stop_id"])
        except Exception:
            log.exception("BE move (1-lot) failed for %s — original stop still active", instrument)
```

- [ ] **Step 4: Call it from the quote handler**

In `app/broker/topstepx.py`, in `_on_quote_update` (inside `subscribe`), after the mid price is computed (`price = Decimal(...)` ~line 1005), add:

```python
            await self._maybe_move_stop_to_be(primary, price)
```

(`primary` is the subscribed instrument symbol already in scope in `subscribe`.)

- [ ] **Step 5: Clear the BE watch when the position closes**

In `_handle_group_fill`, in `_clear_group`, also drop any BE watch for the group's instrument. Update `_clear_group`:

```python
    def _clear_group(self, group: dict) -> None:
        for key in ("stop_id", "partial_id", "target_id"):
            oid = group.get(key)
            if oid is not None:
                self._exit_groups.pop(oid, None)
        self._be_watches.pop(group["instrument"], None)
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -v`
Expected: all PASS.

- [ ] **Step 7: Commit**

```bash
git add app/broker/topstepx.py tests/test_topstepx_partials.py
git commit -m "feat: 1-lot break-even watch on quote stream"
```

---

## Task 8: Failure ladder for the post-partial stop modify

**Files:**
- Modify: `app/broker/topstepx.py`
- Modify: `tests/test_topstepx_partials.py`

Replaces the temporary `_modify_stop_to_be` from Task 6 with the full ladder: modify → retry once → cancel-replace → flatten.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_topstepx_partials.py`:

```python
def test_modify_retries_then_cancel_replaces_on_failure():
    b = _register_group(_broker_with_stub())
    # First two modify calls fail, then cancel+replace path runs.
    b._suite.orders.next_modify_returns = [False, False]
    b._suite.orders.calls.clear()
    asyncio.run(b._handle_group_fill(_exit_fill("PART1", "101.5", size=2)))
    methods = [c[0] for c in b._suite.orders.calls]
    assert methods.count("modify") == 2          # initial + 1 retry
    assert "cancel" in methods                   # old stop cancelled
    assert methods.count("stop") == 1            # fresh BE stop placed
    # New stop id is registered in the group.
    g = next(iter(set(id(x) for x in b._exit_groups.values())), None)
    assert any(v["stop_id"] == "STOP2" for v in b._exit_groups.values())


def test_modify_and_replace_fail_triggers_flatten():
    b = _register_group(_broker_with_stub())
    b._suite.orders.next_modify_returns = [False, False]
    # Make the replacement stop placement fail too.
    b._suite.orders.stop_seq = iter(["STOP1", None])  # second place_stop -> falsy id
    b._suite.orders.calls.clear()
    asyncio.run(b._handle_group_fill(_exit_fill("PART1", "101.5", size=2)))
    methods = [c[0] for c in b._suite.orders.calls]
    assert "market" in methods   # flatten the remainder
```

NOTE: `place_stop_order` must tolerate a `None` from the id sequence in the failure test. In `FakeOrders.place_stop_order`, change to return `FakeResp(oid, success=oid is not None)` so a `None` id yields `success=False` (drives `_place_stop` to return None).

- [ ] **Step 2: Apply the stub tweak + run tests to verify they fail**

In `tests/test_topstepx_partials.py`, update `FakeOrders.place_stop_order`:

```python
    async def place_stop_order(self, instrument_id, side, size, price, account_id):
        oid = next(self.stop_seq)
        self.calls.append(("stop", {"oid": oid, "size": size, "price": price}))
        return FakeResp(oid, success=oid is not None)
```

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -k "modify_retries or modify_and_replace" -v`
Expected: FAIL — current `_modify_stop_to_be` has no retry/replace/flatten.

- [ ] **Step 3: Replace `_modify_stop_to_be` with the full ladder**

In `app/broker/topstepx.py`, replace the temporary `_modify_stop_to_be` (from Task 6) with:

```python
    async def _modify_stop_to_be(self, group: dict) -> None:
        """Move the stop to break-even and resize to remaining. The invariant
        (stop size == position size) must hold or we flatten. Ladder:
        modify -> retry once -> cancel+replace -> flatten the remainder."""
        stop_id = group["stop_id"]
        be = float(group["be_price"])
        remaining = group["remaining_size"]

        for attempt in (1, 2):
            try:
                ok = await self._suite.orders.modify_order(
                    order_id=stop_id, stop_price=be, size=remaining)
                if ok:
                    log.info("Stop moved to BE: order=%s be=%s size=%d", stop_id, be, remaining)
                    return
                log.error("modify_order returned falsy (attempt %d) for stop=%s", attempt, stop_id)
            except Exception:
                log.exception("modify_order raised (attempt %d) for stop=%s", attempt, stop_id)

        # Cancel + replace: the old stop is oversized for the now-smaller position.
        log.error("BE modify failed twice — cancel+replace stop=%s", stop_id)
        try:
            await self._cancel_order(stop_id)
        except Exception:
            log.exception("cancel of oversized stop failed: %s", stop_id)

        new_id = await self._place_stop(
            group.get("close_sdk_side", SIDE_SELL),
            remaining, group["be_price"], group.get("account_id"))
        if new_id is not None:
            # Re-register: drop old stop id, add the new one to the group.
            self._exit_groups.pop(stop_id, None)
            group["stop_id"] = new_id
            self._exit_groups[new_id] = group
            log.info("Replacement BE stop placed: order=%s size=%d", new_id, remaining)
            return

        # Last resort: flatten the remaining position so it is never naked/oversized.
        log.error("Replacement stop failed — flattening remainder of %s", group["instrument"])
        if self.notifier is not None:  # may not exist on broker; guard
            pass
        try:
            await self.flatten(group["instrument"])
        except Exception:
            log.exception("emergency flatten of remainder failed for %s", group["instrument"])
        self._clear_group(group)
```

NOTE: `_place_stop` needs `close_sdk_side` and `account_id`, which the group must now carry. In `_place_partial_bracket_after_fill` Step 3 (Task 5), add `"close_sdk_side": close_sdk_side` and `"account_id": account_id` to the `group` dict. Also remove the stray `if self.notifier is not None` guard block above if `TopstepXBroker` has no `notifier` attribute — it does not, so delete those three lines before running.

- [ ] **Step 4: Add the two group keys**

In `_place_partial_bracket_after_fill`, in the `group = {...}` dict, add:

```python
            "close_sdk_side": close_sdk_side,
            "account_id": account_id,
```

- [ ] **Step 5: Run the full test file**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py -v`
Expected: all PASS.

- [ ] **Step 6: Commit**

```bash
git add app/broker/topstepx.py tests/test_topstepx_partials.py
git commit -m "feat: BE-modify failure ladder (retry -> cancel-replace -> flatten)"
```

---

## Task 9: API expose + hot-apply

**Files:**
- Modify: `app/api/server.py`
- Modify: `tests/test_api_config.py` (create if absent)

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_config_partials.py`:

```python
from decimal import Decimal
from pathlib import Path

from fastapi.testclient import TestClient

from app.api.server import build_app
from app.bot_config import BotConfig, save_bot_config
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState


class _Broker:
    entry_mode = "market"
    partial_profit_r = Decimal("0")


def _client(tmp_path: Path):
    cfg_path = tmp_path / "bot_config.json"
    save_bot_config(BotConfig(partial_profit_r=Decimal("1.5")), cfg_path)
    app = build_app(
        risk_state=RiskState(config=fifty_k_combine()),
        reconciler=None, journal=None, static_dir=tmp_path,
        bot_config_path=cfg_path, effective_instrument="MGC",
        effective_timeframes=["1min"], mode="live", broker=_Broker(),
    )
    return TestClient(app), _Broker


def test_get_config_returns_partial_profit_r(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/api/config")
    assert r.json()["partial_profit_r"] == 1.5
```

NOTE: `build_app`'s exact required kwargs may differ; match the signature in `app/api/server.py` (some args are optional with defaults). Pass only what's required; the test asserts the field is present.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_config_partials.py -v`
Expected: FAIL — `KeyError: 'partial_profit_r'`.

- [ ] **Step 3: Add to GET /api/config**

In `app/api/server.py`, in `get_config` (the returned dict, after the `"risk_per_trade_pct"` line at 387), add:

```python
            "partial_profit_r": float(cfg.partial_profit_r),
```

- [ ] **Step 4: Hot-apply in PATCH + return it**

In `patch_config`, after the `_engine` hot-apply block (after line 403), add a broker hot-apply:

```python
        if _broker is not None and hasattr(_broker, "partial_profit_r"):
            _broker.partial_profit_r = body.partial_profit_r
```

And in the PATCH response dict, after the `"risk_per_trade_pct"` line (412), add:

```python
            "partial_profit_r": float(body.partial_profit_r),
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv\Scripts\python.exe -m pytest tests/test_api_config_partials.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/api/server.py tests/test_api_config_partials.py
git commit -m "feat: GET/PATCH /api/config expose + hot-apply partial_profit_r"
```

---

## Task 10: Frontend type + config field

**Files:**
- Modify: `frontend/src/types.ts`
- Modify: `frontend/src/components/ConfigPanel.tsx`

- [ ] **Step 1: Add the type**

In `frontend/src/types.ts`, in the `BotConfig` interface, after `risk_per_trade_pct: number` (line 35), add:

```ts
  partial_profit_r: number
```

- [ ] **Step 2: Initialize the form value**

In `frontend/src/components/ConfigPanel.tsx`, in the `setForm({...})` object, after the `risk_per_trade_pct` line (193), add:

```tsx
      partial_profit_r:     String(config.partial_profit_r ?? 0),
```

- [ ] **Step 3: Include it in the submit payload**

In the `onSave({...})` object, after the `risk_per_trade_pct` line (243), add:

```tsx
      partial_profit_r:     parseFloat(form.partial_profit_r) || 0,
```

- [ ] **Step 4: Render the field**

In `ConfigPanel.tsx`, immediately after the "Risk % Per Trade" field's closing `</div>` (line 454), add a sibling field:

```tsx
                    <div>
                      <label className="block text-[10px] tracking-wider text-dim uppercase mb-1">
                        Partial Profit (R, 0 = off)
                      </label>
                      <input
                        type="number"
                        min={0}
                        max={5}
                        step={0.25}
                        value={form.partial_profit_r ?? '0'}
                        onChange={e => set('partial_profit_r', e.target.value)}
                        className="w-full bg-bg border border-border text-ink text-xs px-2 py-1 font-mono tabular-nums focus:outline-none focus:border-accent"
                      />
                      <p className="text-[10px] text-dim/80 mt-1 leading-relaxed">
                        Take half off at this R-multiple then move the stop to break-even. For 1-contract entries, the scale-out is skipped but the stop still moves to break-even at this level. 0 disables. Hot-applied — affects the next entry.
                      </p>
                    </div>
```

- [ ] **Step 5: Build the frontend**

Run: `cd frontend; npm run build`
Expected: build succeeds, no TypeScript errors.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/types.ts frontend/src/components/ConfigPanel.tsx
git commit -m "feat: partial_profit_r field in config panel"
```

---

## Task 11: Full regression + demo-account verification

- [ ] **Step 1: Run the full Python suite**

Run: `.venv\Scripts\python.exe -m pytest tests/ -q --deselect tests/test_main.py::test_paper_mode_full_run`
Expected: all pass (the deselected test needs port 5174 free; the live bot holds it). No new failures.

- [ ] **Step 2: Confirm the disabled path is unchanged**

Run: `.venv\Scripts\python.exe -m pytest tests/test_topstepx_partials.py tests/test_bot_config_partials.py -v`
Expected: all PASS. Manually confirm: with `partial_profit_r=0`, `_on_fill_event` takes the existing `_place_bracket_after_fill` path and `_exit_groups`/`_be_watches` stay empty.

- [ ] **Step 3: Demo-account validation (HARD GATE before live — Rule 4)**

Set `partial_profit_r=1.5` in `bot_config.json`, run on the demo/eval account, and verify:
- A size≥2 entry: partial-target fills at the 1.5R level; the stop is then `modify_order`'d to break-even at the remaining size; confirm via `scripts/sdk_diagnostic.py` / exchange UI.
- A size==1 entry: the stop moves to break-even after price crosses the 1.5R level.
- **Stop size equals position size at every point** (no oversized stop) in both flows.
- Disable again (`partial_profit_r=0`) and confirm normal stop+target behavior returns.

- [ ] **Step 4: Commit any fixes from demo testing, then stop**

Do NOT enable partials on the live account until Step 3 passes cleanly on demo.

---

## Self-Review

**Spec coverage:**
- [x] half at 1.5R → BE (size≥2) → Tasks 2, 5, 6
- [x] BE-only at 1.5R (size==1) → Tasks 5, 7
- [x] Approach A (resting partial-limit + fill trigger; quote-watch for 1-lot; `modify_order`) → Tasks 5, 6, 7
- [x] disabled path byte-for-byte unchanged → Task 6 Step 5 dispatch guard; Task 11 Step 2
- [x] stop-size==position-size invariant + failure ladder → Task 8
- [x] early/duplicate fills via `_early_fills`/`_processed_fill_ids` → Task 5 Step 3 (early re-check); existing dedup unchanged
- [x] config wiring (BotConfig, main, broker ctor, GET/PATCH, types, ConfigPanel) → Tasks 3, 4, 9, 10
- [x] tests: `_partial_plan`, group transitions, BE watch, failure ladder, config round-trip, API → Tasks 2,3,6,7,8,9
- [x] demo-account hard gate → Task 11 Step 3
- [x] `modify_order` semantics verified first → Task 1

**Placeholder scan:** none — every code step shows complete code. Task 1 is intentionally a research task (no code) and is labeled as such.

**Type consistency:**
- `PartialPlan(partial_price, partial_size, remaining_size, be_price)` — defined Task 2, used Task 5.
- group dict keys (`stop_id`, `partial_id`, `target_id`, `be_price`, `partial_size`, `remaining_size`, `partial_filled`, `instrument`, `entry_price`, `entry_side`, `close_sdk_side`, `account_id`) — created Task 5 (+ Task 8 Step 4 adds the last two), read Tasks 6/7/8 consistently.
- `_partial_plan`, `_place_partial_bracket_after_fill`, `_handle_group_fill`, `_emit_group_exit`, `_cancel_group_siblings`, `_clear_group`, `_modify_stop_to_be`, `_maybe_move_stop_to_be`, `_place_stop`, `_place_limit` — names consistent across tasks.
- `partial_profit_r` — `Decimal` in Python everywhere (BotConfig, broker, engine N/A), `float` at the JSON boundary (Task 9), `number` in TS (Task 10).
```
