"""The full four-stage cycle (research/loop/cycle.py) — phase 5's own
acceptance tests, from docs/research-loop/PHASE-PROMPTS.md:

  - A full unattended cycle runs end to end and writes candidates and
    rejections to the ledger.
  - Every ledger row has a non-null mechanism, falsifier, model_version
    and prompt_hash.
  - The loop halts cleanly when the annual trial cap is reached.

(The adversarial-review-cannot-see-backtests acceptance test lives in
tests/test_loop_review.py, at the unit level where it's actually
enforced.)
"""
from __future__ import annotations

import copy
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from research.ledger.db import get_connection
from research.loop.cycle import run_cycle
from research.loop.providers import ChatResponse

_STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "research" / "ir" / "strategies"
GOOD_IR = json.loads((_STRATEGIES_DIR / "ifvg_sweep.json").read_text())

FEATURE_COLUMNS = ("realised_vol",)


def _anomaly_rows(n: int) -> list[dict]:
    return [
        {
            "instrument": "NQ", "session_date": date(2024, 1, 1 + i),
            "regime_label": "scheduled_macro", "regime_label_confidence": 0.8,
            "regime_description": "d", "anomaly_score": 0.9 - i * 0.1,
            "realised_vol": 1.0,
        }
        for i in range(n)
    ]


def _triage_all_fn(rows: list[dict]):
    ids = [f"{r['instrument']}:{r['session_date']}" for r in rows]

    def _fn(prompt: str) -> ChatResponse:
        return ChatResponse(
            text=json.dumps({"shortlist": [{"anomaly_id": i, "rationale": "r"} for i in ids]}),
            model_version="kimi-k3-2026-08-01",
        )
    return _fn


def _hypothesis_fn():
    """A distinct, schema-valid IR per call — distinct `name` so ir_hash
    (UNIQUE NOT NULL) never collides across shortlist items."""
    counter = {"n": 0}

    def _fn(prompt: str) -> ChatResponse:
        counter["n"] += 1
        ir = copy.deepcopy(GOOD_IR)
        ir["name"] = f"cycle-test-{counter['n']}"
        return ChatResponse(
            text=json.dumps({"mechanism": f"mechanism #{counter['n']}", "ir": ir}),
            model_version="astra-2026-08-01",
        )
    return _fn


def _review_fn(verdicts: list[bool]):
    calls = {"n": -1}

    def _fn(prompt: str) -> ChatResponse:
        calls["n"] += 1
        survives = verdicts[calls["n"]]
        return ChatResponse(
            text=json.dumps({"survives": survives, "falsifier": f"falsifier #{calls['n']}", "critique": "c"}),
            model_version="kimi-k3-2026-08-01",
        )
    return _fn


def _variants_fn(n_variants: int = 4):
    def _fn(prompt: str) -> ChatResponse:
        variants = []
        for i in range(n_variants):
            v = copy.deepcopy(GOOD_IR)
            v["target"]["multiple"] = 2.0 + i * 0.1
            variants.append(v)
        return ChatResponse(
            text=json.dumps({"param_grid": {"target.multiple": [v["target"]["multiple"] for v in variants]},
                              "variants": variants}),
            model_version="kimi-k3-2026-08-01",
        )
    return _fn


@pytest.fixture
def conn(tmp_path):
    connection = get_connection(tmp_path / "ledger.db")
    yield connection
    connection.close()


def test_cycle_writes_both_a_surviving_candidate_and_a_review_rejection(conn):
    rows = _anomaly_rows(2)
    result = run_cycle(
        conn, rows, feature_columns=FEATURE_COLUMNS,
        triage_chat_fn=_triage_all_fn(rows),
        hypothesis_chat_fn=_hypothesis_fn(),
        review_chat_fn=_review_fn([True, False]),
        variants_chat_fn=_variants_fn(4),
        hypothesis_model_name="gpt-6-astra",
        hypothesis_temperature=0.7,
        data_range="2010-06-06/2024-12-31",
        now=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )

    assert result.n_triaged == 2
    assert len(result.hypothesis_ids) == 2
    assert result.n_survived_review == 1
    assert result.n_review_rejected == 1
    assert result.budget_exhausted is False

    ledger_rows = conn.execute(
        "SELECT mechanism, falsifier, model_version, prompt_hash, outcome, n_variants_swept "
        "FROM hypotheses ORDER BY created_at"
    ).fetchall()
    assert len(ledger_rows) == 2
    for row in ledger_rows:
        assert row["mechanism"]
        assert row["falsifier"]
        assert row["model_version"]
        assert row["prompt_hash"]
        assert row["outcome"] == "rejected"  # default until gates run — this cycle never runs gates

    # the survivor swept 4 variants, the review-rejected one swept exactly 1
    swept = sorted(r["n_variants_swept"] for r in ledger_rows)
    assert swept == [1, 4]


def test_cycle_halts_cleanly_when_the_annual_cap_is_reached(conn):
    rows = _anomaly_rows(3)
    result = run_cycle(
        conn, rows, feature_columns=FEATURE_COLUMNS,
        triage_chat_fn=_triage_all_fn(rows),
        hypothesis_chat_fn=_hypothesis_fn(),
        review_chat_fn=_review_fn([True, True, True]),
        variants_chat_fn=_variants_fn(1),
        hypothesis_model_name="gpt-6-astra",
        hypothesis_temperature=0.7,
        data_range="2010-06-06/2024-12-31",
        cap=2,
        now=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )

    assert result.budget_exhausted is True
    assert len(result.hypothesis_ids) == 2  # stopped exactly at the cap, no exception raised
    count = conn.execute("SELECT COUNT(*) FROM hypotheses").fetchone()[0]
    assert count == 2


def test_cycle_skips_a_hypothesis_generation_failure_and_continues(conn):
    rows = _anomaly_rows(2)
    good_fn = _hypothesis_fn()
    calls = {"n": 0}

    def _flaky(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return ChatResponse(text="not json", model_version="astra-2026-08-01")
        return good_fn(prompt)

    result = run_cycle(
        conn, rows, feature_columns=FEATURE_COLUMNS,
        triage_chat_fn=_triage_all_fn(rows),
        hypothesis_chat_fn=_flaky,
        review_chat_fn=_review_fn([True]),
        variants_chat_fn=_variants_fn(1),
        hypothesis_model_name="gpt-6-astra",
        hypothesis_temperature=0.7,
        data_range="2010-06-06/2024-12-31",
        now=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )

    assert result.n_generation_failed == 1
    assert len(result.hypothesis_ids) == 1
