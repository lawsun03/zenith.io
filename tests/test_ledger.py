"""Full tests for the ledger's append-only schema and thin API (item 1).

docs/research-loop/ledger.sql: "Nothing deletes from `hypotheses`. The row
count (weighted by n_variants_swept) IS the trial count that gates 5 and 6
depend on. Deleting a row silently invalidates every subsequent significance
calculation." These tests check the triggers actually fire against a real
sqlite connection, not just that the DDL text looks right.
"""
from __future__ import annotations

import itertools
import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st

from research.ledger.api import (
    append_hypothesis,
    holdout_usage,
    record_gate_result,
    rejections_by_gate,
    trial_count_at,
)
from research.ledger.db import get_connection
from research.stats.budget import TrialBudgetExceeded

SAMPLE_IR = {"ir_version": "1.0", "name": "orb-test", "instruments": ["NQ", "ES", "GC"]}

_nonce_counter = itertools.count()


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(tmp_path / "ledger.db")
    yield connection
    connection.close()


def _append(conn, **overrides):
    ir = overrides.pop("ir", None)
    if ir is None:
        ir = {**SAMPLE_IR, "_nonce": next(_nonce_counter)}
    kwargs = dict(
        ir=ir,
        mechanism="test mechanism",
        falsifier="test falsifier",
        model_name="kimi-k3",
        model_version="2026-01-01",
        prompt_hash="deadbeef",
        temperature=0.2,
        data_range="2010-06-06/2024-12-31",
        param_grid={"r": [1.0, 1.5]},
        n_variants_swept=2,
        trial_count_at_test=0,
    )
    kwargs.update(overrides)
    return append_hypothesis(conn, **kwargs)


# --- append-only enforcement (the acceptance test, run against a real DB) -

def test_deleting_a_row_raises(conn):
    row_id = _append(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM hypotheses WHERE id = ?", (row_id,))
    row = conn.execute("SELECT id FROM hypotheses WHERE id = ?", (row_id,)).fetchone()
    assert row is not None


@pytest.mark.parametrize(
    "column,value",
    [
        ("ir_hash", "x" * 64),
        ("model_version", "rewritten"),
        ("n_variants_swept", 999),
        ("ir_json", "{}"),
        ("created_at", "1970-01-01T00:00:00Z"),
        ("model_name", "rewritten"),
        ("prompt_hash", "rewritten"),
        ("trial_count_at_test", 999),
    ],
)
def test_rewriting_immutable_columns_raises(conn, column, value):
    row_id = _append(conn)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"UPDATE hypotheses SET {column} = ? WHERE id = ?", (value, row_id))


def test_duplicate_ir_hash_is_rejected(conn):
    """The UNIQUE constraint on ir_hash is what gate 0 depends on to
    recognise a resubmitted idea — canonical hashing (research.stats.ir_hash)
    only matters because this constraint enforces it."""
    ir = {**SAMPLE_IR, "_nonce": "fixed-for-this-test"}
    _append(conn, ir=ir)
    with pytest.raises(sqlite3.IntegrityError):
        _append(conn, ir=ir)


def test_mutable_columns_can_be_updated(conn):
    """Sanity check the trigger isn't over-broad: ledger.sql says gate
    results and disposition are the ONLY mutable fields."""
    row_id = _append(conn)
    conn.execute("UPDATE hypotheses SET outcome = 'blended' WHERE id = ?", (row_id,))
    row = conn.execute("SELECT outcome FROM hypotheses WHERE id = ?", (row_id,)).fetchone()
    assert row["outcome"] == "blended"


# --- trial_count_at --------------------------------------------------------

def test_trial_count_at_sums_variants_not_rows(conn):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _append(conn, n_variants_swept=90, created_at=base)
    _append(conn, n_variants_swept=1, created_at=base + timedelta(days=1))
    assert trial_count_at(conn, base + timedelta(days=2)) == 91


def test_trial_count_at_excludes_rows_after_the_timestamp(conn):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    _append(conn, n_variants_swept=10, created_at=base)
    _append(conn, n_variants_swept=10, created_at=base + timedelta(days=10))
    assert trial_count_at(conn, base) == 10


def test_trial_count_at_with_no_rows_is_zero(conn):
    assert trial_count_at(conn, datetime(2026, 1, 1, tzinfo=timezone.utc)) == 0


# --- record_gate_result -----------------------------------------------------

def test_record_gate_result_sets_first_failed_gate(conn):
    row_id = _append(conn)
    record_gate_result(conn, row_id, gate=1, passed=True, measured=5, threshold=3)
    record_gate_result(conn, row_id, gate=2, passed=False, measured=0.5, threshold=2.0)
    row = conn.execute(
        "SELECT first_failed_gate, gate_results FROM hypotheses WHERE id = ?", (row_id,)
    ).fetchone()
    assert row["first_failed_gate"] == 2
    results = json.loads(row["gate_results"])
    assert {r["gate"] for r in results} == {1, 2}


def test_record_gate_result_first_failed_gate_is_the_minimum_failed(conn):
    row_id = _append(conn)
    record_gate_result(conn, row_id, gate=3, passed=False, measured=0.1, threshold=1.0)
    record_gate_result(conn, row_id, gate=1, passed=False, measured=1, threshold=3)
    row = conn.execute("SELECT first_failed_gate FROM hypotheses WHERE id = ?", (row_id,)).fetchone()
    assert row["first_failed_gate"] == 1


def test_record_gate_result_is_idempotent_per_gate(conn):
    row_id = _append(conn)
    record_gate_result(conn, row_id, gate=1, passed=False, measured=1, threshold=3)
    record_gate_result(conn, row_id, gate=1, passed=True, measured=5, threshold=3)
    row = conn.execute(
        "SELECT first_failed_gate, gate_results FROM hypotheses WHERE id = ?", (row_id,)
    ).fetchone()
    results = json.loads(row["gate_results"])
    assert len(results) == 1
    assert results[0]["pass"] is True
    assert row["first_failed_gate"] is None


def test_record_gate_result_unknown_id_raises(conn):
    with pytest.raises(KeyError):
        record_gate_result(conn, "does-not-exist", gate=1, passed=True)


# --- canned views ------------------------------------------------------------

def test_rejections_by_gate_view_reflects_recorded_results(conn):
    row_id = _append(conn)
    record_gate_result(conn, row_id, gate=3, passed=False, measured=0.1, threshold=1.0)
    rej = rejections_by_gate(conn)
    assert any(r["gate"] == 3 and r["n"] == 1 for r in rej)


def test_holdout_usage_view_starts_at_zero(conn):
    _append(conn)
    usage = holdout_usage(conn)
    assert usage[0]["touches_used"] == 0


# --- budget enforcement wired into append_hypothesis --------------------------

def test_append_hypothesis_raises_on_the_51st_hypothesis_in_a_year(conn):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(50):
        _append(conn, created_at=base + timedelta(hours=i))
    with pytest.raises(TrialBudgetExceeded):
        _append(conn, created_at=base + timedelta(hours=51))


def test_append_hypothesis_budget_resets_next_calendar_year(conn):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(50):
        _append(conn, created_at=base + timedelta(hours=i))
    with pytest.raises(TrialBudgetExceeded):
        _append(conn, created_at=base + timedelta(hours=51))
    # a new calendar year starts a fresh budget
    row_id = _append(conn, created_at=datetime(2027, 1, 1, tzinfo=timezone.utc))
    assert row_id is not None


# --- property test: append-only holds under arbitrary op sequences -----------

@settings(max_examples=25, suppress_health_check=[HealthCheck.too_slow])
@given(
    ops=st.lists(
        st.sampled_from(["delete", "rewrite_hash", "rewrite_version", "rewrite_n_variants"]),
        min_size=1,
        max_size=6,
    )
)
def test_append_only_holds_under_any_op_sequence(tmp_path_factory, ops):
    connection = get_connection(tmp_path_factory.mktemp("ledger") / "ledger.db")
    try:
        row_id = _append(connection)
        for op in ops:
            with pytest.raises(sqlite3.IntegrityError):
                if op == "delete":
                    connection.execute("DELETE FROM hypotheses WHERE id = ?", (row_id,))
                elif op == "rewrite_hash":
                    connection.execute(
                        "UPDATE hypotheses SET ir_hash = ? WHERE id = ?", ("y" * 64, row_id)
                    )
                elif op == "rewrite_version":
                    connection.execute(
                        "UPDATE hypotheses SET model_version = 'new' WHERE id = ?", (row_id,)
                    )
                elif op == "rewrite_n_variants":
                    connection.execute(
                        "UPDATE hypotheses SET n_variants_swept = 12345 WHERE id = ?", (row_id,)
                    )
            # every attempted mutation must have been rejected and left the row untouched
            row = connection.execute(
                "SELECT * FROM hypotheses WHERE id = ?", (row_id,)
            ).fetchone()
            assert row is not None
            assert row["model_version"] == "2026-01-01"
            assert row["n_variants_swept"] == 2
    finally:
        connection.close()
