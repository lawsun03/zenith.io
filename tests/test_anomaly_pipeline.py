"""Pipeline orchestration: top-decile selection, incremental labelling,
budget-cap handling, and carry-forward across repeated runs."""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl
import pytest

from research.anomaly.grok_client import LabelFetchError, LabelResult
from research.anomaly.labels import RegimeLabel
from research.anomaly.pipeline import (
    _ensure_regime_columns,
    label_top_decile,
    sessions_needing_labels,
)
from research.anomaly.spend import SpendLedger


def _table(n: int, *, labelled: set[int] = frozenset()) -> pl.DataFrame:
    """n sessions of NQ, scores descending 1.0, 0.9, 0.8, ... by row index."""
    base = date(2024, 1, 2)
    rows = [
        {
            "instrument": "NQ",
            "session_date": base + timedelta(days=i),
            "anomaly_score": round(1.0 - i * 0.1, 4),
        }
        for i in range(n)
    ]
    table = _ensure_regime_columns(pl.DataFrame(rows))
    if labelled:
        # every fixture row is instrument "NQ", so session_date alone
        # identifies a row for this helper's purposes.
        labelled_dates = [rows[i]["session_date"] for i in labelled]
        table = table.with_columns(
            pl.when(pl.col("session_date").is_in(labelled_dates))
            .then(pl.lit("idiosyncratic_shock"))
            .otherwise(pl.col("regime_label"))
            .alias("regime_label")
        )
    return table


def _fixed_label_fn(cost: float = 1.0, category: str = "idiosyncratic_shock"):
    def _fn(instrument, session_date, feature_summary):
        return LabelResult(
            label=RegimeLabel(category=category, description="d", confidence=0.8, sources=("s",)),
            cost_usd=cost,
        )

    return _fn


def test_sessions_needing_labels_selects_only_the_top_fraction():
    table = _table(10)
    candidates = sessions_needing_labels(table, top_fraction=0.3)
    assert candidates.height == 3
    assert candidates["anomaly_score"].to_list() == [1.0, 0.9, 0.8]


def test_sessions_needing_labels_excludes_sessions_already_labelled():
    table = _table(10, labelled={0})
    candidates = sessions_needing_labels(table, top_fraction=0.3)
    assert candidates.height == 2
    assert 1.0 not in candidates["anomaly_score"].to_list()


def test_sessions_needing_labels_with_zero_fraction_is_empty():
    table = _table(10)
    candidates = sessions_needing_labels(table, top_fraction=0.0)
    assert candidates.height == 0


def test_sessions_needing_labels_ignores_unscored_sessions():
    table = _table(5).with_columns(
        pl.when(pl.col("anomaly_score") == 1.0).then(None).otherwise(pl.col("anomaly_score")).alias(
            "anomaly_score"
        )
    )
    candidates = sessions_needing_labels(table, top_fraction=1.0)
    assert candidates.height == 4  # the null-scored session never enters the pool


def test_label_top_decile_applies_labels_and_tracks_spend(tmp_path):
    table = _table(5)
    spend = SpendLedger.load(tmp_path / "spend.json")
    run = label_top_decile(table, _fixed_label_fn(cost=2.0), spend, top_fraction=1.0, cap=100.0)

    assert run.labelled == 5
    assert run.skipped == 0
    assert run.budget_exhausted is False
    assert run.spend_usd == pytest.approx(10.0)
    assert set(run.table["regime_label"].to_list()) == {"idiosyncratic_shock"}
    assert run.table["labelled_at"].null_count() == 0


def test_label_top_decile_skips_fetch_errors_without_aborting(tmp_path):
    table = _table(3)
    calls = {"n": 0}

    def flaky(instrument, session_date, feature_summary):
        calls["n"] += 1
        if calls["n"] == 2:
            raise LabelFetchError("boom")
        return LabelResult(
            label=RegimeLabel(category="none_identified", description="d", confidence=0.1, sources=()),
            cost_usd=0.1,
        )

    spend = SpendLedger.load(tmp_path / "spend.json")
    run = label_top_decile(table, flaky, spend, top_fraction=1.0, cap=100.0)

    assert run.labelled == 2
    assert run.skipped == 1
    assert run.table["regime_label"].null_count() == 1


def test_label_top_decile_stops_at_budget_cap_and_keeps_partial_progress(tmp_path):
    table = _table(5)  # 5 candidates at top_fraction=1.0, each costs $1
    spend = SpendLedger.load(tmp_path / "spend.json")
    run = label_top_decile(table, _fixed_label_fn(cost=1.0), spend, top_fraction=1.0, cap=2.0)

    assert run.labelled == 2
    assert run.budget_exhausted is True
    assert run.spend_usd == pytest.approx(2.0)
    # the two highest-scoring sessions got labelled; the rest didn't.
    labelled_scores = run.table.filter(pl.col("regime_label").is_not_null())["anomaly_score"].to_list()
    assert sorted(labelled_scores, reverse=True) == [1.0, 0.9]


def test_run_pass_carries_forward_existing_labels_before_labelling_the_rest(tmp_path, monkeypatch):
    """run_pass = build_anomaly_table(bars) + carry-forward existing labels
    + label_top_decile. build_anomaly_table itself (features -> ranking) is
    covered by tests/test_anomaly_features.py, tests/test_anomaly_ranking.py
    and tests/test_anomaly_performance.py, so here `bars` stands in
    directly for an already-ranked table via a monkeypatched identity
    build_anomaly_table — this isolates run_pass's own composition logic.
    """
    import research.anomaly.pipeline as pipeline_module

    monkeypatch.setattr(pipeline_module, "build_anomaly_table", lambda bars: bars)

    fresh = _table(5)  # as if freshly recomputed from bars this run, no labels yet
    existing = _table(5, labelled={0})  # session 0 was labelled on a previous run

    calls = []

    def counting_label_fn(instrument, session_date, feature_summary):
        calls.append(session_date)
        return LabelResult(
            label=RegimeLabel(category="idiosyncratic_shock", description="d", confidence=0.6, sources=()),
            cost_usd=0.1,
        )

    spend = SpendLedger.load(tmp_path / "spend.json")
    run = pipeline_module.run_pass(
        fresh, existing_table=existing, label_fn=counting_label_fn, spend=spend, top_fraction=0.6, cap=100.0
    )

    # top_fraction=0.6 of 5 -> 3 candidates; session 0 already carried a
    # label forward, so only the other 2 should have triggered a Grok call.
    assert len(calls) == 2
    assert run.table.filter(pl.col("regime_label").is_not_null()).height == 3
    session_0_date = _table(5)["session_date"][0]
    assert run.table.filter(pl.col("session_date") == session_0_date)["regime_label"][0] == "idiosyncratic_shock"


def test_repeated_labelling_runs_do_not_re_ask_grok_about_an_already_labelled_session(tmp_path):
    """Simulates two nightly runs against the same ranked table: the
    second run must not re-ask Grok about a session the first run already
    labelled — that's what makes labelling incremental (pipeline.py's
    module docstring)."""
    table = _table(10)
    calls: list[tuple] = []

    def counting_label_fn(instrument, session_date, feature_summary):
        calls.append((instrument, session_date))
        return LabelResult(
            label=RegimeLabel(category="idiosyncratic_shock", description="d", confidence=0.7, sources=()),
            cost_usd=0.1,
        )

    spend = SpendLedger.load(tmp_path / "spend.json")
    first = label_top_decile(table, counting_label_fn, spend, top_fraction=0.3, cap=100.0)
    assert len(calls) == 3
    assert first.labelled == 3

    second = label_top_decile(first.table, counting_label_fn, spend, top_fraction=0.3, cap=100.0)
    assert len(calls) == 3  # no new calls — all three top-decile sessions are already labelled
    assert second.labelled == 0
