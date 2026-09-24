"""Ledger integration for the trainer (research.trainer, docs/research-loop
PHASE-PROMPTS.md Phase 6): get_ensemble/list_active_ensembles (the read
side insert_ensemble never had), insert_drill_session/insert_drill_decision,
and drill_decisions' append-only enforcement. General append-only trigger
testing style matches tests/test_ledger.py.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from decimal import Decimal

import pytest

from research.gates.ensemble import build_ensemble
from research.ledger.api import (
    append_hypothesis,
    get_ensemble,
    insert_drill_decision,
    insert_drill_session,
    insert_ensemble,
    list_active_ensembles,
    list_drill_sessions,
    previously_wrong_decisions,
)
from research.ledger.db import get_connection

SAMPLE_IR = {"ir_version": "1.0", "name": "trainer-ledger-test", "instruments": ["NQ", "ES", "GC"]}
BASE_TS = datetime(2026, 1, 6, 13, 30, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(tmp_path / "ledger.db")
    yield connection
    connection.close()


def _append(conn, nonce):
    return append_hypothesis(
        conn, ir={**SAMPLE_IR, "_nonce": nonce}, mechanism="m", falsifier="f",
        model_name="kimi-k3", model_version="v1", prompt_hash="h", temperature=0.0,
        data_range="2010-06-06/2024-12-31", param_grid={}, n_variants_swept=1,
        trial_count_at_test=0,
    )


def _make_ensemble(conn):
    hid_a = _append(conn, "a")
    hid_b = _append(conn, "b")
    ir_a = {**SAMPLE_IR, "_nonce": "a"}
    ir_b = {**SAMPLE_IR, "_nonce": "b"}
    ensemble = build_ensemble("test-family", [(hid_a, ir_a), (hid_b, ir_b)])
    ensemble_id = insert_ensemble(conn, ensemble, sharpe_oos=0.9)
    return ensemble_id, hid_a, hid_b


# --- get_ensemble / list_active_ensembles --------------------------------

def test_get_ensemble_round_trips_members_and_equal_weights(conn):
    ensemble_id, hid_a, hid_b = _make_ensemble(conn)
    ensemble = get_ensemble(conn, ensemble_id)
    assert ensemble.family == "test-family"
    assert {m.hypothesis_id for m in ensemble.members} == {hid_a, hid_b}
    for m in ensemble.members:
        assert m.weight == Decimal("1") / Decimal("2")
        assert m.ir_doc["_nonce"] in ("a", "b")


def test_get_ensemble_unknown_id_raises(conn):
    with pytest.raises(KeyError):
        get_ensemble(conn, "does-not-exist")


def test_list_active_ensembles_excludes_retired(conn):
    ensemble_id, _, _ = _make_ensemble(conn)
    conn.execute("UPDATE ensembles SET status = 'retired' WHERE id != ?", (ensemble_id,))
    conn.commit()
    active = list_active_ensembles(conn)
    assert any(e["id"] == ensemble_id for e in active)
    assert all(e["id"] == ensemble_id for e in active)


# --- drill_sessions / drill_decisions write paths -------------------------

def test_insert_drill_session_and_list_drill_sessions_trend(conn):
    ensemble_id, _, _ = _make_ensemble(conn)
    insert_drill_session(
        conn, ensemble_id=ensemble_id, n_decisions=10,
        setups_correctly_taken=5, setups_missed=1, false_positives=1,
        direction_errors=0, stop_placement_errors=1, fidelity_score=0.8,
        started_at=BASE_TS,
    )
    insert_drill_session(
        conn, ensemble_id=ensemble_id, n_decisions=10,
        setups_correctly_taken=8, setups_missed=0, false_positives=0,
        direction_errors=0, stop_placement_errors=0, fidelity_score=0.95,
        started_at=BASE_TS.replace(day=7),
    )
    sessions = list_drill_sessions(conn, ensemble_id)
    assert len(sessions) == 2
    assert sessions[0]["fidelity_score"] < sessions[1]["fidelity_score"]  # trends upward, oldest first


def test_insert_drill_decision_and_previously_wrong_lookup(conn):
    ensemble_id, hid_a, _ = _make_ensemble(conn)
    session_id = insert_drill_session(
        conn, ensemble_id=ensemble_id, n_decisions=1,
        setups_correctly_taken=0, setups_missed=1, false_positives=0,
        direction_errors=0, stop_placement_errors=0, fidelity_score=0.0,
    )
    insert_drill_decision(
        conn, session_id=session_id, hypothesis_id=hid_a, instrument="NQ",
        decision_ts=BASE_TS, ir_fired=True, near_miss=False,
        user_is_setup=False, correct_setup=False,
        ir_side="long", ir_stop_price="100.00",
    )
    wrong = previously_wrong_decisions(conn, ensemble_id)
    assert len(wrong) == 1
    (got_hid, got_instrument, got_ts) = next(iter(wrong))
    assert got_hid == hid_a
    assert got_instrument == "NQ"
    assert got_ts == "2026-01-06T13:30:00.000000Z"


def test_previously_wrong_decisions_excludes_correct_ones(conn):
    ensemble_id, hid_a, _ = _make_ensemble(conn)
    session_id = insert_drill_session(
        conn, ensemble_id=ensemble_id, n_decisions=1,
        setups_correctly_taken=1, setups_missed=0, false_positives=0,
        direction_errors=0, stop_placement_errors=0, fidelity_score=1.0,
    )
    insert_drill_decision(
        conn, session_id=session_id, hypothesis_id=hid_a, instrument="NQ",
        decision_ts=BASE_TS, ir_fired=True, near_miss=False,
        user_is_setup=True, correct_setup=True,
        correct_direction=True, correct_stop=True,
        ir_side="long", ir_stop_price="100.00",
    )
    assert previously_wrong_decisions(conn, ensemble_id) == set()


def test_not_applicable_direction_and_stop_stay_null_not_false(conn):
    """correct_direction/correct_stop=None must round-trip as SQL NULL, not
    0 — coercing 'not applicable' to False would make a plain true-negative
    (IR declines, user correctly declines too) look like a wrong decision
    forever, corrupting the weighted sampler's 'gotten wrong before' bias."""
    ensemble_id, hid_a, _ = _make_ensemble(conn)
    session_id = insert_drill_session(
        conn, ensemble_id=ensemble_id, n_decisions=1,
        setups_correctly_taken=0, setups_missed=0, false_positives=0,
        direction_errors=0, stop_placement_errors=0, fidelity_score=1.0,
    )
    decision_id = insert_drill_decision(
        conn, session_id=session_id, hypothesis_id=hid_a, instrument="NQ",
        decision_ts=BASE_TS, ir_fired=False, near_miss=False,
        user_is_setup=False, correct_setup=True,
    )
    row = conn.execute(
        "SELECT correct_direction, correct_stop FROM drill_decisions WHERE id = ?",
        (decision_id,),
    ).fetchone()
    assert row["correct_direction"] is None
    assert row["correct_stop"] is None
    # a true negative must never appear as "gotten wrong before"
    assert previously_wrong_decisions(conn, ensemble_id) == set()


# --- append-only enforcement (mirrors tests/test_ledger.py's hypotheses tests) -

def test_deleting_a_drill_decision_raises(conn):
    ensemble_id, hid_a, _ = _make_ensemble(conn)
    session_id = insert_drill_session(
        conn, ensemble_id=ensemble_id, n_decisions=1,
        setups_correctly_taken=1, setups_missed=0, false_positives=0,
        direction_errors=0, stop_placement_errors=0, fidelity_score=1.0,
    )
    decision_id = insert_drill_decision(
        conn, session_id=session_id, hypothesis_id=hid_a, instrument="NQ",
        decision_ts=BASE_TS, ir_fired=True, near_miss=False,
        user_is_setup=True, correct_setup=True,
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM drill_decisions WHERE id = ?", (decision_id,))
    row = conn.execute("SELECT id FROM drill_decisions WHERE id = ?", (decision_id,)).fetchone()
    assert row is not None


def test_rewriting_a_drill_decision_raises(conn):
    ensemble_id, hid_a, _ = _make_ensemble(conn)
    session_id = insert_drill_session(
        conn, ensemble_id=ensemble_id, n_decisions=1,
        setups_correctly_taken=0, setups_missed=1, false_positives=0,
        direction_errors=0, stop_placement_errors=0, fidelity_score=0.0,
    )
    decision_id = insert_drill_decision(
        conn, session_id=session_id, hypothesis_id=hid_a, instrument="NQ",
        decision_ts=BASE_TS, ir_fired=True, near_miss=False,
        user_is_setup=False, correct_setup=False,
    )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE drill_decisions SET correct_setup = 1 WHERE id = ?", (decision_id,)
        )
