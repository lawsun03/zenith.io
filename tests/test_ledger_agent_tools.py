"""research.ledger_agent.tools.run_query — the one capability the chat
agent gets. Note the real write-block guarantee is tested at the
connection level (tests/test_ledger_agent_readonly.py); these tests cover
run_query's own behaviour: SELECTs work, obvious writes get a legible
error before ever reaching SQLite, and results are capped so the model
never receives an unbounded dump.
"""
from __future__ import annotations

import pytest

from research.ledger.api import append_hypothesis
from research.ledger.db import get_connection, get_readonly_connection
from research.ledger_agent.tools import MAX_ROWS, run_query

SAMPLE_IR = {"ir_version": "1.0", "name": "tools-fixture", "instruments": ["NQ", "ES", "GC"]}


@pytest.fixture
def ro_conn(tmp_path):
    path = tmp_path / "ledger.db"
    conn = get_connection(path)
    ids = []
    for i in range(MAX_ROWS + 5):
        ids.append(append_hypothesis(
            conn, ir={**SAMPLE_IR, "name": f"tools-fixture-{i}"}, mechanism="m", falsifier="f",
            model_name="kimi-k3", model_version="v1", prompt_hash="h", temperature=0.0,
            data_range="2010-06-06/2024-12-31", param_grid={}, n_variants_swept=1,
            trial_count_at_test=0, cap=MAX_ROWS + 10,  # more rows than the annual cap allows by default
        ))
    conn.close()
    ro = get_readonly_connection(path)
    yield ro, ids
    ro.close()


def test_select_returns_rows_with_columns(ro_conn):
    conn, ids = ro_conn
    result = run_query(conn, "SELECT id, mechanism FROM hypotheses LIMIT 1")
    assert result.error is None
    assert result.columns == ["id", "mechanism"]
    assert result.rows[0]["id"] in ids


def test_write_statement_rejected_before_reaching_sqlite(ro_conn):
    conn, ids = ro_conn
    result = run_query(conn, f"UPDATE hypotheses SET outcome='promoted' WHERE id='{ids[0]}'")
    assert result.error is not None
    assert result.rows == []


def test_multi_statement_injection_attempt_rejected(ro_conn):
    conn, _ = ro_conn
    result = run_query(conn, "SELECT 1; DROP TABLE hypotheses")
    assert result.error is not None


def test_large_result_is_capped_and_flagged_truncated(ro_conn):
    conn, ids = ro_conn
    result = run_query(conn, "SELECT id FROM hypotheses")
    assert len(result.rows) == MAX_ROWS
    assert result.truncated is True


def test_small_result_is_not_flagged_truncated(ro_conn):
    conn, ids = ro_conn
    result = run_query(conn, "SELECT id FROM hypotheses LIMIT 3")
    assert len(result.rows) == 3
    assert result.truncated is False


def test_sql_error_surfaces_as_a_result_error_not_an_exception(ro_conn):
    conn, _ = ro_conn
    result = run_query(conn, "SELECT nonexistent_column FROM hypotheses")
    assert result.error is not None
    assert "nonexistent_column" in result.error.lower() or "no such column" in result.error.lower()


def test_view_query_works(ro_conn):
    conn, _ = ro_conn
    result = run_query(conn, "SELECT * FROM v_trial_budget")
    assert result.error is None
