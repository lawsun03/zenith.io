"""Static enforcement (docs/research-loop/PHASE-PROMPTS.md phase 7a): only
the hypothesis loop's real entrypoint may open a connection to the real
ledger to log hypotheses/ensembles rows, and only the trainer's API routes
may open one to log drill_sessions/drill_decisions rows. Nothing else —
least of all a demo, a fixture, or a test — may connect to LEDGER_DB_PATH
at all, whether by omitting an override (research.ledger.db.get_connection
and get_readonly_connection both default to it) or by naming it explicitly.

This is exactly the failure mode var/ledger/archive/demo-2026-09-23.db
came from: a one-off script called get_connection() with no override and
wrote a "demo" hypothesis (empty gate_results, outcome='blended' — a shape
no real candidate can have, since evaluate_hypothesis always writes all
10 gate results before an ensemble is ever joined) straight into the real
ledger. Source-level rather than behavioural, same reasoning as
tests/test_gates_no_selection.py: a behavioural test only catches a
violation the moment it runs against real data; this fails the instant a
disallowed call is typed, before it can ever touch var/ledger/research.db.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

_CONNECTION_FUNCS = {"get_connection", "get_readonly_connection"}

# path -> None (the whole file may connect to the real ledger) or a set of
# function names (only calls textually inside one of these functions may).
# Anything not listed here gets NO allowance at all.
_ALLOWED_REAL_LEDGER_CALL_SITES: dict[Path, set[str] | None] = {
    REPO_ROOT / "scripts" / "run_hypothesis_loop.py": None,
    REPO_ROOT / "scripts" / "ledger_chat.py": None,  # read-only (get_readonly_connection)
    REPO_ROOT / "app" / "api" / "server.py": {"_trainer_conn"},  # drill_sessions/drill_decisions only
}


def _call_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


def _is_real_ledger_call(node: ast.Call) -> bool:
    """No path argument (both functions default to LEDGER_DB_PATH), or an
    explicit reference to the LEDGER_DB_PATH name."""
    if not node.args:
        return True
    first = node.args[0]
    return isinstance(first, ast.Name) and first.id == "LEDGER_DB_PATH"


def _hits_outside_allowed_functions(tree: ast.Module, allowed_functions: set[str]) -> list[int]:
    """Every real-ledger connection call, excluding ones whose innermost
    enclosing function is in `allowed_functions` — walks function bodies
    explicitly (not ast.walk on the whole tree) so nesting is tracked."""
    hits: list[int] = []

    def walk(node: ast.AST, enclosing_allowed: bool) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(child, enclosing_allowed or child.name in allowed_functions)
                continue
            if isinstance(child, ast.Call) and _call_name(child) in _CONNECTION_FUNCS:
                if _is_real_ledger_call(child) and not enclosing_allowed:
                    hits.append(child.lineno)
            walk(child, enclosing_allowed)

    walk(tree, False)
    return hits


def _iter_repo_python_files():
    for base in ("research", "app", "scripts", "tests"):
        for path in (REPO_ROOT / base).rglob("*.py"):
            if "__pycache__" not in path.parts:
                yield path


def test_only_allowlisted_call_sites_connect_to_the_real_ledger():
    violations: list[str] = []
    for path in _iter_repo_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        allowed = _ALLOWED_REAL_LEDGER_CALL_SITES.get(path)
        if allowed is None and path in _ALLOWED_REAL_LEDGER_CALL_SITES:
            continue  # whole file allowed
        hits = _hits_outside_allowed_functions(tree, allowed or set())
        violations.extend(f"{path.relative_to(REPO_ROOT)}:{line}" for line in hits)

    assert not violations, (
        "these locations connect to the REAL ledger (LEDGER_DB_PATH) without being on "
        "the allowlist in this test — a demo, fixture, or test must always pass an "
        "explicit override path (tmp_path, ':memory:', ...) instead:\n" + "\n".join(violations)
    )


def test_no_test_file_ever_omits_an_explicit_ledger_path():
    """Belt and suspenders on tests specifically, independent of the
    allowlist above: every get_connection/get_readonly_connection call
    anywhere under tests/ must pass an explicit path argument."""
    violations: list[str] = []
    for path in (REPO_ROOT / "tests").rglob("*.py"):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in _CONNECTION_FUNCS and not node.args:
                violations.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")
    assert not violations, (
        f"test file(s) call get_connection()/get_readonly_connection() with no explicit "
        f"path — this silently defaults to the real ledger: {violations}"
    )
