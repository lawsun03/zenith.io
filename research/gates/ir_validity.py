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


def is_duplicate(conn: sqlite3.Connection, doc: dict, *, exclude_hypothesis_id: str | None = None) -> bool:
    """`exclude_hypothesis_id` matters for a gate battery run AFTER
    append_hypothesis has already inserted this exact candidate's own row
    (research.loop.gate_runner — gate results are UPDATEs against an
    existing hypothesis_id, so the row necessarily exists by the time gates
    run): without excluding it, a candidate would always find itself as a
    "duplicate" the moment its own row exists, failing gate 0 on every
    single candidate. Unset (the default) preserves the original
    "any matching hash at all" check, correct for evaluating a doc that
    hasn't been appended yet."""
    row = conn.execute(
        "SELECT id FROM hypotheses WHERE ir_hash = ?", (compute_ir_hash(doc),)
    ).fetchone()
    if row is None:
        return False
    if exclude_hypothesis_id is not None and row["id"] == exclude_hypothesis_id:
        return False
    return True


def evaluate(conn: sqlite3.Connection, doc: dict, *, exclude_hypothesis_id: str | None = None) -> GateResult:
    errors = validate_ir(doc)
    if errors:
        return GateResult(gate=0, passed=False, measured=0.0, threshold=THRESHOLD)
    if is_duplicate(conn, doc, exclude_hypothesis_id=exclude_hypothesis_id):
        return GateResult(gate=0, passed=False, measured=0.0, threshold=THRESHOLD)
    return GateResult(gate=0, passed=True, measured=1.0, threshold=THRESHOLD)
