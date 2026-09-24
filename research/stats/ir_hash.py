"""Canonical hashing for strategy IR documents.

Two IR documents that are semantically identical but differ only in key
order must hash identically. This is what gate 0 ("ir_hash already present
in the ledger", docs/research-loop/gates.md) depends on to recognise a
resubmitted idea — without it, re-serializing the same IR with different key
order would silently burn another slot in the annual trial budget on
something already tested.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(doc: Any) -> str:
    """Serialize `doc` so dict key order never affects the output.

    `sort_keys=True` sorts keys at every nesting level, not just the top
    one. List/tuple element order is left untouched because it's semantic
    (e.g. an ordered list of predicates), unlike dict key order.
    """
    return json.dumps(doc, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def ir_hash(doc: Any) -> str:
    """sha256 of the canonical serialization, as a lowercase hex digest."""
    return hashlib.sha256(canonical_json(doc).encode("utf-8")).hexdigest()
