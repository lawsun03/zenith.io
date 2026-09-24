"""The dual macro-release backtest (docs/research-loop/gates.md, "Macro
releases"): every candidate is evaluated twice — once on the trade sequence
generated with macro-release sessions included, once with them excluded.
Both runs must clear every gate; the candidate's recorded score on each
gate is the WORSE of the two runs. Recorded as ONE trial: callers run this
once per candidate and log a single ledger row, not two.

"Worse" is direction-aware per gate (see types.GATE_DIRECTION) — gates 4 and
7 reject when the measured value is too HIGH, every other gate rejects when
it's too LOW, so "worse" is min() for one set and max() for the other. This
generalizes gates.md's specific example (Sharpe: worse = min) to every gate
in the pipeline uniformly, since every one of them is a single-threshold
check in one direction or the other.
"""
from __future__ import annotations

from research.gates.types import GATE_DIRECTION, GateResult, worse_of


def combine_dual(with_releases: list[GateResult], without_releases: list[GateResult]) -> list[GateResult]:
    by_gate_a = {r.gate: r for r in with_releases}
    by_gate_b = {r.gate: r for r in without_releases}
    gates = sorted(set(by_gate_a) | set(by_gate_b))

    out: list[GateResult] = []
    for g in gates:
        a, b = by_gate_a.get(g), by_gate_b.get(g)
        if a is None or b is None:
            present = a or b
            out.append(GateResult(gate=g, passed=False, measured=None, threshold=present.threshold))
            continue
        if a.measured is None or b.measured is None:
            out.append(GateResult(gate=g, passed=a.passed and b.passed,
                                   measured=None, threshold=a.threshold))
            continue
        measured = worse_of(GATE_DIRECTION[g], a.measured, b.measured)
        out.append(GateResult(gate=g, passed=a.passed and b.passed,
                               measured=measured, threshold=a.threshold))
    return out
