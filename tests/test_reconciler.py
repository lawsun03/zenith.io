"""
Reconciler tests.

Test surface:
  - Clean state: no drift, no action.
  - First-tick grace: drift on first tick is silently absorbed.
  - Contract drift after grace: flattens and locks.
  - Balance drift below tolerance: silent adjustment.
  - Balance drift above tolerance: adopts broker truth, locks.
  - Broker query failure: tick fails gracefully, no action.
  - Multiple ticks: state persists correctly between them.
  - Lifecycle: start/stop, no leaked tasks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from app.broker.events import BrokerPosition
from app.broker.paper import PaperBroker
from app.execution.reconciler import (
    ReconcileReport,
    Reconciler,
    ReconcilerConfig,
)
from app.risk.config import fifty_k_combine
from app.risk.state import LockoutReason, RiskState


def fresh_state(soft_buffer: Decimal = Decimal("500")) -> RiskState:
    """RiskState with $50K starting balance and equity seeded to match."""
    state = RiskState(config=fifty_k_combine(soft_buffer=soft_buffer))
    # Seed equity so high-water doesn't sit at 0 (fresh state defaults
    # equity_high_water to starting_balance, but _current_equity stays 0
    # until the first mark_equity).
    state.mark_equity(Decimal("50000"), datetime.now(timezone.utc))
    return state


def no_grace() -> ReconcilerConfig:
    """Config with grace disabled so the very first tick acts."""
    return ReconcilerConfig(
        interval_seconds=0.01,
        balance_tolerance=Decimal("50"),
        grace_first_tick=False,
    )


# =====================================================================
# Clean state — no drift
# =====================================================================

async def test_no_drift_no_action():
    """When everything matches, the report shows no drift and no flatten."""
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    state = fresh_state()
    rec = Reconciler(broker, state, no_grace())

    report = await rec.tick()

    assert report.drift_detected is False
    assert report.flattened is False
    assert state.locked_out is None


async def test_minor_balance_drift_adjusts_silently():
    """
    Balance off by less than tolerance — adopt broker value, no lockout.
    Tests the "tolerance" branch.
    """
    broker = PaperBroker(starting_balance=Decimal("50030"))  # off by $30
    await broker.connect()
    state = fresh_state()
    rec = Reconciler(broker, state, no_grace())

    report = await rec.tick()

    assert report.drift_detected is False
    assert state.locked_out is None
    # State adopted the broker balance.
    assert state.realized_balance == Decimal("50030")


# =====================================================================
# First-tick grace
# =====================================================================

async def test_first_tick_grace_absorbs_initial_state():
    """
    With grace enabled, first tick can find a position and not flatten.
    This is the bot-restart scenario — operator may have inherited a
    position, we shouldn't immediately blow it up.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    # Seed broker with a position the bot doesn't know about.
    await broker.place_bracket(
        instrument="MGC",
        side="long",
        size=2,
        entry=Decimal("2400"),
        stop=Decimal("2390"),
        target=Decimal("2410"),
    )
    state = fresh_state()
    # State.open_contracts is 0; broker has 2. Drift exists.

    rec = Reconciler(broker, state, ReconcilerConfig(
        interval_seconds=0.01,
        grace_first_tick=True,
    ))

    report = await rec.tick()

    assert report.drift_detected is False
    assert report.flattened is False
    assert state.locked_out is None
    assert "first tick" in report.notes


async def test_drift_after_grace_tick_triggers_flatten():
    """
    After first tick passes, subsequent drift IS treated as drift.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    state = fresh_state()
    rec = Reconciler(broker, state, ReconcilerConfig(
        interval_seconds=0.01,
        grace_first_tick=True,
    ))

    # First tick: no drift, just consumes the grace.
    await rec.tick()
    assert state.locked_out is None

    # Now create drift: open a position via broker that the state
    # doesn't know about.
    await broker.place_bracket(
        instrument="MGC",
        side="long",
        size=1,
        entry=Decimal("2400"),
        stop=Decimal("2390"),
        target=Decimal("2410"),
    )

    report = await rec.tick()

    assert report.drift_detected is True
    assert report.drift_kind == "contract_count"
    assert state.locked_out is not None
    assert state.locked_out.code == "RECONCILE_DRIFT"


# =====================================================================
# Contract drift
# =====================================================================

async def test_contract_drift_locks_and_flattens():
    """
    Internal says 0 contracts. Broker says 3. This means we missed
    a fill or someone placed an order behind our back. Flatten and lock.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    # Three open contracts the bot doesn't know about.
    await broker.place_bracket(
        instrument="MGC",
        side="long",
        size=3,
        entry=Decimal("2400"),
        stop=Decimal("2390"),
        target=Decimal("2410"),
    )

    state = fresh_state()
    # Mismatch: state has 0 open contracts, broker has 3.

    rec = Reconciler(broker, state, no_grace())
    report = await rec.tick()

    assert report.drift_detected is True
    assert report.drift_kind == "contract_count"
    assert report.broker_open_contracts == 3
    assert report.internal_open_contracts == 0
    assert state.locked_out is not None
    assert state.locked_out.code == "RECONCILE_DRIFT"

    # Broker should be flat after the emergency flatten.
    positions_after = await broker.get_positions()
    assert positions_after == []


async def test_contract_drift_other_direction():
    """
    Internal says 2 contracts; broker says 0. Equally bad — maybe a
    fill we got was a phantom or duplicate. Same response: lock out.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    state = fresh_state()
    # State thinks we have 2 contracts; broker has none.
    state.record_fill(
        realized_pnl_delta=Decimal("0"),
        contracts_delta=2,
        ts=datetime.now(timezone.utc),
    )

    rec = Reconciler(broker, state, no_grace())
    report = await rec.tick()

    assert report.drift_detected is True
    assert report.drift_kind == "contract_count"
    assert state.locked_out is not None


# =====================================================================
# Balance drift
# =====================================================================

async def test_large_balance_drift_locks_and_adopts_broker_truth():
    """
    Balance off by more than tolerance and no positions to explain it.
    Almost certainly a missed fill. Adopt broker, lock out.
    """
    broker = PaperBroker(starting_balance=Decimal("50300"))  # +$300 vs internal
    await broker.connect()
    state = fresh_state()  # state thinks balance is $50,000

    rec = Reconciler(broker, state, ReconcilerConfig(
        interval_seconds=0.01,
        balance_tolerance=Decimal("50"),
        grace_first_tick=False,
    ))

    report = await rec.tick()

    assert report.drift_detected is True
    assert report.drift_kind == "balance"
    assert state.realized_balance == Decimal("50300")
    assert state.locked_out is not None
    assert state.locked_out.code == "RECONCILE_DRIFT"


async def test_lockout_message_explains_drift():
    """The lockout message should be specific enough to debug from."""
    broker = PaperBroker(starting_balance=Decimal("49000"))  # -$1,000
    await broker.connect()
    state = fresh_state()

    rec = Reconciler(broker, state, no_grace())
    await rec.tick()

    msg = state.locked_out.message
    assert "Balance drift" in msg or "balance" in msg.lower()
    assert "tolerance" in msg.lower() or "manual review" in msg.lower()


# =====================================================================
# Broker query failures
# =====================================================================

class FailingBroker:
    """Mock broker whose queries raise. For testing graceful failure."""

    def __init__(self) -> None:
        self.fail_count = 0

    async def get_positions(self):
        self.fail_count += 1
        raise RuntimeError("simulated network error")

    async def account_balance(self):
        raise RuntimeError("simulated network error")

    # Stubs for the rest of the protocol; reconciler only calls the two above.
    async def connect(self): pass
    async def disconnect(self): pass
    async def place_bracket(self, *a, **kw): raise NotImplementedError
    async def flatten(self, *a, **kw): return True
    async def cancel_all(self, *a, **kw): return 0
    def on_bar(self, h): pass
    def on_fill(self, h): pass
    def on_equity(self, h): pass
    async def subscribe(self, *a, **kw): pass


async def test_broker_query_failure_does_not_crash():
    """
    If the broker fails to respond, the tick must complete with a
    sentinel report and NO action — we cannot make safety decisions
    on partial data.
    """
    broker = FailingBroker()
    state = fresh_state()
    rec = Reconciler(broker, state, no_grace())

    report = await rec.tick()

    assert "broker query failed" in report.notes
    assert report.flattened is False
    assert state.locked_out is None  # no false lockout


async def test_broker_failure_then_recovery():
    """
    A transient broker failure followed by a successful tick must
    behave normally — no stale state, no spurious lockouts.
    """
    # Custom broker that fails the first call, then works.
    class FlakyBroker(PaperBroker):
        def __init__(self):
            super().__init__(starting_balance=Decimal("50000"))
            self._calls = 0

        async def get_positions(self):
            self._calls += 1
            if self._calls == 1:
                raise RuntimeError("network blip")
            return await super().get_positions()

    broker = FlakyBroker()
    await broker.connect()
    state = fresh_state()
    rec = Reconciler(broker, state, no_grace())

    failed = await rec.tick()
    assert "broker query failed" in failed.notes

    ok = await rec.tick()
    assert ok.drift_detected is False
    assert state.locked_out is None


# =====================================================================
# Lifecycle
# =====================================================================

async def test_start_and_stop_no_leaked_tasks():
    """The background task must terminate cleanly on stop()."""
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    state = fresh_state()
    rec = Reconciler(broker, state, ReconcilerConfig(
        interval_seconds=0.05,
        grace_first_tick=True,
    ))

    await rec.start()
    # Let the loop run a couple of ticks.
    await asyncio.sleep(0.15)
    assert rec.last_report is not None  # at least one tick happened

    await rec.stop()
    assert rec._task is None


async def test_double_start_is_idempotent():
    """Calling start twice does not spawn a second loop."""
    broker = PaperBroker()
    await broker.connect()
    state = fresh_state()
    rec = Reconciler(broker, state, ReconcilerConfig(interval_seconds=0.01))

    await rec.start()
    first_task = rec._task
    await rec.start()
    assert rec._task is first_task

    await rec.stop()


# =====================================================================
# Order grace period — fill-latency race condition fix
# =====================================================================

async def test_order_grace_period_suppresses_false_positive():
    """
    Regression test for the 09:18 incident: reconciler ticked while
    broker already showed the position open but RiskState.open_contracts
    was still 0 (fill WebSocket event hadn't arrived yet).

    After notify_order_placed(), the reconciler must NOT flatten during
    the grace window even though broker=1 and internal=0.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    # Open a position on the broker side (simulates the exchange fill that
    # the WebSocket hasn't echoed back yet).
    await broker.place_bracket(
        instrument="MGC",
        side="short",
        size=1,
        entry=Decimal("2400"),
        stop=Decimal("2410"),
        target=Decimal("2390"),
    )
    state = fresh_state()
    # state.open_contracts is 0; broker shows 1 — this IS the race window.

    rec = Reconciler(broker, state, ReconcilerConfig(
        interval_seconds=0.01,
        grace_first_tick=False,
        grace_period_after_order_seconds=15.0,
    ))

    # Simulate the engine calling notify_order_placed() right after
    # broker.place_bracket() returned.
    rec.notify_order_placed()

    report = await rec.tick()

    # Must NOT have flattened or locked out during the grace window.
    assert report.drift_detected is False
    assert report.flattened is False
    assert state.locked_out is None
    assert "grace period" in report.notes
    # The broker position must still be open.
    positions = await broker.get_positions()
    assert len(positions) == 1


async def test_order_grace_period_expiry_catches_genuine_drift():
    """
    After the grace period expires, the same mismatch IS treated as
    genuine drift. A real orphaned fill (WebSocket permanently dropped)
    must still be caught, just with a short delay.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    await broker.place_bracket(
        instrument="MGC",
        side="long",
        size=1,
        entry=Decimal("2400"),
        stop=Decimal("2390"),
        target=Decimal("2410"),
    )
    state = fresh_state()

    rec = Reconciler(broker, state, ReconcilerConfig(
        interval_seconds=0.01,
        grace_first_tick=False,
        # Very short grace — expires before we tick again.
        grace_period_after_order_seconds=0.01,
    ))

    rec.notify_order_placed()

    # Wait for the grace period to expire.
    import asyncio as _asyncio
    await _asyncio.sleep(0.05)

    report = await rec.tick()

    # Grace expired — genuine drift must be caught and flattened.
    assert report.drift_detected is True
    assert report.drift_kind == "contract_count"
    assert state.locked_out is not None
    assert state.locked_out.code == "RECONCILE_DRIFT"


async def test_order_grace_period_disabled_when_zero():
    """
    Setting grace_period_after_order_seconds=0 opts out entirely.
    Confirms the safety valve can be disabled if needed.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    await broker.place_bracket(
        instrument="MGC",
        side="long",
        size=1,
        entry=Decimal("2400"),
        stop=Decimal("2390"),
        target=Decimal("2410"),
    )
    state = fresh_state()

    rec = Reconciler(broker, state, ReconcilerConfig(
        interval_seconds=0.01,
        grace_first_tick=False,
        grace_period_after_order_seconds=0,
    ))

    rec.notify_order_placed()

    report = await rec.tick()

    # Grace is disabled — drift must be caught immediately.
    assert report.drift_detected is True
    assert state.locked_out is not None


async def test_failed_tick_does_not_kill_loop():
    """
    Even if a tick raises an *unexpected* exception (not a broker
    query failure), the loop must keep running.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    state = fresh_state()
    rec = Reconciler(broker, state, ReconcilerConfig(
        interval_seconds=0.05,
        grace_first_tick=True,
    ))

    # Monkey-patch tick() to raise on first call, succeed after.
    original_tick = rec.tick
    call_count = 0

    async def flaky_tick():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("unexpected!")
        return await original_tick()

    rec.tick = flaky_tick

    await rec.start()
    await asyncio.sleep(0.20)  # enough for several ticks
    await rec.stop()

    # We should have made it past the failure.
    assert call_count >= 2
