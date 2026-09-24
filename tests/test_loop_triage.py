"""Stage a — triage (research/loop/triage.py)."""
from __future__ import annotations

import json
from datetime import date

import pytest

from research.loop.providers import ChatResponse
from research.loop.triage import TriageParseError, triage_anomalies

FEATURE_COLUMNS = ("realised_vol", "gap_size_atr")


def _rows():
    return [
        {
            "instrument": "NQ", "session_date": date(2024, 1, 2),
            "regime_label": "scheduled_macro", "regime_label_confidence": 0.9,
            "regime_description": "CPI print", "anomaly_score": 0.9,
            "realised_vol": 1.2, "gap_size_atr": 0.5,
        },
        {
            "instrument": "GC", "session_date": date(2024, 1, 3),
            "regime_label": "none_identified", "regime_label_confidence": 0.1,
            "regime_description": None, "anomaly_score": 0.4,
            "realised_vol": 0.3, "gap_size_atr": 0.1,
        },
    ]


def _fake_chat(shortlist_ids: list[str], model_version="kimi-k3-2026-08-01"):
    def _fn(prompt: str) -> ChatResponse:
        return ChatResponse(
            text=json.dumps({"shortlist": [{"anomaly_id": i, "rationale": "r"} for i in shortlist_ids]}),
            model_version=model_version,
        )
    return _fn


def test_empty_anomaly_rows_short_circuits_without_calling_the_model():
    def _boom(prompt):
        raise AssertionError("should never be called")

    result = triage_anomalies(_boom, [], feature_columns=FEATURE_COLUMNS)
    assert result.shortlist == ()


def test_shortlist_resolves_to_full_triaged_anomalies():
    rows = _rows()
    result = triage_anomalies(_fake_chat(["NQ:2024-01-02"]), rows, feature_columns=FEATURE_COLUMNS)
    assert len(result.shortlist) == 1
    item = result.shortlist[0]
    assert item.instrument == "NQ"
    assert item.regime_label == "scheduled_macro"
    assert item.regime_label_confidence == 0.9
    assert result.model_version == "kimi-k3-2026-08-01"


def test_unknown_anomaly_id_is_dropped_not_crashed():
    rows = _rows()
    result = triage_anomalies(
        _fake_chat(["NQ:2024-01-02", "ES:1999-01-01"]), rows, feature_columns=FEATURE_COLUMNS,
    )
    assert len(result.shortlist) == 1


def test_shortlist_truncated_to_max_shortlist():
    rows = _rows()
    result = triage_anomalies(
        _fake_chat(["NQ:2024-01-02", "GC:2024-01-03"]), rows,
        feature_columns=FEATURE_COLUMNS, max_shortlist=1,
    )
    assert len(result.shortlist) == 1


def test_unparsable_response_raises_triage_parse_error():
    def _fn(prompt):
        return ChatResponse(text="not json", model_version="v1")

    with pytest.raises(TriageParseError):
        triage_anomalies(_fn, _rows(), feature_columns=FEATURE_COLUMNS)


def test_missing_shortlist_key_raises_triage_parse_error():
    def _fn(prompt):
        return ChatResponse(text=json.dumps({"oops": []}), model_version="v1")

    with pytest.raises(TriageParseError):
        triage_anomalies(_fn, _rows(), feature_columns=FEATURE_COLUMNS)
