"""Fold definitions must be cut once, committed, and read-only at runtime.

CLAUDE.md domain invariant 8 (expanding/rolling windows only) and the phase-0
Do NOT ("Regenerate folds dynamically for any reason") both depend on this
file being static. These tests check the committed artifact itself, not a
freshly-generated one — that's the point.
"""
from __future__ import annotations

import ast
import importlib
import json
from datetime import date
from pathlib import Path

import pytest

from research.data.paths import FOLDS_PATH
import research.data.folds as folds_mod


def test_folds_file_exists_and_committed():
    assert FOLDS_PATH.exists(), (
        f"{FOLDS_PATH} must exist and be committed — the acceptance test "
        "checks for a frozen, checked-in file, not a runtime artifact."
    )


def test_folds_file_is_tracked_by_git():
    import subprocess
    result = subprocess.run(
        ["git", "ls-files", "--error-unmatch", str(FOLDS_PATH)],
        cwd=FOLDS_PATH.parent.parent.parent,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"{FOLDS_PATH} exists on disk but is not tracked by git: {result.stderr}"
    )


def test_folds_marked_frozen():
    raw = json.loads(FOLDS_PATH.read_text())
    assert raw["frozen"] is True


def test_load_folds_matches_file_contents():
    folds_mod.load_folds.cache_clear()
    folds = folds_mod.load_folds()
    raw = json.loads(FOLDS_PATH.read_text())
    assert folds.corpus_start == date.fromisoformat(raw["corpus_start"])
    assert folds.corpus_end == date.fromisoformat(raw["corpus_end"])
    assert folds.holdout_start == date.fromisoformat(raw["holdout"]["start"])
    assert folds.holdout_end == date.fromisoformat(raw["holdout"]["end"])
    assert len(folds.robustness_blocks) == 4
    assert len(folds.walk_forward_folds) == len(raw["walk_forward_folds"])


def test_holdout_is_eighteen_months():
    folds = folds_mod.load_folds()
    months = (folds.holdout_end.year - folds.holdout_start.year) * 12 + (
        folds.holdout_end.month - folds.holdout_start.month
    )
    assert months == 18


def test_robustness_blocks_are_the_four_disjoint_periods():
    folds = folds_mod.load_folds()
    names = [b.name for b in folds.robustness_blocks]
    assert names == ["2010-13", "2014-17", "2018-21", "2022-25"]
    # Disjoint: each block ends strictly before the next one starts.
    for a, b in zip(folds.robustness_blocks, folds.robustness_blocks[1:]):
        assert a.end < b.start


def test_walk_forward_folds_are_expanding_and_strictly_past():
    """Every fold fits only on data strictly before its own test period
    (CLAUDE.md statistics rule 8), and fit windows only ever grow."""
    folds = folds_mod.load_folds()
    assert len(folds.walk_forward_folds) >= 3, "need enough folds to be a real walk-forward scheme"

    prev_fit_end = None
    for f in folds.walk_forward_folds:
        assert f.fit_end < f.test_start, "fit window must end strictly before the test window starts"
        assert f.fit_start == folds.corpus_start, "expanding window: fit always starts at the corpus start"
        if prev_fit_end is not None:
            assert f.fit_end > prev_fit_end, "expanding window: fit_end must grow fold over fold"
        prev_fit_end = f.fit_end


def test_walk_forward_folds_never_touch_the_holdout():
    folds = folds_mod.load_folds()
    for f in folds.walk_forward_folds:
        assert f.test_end < folds.holdout_start, (
            f"fold test window {f.test_start}..{f.test_end} reaches into the "
            f"holdout starting {folds.holdout_start}"
        )


def test_missing_folds_file_raises(tmp_path, monkeypatch):
    missing = tmp_path / "nope.json"
    monkeypatch.setattr(folds_mod, "FOLDS_PATH", missing)
    folds_mod.load_folds.cache_clear()
    with pytest.raises(FileNotFoundError):
        folds_mod.load_folds()
    folds_mod.load_folds.cache_clear()  # don't leak the monkeypatched result into other tests


def test_no_runtime_code_path_regenerates_folds():
    """Static check: nothing under research/ imports the freeze script, and
    the one module that reads FOLDS_PATH (research/data/folds.py) contains no
    write call. Folds are frozen by a human running
    scripts/freeze_research_folds.py directly — never from research/ code."""
    research_dir = Path(__file__).resolve().parent.parent / "research"
    write_calls = {"dump", "write_text", "write", "dumps"}
    offenders = []
    for path in research_dir.rglob("*.py"):
        text = path.read_text()
        tree = ast.parse(text, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                names = " ".join(a.name for a in node.names)
                module = getattr(node, "module", "") or ""
                if "freeze_research_folds" in names or "freeze_research_folds" in module:
                    offenders.append(f"{path}: imports the freeze script")
        if "FOLDS_PATH" not in text:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                name = getattr(func, "attr", getattr(func, "id", ""))
                if name in write_calls:
                    offenders.append(f"{path}: calls {name}() while referencing FOLDS_PATH")
    assert not offenders, f"runtime code path(s) that could regenerate folds: {offenders}"
