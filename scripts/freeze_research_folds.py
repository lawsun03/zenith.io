#!/usr/bin/env python3
"""Cut the research-loop fold definitions ONCE and write the frozen JSON.

This script is deliberately outside `research/` and is never imported by
runtime code — CLAUDE.md Do NOT: "Regenerate folds dynamically for any
reason." Run it by hand, review the diff, commit it.

Re-running it after folds.json already exists refuses to overwrite unless
--force is passed, because changing fold boundaries after any hypothesis has
been tested against them silently invalidates every prior result.

Usage:
    python scripts/freeze_research_folds.py
    python scripts/freeze_research_folds.py --force   # deliberate re-cut
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FOLDS_PATH = REPO_ROOT / "docs" / "research-loop" / "folds.json"

CORPUS_START = date(2010, 6, 6)  # Databento GLBX.MDP3 intraday coverage begins here
HOLDOUT_MONTHS = 18

ROBUSTNESS_BLOCKS = [
    ("2010-13", date(2010, 6, 6), date(2013, 12, 31)),
    ("2014-17", date(2014, 1, 1), date(2017, 12, 31)),
    ("2018-21", date(2018, 1, 1), date(2021, 12, 31)),
    ("2022-25", date(2022, 1, 1), date(2025, 12, 31)),
]

# First test fold needs at least this much fit history behind it.
MIN_FIT_YEARS = 2


def _months_before(d: date, months: int) -> date:
    y, m = d.year, d.month - months
    while m <= 0:
        m += 12
        y -= 1
    day = min(d.day, 28)  # avoid month-length edge cases; folds are year-granular anyway
    return date(y, m, day)


def _year_end(y: int) -> date:
    return date(y, 12, 31)


def _year_start(y: int) -> date:
    return date(y, 1, 1)


def build_walk_forward_folds(corpus_start: date, last_full_test_year: int) -> list[dict]:
    """Expanding-window folds: fit strictly on the past, test one calendar year.

    Fit only on data before the test period (CLAUDE.md statistics rule 8).
    The first test fold starts once MIN_FIT_YEARS of fit history exists.
    """
    first_test_year = corpus_start.year + MIN_FIT_YEARS
    folds = []
    for test_year in range(first_test_year, last_full_test_year + 1):
        fit_end = date(test_year - 1, 12, 31)
        folds.append({
            "fit_start": corpus_start.isoformat(),
            "fit_end": fit_end.isoformat(),
            "test_start": _year_start(test_year).isoformat(),
            "test_end": _year_end(test_year).isoformat(),
        })
    return folds


def build_folds(as_of: date) -> dict:
    holdout_start = _months_before(as_of, HOLDOUT_MONTHS)
    holdout_end = as_of

    # Walk-forward test folds only over full calendar years strictly before
    # the holdout starts — a test fold reaching into the holdout would defeat
    # the point of sealing it. holdout_start's own year is never full (it's
    # cut short by the holdout), so the last usable test year is always the
    # one before it.
    last_full_test_year = holdout_start.year - 1

    walk_forward = build_walk_forward_folds(CORPUS_START, last_full_test_year)

    return {
        "corpus_start": CORPUS_START.isoformat(),
        "corpus_end": as_of.isoformat(),
        "holdout": {
            "start": holdout_start.isoformat(),
            "end": holdout_end.isoformat(),
            "months": HOLDOUT_MONTHS,
        },
        "robustness_blocks": [
            {"name": name, "start": s.isoformat(), "end": e.isoformat()}
            for name, s, e in ROBUSTNESS_BLOCKS
        ],
        "walk_forward_folds": walk_forward,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generator": "scripts/freeze_research_folds.py",
        "frozen": True,
        "_note": (
            "Cut once, committed, and read-only at runtime. Robustness block "
            "end dates are fixed calendar boundaries and may nominally reach "
            "into the holdout window (e.g. 2022-25 vs an 18-month holdout) — "
            "that overlap is expected and harmless: research/data/loader.py "
            "independently refuses any read into [holdout.start, holdout.end] "
            "regardless of which fold or block requested it."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="overwrite an existing folds.json")
    parser.add_argument(
        "--as-of", default=None,
        help="ISO date to treat as 'present' (default: today). For reproducible test fixtures only.",
    )
    args = parser.parse_args()

    if FOLDS_PATH.exists() and not args.force:
        print(
            f"{FOLDS_PATH} already exists. Folds are cut once. "
            "Pass --force to deliberately re-cut (this invalidates the "
            "trial history's fold boundaries — think hard before doing this).",
            file=sys.stderr,
        )
        sys.exit(1)

    as_of = date.fromisoformat(args.as_of) if args.as_of else date.today()
    folds = build_folds(as_of)

    FOLDS_PATH.parent.mkdir(parents=True, exist_ok=True)
    FOLDS_PATH.write_text(json.dumps(folds, indent=2) + "\n")
    print(f"wrote {FOLDS_PATH}")
    print(f"  corpus:  {folds['corpus_start']} -> {folds['corpus_end']}")
    print(f"  holdout: {folds['holdout']['start']} -> {folds['holdout']['end']}")
    print(f"  walk-forward test folds: {len(folds['walk_forward_folds'])}")


if __name__ == "__main__":
    main()
