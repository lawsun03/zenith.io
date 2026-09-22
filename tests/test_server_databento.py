"""Tests for POST /api/databento/fetch endpoint."""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.api.journal import Journal
from app.api.server import build_app
from app.sim.paper import PaperBroker
from app.execution.reconciler import Reconciler, ReconcilerConfig
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState


def _fresh_state() -> RiskState:
    state = RiskState(config=fifty_k_combine(soft_buffer=Decimal("500")))
    state.mark_equity(Decimal("50000"), datetime.now(timezone.utc))
    return state


async def _make_client() -> TestClient:
    broker = PaperBroker(starting_balance=Decimal("50000"))
    await broker.connect()
    state = _fresh_state()
    journal = Journal()
    reconciler = Reconciler(broker, state, ReconcilerConfig(
        interval_seconds=10,
        grace_first_tick=False,
    ))
    app = build_app(risk_state=state, reconciler=reconciler, journal=journal)
    return TestClient(app)


async def test_databento_fetch_missing_api_key(monkeypatch):
    """Returns ok=false immediately when DATABENTO_API_KEY is absent."""
    client = await _make_client()
    monkeypatch.delenv("DATABENTO_API_KEY", raising=False)
    resp = client.post(
        "/api/databento/fetch",
        json={"start": "2026-01-01", "end": "2026-05-29", "symbol": "MGC", "dry_run": False},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert "DATABENTO_API_KEY" in body["reason"]


async def test_databento_fetch_dry_run_returns_estimate(monkeypatch):
    """dry_run=true calls script with --estimate-only and returns cost without fetching."""
    client = await _make_client()
    monkeypatch.setenv("DATABENTO_API_KEY", "fake-key")
    fake_result = MagicMock()
    fake_result.returncode = 0
    fake_result.stdout = "[estimate] ohlcv-1m GC.c.0 2026-01-01T00:00:00 -> 2026-05-29T00:00:00: $0.4200\n"
    fake_result.stderr = ""

    with patch("app.api.server.subprocess.run", return_value=fake_result) as mock_run:
        resp = client.post(
            "/api/databento/fetch",
            json={"start": "2026-01-01", "end": "2026-05-29", "symbol": "MGC", "dry_run": True},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert abs(body["cost_estimate"] - 0.42) < 0.001
    assert body["days_fetched"] == 0
    args_used = mock_run.call_args[0][0]
    assert "--estimate-only" in args_used


async def test_databento_fetch_cache_hit_skips_download(monkeypatch, tmp_path):
    """If CSV already covers the requested end date, skip the download."""
    client = await _make_client()
    monkeypatch.setenv("DATABENTO_API_KEY", "fake-key")
    csv_path = tmp_path / "bars_MGC.csv"
    # The cache must COVER the requested range: the fetch script's backfill
    # logic (correctly) re-fetches when the file starts after `start`.
    csv_path.write_text(
        "timestamp,open,high,low,close,volume\n"
        "2025-12-31T23:59:00+00:00,100,101,99,100,10\n"
        "2026-05-29T23:59:00+00:00,100,101,99,100,10\n"
    )
    with patch("app.api.server._bars_csv_path", return_value=str(csv_path)):
        resp = client.post(
            "/api/databento/fetch",
            json={"start": "2026-01-01", "end": "2026-05-29", "symbol": "MGC", "dry_run": False},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["days_fetched"] == 0
    assert body["cached_through"] == "2026-05-29"
