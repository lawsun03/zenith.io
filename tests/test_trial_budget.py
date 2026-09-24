"""Full tests for the annual hypothesis budget enforcer (item 6).

CLAUDE.md rule 6: the research loop has a hard cap of 30-50 hypotheses per
calendar year, enforced in the ledger, and "when it is spent, the loop
stops. That pause is intended behaviour" — a raise, not a logged warning the
caller can ignore. These tests check the enforcer trips exactly at the
boundary (never one early, never one late) and is scoped per calendar year.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from hypothesis import given, settings, strategies as st

from research.ledger.api import append_hypothesis
from research.ledger.db import get_connection
from research.stats.budget import (
    TrialBudgetExceeded,
    count_hypotheses_in_year,
    enforce_trial_budget,
)

SAMPLE_IR = {"ir_version": "1.0", "name": "orb-budget-test", "instruments": ["NQ", "ES", "GC"]}


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(tmp_path / "ledger.db")
    yield connection
    connection.close()


def _append(conn, created_at, nonce, cap=10_000):
    """Insert a row with a uniquely-hashing IR, at an explicit `cap` so
    append_hypothesis's own budget check (item 1's integration) never
    interferes with these unit tests of the enforcer itself — that
    integration is covered separately in tests/test_ledger.py.
    """
    return append_hypothesis(
        conn,
        ir={**SAMPLE_IR, "_nonce": nonce},
        mechanism="m",
        falsifier="f",
        model_name="kimi-k3",
        model_version="v1",
        prompt_hash="h",
        temperature=0.0,
        data_range="2010-06-06/2024-12-31",
        param_grid={},
        n_variants_swept=1,
        trial_count_at_test=0,
        created_at=created_at,
        cap=cap,
    )


def test_count_hypotheses_in_year_counts_rows_not_swept_variants(conn):
    base = datetime(2026, 3, 1, tzinfo=timezone.utc)
    _append(conn, base, nonce="a")
    _append(conn, base + timedelta(days=1), nonce="b")
    assert count_hypotheses_in_year(conn, 2026) == 2


def test_count_hypotheses_in_year_is_zero_for_an_untouched_year(conn):
    assert count_hypotheses_in_year(conn, 2030) == 0


def test_enforce_trial_budget_does_not_raise_below_cap(conn):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(9):
        _append(conn, base + timedelta(hours=i), nonce=i)
    enforce_trial_budget(conn, 2026, cap=10)  # 9 logged, cap 10: must not raise


def test_enforce_trial_budget_raises_once_cap_is_reached(conn):
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(10):
        _append(conn, base + timedelta(hours=i), nonce=i)
    with pytest.raises(TrialBudgetExceeded):
        enforce_trial_budget(conn, 2026, cap=10)  # 10 logged, cap 10: must raise


def test_append_hypothesis_raises_on_the_51st_hypothesis_in_a_year(conn):
    """The literal acceptance test, exercised end to end through the real
    insert path (append_hypothesis's default cap of 50), not just the
    enforcer function in isolation."""
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for i in range(50):
        append_hypothesis(
            conn,
            ir={**SAMPLE_IR, "_nonce": f"cap-{i}"},
            mechanism="m",
            falsifier="f",
            model_name="kimi-k3",
            model_version="v1",
            prompt_hash="h",
            temperature=0.0,
            data_range="2010-06-06/2024-12-31",
            param_grid={},
            n_variants_swept=1,
            trial_count_at_test=0,
            created_at=base + timedelta(hours=i),
        )
    assert count_hypotheses_in_year(conn, 2026) == 50

    with pytest.raises(TrialBudgetExceeded):
        append_hypothesis(
            conn,
            ir={**SAMPLE_IR, "_nonce": "cap-51"},
            mechanism="m",
            falsifier="f",
            model_name="kimi-k3",
            model_version="v1",
            prompt_hash="h",
            temperature=0.0,
            data_range="2010-06-06/2024-12-31",
            param_grid={},
            n_variants_swept=1,
            trial_count_at_test=0,
            created_at=base + timedelta(hours=51),
        )
    # the rejected 51st hypothesis must never have been inserted
    assert count_hypotheses_in_year(conn, 2026) == 50


def test_budget_is_scoped_per_calendar_year(conn):
    dec_2026 = datetime(2026, 12, 31, tzinfo=timezone.utc)
    for i in range(10):
        _append(conn, dec_2026 - timedelta(hours=i), nonce=f"2026-{i}")

    with pytest.raises(TrialBudgetExceeded):
        enforce_trial_budget(conn, 2026, cap=10)

    enforce_trial_budget(conn, 2027, cap=10)  # a different year is unaffected


@settings(max_examples=15)
@given(
    cap=st.integers(min_value=1, max_value=8),
    n_logged=st.integers(min_value=0, max_value=8),
)
def test_enforce_trial_budget_boundary_property(tmp_path_factory, cap, n_logged):
    """For any cap and any number of hypotheses already logged this year,
    the enforcer raises iff n_logged >= cap — never off by one in either
    direction."""
    connection = get_connection(tmp_path_factory.mktemp("ledger") / "ledger.db")
    try:
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        for i in range(n_logged):
            _append(connection, base + timedelta(hours=i), nonce=f"prop-{i}")

        if n_logged >= cap:
            with pytest.raises(TrialBudgetExceeded):
                enforce_trial_budget(connection, 2026, cap=cap)
        else:
            enforce_trial_budget(connection, 2026, cap=cap)
    finally:
        connection.close()
