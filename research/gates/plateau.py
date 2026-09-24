"""Gate 3 — parameter plateau (docs/research-loop/gates.md).

gates.md states the rule qualitatively ("best parameters sit on a spike
rather than a plateau") without a numeric threshold — unlike gates 1, 2, 5
and 8, no source document fixes this number. PLATEAU_MIN_NEIGHBOR_RATIO
below is this implementation's own judgment call (CLAUDE.md rule 1: "state
assumptions explicitly"): the immediate neighbors of the best point in the
sweep grid must retain at least half its performance, on average, or the
peak is treated as an isolated spike rather than a plateau.

The sweep surface is `dict[tuple[float, ...], float]`, one entry per swept
parameter combination, keyed by its position along each of `axes` (one
sorted tuple of candidate values per swept parameter, in the same order as
the surface's key tuples). A "neighbor" of a point is any point exactly one
grid step away along exactly one axis.
"""
from __future__ import annotations

from typing import Sequence

from research.gates.types import GateResult

PLATEAU_MIN_NEIGHBOR_RATIO = 0.5


def _neighbors(point: tuple[float, ...], axes: Sequence[Sequence[float]]) -> list[tuple[float, ...]]:
    out = []
    for i, axis in enumerate(axes):
        idx = axis.index(point[i])
        for step in (-1, 1):
            j = idx + step
            if 0 <= j < len(axis):
                out.append(point[:i] + (axis[j],) + point[i + 1:])
    return out


def evaluate(surface: dict[tuple[float, ...], float], axes: Sequence[Sequence[float]]) -> GateResult:
    if not surface:
        raise ValueError("sweep surface is empty — cannot evaluate a plateau")

    best_point = max(surface, key=surface.get)
    best_val = surface[best_point]

    if best_val <= 0:
        # No positive edge at the peak at all — not a plateau question,
        # a straightforwardly bad candidate. Fail loud rather than divide
        # by a non-positive number.
        return GateResult(gate=3, passed=False, measured=0.0, threshold=PLATEAU_MIN_NEIGHBOR_RATIO)

    neighbor_vals = [surface[p] for p in _neighbors(best_point, axes) if p in surface]
    if not neighbor_vals:
        raise ValueError(
            f"best point {best_point} has no neighbors in the given sweep "
            "surface/axes — cannot assess plateau vs spike"
        )

    ratio = (sum(neighbor_vals) / len(neighbor_vals)) / best_val
    return GateResult(
        gate=3, passed=ratio >= PLATEAU_MIN_NEIGHBOR_RATIO,
        measured=ratio, threshold=PLATEAU_MIN_NEIGHBOR_RATIO,
    )
