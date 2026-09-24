"""Ledger integration added for the gate pipeline (research.gates.pipeline):
record_gate_results, record_candidate_scores, insert_ensemble. Append-only
enforcement itself is tested in tests/test_ledger.py — these tests cover
only the new, phase-4 write paths."""
from __future__ import annotations

import json

import pytest

from research.gates.ensemble import build_ensemble
from research.ledger.api import (
    append_hypothesis, insert_ensemble, record_candidate_scores, record_gate_results,
)
from research.ledger.db import get_connection

SAMPLE_IR = {"ir_version": "1.0", "name": "ledger-gate-test", "instruments": ["NQ", "ES", "GC"]}


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


def test_record_gate_results_writes_every_gate_with_thresholds(conn):
    row_id = _append(conn, "a")
    results = [
        {"gate": g, "passed": g < 3, "measured": (1.0 if g < 3 else None), "threshold": 0.5}
        for g in range(10)
    ]
    record_gate_results(conn, row_id, results)
    row = conn.execute("SELECT gate_results, first_failed_gate FROM hypotheses WHERE id = ?",
                        (row_id,)).fetchone()
    stored = json.loads(row["gate_results"])
    assert len(stored) == 10
    assert all(r["threshold"] == 0.5 for r in stored)
    assert row["first_failed_gate"] == 3


def test_record_candidate_scores_updates_mutable_columns(conn):
    row_id = _append(conn, "b")
    record_candidate_scores(
        conn, row_id, sharpe_is=1.2, sharpe_oos=0.9, sharpe_decay=0.25,
        sharpe_with_releases=1.1, sharpe_without_releases=0.4, combine_payout_prob=0.7,
        weekly_histogram={"2024-W01": 3}, outcome="blended",
    )
    row = conn.execute(
        "SELECT sharpe_oos, sharpe_with_releases, weekly_histogram, outcome "
        "FROM hypotheses WHERE id = ?", (row_id,),
    ).fetchone()
    assert row["sharpe_oos"] == 0.9
    assert row["sharpe_with_releases"] == 1.1
    assert json.loads(row["weekly_histogram"]) == {"2024-W01": 3}
    assert row["outcome"] == "blended"


def test_record_candidate_scores_rejects_unknown_column(conn):
    row_id = _append(conn, "c")
    with pytest.raises(ValueError, match="unknown column"):
        record_candidate_scores(conn, row_id, ir_hash="not allowed")


def test_record_candidate_scores_unknown_hypothesis_raises(conn):
    with pytest.raises(KeyError):
        record_candidate_scores(conn, "does-not-exist", outcome="blended")


def test_insert_ensemble_marks_every_member_blended(conn):
    ids = [_append(conn, f"member-{i}") for i in range(3)]
    survivors = [(hid, {"name": f"s{i}"}) for i, hid in enumerate(ids)]
    ensemble = build_ensemble("sweep-displacement", survivors)

    ensemble_id = insert_ensemble(conn, ensemble, sharpe_oos=0.9, combine_payout_prob=0.7)

    ens_row = conn.execute("SELECT * FROM ensembles WHERE id = ?", (ensemble_id,)).fetchone()
    assert ens_row["member_count"] == 3
    assert ens_row["status"] == "active"

    for hid in ids:
        row = conn.execute("SELECT ensemble_id, outcome FROM hypotheses WHERE id = ?",
                            (hid,)).fetchone()
        assert row["ensemble_id"] == ensemble_id
        assert row["outcome"] == "blended"
