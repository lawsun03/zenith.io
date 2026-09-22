"""The Polars loading API: a typed frame for (instrument, date range, adjustment).

CLAUDE.md domain invariant 6 — the holdout is off-limits to `research/` code,
enforced by filesystem permissions, not application logic. This module still
carries a fast application-level refusal (`_overlaps_holdout`) as defense in
depth, but the real backstop is that the holdout directory on disk is sealed
(chmod'd unreadable) by `scripts/seal_holdout.py`, a human-run action — so
even a bug or a bypass in the guard below cannot actually read holdout bytes.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Literal

import polars as pl

from research.data import paths as _paths
from research.data.folds import load_folds

Adjustment = Literal["back_adjusted", "unadjusted"]


class HoldoutAccessError(PermissionError):
    """A request would read inside the sealed holdout window."""


def _overlaps_holdout(start: date, end: date) -> bool:
    folds = load_folds()
    return start <= folds.holdout_end and end >= folds.holdout_start


def _read_corpus_parquet(path: Path, start: date, end: date) -> pl.DataFrame:
    """Physically read a continuous-series Parquet file and slice it to
    [start, end]. Deliberately holdout-ignorant: this is the low-level
    primitive that filesystem permissions — not this function's logic —
    must stop from ever reaching data under a `holdout/` directory.
    """
    return (
        pl.scan_parquet(path)
        .filter((pl.col("ts").dt.date() >= start) & (pl.col("ts").dt.date() <= end))
        .collect()
    )


def load_bars(
    root: str,
    start: date,
    end: date,
    adjustment: Adjustment,
    *,
    continuous_root: Path = _paths.CONTINUOUS_ROOT,
) -> pl.DataFrame:
    """Typed frame of continuous-series bars for `root` over [start, end].

    Columns: ts (UTC datetime), open/high/low/close (Float64), volume
    (UInt64), contract (Utf8, which physical contract each bar came from),
    instrument (Utf8).

    Raises HoldoutAccessError before touching the filesystem if the
    requested range overlaps the sealed holdout at all.
    """
    if _overlaps_holdout(start, end):
        folds = load_folds()
        raise HoldoutAccessError(
            f"requested range {start}..{end} overlaps the sealed holdout "
            f"({folds.holdout_start}..{folds.holdout_end}) — research/ code "
            "may never read it (CLAUDE.md domain invariant 6)"
        )

    path = continuous_root / root / "corpus" / f"{adjustment}.parquet"
    df = _read_corpus_parquet(path, start, end)
    return df.with_columns(pl.lit(root).alias("instrument"))
