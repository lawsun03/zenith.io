"""Read-only access to the frozen fold definitions.

The file at FOLDS_PATH is generated exactly once by
`scripts/freeze_research_folds.py` (which lives outside `research/` on
purpose) and then committed. Nothing in this module, or anywhere under
`research/`, regenerates it — CLAUDE.md: "Regenerate folds dynamically for
any reason" is a Do NOT.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from functools import lru_cache

from research.data.paths import FOLDS_PATH


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


@dataclass(frozen=True)
class RobustnessBlock:
    name: str
    start: date
    end: date


@dataclass(frozen=True)
class WalkForwardFold:
    fit_start: date
    fit_end: date
    test_start: date
    test_end: date


@dataclass(frozen=True)
class Folds:
    corpus_start: date
    corpus_end: date
    holdout_start: date
    holdout_end: date
    robustness_blocks: tuple[RobustnessBlock, ...]
    walk_forward_folds: tuple[WalkForwardFold, ...]
    generated_at: str
    frozen: bool


@lru_cache(maxsize=1)
def load_folds() -> Folds:
    """Load the committed folds file. Raises if it is missing or not frozen.

    Cached in-process — this is a read of a static file, not a live query.
    """
    if not FOLDS_PATH.exists():
        raise FileNotFoundError(
            f"{FOLDS_PATH} does not exist. Folds are frozen once by "
            "scripts/freeze_research_folds.py and committed — they are not "
            "generated at runtime."
        )
    raw = json.loads(FOLDS_PATH.read_text())
    if not raw.get("frozen"):
        raise ValueError(f"{FOLDS_PATH} is missing frozen: true")

    return Folds(
        corpus_start=_parse_date(raw["corpus_start"]),
        corpus_end=_parse_date(raw["corpus_end"]),
        holdout_start=_parse_date(raw["holdout"]["start"]),
        holdout_end=_parse_date(raw["holdout"]["end"]),
        robustness_blocks=tuple(
            RobustnessBlock(b["name"], _parse_date(b["start"]), _parse_date(b["end"]))
            for b in raw["robustness_blocks"]
        ),
        walk_forward_folds=tuple(
            WalkForwardFold(
                _parse_date(f["fit_start"]),
                _parse_date(f["fit_end"]),
                _parse_date(f["test_start"]),
                _parse_date(f["test_end"]),
            )
            for f in raw["walk_forward_folds"]
        ),
        generated_at=raw["generated_at"],
        frozen=raw["frozen"],
    )
