"""
Dashboard API tests.

Use httpx for the REST endpoints (FastAPI's TestClient is sync; we want
async to share state cleanly with the journal). Use FastAPI's
WebSocketTestSession for the WS endpoint.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api.journal import Journal
from app.api.server import build_app
from app.broker.events import Fill
from app.execution.engine import OrderOutcome
from app.execution.reconciler import (
    ReconcileReport,
    Reconciler,
    ReconcilerConfig,
)
from app.broker.paper import PaperBroker
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState
from app.strategy.composer import Signal


def fresh_state() -> RiskState:
    state = RiskState(config=fifty_k_combine(soft_buffer=Decimal("500")))
    state.mark_equity(Decimal("50000"), datetime.now(timezone.utc))
    return state


async def make_app_with_state():
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    state = fresh_state()
    journal = Journal()
    reconciler = Reconciler(broker, state, ReconcilerConfig(
        interval_seconds=10,  # not started; we just need .last_report
        grace_first_tick=False,
    ))
    app = build_app(risk_state=state, reconciler=reconciler, journal=journal)
    return app, state, journal, reconciler, broker


# =====================================================================
# /api/status
# =====================================================================

async def test_status_endpoint_shape():
    app, state, _, _, _ = await make_app_with_state()
    client = TestClient(app)
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.json()
    assert "account" in data
    assert "equity" in data
    assert "limits" in data
    assert data["limits"]["mll_floor"] == "48000"
    assert data["lockout"] is None


async def test_status_reflects_lockout():
    app, state, _, _, _ = await make_app_with_state()
    state.mark_equity(Decimal("47999"), datetime.now(timezone.utc))
    assert state.locked_out is not None

    client = TestClient(app)
    r = client.get("/api/status")
    data = r.json()
    assert data["lockout"] is not None
    assert data["lockout"]["code"] == "MLL_BREACH"


async def test_status_decimals_serialized_as_strings():
    """Critical: floats lose precision. Buffer to MLL must round-trip."""
    app, state, _, _, _ = await make_app_with_state()
    client = TestClient(app)
    data = client.get("/api/status").json()
    assert isinstance(data["limits"]["buffer_to_mll"], str)
    # Round-trip back to Decimal cleanly.
    assert Decimal(data["limits"]["buffer_to_mll"]) == state.buffer_to_mll


# =====================================================================
# Journal endpoints
# =====================================================================

def make_signal() -> Signal:
    return Signal(
        instrument="MGC",
        side="short",
        entry=Decimal("2400.5"),
        stop=Decimal("2403.8"),
        target=Decimal("2393.9"),
        created_at=datetime.now(timezone.utc),
        killzone="NY AM",
        sweep_pattern="B_one_bar",
        sweep_extreme=Decimal("2403.5"),
        fvg_low=Decimal("2400"),
        fvg_high=Decimal("2400.5"),
        rationale="NY AM: B_one_bar sweep of high @ 2403, bearish displacement",
    )


async def test_signals_endpoint_returns_recorded_entries():
    app, state, journal, _, _ = await make_app_with_state()
    sig = make_signal()
    out = OrderOutcome(placed=True, reason="allowed", allowed_size=1, broker_order_id="x1")
    await journal.record_signal(sig, out)

    client = TestClient(app)
    r = client.get("/api/signals")
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["payload"]["side"] == "short"
    assert items[0]["payload"]["outcome"]["placed"] is True


async def test_signals_endpoint_returns_newest_first():
    app, state, journal, _, _ = await make_app_with_state()
    for i in range(3):
        sig = make_signal()
        await journal.record_signal(
            sig,
            OrderOutcome(placed=True, reason="allowed",
                         allowed_size=1, broker_order_id=f"x{i}"),
        )
        await asyncio.sleep(0.001)  # ensure distinct ts

    client = TestClient(app)
    items = client.get("/api/signals").json()["items"]
    assert len(items) == 3
    # Newest first by broker_order_id.
    ids = [it["payload"]["outcome"]["broker_order_id"] for it in items]
    assert ids == ["x2", "x1", "x0"]


async def test_fills_endpoint():
    app, state, journal, _, _ = await make_app_with_state()
    fill = Fill(
        ts=datetime.now(timezone.utc),
        instrument="MGC",
        side="short",
        fill_price=Decimal("2400.5"),
        size=1,
        is_entry=True,
        realized_pnl_delta=Decimal("0"),
        contracts_delta=-1,
        broker_order_id="ENTRY-1",
    )
    await journal.record_fill(fill)

    client = TestClient(app)
    items = client.get("/api/fills").json()["items"]
    assert len(items) == 1
    assert items[0]["payload"]["broker_order_id"] == "ENTRY-1"


async def test_reconciles_endpoint():
    app, state, journal, _, _ = await make_app_with_state()
    report = ReconcileReport(
        ts=datetime.now(timezone.utc),
        broker_open_contracts=0,
        broker_balance=Decimal("50000"),
        internal_open_contracts=0,
        internal_balance=Decimal("50000"),
        drift_detected=False,
        drift_kind=None,
        flattened=False,
        notes="all clear",
    )
    await journal.record_reconcile(report)
    client = TestClient(app)
    items = client.get("/api/reconciles").json()["items"]
    assert len(items) == 1
    assert items[0]["payload"]["drift_detected"] is False


# =====================================================================
# WebSocket
# =====================================================================

async def test_websocket_sends_initial_snapshot():
    app, state, journal, _, _ = await make_app_with_state()
    client = TestClient(app)
    with client.websocket_connect("/api/stream") as ws:
        msg = json.loads(ws.receive_text())
        assert msg["kind"] == "snapshot"
        assert "limits" in msg["payload"]


async def test_websocket_receives_new_signal():
    """Connect a WebSocket, then record a signal; client should see it."""
    app, state, journal, _, _ = await make_app_with_state()
    client = TestClient(app)
    with client.websocket_connect("/api/stream") as ws:
        # Drain the snapshot.
        snapshot = json.loads(ws.receive_text())
        assert snapshot["kind"] == "snapshot"

        # Record a signal — TestClient runs the WS in the same loop, so
        # we can do this by hand via the journal's publish path. But the
        # journal needs to be the SAME instance the app uses. Above
        # make_app_with_state returns it, but the WS's subscriber is set
        # up on connect. Just publish:
        sig = make_signal()
        out = OrderOutcome(placed=True, reason="allowed",
                           allowed_size=1, broker_order_id="ws-test")
        await journal.record_signal(sig, out)

        # Receive the new entry.
        msg = json.loads(ws.receive_text())
        assert msg["kind"] == "signal"
        assert msg["payload"]["outcome"]["broker_order_id"] == "ws-test"


# =====================================================================
# Journal limits
# =====================================================================

async def test_journal_caps_at_max_signals():
    j = Journal(max_signals=3)
    sig = make_signal()
    out = OrderOutcome(placed=True, reason="allowed", allowed_size=1, broker_order_id="x")
    for _ in range(5):
        await j.record_signal(sig, out)
    items = await j.recent_signals(limit=10)
    assert len(items) == 3


async def test_journal_subscriber_drops_old_under_backpressure():
    """If a subscriber doesn't drain, new entries displace old."""
    j = Journal()
    q = j.subscribe()
    sig = make_signal()
    out = OrderOutcome(placed=True, reason="allowed", allowed_size=1, broker_order_id="x")

    # Fill the queue past its limit (100).
    for _ in range(150):
        await j.record_signal(sig, out)

    # Queue size must be capped at maxsize.
    assert q.qsize() <= 100
