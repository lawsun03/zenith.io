"""research.ledger_agent.health — the numbers surfaced unprompted on open
(PHASE-PROMPTS.md phase 7 item 3): current PBO, trials consumed against
the annual budget, holdout touches, rejections by gate.
"""
from __future__ import annotations

from datetime import datetime, timezone

from research.ledger.api import append_hypothesis, record_gate_result, record_loop_pbo
from research.ledger.db import get_connection
from research.ledger_agent.health import format_health_summary, loop_health_summary

SAMPLE_IR = {"ir_version": "1.0", "name": "health-fixture", "instruments": ["NQ", "ES", "GC"]}
NOW = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _append(conn, name, created_at=NOW):
    return append_hypothesis(
        conn, ir={**SAMPLE_IR, "name": name}, mechanism="m", falsifier="f", model_name="kimi-k3",
        model_version="v1", prompt_hash="h", temperature=0.0, data_range="x",
        param_grid={}, n_variants_swept=3, trial_count_at_test=0, created_at=created_at,
    )


def test_health_on_a_fresh_ledger_shows_zeros_not_errors():
    conn = get_connection(":memory:")
    health = loop_health_summary(conn, now=NOW)
    assert health.loop_pbo is None
    assert health.hypotheses_this_year == 0
    assert health.trial_count == 0
    assert health.holdout_touches_used == 0
    assert health.rejections_by_gate == []
    text = format_health_summary(health)
    assert "not yet computed" in text
    assert "never touched" in text


def test_health_reflects_seeded_ledger_state():
    conn = get_connection(":memory:")
    hid_a = _append(conn, "a")
    hid_b = _append(conn, "b")
    record_gate_result(conn, hid_a, gate=1, passed=False, measured=0.1, threshold=0.8)
    record_gate_result(conn, hid_b, gate=1, passed=False, measured=0.2, threshold=0.8)
    record_loop_pbo(conn, 0.62, computed_at=NOW)

    health = loop_health_summary(conn, now=NOW)
    assert health.loop_pbo == 0.62
    assert health.hypotheses_this_year == 2
    assert health.trial_count == 6  # 3 + 3, variant-weighted
    assert health.rejections_by_gate == [{"gate": 1, "n": 2, "first_seen": health.rejections_by_gate[0]["first_seen"], "last_seen": health.rejections_by_gate[0]["last_seen"]}]

    text = format_health_summary(health)
    assert "0.620" in text
    assert "BLOCKS ALL PROMOTION" in text  # > 0.5
    assert "2/50" in text
    assert "gate 1 (2)" in text


def test_health_pbo_below_threshold_does_not_flag_blocking():
    conn = get_connection(":memory:")
    record_loop_pbo(conn, 0.3, computed_at=NOW)
    text = format_health_summary(loop_health_summary(conn, now=NOW))
    assert "BLOCKS ALL PROMOTION" not in text
    assert "0.300" in text


def test_health_excludes_hypotheses_from_a_different_year():
    conn = get_connection(":memory:")
    _append(conn, "this-year", created_at=NOW)
    _append(conn, "last-year", created_at=datetime(2025, 1, 1, tzinfo=timezone.utc))
    health = loop_health_summary(conn, now=NOW)
    assert health.hypotheses_this_year == 1
