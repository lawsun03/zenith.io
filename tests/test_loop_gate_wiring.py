"""Phase 6b's own acceptance tests (docs/research-loop/PHASE-PROMPTS.md):

- A fixture candidate runs end to end; its ledger row has all 10 gate
  results with thresholds.
- A known-bad candidate stops at the right gate; later gates are
  recorded as unreached.
- Gate 6 uses null_sr_variance below 20 recorded trials and
  empirical_sr_variance at 20+. Test both.
- A survivor lands in an ensemble, and nothing sorts survivors by
  performance.

No real model APIs and no real ledger — every test uses an in-memory
(tmp_path) ledger and hand-built/mocked inputs, per the task's own
"no real budget spent" instruction.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from research.gates.pipeline import CandidateOutcome
from research.gates.types import GATE_ORDER, GateResult
from research.ir.sizing import load_account_config
from research.ledger.api import append_hypothesis, record_candidate_scores
from research.ledger.db import get_connection
from research.loop import gate_runner
from research.loop.gate_runner import (
    EMPIRICAL_SR_VARIANCE_MIN_TRIALS,
    evaluate_hypothesis,
    resolve_sr_variance_across_trials,
)
from research.stats.deflated_sharpe import empirical_sr_variance, null_sr_variance

_STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "research" / "ir" / "strategies"
GOOD_IR = json.loads((_STRATEGIES_DIR / "ifvg_sweep.json").read_text())

EMPTY_BARS = {"NQ": [], "ES": [], "GC": []}
ACCOUNT = load_account_config("topstep-50k")
NOW = datetime(2024, 6, 1, tzinfo=timezone.utc)


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(tmp_path / "ledger.db")
    yield connection
    connection.close()


def _append_fixture(conn, name: str = "gate-wiring-fixture") -> tuple[str, dict, dict]:
    ir = {**GOOD_IR, "name": name}
    hyp_id = append_hypothesis(
        conn, ir=ir, mechanism="m", falsifier="f", model_name="astra", model_version="v1",
        prompt_hash="h", temperature=0.7, data_range="2010-06-06/2025-03-21",
        param_grid={"target.multiple": [2.0, 2.5]}, n_variants_swept=2, trial_count_at_test=1,
        created_at=NOW,
    )
    return hyp_id, ir, {"target.multiple": [2.0, 2.5]}


def _run(conn, hyp_id, ir, param_grid, *, n_trials=1):
    from research.data.folds import load_folds
    folds = load_folds()
    sr_var, sr_source = resolve_sr_variance_across_trials(conn, NOW)
    return evaluate_hypothesis(
        conn, hyp_id, ir, param_grid,
        bars_by_instrument=EMPTY_BARS, account=ACCOUNT,
        folds=folds.walk_forward_folds, corpus_start=folds.corpus_start, corpus_end=folds.holdout_start,
        n_trials=n_trials, years=(folds.holdout_start - folds.corpus_start).days / 365.25,
        sr_variance_across_trials=sr_var, sr_variance_source=sr_source,
    )


# --- all 10 gates recorded, with thresholds ---------------------------

def test_fixture_candidate_records_all_ten_gates_with_thresholds(conn):
    hyp_id, ir, param_grid = _append_fixture(conn)
    _run(conn, hyp_id, ir, param_grid)

    row = conn.execute("SELECT gate_results FROM hypotheses WHERE id = ?", (hyp_id,)).fetchone()
    results = json.loads(row["gate_results"])
    assert {r["gate"] for r in results} == set(GATE_ORDER)
    for r in results:
        assert r["threshold"] is not None, f"gate {r['gate']} recorded with no threshold"
        assert "pass" in r and "measured" in r


# --- known-bad candidate stops at the right gate, later gates unreached -

def test_known_bad_candidate_stops_at_first_failed_gate_rest_unreached(conn):
    """EMPTY_BARS produces zero trades — gate 0 (valid, novel IR) passes,
    gate 1 (frequency) fails on zero trades over a 15-year sample, and
    every later gate must be recorded as unreached (measured=None,
    passed=False), never silently skipped (CLAUDE.md rule 12)."""
    hyp_id, ir, param_grid = _append_fixture(conn)
    evaluation = _run(conn, hyp_id, ir, param_grid)

    assert evaluation.outcome.first_failed_gate == 1
    row = conn.execute(
        "SELECT gate_results, first_failed_gate FROM hypotheses WHERE id = ?", (hyp_id,)
    ).fetchone()
    assert row["first_failed_gate"] == 1
    results = {r["gate"]: r for r in json.loads(row["gate_results"])}
    assert results[0]["pass"] is True
    assert results[1]["pass"] is False
    for gate in range(2, 10):
        assert results[gate]["pass"] is False
        assert results[gate]["measured"] is None       # unreached, not silently skipped
        assert results[gate]["threshold"] is not None  # still statically knowable


# --- gate 6 sr_variance source switches at the 20-trial boundary -------

def test_sr_variance_uses_null_below_twenty_trials(conn):
    for i in range(5):
        hyp_id, _, _ = _append_fixture(conn, name=f"sr-fixture-{i}")
        record_candidate_scores(conn, hyp_id, sharpe_oos_per_trade=0.1 * i)

    value, source = resolve_sr_variance_across_trials(conn, NOW)
    assert source == "null"
    assert value == null_sr_variance(max(5, 2))


def test_sr_variance_uses_empirical_at_twenty_trials_or_more(conn):
    sharpes = [0.05 * i for i in range(EMPIRICAL_SR_VARIANCE_MIN_TRIALS)]
    for i, sharpe in enumerate(sharpes):
        hyp_id, _, _ = _append_fixture(conn, name=f"sr-fixture-{i}")
        record_candidate_scores(conn, hyp_id, sharpe_oos_per_trade=sharpe)

    value, source = resolve_sr_variance_across_trials(conn, NOW)
    assert source == "empirical"
    assert value == pytest.approx(empirical_sr_variance(sharpes))


def test_sr_variance_respects_the_as_of_timestamp(conn):
    """Trials recorded AFTER `now` must not count — the same "as of"
    contract research.ledger.api.trial_count_at already has. created_at is
    immutable (append-only trigger), so the future trial is appended WITH
    a future created_at directly, not updated after the fact."""
    past_sharpes = [0.05 * i for i in range(EMPIRICAL_SR_VARIANCE_MIN_TRIALS)]
    for i, sharpe in enumerate(past_sharpes):
        hyp_id, _, _ = _append_fixture(conn, name=f"past-{i}")
        record_candidate_scores(conn, hyp_id, sharpe_oos_per_trade=sharpe)

    future_ir = {**GOOD_IR, "name": "future-one"}
    future_id = append_hypothesis(
        conn, ir=future_ir, mechanism="m", falsifier="f", model_name="astra", model_version="v1",
        prompt_hash="h", temperature=0.7, data_range="2010-06-06/2025-03-21",
        param_grid={}, n_variants_swept=1, trial_count_at_test=1,
        created_at=datetime(2099, 1, 1, tzinfo=timezone.utc),
    )
    record_candidate_scores(conn, future_id, sharpe_oos_per_trade=9.0)

    value, source = resolve_sr_variance_across_trials(conn, NOW)
    assert source == "empirical"  # exactly the 20 past trials, future one excluded
    assert value == pytest.approx(empirical_sr_variance(past_sharpes))


# --- a survivor joins an equal-weighted ensemble, nothing ranks --------

def test_survivor_joins_an_equal_weighted_ensemble(conn, monkeypatch):
    hyp_id, ir, param_grid = _append_fixture(conn)

    all_pass = [GateResult(gate=g, passed=True, measured=1.0, threshold=0.5) for g in GATE_ORDER]
    canned_outcome = CandidateOutcome(
        gate_results=all_pass, first_failed_gate=None,
        sharpe_with_releases=0.9, sharpe_without_releases=0.85, sharpe_recorded=0.85,
        combine_payout_prob=0.7, cleared_all_gates=True,
    )
    monkeypatch.setattr(gate_runner, "evaluate_candidate", lambda *a, **kw: canned_outcome)

    evaluation = _run(conn, hyp_id, ir, param_grid)
    assert evaluation.outcome.cleared_all_gates is True

    row = conn.execute("SELECT outcome, ensemble_id FROM hypotheses WHERE id = ?", (hyp_id,)).fetchone()
    assert row["outcome"] == "blended"
    assert row["ensemble_id"] is not None

    ensemble = conn.execute(
        "SELECT family, member_count, weights_json, status FROM ensembles WHERE id = ?",
        (row["ensemble_id"],),
    ).fetchone()
    assert ensemble["status"] == "active"
    assert ensemble["member_count"] == 1
    weights = json.loads(ensemble["weights_json"])
    assert weights == {hyp_id: "1"}  # equal-weighted (of one) — no score-based weighting


def test_a_candidate_that_fails_never_joins_an_ensemble(conn):
    hyp_id, ir, param_grid = _append_fixture(conn)
    _run(conn, hyp_id, ir, param_grid)  # EMPTY_BARS -> fails gate 1

    row = conn.execute("SELECT outcome, ensemble_id FROM hypotheses WHERE id = ?", (hyp_id,)).fetchone()
    assert row["outcome"] == "rejected"
    assert row["ensemble_id"] is None


# "Nothing sorts survivors by performance" is already enforced at the
# source level for the actual promotion path (research/gates/ensemble.py,
# research/gates/pipeline.py — both of which evaluate_hypothesis calls
# into unmodified) by tests/test_gates_no_selection.py. research/loop/
# gate_runner.py itself legitimately sorts ONE candidate's own trades
# chronologically for backtesting (_pooled_trades), which is not a
# candidate-ranking sort — re-scanning this file would false-positive on
# that, so this suite relies on the behavioural check above
# (test_survivor_joins_an_equal_weighted_ensemble: weight is exactly "1"
# for a lone survivor, not derived from any score) plus the existing
# source-level guard on the two files that actually could rank.
