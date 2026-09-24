"""Gate 0 — IR validity (schema + duplicate-hash check against the ledger)."""
from __future__ import annotations

import pytest

from research.gates.ir_validity import evaluate, is_duplicate
from research.ledger.api import append_hypothesis
from research.ledger.db import get_connection

VALID_DOC = {
    "ir_version": "1.0", "name": "gate0-fixture",
    "instruments": ["NQ", "ES", "GC"],
    "session": {"start": "08:30", "end": "11:00", "tz": "America/New_York"},
    "entry": {"op": "displacement", "direction": "up", "min_atr": 1.0, "recognizable": True},
    "exit": {"op": "session_end", "recognizable": False},
    "stop": {"type": "atr", "multiple": 1.0, "lookback": 14},
    "target": {"type": "r_multiple", "multiple": 2.0},
    "sizing": {"family": "micro", "vol_target_annual": 0.15, "max_contracts": 10},
}


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(tmp_path / "ledger.db")
    yield connection
    connection.close()


def test_valid_novel_doc_passes(conn):
    result = evaluate(conn, VALID_DOC)
    assert result.passed
    assert result.measured == 1.0


def test_invalid_doc_fails(conn):
    result = evaluate(conn, {**VALID_DOC, "instruments": ["NQ"]})
    assert not result.passed


def test_duplicate_hash_fails(conn):
    append_hypothesis(
        conn, ir=VALID_DOC, mechanism="m", falsifier="f", model_name="kimi-k3",
        model_version="v1", prompt_hash="h", temperature=0.0,
        data_range="2010-06-06/2024-12-31", param_grid={}, n_variants_swept=1,
        trial_count_at_test=0,
    )
    assert is_duplicate(conn, VALID_DOC)
    assert not evaluate(conn, VALID_DOC).passed
