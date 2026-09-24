"""Gate 0 — IR validity (docs/research-loop/gates.md).

Rejects when the document is schema-invalid, or its canonical ir_hash is
already present in the ledger. Both checks are microseconds — this is why
it's gate 0, cheapest-first.
"""
from __future__ import annotations

import sqlite3

from research.gates.types import GateResult
from research.ir.schema import validate as validate_ir
from research.stats.ir_hash import ir_hash as compute_ir_hash

THRESHOLD = 1.0  # measured is 1.0 (valid & novel) or 0.0 (invalid or duplicate)


def is_duplicate(conn: sqlite3.Connection, doc: dict) -> bool:
    row = conn.execute(
        "SELECT 1 FROM hypotheses WHERE ir_hash = ?", (compute_ir_hash(doc),)
    ).fetchone()
    return row is not None


def evaluate(conn: sqlite3.Connection, doc: dict) -> GateResult:
    errors = validate_ir(doc)
    if errors:
        return GateResult(gate=0, passed=False, measured=0.0, threshold=THRESHOLD)
    if is_duplicate(conn, doc):
        return GateResult(gate=0, passed=False, measured=0.0, threshold=THRESHOLD)
    return GateResult(gate=0, passed=True, measured=1.0, threshold=THRESHOLD)
