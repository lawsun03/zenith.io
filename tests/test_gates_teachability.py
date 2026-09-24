"""Gate 9 — teachability."""
from __future__ import annotations

from research.gates.teachability import entry_is_teachable, evaluate

RECOGNIZABLE_ENTRY = {"op": "displacement", "direction": "up", "recognizable": True}
NON_RECOGNIZABLE_LEAF = {"op": "bars_elapsed", "n": 3, "recognizable": False}


def test_single_recognizable_leaf_passes():
    doc = {"entry": RECOGNIZABLE_ENTRY}
    assert entry_is_teachable(doc)
    assert evaluate(doc).passed


def test_and_of_recognizable_leaves_passes():
    doc = {"entry": {"op": "and", "operands": [RECOGNIZABLE_ENTRY, RECOGNIZABLE_ENTRY]}}
    assert evaluate(doc).passed


def test_non_recognizable_leaf_in_entry_fails():
    doc = {"entry": {"op": "or", "operands": [RECOGNIZABLE_ENTRY, NON_RECOGNIZABLE_LEAF]}}
    result = evaluate(doc)
    assert not result.passed
    assert result.measured == 0.0
