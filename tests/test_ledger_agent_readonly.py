"""Phase 7 acceptance test: "An attempt to get the agent to update a row
fails at the connection layer." Verified directly against the connection —
no app code, no chat model, no string-filtering — since that's the actual
claim: SQLite itself refuses the write, regardless of how the SQL text
was constructed or what called it.
"""
from __future__ import annotations

import sqlite3

import pytest

from research.ledger.api import append_hypothesis
from research.ledger.db import get_connection, get_readonly_connection

SAMPLE_IR = {"ir_version": "1.0", "name": "readonly-fixture", "instruments": ["NQ", "ES", "GC"]}


@pytest.fixture
def seeded_db_path(tmp_path):
    path = tmp_path / "ledger.db"
    conn = get_connection(path)
    hyp_id = append_hypothesis(
        conn, ir=SAMPLE_IR, mechanism="m", falsifier="f", model_name="kimi-k3",
        model_version="v1", prompt_hash="h", temperature=0.0,
        data_range="2010-06-06/2024-12-31", param_grid={}, n_variants_swept=1,
        trial_count_at_test=0,
    )
    conn.close()
    return path, hyp_id


def test_readonly_connection_blocks_update(seeded_db_path):
    path, hyp_id = seeded_db_path
    ro = get_readonly_connection(path)
    with pytest.raises(sqlite3.OperationalError, match="readonly database"):
        ro.execute("UPDATE hypotheses SET outcome = 'promoted' WHERE id = ?", (hyp_id,))
    ro.close()

    check = get_connection(path)
    row = check.execute("SELECT outcome FROM hypotheses WHERE id = ?", (hyp_id,)).fetchone()
    assert row["outcome"] == "rejected"  # genuinely unchanged, not just an error in-flight


def test_readonly_connection_blocks_delete_insert_and_ddl(seeded_db_path):
    path, hyp_id = seeded_db_path
    ro = get_readonly_connection(path)
    for stmt, params in [
        ("DELETE FROM hypotheses WHERE id = ?", (hyp_id,)),
        ("INSERT INTO hypotheses (id) VALUES (?)", ("x",)),
        ("DROP TABLE hypotheses", ()),
        ("ALTER TABLE hypotheses ADD COLUMN evil TEXT", ()),
        ("CREATE TABLE evil (a INT)", ()),
    ]:
        with pytest.raises(sqlite3.OperationalError, match="readonly database"):
            ro.execute(stmt, params)
    ro.close()


def test_readonly_connection_still_reads_tables_and_views(seeded_db_path):
    path, hyp_id = seeded_db_path
    ro = get_readonly_connection(path)
    rows = ro.execute("SELECT id FROM hypotheses").fetchall()
    assert [r["id"] for r in rows] == [hyp_id]
    assert ro.execute("SELECT * FROM v_rejections_by_gate").fetchall() == []
    ro.close()


def test_readonly_connection_refuses_a_missing_database(tmp_path):
    with pytest.raises(sqlite3.OperationalError):
        get_readonly_connection(tmp_path / "does-not-exist.db")


def test_chat_session_refuses_a_writable_connection(seeded_db_path):
    """Defense in depth: research.ledger_agent.agent.ChatSession itself
    refuses to start unless the connection it's given is genuinely
    query_only — never trusting a caller's word for it."""
    from research.ledger_agent.agent import ChatSession
    path, _ = seeded_db_path
    writable = get_connection(path)
    with pytest.raises(ValueError, match="read-only connection"):
        ChatSession(conn=writable, chat_fn=lambda messages: None)
    writable.close()
