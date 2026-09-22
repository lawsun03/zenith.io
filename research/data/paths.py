"""Cache and spec file locations.

Downloaded data is a cache, not a project artifact — it never lives under
`research/` or `docs/` (CLAUDE.md repository boundaries: "Scratch, caches and
downloaded data never go in a user-facing directory"). It lives under
`var/databento/` at the repo root, gitignored.

The holdout directory under here is sealed with filesystem permissions by
`scripts/seal_holdout.py` (a human-invoked action, never automated) — that
seal, not any code in this module, is what CLAUDE.md rule 6 depends on.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

CACHE_ROOT = REPO_ROOT / "var" / "databento"
RAW_ROOT = CACHE_ROOT / "raw"                 # per-contract raw pulls
CONTINUOUS_ROOT = CACHE_ROOT / "continuous"   # stitched continuous series

FOLDS_PATH = REPO_ROOT / "docs" / "research-loop" / "folds.json"


def raw_contract_path(root: str, raw_symbol: str) -> Path:
    return RAW_ROOT / root / f"{raw_symbol}.parquet"


def corpus_path(root: str, adjustment: str) -> Path:
    return CONTINUOUS_ROOT / root / "corpus" / f"{adjustment}.parquet"


def holdout_path(root: str, adjustment: str) -> Path:
    return CONTINUOUS_ROOT / root / "holdout" / f"{adjustment}.parquet"


def holdout_dir(root: str) -> Path:
    return CONTINUOUS_ROOT / root / "holdout"
