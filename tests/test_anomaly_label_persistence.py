"""Interrupt-safe, concurrent labelling: every label paid for survives an
interrupted run (labels.jsonl + grok_spend.json), a re-run labels only the
rest, and the abort rules still hold with several calls in flight. All
label functions here are fakes — no network, no spend.
"""
from __future__ import annotations

import json
import threading
import time
from datetime import date, timedelta

import polars as pl
import pytest

from research.anomaly.grok_client import (
    LabelFetchError, LabelFetchFatalError, LabelResult, _post_with_rate_limit_backoff,
)
from research.anomaly.label_store import append_label, load_stored_labels
from research.anomaly.labels import RegimeLabel
from research.anomaly.pipeline import (
    MAX_CONSECUTIVE_LABEL_FAILURES, _ensure_regime_columns, label_top_decile,
)
from research.anomaly.spend import SpendLedger

COST = 0.25


def _table(n: int) -> pl.DataFrame:
    base = date(2024, 1, 2)
    rows = [{"instrument": "NQ", "session_date": base + timedelta(days=i),
             "anomaly_score": round(1.0 - i * 0.01, 4)} for i in range(n)]
    return _ensure_regime_columns(pl.DataFrame(rows))


def _ok(instrument, session_date, features):
    return LabelResult(
        label=RegimeLabel(category="none_identified", description=f"d {session_date}",
                          confidence=0.5, sources=("https://example.com",)),
        cost_usd=COST,
    )


class _CountingLabelFn:
    def __init__(self, inner=_ok, interrupt_on_call: int | None = None):
        self.inner, self.interrupt_on_call = inner, interrupt_on_call
        self.calls: list[date] = []
        self._lock = threading.Lock()

    def __call__(self, instrument, session_date, features):
        with self._lock:
            self.calls.append(session_date)
            n = len(self.calls)
        if self.interrupt_on_call == n:
            raise KeyboardInterrupt
        return self.inner(instrument, session_date, features)


def test_interrupted_run_leaves_n_labels_on_disk_and_n_times_cost_in_the_spend_file(tmp_path):
    n_done, total = 3, 8
    labels_path, spend_path = tmp_path / "labels.jsonl", tmp_path / "spend.json"
    table = _table(total)

    fn = _CountingLabelFn(interrupt_on_call=n_done + 1)
    run = label_top_decile(table, fn, SpendLedger.load(spend_path), top_fraction=1.0,
                           cap=100.0, concurrency=1, labels_path=labels_path)

    assert run.interrupted is True
    assert run.labelled == n_done
    assert len(labels_path.read_text().splitlines()) == n_done
    assert json.loads(spend_path.read_text())["total_usd"] == pytest.approx(n_done * COST)
    assert run.table["regime_label"].null_count() == total - n_done  # table has them too

    # Re-running (fresh process: reload spend, merge the file) labels only the rest.
    stored = load_stored_labels(labels_path)
    assert len(stored) == n_done
    resumed = table.join(
        pl.DataFrame(stored).select("instrument", "session_date", "regime_label")
        .rename({"regime_label": "stored"}), on=["instrument", "session_date"], how="left",
    ).with_columns(pl.coalesce(["stored", "regime_label"]).alias("regime_label")).drop("stored")
    fn2 = _CountingLabelFn()
    run2 = label_top_decile(resumed, fn2, SpendLedger.load(spend_path), top_fraction=1.0,
                            cap=100.0, concurrency=1, labels_path=labels_path)

    assert len(fn2.calls) == total - n_done
    assert not set(fn2.calls) & set(fn.calls[:n_done])
    assert run2.interrupted is False
    assert len(load_stored_labels(labels_path)) == total
    assert json.loads(spend_path.read_text())["total_usd"] == pytest.approx(total * COST)


def test_run_pass_merges_stored_labels_at_start_so_only_the_rest_is_asked(tmp_path, monkeypatch):
    from research.anomaly import pipeline
    table = _table(6)
    monkeypatch.setattr(pipeline, "build_anomaly_table", lambda bars: table)
    labels_path = tmp_path / "labels.jsonl"
    for i in range(2):
        d = date(2024, 1, 2) + timedelta(days=i)
        append_label(labels_path, {
            "instrument": "NQ", "session_date": d, "regime_label": "scheduled_macro",
            "regime_label_confidence": 0.9, "regime_description": "x",
            "regime_sources": ["u"], "labelled_at": "2026-09-24T00:00:00+00:00",
        })

    fn = _CountingLabelFn()
    run = pipeline.run_pass(pl.DataFrame(), existing_table=None, label_fn=fn,
                            spend=SpendLedger.load(tmp_path / "s.json"), top_fraction=1.0,
                            cap=100.0, labels_path=labels_path)

    assert len(fn.calls) == 4
    assert run.table["regime_label"].null_count() == 0
    assert run.table.filter(pl.col("regime_label") == "scheduled_macro").height == 2


def test_truncated_final_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "labels.jsonl"
    append_label(path, {
        "instrument": "NQ", "session_date": date(2024, 1, 2), "regime_label": "none_identified",
        "regime_label_confidence": 0.5, "regime_description": "d", "regime_sources": [],
        "labelled_at": "t",
    })
    with path.open("a") as f:
        f.write('{"instrument": "NQ", "session_date": "2024-01-0')  # crash mid-write
    assert len(load_stored_labels(path)) == 1


def test_concurrency_runs_calls_in_parallel_and_saves_every_label(tmp_path):
    in_flight, peak, lock = 0, 0, threading.Lock()

    def slow(instrument, session_date, features):
        nonlocal in_flight, peak
        with lock:
            in_flight += 1
            peak = max(peak, in_flight)
        time.sleep(0.05)
        with lock:
            in_flight -= 1
        return _ok(instrument, session_date, features)

    labels_path = tmp_path / "labels.jsonl"
    run = label_top_decile(_table(12), slow, SpendLedger.load(tmp_path / "s.json"),
                           top_fraction=1.0, cap=100.0, concurrency=4, labels_path=labels_path)

    assert peak == 4
    assert run.labelled == 12
    assert len(load_stored_labels(labels_path)) == 12
    assert run.spend_usd == pytest.approx(12 * COST)


def test_consecutive_failure_abort_still_works_with_concurrency(tmp_path):
    def always_fails(instrument, session_date, features):
        time.sleep(0.01)
        raise LabelFetchError("boom", status_code=500)

    fn = _CountingLabelFn(inner=always_fails)
    run = label_top_decile(_table(60), fn, SpendLedger.load(tmp_path / "s.json"),
                           top_fraction=1.0, cap=100.0, concurrency=4)

    assert run.aborted is True
    assert run.labelled == 0
    # no new call starts once the abort trips; only those already in flight finish
    assert MAX_CONSECUTIVE_LABEL_FAILURES <= len(fn.calls) <= MAX_CONSECUTIVE_LABEL_FAILURES + 3


def test_fatal_status_aborts_immediately_with_concurrency(tmp_path):
    def dead_key(instrument, session_date, features):
        raise LabelFetchFatalError("nope", status_code=401, body_excerpt="bad key")

    fn = _CountingLabelFn(inner=dead_key)
    run = label_top_decile(_table(40), fn, SpendLedger.load(tmp_path / "s.json"),
                           top_fraction=1.0, cap=100.0, concurrency=4)

    assert run.aborted is True
    assert "401" in run.abort_reason
    assert len(fn.calls) <= 4  # never got past the first wave


def test_in_flight_successes_are_still_saved_after_an_abort(tmp_path):
    def mixed(instrument, session_date, features):
        if session_date == date(2024, 1, 2):
            raise LabelFetchFatalError("nope", status_code=403)
        time.sleep(0.05)
        return _ok(instrument, session_date, features)

    labels_path = tmp_path / "labels.jsonl"
    run = label_top_decile(_table(20), mixed, SpendLedger.load(tmp_path / "s.json"),
                           top_fraction=1.0, cap=100.0, concurrency=4, labels_path=labels_path)
    assert run.aborted is True
    assert len(load_stored_labels(labels_path)) == run.labelled >= 1


def test_budget_cap_holds_with_concurrency(tmp_path):
    spend_path = tmp_path / "s.json"
    run = label_top_decile(_table(20), _ok, SpendLedger.load(spend_path), top_fraction=1.0,
                           cap=1.0, concurrency=4)
    assert run.budget_exhausted is True
    assert json.loads(spend_path.read_text())["total_usd"] <= 1.0 + 1e-9
    assert run.labelled == 4  # 4 x $0.25


# --- 429 backoff -----------------------------------------------------------

class _Resp:
    def __init__(self, status, headers=None, text=""):
        self.status_code, self.headers, self.text = status, headers or {}, text


def test_429_backs_off_then_succeeds():
    responses = iter([_Resp(429), _Resp(429), _Resp(200)])
    sleeps: list[float] = []
    resp = _post_with_rate_limit_backoff(lambda: next(responses), sleep=sleeps.append,
                                         jitter=lambda: 0.0)
    assert resp.status_code == 200
    assert sleeps == [2.0, 4.0]


def test_429_honours_retry_after():
    responses = iter([_Resp(429, {"Retry-After": "7"}), _Resp(200)])
    sleeps: list[float] = []
    _post_with_rate_limit_backoff(lambda: next(responses), sleep=sleeps.append)
    assert sleeps == [7.0]


def test_429_gives_up_after_max_retries_and_returns_the_429():
    sleeps: list[float] = []
    resp = _post_with_rate_limit_backoff(lambda: _Resp(429), sleep=sleeps.append,
                                         jitter=lambda: 0.0, max_retries=3)
    assert resp.status_code == 429
    assert len(sleeps) == 3


def test_billing_429_is_not_retried():
    sleeps: list[float] = []
    resp = _post_with_rate_limit_backoff(
        lambda: _Resp(429, text="You have no credits remaining"), sleep=sleeps.append)
    assert resp.status_code == 429
    assert sleeps == []
