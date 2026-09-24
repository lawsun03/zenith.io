"""End-to-end trainer API tests: start a session, answer every decision
point, complete it, and check the ledger round-trip — the wiring
test_trainer_*.py's unit-level coverage can't reach on its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import polars as pl
import pytest
from fastapi.testclient import TestClient

import research.data.loader as loader_mod
import research.trainer.sessions as sessions_mod
from app.api.journal import Journal
from app.api.server import build_app
from app.execution.reconciler import Reconciler, ReconcilerConfig
from app.risk.config import fifty_k_combine
from app.risk.state import RiskState
from app.sim.paper import PaperBroker
from research.gates.ensemble import build_ensemble
from research.ledger.api import append_hypothesis, insert_ensemble
from research.ledger.db import get_connection

BASE_TS = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)  # 08:30 ET

_IR_DOC = {
    "ir_version": "1.0", "name": "api trainer fixture",
    "instruments": ["NQ"],
    "session": {"start": "08:30", "end": "11:00", "tz": "America/New_York"},
    "entry": {"op": "or", "operands": [
        {"op": "and", "operands": [
            {"op": "sweep_of", "level": "swing_low", "n": 2,
             "min_offset": 0.20, "within_bars": 5, "recognizable": True},
            {"op": "displacement", "direction": "up", "min_atr": 1.0, "recognizable": True},
            {"op": "fvg", "direction": "up", "recognizable": True},
        ]},
        {"op": "and", "operands": [
            {"op": "sweep_of", "level": "swing_high", "n": 2,
             "min_offset": 0.20, "within_bars": 5, "recognizable": True},
            {"op": "displacement", "direction": "down", "min_atr": 1.0, "recognizable": True},
            {"op": "fvg", "direction": "down", "recognizable": True},
        ]},
    ]},
    "exit": {"op": "session_end", "recognizable": False},
    "stop": {"type": "structural", "anchor": "sweep_extreme", "multiple": 1.0,
             "lookback": 14, "buffer": 0.30},
    "target": {"type": "r_multiple", "multiple": 2.5},
    "sizing": {"family": "micro", "vol_target_annual": 0.15, "max_contracts": 10},
}


def _fixture_frame() -> pl.DataFrame:
    rows = [(i, 100.0, 100.5, 99.5, 100.0) for i in range(14)]
    rows += [
        (14, 99.0, 99.2, 98.8, 99.0),
        (15, 98.8, 98.9, 97.5, 97.8),
        (16, 97.7, 97.8, 97.0, 97.3),
        (17, 97.3, 97.5, 97.1, 97.2),
        (18, 97.2, 97.3, 96.5, 97.0),
        (19, 97.0, 97.4, 96.8, 97.2),
        (20, 97.2, 97.6, 96.9, 97.4),
        (21, 97.4, 97.5, 95.5, 97.3),
        (22, 97.3, 99.5, 97.2, 99.3),
        (23, 99.3, 99.6, 99.1, 99.4),
        (24, 99.4, 108.0, 99.3, 107.5),
    ]
    return pl.DataFrame({
        "ts": [BASE_TS + timedelta(minutes=i) for i, *_ in rows],
        "open": [o for _, o, h, l, c in rows],
        "high": [h for _, o, h, l, c in rows],
        "low": [l for _, o, h, l, c in rows],
        "close": [c for _, o, h, l, c in rows],
        "volume": [100] * len(rows),
    })


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(sessions_mod, "load_bars", lambda *a, **kw: _fixture_frame())
    monkeypatch.setattr(loader_mod, "load_bars", lambda *a, **kw: _fixture_frame())

    db_path = tmp_path / "ledger.db"
    conn = get_connection(db_path)
    hid = append_hypothesis(
        conn, ir=_IR_DOC, mechanism="m", falsifier="f", model_name="kimi-k3",
        model_version="v1", prompt_hash="h", temperature=0.0,
        data_range="2010-06-06/2024-12-31", param_grid={}, n_variants_swept=1,
        trial_count_at_test=0,
    )
    ensemble = build_ensemble("api-test-family", [(hid, _IR_DOC)])
    ensemble_id = insert_ensemble(conn, ensemble, sharpe_oos=0.9)
    conn.close()

    async def _make():
        broker = PaperBroker(starting_balance=Decimal("50000"))
        await broker.connect()
        state = RiskState(config=fifty_k_combine(soft_buffer=Decimal("500")))
        state.mark_equity(Decimal("50000"), datetime.now(timezone.utc))
        journal = Journal()
        reconciler = Reconciler(broker, state, ReconcilerConfig(
            interval_seconds=10, grace_first_tick=False))
        return build_app(
            risk_state=state, reconciler=reconciler, journal=journal,
            trainer_ledger_db_path=db_path,
        )

    import asyncio
    app = asyncio.run(_make())
    return TestClient(app), ensemble_id


def test_full_drill_session_round_trips_through_the_ledger(client):
    c, ensemble_id = client

    r = c.get("/api/trainer/ensembles")
    assert r.status_code == 200
    assert any(e["id"] == ensemble_id for e in r.json())

    r = c.post("/api/trainer/sessions", json={
        "ensemble_id": ensemble_id, "start_date": "2026-01-06", "end_date": "2026-01-06",
        "n_decisions": 25, "seed": 1,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    session_id = body["session_id"]
    assert body["n_decisions"] > 0
    current = body["current"]
    assert current is not None
    # Future must be hidden: none of the answer-bearing fields leak pre-answer.
    assert "fired" not in current and "side" not in current and "stop_price" not in current
    assert all(bar["ts"] <= current["decision_ts"] for bar in current["bars"])

    done = False
    n_fired_seen = 0
    while not done:
        r = c.get(f"/api/trainer/sessions/{session_id}/current")
        cur = r.json()["current"]
        # Always answer "no setup" — a deliberately wrong guess on the one
        # fired decision, so we can check it lands as a miss, not silently
        # scored as correct.
        r = c.post(f"/api/trainer/sessions/{session_id}/answer", json={"is_setup": False})
        assert r.status_code == 200, r.text
        answer_body = r.json()
        assert "score" in answer_body and "ir_ground_truth" in answer_body
        if answer_body["ir_ground_truth"]["fired"]:
            n_fired_seen += 1
            assert answer_body["score"]["correct_setup"] is False
        done = answer_body["done"]

    assert n_fired_seen == 1  # the known-good iFVG fixture fires exactly once

    r = c.post(f"/api/trainer/sessions/{session_id}/complete", json={"notes": "felt rushed"})
    assert r.status_code == 200, r.text
    result = r.json()
    assert result["setups_missed"] == 1
    assert result["fidelity_score"] < 1.0
    assert "divergence" in result
    assert result["divergence"]["trades_taken"] == 0
    assert result["divergence"]["trades_available"] == 1

    r = c.get(f"/api/trainer/sessions?ensemble_id={ensemble_id}")
    assert r.status_code == 200
    sessions = r.json()
    assert len(sessions) == 1
    assert sessions[0]["notes"] == "felt rushed"


def test_answering_a_losing_but_ir_matching_trade_scores_correct(client, monkeypatch):
    """CLAUDE.md's scoring rule, exercised through the actual HTTP surface:
    a trade that follows the IR and loses must still score correct_setup.
    We fake a losing outcome by truncating the bars to end right after
    entry (stop never explicitly touches, but the point is the score
    never looks at market outcome at all — it's graded the instant the
    trainee answers, before any future bars are even revealed)."""
    c, ensemble_id = client

    r = c.post("/api/trainer/sessions", json={
        "ensemble_id": ensemble_id, "start_date": "2026-01-06", "end_date": "2026-01-06",
        "n_decisions": 25, "seed": 1,
    })
    session_id = r.json()["session_id"]

    done = False
    while not done:
        cur = c.get(f"/api/trainer/sessions/{session_id}/current").json()["current"]
        r = c.post(f"/api/trainer/sessions/{session_id}/answer",
                    json={"is_setup": True, "direction": "long", "stop_price": "97.00"})
        body = r.json()
        if body["ir_ground_truth"]["fired"]:
            assert body["score"]["correct_setup"] is True
            assert body["score"]["correct_direction"] is True
        done = body["done"]
