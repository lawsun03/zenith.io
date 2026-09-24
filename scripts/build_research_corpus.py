#!/usr/bin/env python3
"""Build the research corpus: fetch per-contract 1-minute bars from
Databento, stitch each root into a continuous series (back-adjusted and
unadjusted), and split the result into corpus/holdout per the frozen folds.

Deliberate, human-run action. This calls the real Databento API and costs
real money against a funded account (research/data/ingest.py's
make_databento_fetch_fn docstring) — never invoked automatically by
anything under research/.

Requires DATABENTO_API_KEY (read from the environment, or from a .env file
at the repo root in KEY=VALUE form).

Usage:
    python scripts/build_research_corpus.py [--roots NQ,ES,GC]
                                             [--start YYYY-MM-DD] [--end YYYY-MM-DD]

Defaults to every root over the full frozen corpus window
(docs/research-loop/folds.json). Pass --start/--end for a smaller pilot
range to validate the pipeline before committing to the full pull.

After this completes, the holdout window sits UNSEALED on disk. Run
`python scripts/seal_holdout.py` yourself to lock it — sealing is never
automated (CLAUDE.md domain invariant 6: "opened only by explicit human
action").
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

import polars as pl

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from research.data.contracts import ContractMonth, contract_months_covering  # noqa: E402
from research.data.folds import load_folds  # noqa: E402
from research.data.ingest import fetch_contract, make_databento_fetch_fn  # noqa: E402
from research.data.instruments import INSTRUMENTS, MONTH_CODE_TO_NUM, ROOTS  # noqa: E402
from research.data.paths import RAW_ROOT, corpus_path, holdout_path  # noqa: E402
from research.data.roll import RollConfig, build_continuous  # noqa: E402


def _load_env_file(path: Path) -> None:
    """Minimal KEY=VALUE .env loader — matches this repo's existing .env
    format (see .env's own header comment); doesn't override an already-set
    environment variable."""
    if not path.exists():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def _add_months(d: date, months: int) -> date:
    total = (d.year * 12 + (d.month - 1)) + months
    return date(total // 12, total % 12 + 1, 1)


def _contract_fetch_window(m: ContractMonth, corpus_start: date, corpus_end: date) -> tuple[date, date] | None:
    """Bounding window for one contract's plausible trading life: 15 months
    before its delivery month through 2 months after (covers the standard
    NQ/ES/GC listing-to-expiry span with margin), clipped to the corpus
    range. Missing the earliest, near-zero-volume months of a far-dated
    contract is harmless — build_continuous rolls on the volume crossover,
    which happens close to expiry, not at first listing."""
    month_num = MONTH_CODE_TO_NUM[m.month_code]
    delivery = date(m.year, month_num, 1)
    start = max(_add_months(delivery, -15), corpus_start)
    end = min(_add_months(delivery, 2), corpus_end)
    if start >= end:
        return None
    return start, end


def build_root(root: str, start: date, end: date, api_key: str) -> dict[str, pl.DataFrame]:
    spec = INSTRUMENTS[root]
    fetch_fn = make_databento_fetch_fn(api_key, spec.dataset)
    months = contract_months_covering(spec, start, end)

    contracts: dict[str, pl.DataFrame] = {}
    order: list[str] = []
    for m in months:
        window = _contract_fetch_window(m, start, end)
        if window is None:
            continue
        win_start, win_end = window
        # cache_key uses the full 4-digit year — m.raw_symbol (single-digit
        # year, e.g. "GCZ3") repeats every decade and is only disambiguated
        # by this window, never by the string itself (research/data/ingest.py).
        # A 16-year corpus spans more than one decade, so this dict/order
        # list MUST key on something decade-unique or two unrelated
        # contracts silently collapse into one.
        cache_key = f"{m.root}{m.month_code}{m.year}"
        print(f"  fetching {m.raw_symbol} ({cache_key}) [{win_start} .. {win_end})", flush=True)
        df = fetch_contract(fetch_fn, RAW_ROOT, root, m.raw_symbol, win_start, win_end,
                             cache_key=cache_key)
        if df.is_empty():
            continue
        contracts[cache_key] = df
        order.append(cache_key)

    if not order:
        raise RuntimeError(f"no contract data fetched for {root} over [{start}, {end})")

    result = build_continuous(contracts, order, RollConfig(), spec.tick_size)
    print(f"  {root}: {len(order)} contracts, {len(result.roll_events)} rolls")

    folds = load_folds()
    for adjustment, df in (("unadjusted", result.unadjusted), ("back_adjusted", result.back_adjusted)):
        _write_split(root, adjustment, df, folds.holdout_start)
    return contracts


def _write_split(root: str, adjustment: str, df: pl.DataFrame, holdout_start: date) -> None:
    d = df["ts"].dt.date()
    corpus_df = df.filter(d < holdout_start)
    holdout_df = df.filter(d >= holdout_start)

    cpath = corpus_path(root, adjustment)
    cpath.parent.mkdir(parents=True, exist_ok=True)
    corpus_df.write_parquet(cpath)

    hpath = holdout_path(root, adjustment)
    hpath.parent.mkdir(parents=True, exist_ok=True)
    holdout_df.write_parquet(hpath)

    print(f"  {root}/{adjustment}: corpus {len(corpus_df)} rows -> {cpath}")
    print(f"  {root}/{adjustment}: holdout {len(holdout_df)} rows -> {hpath}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--roots", default=",".join(ROOTS))
    parser.add_argument("--start", default=None, help="YYYY-MM-DD, default: frozen corpus_start")
    parser.add_argument("--end", default=None, help="YYYY-MM-DD, default: frozen corpus_end")
    args = parser.parse_args()

    _load_env_file(REPO_ROOT / ".env")
    api_key = os.environ.get("DATABENTO_API_KEY")
    if not api_key:
        print("DATABENTO_API_KEY not set (checked environment and .env)", file=sys.stderr)
        sys.exit(1)

    folds = load_folds()
    start = date.fromisoformat(args.start) if args.start else folds.corpus_start
    end = date.fromisoformat(args.end) if args.end else folds.corpus_end
    roots = [r.strip() for r in args.roots.split(",") if r.strip()]

    for root in roots:
        if root not in ROOTS:
            print(f"unknown root {root!r}, must be one of {ROOTS}", file=sys.stderr)
            sys.exit(1)

    for root in roots:
        print(f"=== {root}: {start} .. {end} ===", flush=True)
        build_root(root, start, end, api_key)

    print(
        "\nDone. Holdout data now sits UNSEALED under "
        "var/databento/continuous/*/holdout/. Run `python scripts/seal_holdout.py` "
        "to lock it — sealing is a deliberate human action, never automated here."
    )


if __name__ == "__main__":
    main()
