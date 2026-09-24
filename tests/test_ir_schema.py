"""Validator tests: structural schema compliance plus the three invariants
JSON Schema can't express (CLAUDE.md: recognizable-only entry, banned
terms, pooled instruments)."""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from research.ir.schema import validate

_STRATEGIES_DIR = Path(__file__).resolve().parents[1] / "research" / "ir" / "strategies"


def _load(name: str) -> dict:
    return json.loads((_STRATEGIES_DIR / name).read_text())


@pytest.mark.parametrize("name", ["ifvg_sweep.json", "open_line_10am.json"])
def test_shipped_strategies_are_valid(name: str) -> None:
    assert validate(_load(name)) == []


def test_instruments_must_be_exactly_all_three() -> None:
    doc = _load("ifvg_sweep.json")
    doc["instruments"] = ["NQ", "ES"]
    errors = validate(doc)
    assert any("instruments must be exactly" in e for e in errors)


def test_instruments_rejects_duplicate() -> None:
    doc = _load("ifvg_sweep.json")
    doc["instruments"] = ["NQ", "NQ", "ES"]
    errors = validate(doc)
    assert any("instruments must be exactly" in e for e in errors)


def test_entry_predicate_missing_recognizable_flag_is_rejected() -> None:
    doc = _load("ifvg_sweep.json")
    # Drop "recognizable": true from one leaf — structurally still a valid
    # recognizablePredicate per the JSON Schema (the field is optional there),
    # which is exactly the gap this validator closes.
    del doc["entry"]["operands"][0]["operands"][0]["recognizable"]
    errors = validate(doc)
    assert any("must set \"recognizable\": true" in e for e in errors)


@pytest.mark.parametrize("term", [
    "regime_label", "account_balance", "daily_loss_limit", "trailing_drawdown",
    "REGIME_LABEL",  # case-insensitive
])
def test_banned_terms_rejected_anywhere_in_the_document(term: str) -> None:
    doc = copy.deepcopy(_load("ifvg_sweep.json"))
    doc["name"] = f"strategy referencing {term} somehow"
    errors = validate(doc)
    assert any("banned term" in e for e in errors)


def test_missing_required_top_level_field() -> None:
    doc = _load("ifvg_sweep.json")
    del doc["stop"]
    errors = validate(doc)
    assert any("missing required field 'stop'" in e for e in errors)


def test_stop_type_must_be_known() -> None:
    doc = _load("ifvg_sweep.json")
    doc["stop"]["type"] = "account_balance_based"
    errors = validate(doc)
    assert any("stop.type" in e for e in errors)


def test_ir_version_pinned() -> None:
    doc = _load("ifvg_sweep.json")
    doc["ir_version"] = "2.0"
    errors = validate(doc)
    assert any("ir_version" in e for e in errors)


def test_exit_may_use_non_recognizable_ops() -> None:
    # exit ops like session_end are fine even though they'd be rejected in entry.
    doc = _load("ifvg_sweep.json")
    assert doc["exit"]["op"] == "session_end"
    assert validate(doc) == []


def test_non_dict_document_rejected() -> None:
    assert validate([]) == ["document must be a JSON object"]


def test_sweep_of_rejects_both_min_atr_and_min_offset() -> None:
    doc = _load("ifvg_sweep.json")
    leaf = doc["entry"]["operands"][0]["operands"][0]
    assert leaf["op"] == "sweep_of"
    leaf["min_atr"] = 0.05  # already has min_offset — now sets both
    errors = validate(doc)
    assert any("sets both min_atr and min_offset" in e for e in errors)


def test_sweep_of_rejects_negative_min_offset() -> None:
    doc = _load("ifvg_sweep.json")
    doc["entry"]["operands"][0]["operands"][0]["min_offset"] = -0.1
    errors = validate(doc)
    assert any("min_offset must be a non-negative number" in e for e in errors)


def test_stop_buffer_must_be_non_negative() -> None:
    doc = _load("ifvg_sweep.json")
    doc["stop"]["buffer"] = -0.30
    errors = validate(doc)
    assert any("stop.buffer must be a non-negative number" in e for e in errors)


def test_stop_buffer_zero_is_valid() -> None:
    doc = _load("ifvg_sweep.json")
    doc["stop"]["buffer"] = 0
    assert validate(doc) == []
