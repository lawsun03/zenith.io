"""Shared types for the gate pipeline (docs/research-loop/gates.md).

`GateResult` is exactly the shape the ledger's `gate_results` JSON column
stores (research/ledger/api.py: `record_gate_result`) — this module is the
one place that shape is defined, so gate implementations and the ledger
never drift apart.

`GATE_DIRECTION` matters for the dual macro-release combine (macro_releases.
combine_dual): every gate in this pipeline is a threshold check, but not all
of them reject on the SAME side of the threshold — gates 4 (walk-forward
decay) and 7 (Sharpe ceiling) reject when the measured value is too HIGH,
every other gate rejects when it's too LOW. "The worse of the two runs"
(gates.md, "Macro releases") therefore means min() for a high-is-better gate
and max() for a low-is-better gate, not min() uniformly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

GATE_NAMES: dict[int, str] = {
    0: "ir_validity",
    1: "frequency_floor",
    2: "cost_survival",
    3: "parameter_plateau",
    4: "walk_forward",
    5: "sharpe_floor",
    6: "deflated_sharpe_pbo",
    7: "sharpe_ceiling",
    8: "combine_simulator",
    9: "teachability",
}

GATE_ORDER: tuple[int, ...] = tuple(sorted(GATE_NAMES))

Direction = Literal["high", "low", "bool"]

# high = passes when measured >= threshold; low = passes when measured <= threshold;
# bool = pass/fail only, direction is meaningless (0/1-valued measured).
GATE_DIRECTION: dict[int, Direction] = {
    0: "bool",
    1: "high",
    2: "high",
    3: "high",
    4: "low",
    5: "high",
    6: "high",
    7: "low",
    8: "high",
    9: "bool",
}


@dataclass(frozen=True)
class GateResult:
    gate: int
    passed: bool
    measured: float | None
    threshold: float | None

    def __post_init__(self) -> None:
        if self.gate not in GATE_NAMES:
            raise ValueError(f"unknown gate number {self.gate}")


def unreached(gate: int, threshold: float | None) -> GateResult:
    """A gate never evaluated because an earlier (cheaper) gate already
    failed. `pass` is False, not None — CLAUDE.md rule 12: "a gate that
    cannot be evaluated is a failure, not a pass." `threshold` is still
    recorded when it's statically knowable ahead of running the gate (every
    threshold in this pipeline is: see pipeline.gate_threshold)."""
    return GateResult(gate=gate, passed=False, measured=None, threshold=threshold)


def worse_of(direction: Direction, a: float, b: float) -> float:
    if direction == "low":
        return max(a, b)
    return min(a, b)
