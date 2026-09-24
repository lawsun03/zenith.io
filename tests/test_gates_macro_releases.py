"""The dual macro-release combine. Acceptance test: "A candidate whose
with-releases Sharpe is 1.1 and without-releases is 0.4 is scored at 0.4"
(docs/research-loop/PHASE-PROMPTS.md phase 4)."""
from __future__ import annotations

from research.gates.macro_releases import combine_dual
from research.gates.types import GateResult


def _passing_results(sharpe_oos: float) -> list[GateResult]:
    """All 10 gates passing, with gate 5's measured set to `sharpe_oos`."""
    return [
        GateResult(0, True, 1.0, 1.0),
        GateResult(1, True, 0.9, 0.8),
        GateResult(2, True, 3.0, 2.0),
        GateResult(3, True, 0.7, 0.5),
        GateResult(4, True, 0.1, 0.6),   # low-is-better
        GateResult(5, True, sharpe_oos, 0.6),
        GateResult(6, True, 0.98, 0.95),
        GateResult(7, True, sharpe_oos, 1.5),  # low-is-better
        GateResult(8, True, 0.9, 0.6),
        GateResult(9, True, 1.0, 1.0),
    ]


def test_scored_at_the_worse_of_the_two_sharpes():
    with_releases = _passing_results(1.1)
    without_releases = _passing_results(0.4)
    combined = combine_dual(with_releases, without_releases)
    by_gate = {r.gate: r for r in combined}
    assert by_gate[5].measured == 0.4
    assert by_gate[5].passed  # both individually passed gate 5


def test_low_is_better_gate_takes_the_max_as_worse():
    with_releases = _passing_results(1.1)   # gate 7 measured = 1.1 (sharpe, <=1.5 passes)
    without_releases = _passing_results(1.4)  # gate 7 measured = 1.4
    combined = combine_dual(with_releases, without_releases)
    by_gate = {r.gate: r for r in combined}
    assert by_gate[7].measured == 1.4  # the higher (worse, closer to ceiling) of the two


def test_failure_on_either_side_fails_the_combined_gate():
    with_releases = _passing_results(1.1)
    without_releases = _passing_results(0.4)
    without_releases[1] = GateResult(1, False, 0.5, 0.8)  # fails gate 1
    combined = combine_dual(with_releases, without_releases)
    assert not {r.gate: r for r in combined}[1].passed


def test_gate_unreached_on_one_side_is_not_passed():
    with_releases = _passing_results(1.1)          # reaches every gate
    without_releases = _passing_results(0.4)[:3]   # short-circuited after gate 2
    combined = combine_dual(with_releases, without_releases)
    by_gate = {r.gate: r for r in combined}
    assert not by_gate[5].passed
    assert by_gate[5].measured is None
    assert by_gate[5].threshold == 0.6  # still recorded, per gates.md
