"""Gate 7 — Sharpe ceiling (docs/research-loop/gates.md).

"Not a rejection, a hold": an observed Sharpe above 1.5 is more often a
defect (negative skew, lookahead, an over-generous cost model) than an edge.
The ledger's `outcome` enum (docs/research-loop/ledger.sql) has no "hold"
state distinct from "rejected", so this still short-circuits the pipeline
like every other gate — `first_failed_gate = 7` is exactly the flag a human
reviewer needs to go investigate rather than promote.
"""
from __future__ import annotations

from research.gates.types import GateResult

SHARPE_CEILING = 1.5


def evaluate(sharpe_oos: float) -> GateResult:
    return GateResult(gate=7, passed=sharpe_oos <= SHARPE_CEILING,
                       measured=sharpe_oos, threshold=SHARPE_CEILING)
