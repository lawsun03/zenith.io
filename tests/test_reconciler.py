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
from datetime import datetime, timezone, timedelta
from decimal import Decimal

import pytest

from app.broker.events import BrokerPosition, ExitCoverage
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


async def test_sign_flipped_position_is_drift():
    """
    Regression for the 2026-06-07 orphan: internal short 3, broker long 3.
    Same magnitude, opposite sign. The old magnitude-only comparison
    (abs(-3) == 3 == broker 3) reported NO drift, so a real orphaned/
    sign-flipped position sat open and unprotected on a live account and
    the reconciler never flattened it. Signed comparison must flag it.
    """
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    # Broker is actually LONG 3.
    await broker.place_bracket(
        instrument="MGC",
        side="long",
        size=3,
        entry=Decimal("2400"),
        stop=Decimal("2390"),
        target=Decimal("2410"),
    )
    state = fresh_state()
    # Internal thinks we are SHORT 3 — opposite sign, same magnitude.
    state.record_fill(
        realized_pnl_delta=Decimal("0"),
        contracts_delta=-3,
        ts=datetime.now(timezone.utc),
    )

    rec = Reconciler(broker, state, no_grace())
    report = await rec.tick()

    assert report.drift_detected is True
    assert report.drift_kind == "contract_count"
    assert report.broker_open_contracts == 3   # signed: long 3 = +3
    assert report.internal_open_contracts == -3
    assert state.locked_out is not None
    assert state.locked_out.code == "RECONCILE_DRIFT"
    # The orphan must be gone after the emergency flatten.
    assert await broker.get_positions() == []


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


# =====================================================================
# Exit-coverage / naked-position detection
# =====================================================================

@pytest.mark.asyncio
async def test_first_naked_tick_is_grace_no_action():
    from unittest.mock import AsyncMock, MagicMock

    pos = BrokerPosition(
        instrument="MGC", side="long", size=2,
        average_price=Decimal("2400.0"), unrealized_pnl=Decimal("0"),
    )
    # covered_stop=0: stop is missing; covered_target=2: target is present.
    # fully_covered is False (stop missing).
    cov = ExitCoverage("MGC", 2, "long", Decimal("2400.0"), covered_stop=0, covered_target=2)
    broker = MagicMock()
    broker.get_positions = AsyncMock(return_value=[pos])
    broker.account_balance = AsyncMock(return_value=Decimal("50000"))
    broker.exit_coverage = AsyncMock(return_value=cov)
    broker.place_protective_stop = AsyncMock(return_value=True)
    broker.place_protective_target = AsyncMock(return_value=True)
    broker.flatten = AsyncMock(return_value=True)

    risk = RiskState(config=fifty_k_combine())
    risk.realized_balance = Decimal("50000")
    risk.open_contracts = 2

    rec = Reconciler(
        broker=broker, risk_state=risk,
        config=ReconcilerConfig(
            grace_first_tick=False, grace_period_after_order_seconds=0,
            naked_grace_seconds=15.0,
            emergency_stop_distance={"MGC": Decimal("3.0")},
            emergency_target_r=Decimal("2.0"),
        ),
    )
    rec._first_tick_done = True
    report = await rec.tick()
    broker.place_protective_stop.assert_not_called()
    broker.flatten.assert_not_called()
    assert "MGC" in rec._naked_since
    assert report.drift_kind != "naked_position"


@pytest.mark.asyncio
async def test_naked_since_pruned_when_position_closes():
    from app.broker.events import BrokerPosition, ExitCoverage
    from app.execution.reconciler import Reconciler, ReconcilerConfig
    from app.risk.state import RiskState
    from app.risk.config import fifty_k_combine
    from unittest.mock import AsyncMock, MagicMock

    broker = MagicMock()
    broker.account_balance = AsyncMock(return_value=Decimal("50000"))
    broker.exit_coverage = AsyncMock(return_value=ExitCoverage("MGC", 2, "long", Decimal("2400.0"), 0, 2))
    risk = RiskState(config=fifty_k_combine())
    risk.realized_balance = Decimal("50000")

    rec = Reconciler(broker=broker, risk_state=risk,
                     config=ReconcilerConfig(grace_first_tick=False, grace_period_after_order_seconds=0,
                                             naked_grace_seconds=15.0))
    rec._first_tick_done = True
    # Seed a stale naked timestamp, then tick with NO open positions.
    from datetime import datetime, timezone
    rec._naked_since["MGC"] = datetime.now(timezone.utc)
    broker.get_positions = AsyncMock(return_value=[])
    risk.open_contracts = 0
    await rec.tick()
    assert "MGC" not in rec._naked_since, "stale naked entry must be pruned when position is gone"


# =====================================================================
# Escalating naked-position remediation
# =====================================================================

def _naked_reconciler_multi(instrument, avg, stop_dist, side, position_size,
                            covered_stop, covered_target,
                            stop_ok=True, target_ok=True):
    from app.broker.events import BrokerPosition, ExitCoverage
    from app.execution.reconciler import Reconciler, ReconcilerConfig
    from app.risk.state import RiskState
    from app.risk.config import fifty_k_combine
    from unittest.mock import AsyncMock, MagicMock
    from datetime import datetime, timezone, timedelta

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
    rec._naked_since[instrument] = datetime.now(timezone.utc) - timedelta(seconds=60)
    return rec, broker, cov


@pytest.mark.parametrize("instrument,avg,stop_dist,side", [
    ("MGC", Decimal("2400.0"), Decimal("3.0"), "long"),
    ("MNQ", Decimal("20000.0"), Decimal("40.0"), "short"),
    ("MES", Decimal("5300.0"), Decimal("5.0"), "long"),
])
@pytest.mark.asyncio
async def test_reattach_missing_stop_at_emergency_distance(instrument, avg, stop_dist, side):
    rec, broker, _ = _naked_reconciler_multi(
        instrument, avg, stop_dist, side, position_size=2,
        covered_stop=0, covered_target=2,
    )
    report = await rec.tick()
    broker.place_protective_stop.assert_awaited_once()
    args = broker.place_protective_stop.call_args.args
    assert args[0] == instrument
    assert args[1] == 2
    expected_stop = avg - stop_dist if side == "long" else avg + stop_dist
    assert args[2] == expected_stop
    broker.place_protective_target.assert_not_called()
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
    broker.place_protective_stop.assert_not_called()
    broker.place_protective_target.assert_awaited_once()
    args = broker.place_protective_target.call_args.args
    expected_target = (avg + Decimal("2.0") * stop_dist) if side == "long" \
        else (avg - Decimal("2.0") * stop_dist)
    assert args[2] == expected_target
    broker.flatten.assert_not_called()


@pytest.mark.asyncio
async def test_stop_reattach_failure_triggers_flatten():
    rec, broker, _ = _naked_reconciler_multi(
        "MGC", Decimal("2400.0"), Decimal("3.0"), "long", position_size=2,
        covered_stop=0, covered_target=2, stop_ok=False,
    )
    await rec.tick()
    broker.place_protective_stop.assert_awaited_once()
    broker.flatten.assert_awaited_once_with("MGC")


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
    rec.config.emergency_stop_distance = {}
    await rec.tick()
    broker.place_protective_stop.assert_not_called()
    broker.flatten.assert_awaited_once_with("MGC")


@pytest.mark.asyncio
async def test_contract_drift_takes_precedence_over_naked():
    rec, broker, _ = _naked_reconciler_multi(
        "MGC", Decimal("2400.0"), Decimal("3.0"), "long", position_size=2,
        covered_stop=0, covered_target=0,
    )
    rec.risk_state.open_contracts = 0  # internal disagrees with broker (2)
    report = await rec.tick()
    assert report.drift_kind == "contract_count"
    broker.place_protective_stop.assert_not_called()
