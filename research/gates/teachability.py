"""Gate 9 — teachability (docs/research-loop/gates.md).

"Entry conditions may compose only from recognizable predicates." This is
already enforced structurally at gate 0 by research.ir.schema.validate — a
document that reaches gate 9 at all has already passed that check. This
gate re-checks independently anyway, at its own position in the gate
sequence: the same defense-in-depth pattern research/ir/schema.py itself
uses for the banned-term scan (belt-and-suspenders against a future
loosening of the schema validator, not because the check is expected to
ever actually fire here).
"""
from __future__ import annotations

from research.gates.types import GateResult

THRESHOLD = 1.0


def entry_is_teachable(ir_doc: dict) -> bool:
    def walk(node: dict) -> bool:
        op = node.get("op")
        if op in ("and", "or"):
            return all(walk(o) for o in node["operands"])
        return node.get("recognizable") is True

    return walk(ir_doc["entry"])


def evaluate(ir_doc: dict) -> GateResult:
    ok = entry_is_teachable(ir_doc)
    return GateResult(gate=9, passed=ok, measured=1.0 if ok else 0.0, threshold=THRESHOLD)
