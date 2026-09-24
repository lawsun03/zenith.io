"""Static guard against the one bug CLAUDE.md and gates.md call out by name:
"Any code path that sorts candidates by performance and takes the top N is
a bug." (CLAUDE.md domain invariant 3 / docs/research-loop/gates.md "After
the gates: blend, do not rank.")

This scans the AST of the promotion path (research/gates/ensemble.py and
research/gates/pipeline.py) for `sorted(`, `list.sort(`, `heapq.nlargest`
and `heapq.nsmallest` — every standard-library way to rank-then-truncate a
collection of candidates. It is intentionally source-level rather than a
behavioural test: a behavioural test can be satisfied by an implementation
that happens to preserve order on today's fixtures while still containing a
latent sort-and-truncate; this test fails the instant one is typed, before
it can ever run against real candidates.
"""
from __future__ import annotations

import ast
from pathlib import Path

PROMOTION_PATH_MODULES = (
    Path(__file__).resolve().parents[1] / "research" / "gates" / "ensemble.py",
    Path(__file__).resolve().parents[1] / "research" / "gates" / "pipeline.py",
)

_FORBIDDEN_NAMES = {"sorted", "nlargest", "nsmallest"}
_FORBIDDEN_METHODS = {"sort"}


def _forbidden_calls(tree: ast.AST) -> list[str]:
    found = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Name) and func.id in _FORBIDDEN_NAMES:
            found.append(func.id)
        elif isinstance(func, ast.Attribute) and func.attr in (_FORBIDDEN_METHODS | _FORBIDDEN_NAMES):
            found.append(func.attr)
    return found


def test_promotion_path_never_sorts_or_truncates_candidates():
    for path in PROMOTION_PATH_MODULES:
        tree = ast.parse(path.read_text(), filename=str(path))
        forbidden = _forbidden_calls(tree)
        assert not forbidden, (
            f"{path} contains a rank-and-truncate call {forbidden} — selection "
            "destroys value (gates.md: 0.07 Sharpe selecting the best vs 0.33 "
            "blending everything). The gates filter garbage; they never rank."
        )
