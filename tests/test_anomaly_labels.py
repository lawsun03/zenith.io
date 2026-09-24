"""RegimeLabel validation, response parsing, and the cross-module guarantee
that research.ir.schema bans the exact field name this module writes to
the ledger — CLAUDE.md domain invariant #5's acceptance test for phase 3."""
from __future__ import annotations

import pytest

from research.anomaly.labels import (
    CATEGORIES,
    REGIME_LEDGER_FIELD,
    RegimeLabel,
    parse_label_response,
)
from research.ir.schema import validate


def test_regime_ledger_field_is_the_string_the_ir_validator_bans():
    assert REGIME_LEDGER_FIELD == "regime_label"


@pytest.mark.parametrize("category", CATEGORIES)
def test_valid_categories_construct(category):
    label = RegimeLabel(category=category, description="x", confidence=0.5, sources=())
    assert label.category == category


def test_unknown_category_rejected():
    with pytest.raises(ValueError, match="unknown regime category"):
        RegimeLabel(category="market_wide_panic", description="x", confidence=0.5, sources=())


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_confidence_out_of_range_rejected(confidence):
    with pytest.raises(ValueError, match="confidence must be in"):
        RegimeLabel(category="none_identified", description="x", confidence=confidence, sources=())


def test_parse_label_response_round_trips():
    raw = {
        "category": "scheduled_macro",
        "description": "CPI print beat expectations",
        "confidence": 0.9,
        "sources": ["https://example.com/a", "https://example.com/b"],
    }
    label = parse_label_response(raw)
    assert label.category == "scheduled_macro"
    assert label.confidence == 0.9
    assert label.sources == ("https://example.com/a", "https://example.com/b")


def test_parse_label_response_defaults_missing_sources_to_empty():
    raw = {"category": "none_identified", "description": "nothing found", "confidence": 0.1}
    label = parse_label_response(raw)
    assert label.sources == ()


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"category": "scheduled_macro"},  # missing description/confidence
        {"category": "scheduled_macro", "description": "x", "confidence": "high"},  # bad type
        {"category": "not_a_real_category", "description": "x", "confidence": 0.5},
    ],
)
def test_parse_label_response_rejects_malformed_input(raw):
    with pytest.raises(ValueError):
        parse_label_response(raw)


def test_ir_validator_rejects_a_document_referencing_regime_label():
    """The phase-3 acceptance test from docs/research-loop/PHASE-PROMPTS.md:
    the IR validator must reject any document referencing a regime field.
    Ties directly to REGIME_LEDGER_FIELD (rather than hardcoding the
    string "regime_label" again) so this test breaks loudly if either
    module's field name ever drifts from the other's.
    """
    doc = {
        "ir_version": "1.0",
        "name": f"strategy conditioned on {REGIME_LEDGER_FIELD}",
        "instruments": ["NQ", "ES", "GC"],
        "session": {"start": "09:30", "end": "16:00", "tz": "America/New_York"},
        "entry": {"op": "time_window", "start": "09:30", "end": "10:00", "recognizable": True},
        "exit": {"op": "session_end", "recognizable": False},
        "stop": {"type": "atr", "multiple": 1.0, "lookback": 14},
        "sizing": {"family": "micro", "vol_target_annual": 0.15},
    }
    errors = validate(doc)
    assert any("banned term" in e for e in errors)
